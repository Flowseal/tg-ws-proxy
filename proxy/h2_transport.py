from __future__ import annotations

import asyncio
import time
from collections import deque
from functools import lru_cache

import httpx
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.events import (
    ConnectionTerminated, DataReceived, RemoteSettingsChanged,
    ResponseReceived, StreamEnded, StreamReset, WindowUpdated,
)
from h2.exceptions import H2Error, StreamClosedError
from h2.settings import SettingCodes, Settings

from .utils import create_ssl_context

STREAM_RECEIVE_WINDOW = 256 * 1024
CONNECTION_RECEIVE_WINDOW = 4 * 1024 * 1024
IO_BATCH_SIZE = 64 * 1024


@lru_cache(maxsize=1)
def _default_ssl_context():
    context = create_ssl_context()
    context.set_alpn_protocols(['h2'])
    return context


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, connection, stream_id, read_timeout):
        self.connection = connection
        self.stream_id = stream_id
        self.read_timeout = read_timeout
        self.changed = asyncio.Event()
        self.chunks = deque()
        self.consumed_credit = 0
        self.headers = None
        self.response_started = None
        self.error = None
        self.ended = False
        self.closed = False
        self.extensions = {'http_version': b'HTTP/2', 'stream_id': stream_id}

    async def wait(self):
        try:
            await asyncio.wait_for(self.changed.wait(), self.read_timeout)
        except asyncio.TimeoutError as exc:
            error = httpx.ReadTimeout('Timed out waiting for H2 stream %d' % self.stream_id)
            # An idle socket can remain established after the path stops
            # delivering data. Reconnect once its last active request times
            # out, while preserving unrelated requests and buffered replies.
            if not any(stream is not self and not stream.ended
                       for stream in self.connection.streams.values()):
                self.connection.fail(error)
            raise error from exc

    def check_error(self):
        if self.error is not None:
            raise self.error

    async def __aiter__(self):
        while not self.closed:
            self.changed.clear()
            self.check_error()
            if self.chunks:
                data, credit = self.chunks.popleft()
                yield data
                self.connection.consumed(self, credit)
            elif self.ended:
                return
            else:
                await self.wait()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            self.chunks.clear()
            self.connection.release(self)
            self.changed.set()


class _Connection:
    def __init__(self, reader, writer, max_streams, write_timeout):
        self.reader, self.writer = reader, writer
        self.max_streams = max_streams
        self.write_timeout = write_timeout
        self.h2 = H2Connection(config=H2Configuration(client_side=True))
        self.h2.local_settings = Settings(
            client=True, initial_values={SettingCodes.ENABLE_PUSH: 0,
                                         SettingCodes.INITIAL_WINDOW_SIZE: STREAM_RECEIVE_WINDOW})
        self.streams = {}
        self.changed = asyncio.Event()
        self.write_lock = asyncio.Lock()
        self.settings_received = False
        self.error = None
        self.h2.initiate_connection()
        self.h2.increment_flow_control_window(CONNECTION_RECEIVE_WINDOW - 65535)
        self.flush()
        self.reader_task = asyncio.create_task(self.read_loop())

    def get_extra_info(self, name):
        return self.writer.get_extra_info(name)

    def flush(self):
        data = self.h2.data_to_send()
        if data:
            self.writer.write(data)
            return True
        return False

    async def drain(self):
        try:
            async with self.write_lock:
                await asyncio.wait_for(self.writer.drain(), self.write_timeout)
        except asyncio.TimeoutError as exc:
            error = httpx.WriteTimeout('H2 connection write timed out')
            self.fail(error)
            raise error from exc
        except OSError as exc:
            error = httpx.WriteError(str(exc))
            self.fail(error)
            raise error from exc

    def fail(self, error):
        if self.error is None:
            self.error = error
            for stream in self.streams.values():
                if not stream.ended:
                    stream.error = error
                stream.changed.set()
            self.changed.set()
            self.writer.close()

    async def read_loop(self):
        try:
            while self.error is None:
                data = await self.reader.read(IO_BATCH_SIZE)
                if not data:
                    raise httpx.ReadError('H2 peer closed the connection')
                events = self.h2.receive_data(data)
                terminated = any(isinstance(e, ConnectionTerminated) for e in events)
                connection_credit = 0
                for event in events:
                    if isinstance(event, RemoteSettingsChanged):
                        self.settings_received = True
                        self.changed.set()
                    elif isinstance(event, WindowUpdated):
                        self.changed.set()
                    elif isinstance(event, ConnectionTerminated):
                        raise httpx.RemoteProtocolError('H2 GOAWAY error=%s last_stream=%s' % (
                            event.error_code, event.last_stream_id))
                    else:
                        stream = self.streams.get(getattr(event, 'stream_id', None))
                        if isinstance(event, DataReceived):
                            if event.flow_controlled_length and not terminated:
                                connection_credit += event.flow_controlled_length
                            if stream is not None:
                                if event.data:
                                    stream.chunks.append((event.data, event.flow_controlled_length))
                                elif not terminated:
                                    self.consumed(stream, event.flow_controlled_length)
                        elif stream is not None:
                            if isinstance(event, ResponseReceived):
                                stream.headers = event.headers
                                if stream.response_started is not None:
                                    stream.response_started()
                            elif isinstance(event, StreamEnded):
                                stream.ended = True
                            elif isinstance(event, StreamReset):
                                stream.error = httpx.RemoteProtocolError(
                                    'H2 stream %d reset: %s' % (event.stream_id, event.error_code))
                                self.changed.set()
                        if stream is not None:
                            stream.changed.set()
                if connection_credit:
                    self.h2.increment_flow_control_window(connection_credit)
                if self.flush():
                    await self.drain()
        except asyncio.CancelledError:
            self.fail(httpx.ReadError('H2 reader stopped'))
            raise
        except (OSError, H2Error, httpx.HTTPError) as exc:
            error = exc if isinstance(exc, httpx.HTTPError) else httpx.RemoteProtocolError(str(exc))
            self.fail(error)

    def check_error(self):
        if self.error is not None:
            raise self.error

    async def reserve(self, timeouts):
        timeout = timeouts.get('pool')
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self.changed.clear()
            self.check_error()
            if self.settings_received and len(self.streams) < min(
                    self.max_streams, self.h2.remote_settings.max_concurrent_streams):
                stream_id = self.h2.get_next_available_stream_id()
                stream = _ResponseStream(self, stream_id, timeouts.get('read'))
                self.streams[stream_id] = stream
                return stream
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except asyncio.TimeoutError as exc:
                raise httpx.PoolTimeout('H2 stream capacity exhausted') from exc

    async def send_body(self, request, stream):
        async for data in request.stream:
            offset = 0
            while offset < len(data):
                self.changed.clear()
                self.check_error()
                stream.check_error()
                count = min(len(data) - offset, IO_BATCH_SIZE,
                            self.h2.local_flow_control_window(stream.stream_id))
                if count <= 0:
                    try:
                        await asyncio.wait_for(self.changed.wait(), self.write_timeout)
                    except asyncio.TimeoutError as exc:
                        raise httpx.WriteTimeout('H2 upload flow control timed out') from exc
                    continue
                batch_end = offset + count
                while offset < batch_end:
                    frame_end = min(batch_end, offset + self.h2.max_outbound_frame_size)
                    self.h2.send_data(stream.stream_id, data[offset:frame_end])
                    offset = frame_end
                self.flush()
                await self.drain()
        self.h2.end_stream(stream.stream_id)
        self.flush()
        await self.drain()

    async def request(self, request):
        timeouts = request.extensions.get('timeout', {})
        trace = request.extensions.get('trace')
        if trace:
            await trace('http2.wait_for_stream.started', {})
        stream = await self.reserve(timeouts)
        stream.response_started = request.extensions.get('h2_response_started')
        stream_id = stream.stream_id
        try:
            headers = [(b':method', request.method.encode('ascii')),
                       (b':scheme', request.url.raw_scheme),
                       (b':authority', request.headers['host'].encode('ascii')),
                       (b':path', request.url.raw_path)]
            headers.extend((k.lower(), v) for k, v in request.headers.raw if k.lower() not in (
                b'host', b'connection', b'keep-alive', b'proxy-connection',
                b'upgrade', b'transfer-encoding'))
            self.h2.send_headers(stream_id, headers)
            self.flush()
            if trace:
                await trace('http2.send_request_headers.started', {'stream_id': stream_id})
            await self.send_body(request, stream)
            if trace:
                await trace('http2.send_request_body.complete', {'stream_id': stream_id})
            while stream.headers is None:
                stream.changed.clear()
                stream.check_error()
                await stream.wait()
            stream.check_error()
            status = next(int(v) for k, v in stream.headers if k == b':status')
            return httpx.Response(status, headers=[(k, v) for k, v in stream.headers
                                                  if not k.startswith(b':')],
                                  stream=stream, extensions=stream.extensions)
        except H2Error as exc:
            await stream.aclose()
            raise httpx.LocalProtocolError(str(exc)) from exc
        except BaseException:
            await stream.aclose()
            raise

    def consumed(self, stream, credit):
        if credit and not stream.ended and not stream.closed and self.error is None:
            stream.consumed_credit += credit
            if stream.chunks and stream.consumed_credit < IO_BATCH_SIZE:
                return
            try:
                self.h2.increment_flow_control_window(stream.consumed_credit, stream.stream_id)
                stream.consumed_credit = 0
                self.flush()
            except StreamClosedError:
                pass

    def release(self, stream):
        self.streams.pop(stream.stream_id, None)
        if self.error is None:
            try:
                self.h2.reset_stream(stream.stream_id, ErrorCodes.CANCEL)
                self.flush()
            except StreamClosedError:
                pass
            except OSError as exc:
                self.fail(httpx.WriteError(str(exc)))
        self.changed.set()

    async def aclose(self):
        self.fail(httpx.ReadError('H2 connection closed'))
        self.reader_task.cancel()
        await asyncio.gather(self.reader_task, return_exceptions=True)
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 2)
        except (OSError, asyncio.TimeoutError):
            pass


class H2Transport(httpx.AsyncBaseTransport):
    def __init__(self, ssl_context=None, max_streams=64):
        if ssl_context is None:
            self.ssl_context = _default_ssl_context()
        else:
            self.ssl_context = ssl_context
            self.ssl_context.set_alpn_protocols(['h2'])
        self.max_streams = max_streams
        self.connection = None
        self.origin = None
        self.connect_lock = asyncio.Lock()
        self.closed = False

    async def handle_async_request(self, request):
        origin = (request.url.host, request.url.port or 443)
        if request.url.scheme != 'https' or self.origin not in (None, origin):
            raise httpx.UnsupportedProtocol('CF H2 transport requires a single HTTPS origin')
        async with self.connect_lock:
            if self.closed:
                raise httpx.ConnectError('H2 transport closed')
            self.origin = origin
            if self.connection is None or self.connection.error is not None:
                if self.connection is not None:
                    await self.connection.aclose()
                timeouts = request.extensions.get('timeout', {})
                trace = request.extensions.get('trace')
                if trace:
                    await trace('connection.connect_tcp.started', {})
                writer = None
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(
                        *origin, ssl=self.ssl_context, server_hostname=origin[0]),
                        timeouts.get('connect'))
                    if writer.get_extra_info('ssl_object').selected_alpn_protocol() != 'h2':
                        raise httpx.RemoteProtocolError('CF origin did not negotiate h2')
                    self.connection = _Connection(reader, writer, self.max_streams, timeouts.get('write'))
                    if trace:
                        await trace('connection.start_tls.complete', {'return_value': self.connection})
                except BaseException as exc:
                    if self.connection is not None:
                        self.connection.fail(httpx.ConnectError('H2 connection setup interrupted'))
                    if writer is not None:
                        writer.close()
                    if isinstance(exc, asyncio.TimeoutError):
                        raise httpx.ConnectTimeout('H2 TLS connect timed out') from exc
                    if isinstance(exc, OSError):
                        raise httpx.ConnectError(str(exc)) from exc
                    raise
            connection = self.connection
        return await connection.request(request)

    async def aclose(self):
        self.closed = True
        async with self.connect_lock:
            if self.connection is not None:
                await self.connection.aclose()
