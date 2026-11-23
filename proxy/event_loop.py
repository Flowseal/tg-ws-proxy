import asyncio
import logging
import signal
import socket
import sys
import threading
from contextlib import contextmanager, suppress


log = logging.getLogger('tg-mtproto-proxy')


class ClientStreamProtocol(asyncio.StreamReaderProtocol):
    def __init__(self, client_cb):
        super().__init__(asyncio.StreamReader(), client_cb)
        self.cancel_setup = None

    def eof_received(self):
        keep_open = super().eof_received()
        if self.cancel_setup is not None:
            self.cancel_setup(False)
        return keep_open

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if self.cancel_setup is not None:
            self.cancel_setup(True)


@contextmanager
def client_setup(reader, writer):
    """Cancel an upstream dial when its client leaves, without reading client bytes."""
    if reader.at_eof() or writer.is_closing():
        raise asyncio.CancelledError()
    protocol = writer.transport.get_protocol()
    if not isinstance(protocol, ClientStreamProtocol):
        yield
        return

    task = asyncio.current_task()

    def cancel(lost):
        if lost or reader.at_eof():
            protocol.cancel_setup = None
            task.cancel()

    protocol.cancel_setup = cancel
    try:
        yield
    finally:
        protocol.cancel_setup = None


def _close_proactor_socket(transport, exc):
    sock = transport._sock
    if sock is None:
        return
    try:
        transport._protocol.connection_lost(exc)
    finally:
        try:
            if sock.fileno() != -1:
                with suppress(ConnectionResetError):
                    sock.shutdown(socket.SHUT_RDWR)
        finally:
            sock.close()
            transport._sock = None
            transport._called_connection_lost = True
            server, transport._server = transport._server, None
            if server is not None:
                if sys.version_info >= (3, 13):
                    server._detach(transport)
                else:
                    server._detach()


def install_proactor_socket_guard(loop):
    # https://github.com/Flowseal/tg-ws-proxy/issues/1614
    if sys.platform == 'win32' and isinstance(loop, asyncio.ProactorEventLoop):
        from asyncio.proactor_events import _ProactorSocketTransport
        _ProactorSocketTransport._call_connection_lost = _close_proactor_socket


def _replace_self_pipe(loop):
    reader, writer = socket.socketpair()
    try:
        reader.setblocking(False)
        writer.setblocking(False)
        if threading.current_thread() is threading.main_thread():
            previous_fd = signal.set_wakeup_fd(writer.fileno())
            if previous_fd != loop._csock.fileno():
                signal.set_wakeup_fd(previous_fd)
    except BaseException:
        reader.close()
        writer.close()
        raise

    old_reader, old_writer = loop._ssock, loop._csock
    loop._ssock, loop._csock = reader, writer
    old_reader.close()
    old_writer.close()


class _SelfPipeGuard:
    """Work around https://github.com/python/cpython/issues/156333"""

    def __init__(self, loop):
        self.loop = loop
        self.read_self_pipe = loop._loop_self_reading

    def __call__(self, future=None):
        loop = self.loop
        if future is not None:
            if loop._self_reading_future is not future:
                return
            try:
                data = future.result()
            except OSError as exc:
                reason = str(exc)
            except BaseException:
                return self.read_self_pipe(future)
            else:
                if data:
                    return self.read_self_pipe(future)
                reason = 'EOF'

            loop._self_reading_future = None
            self._recover(reason)
            return
        return self.read_self_pipe(future)

    def _recover(self, reason):
        loop = self.loop
        if loop.is_closed() or loop._self_reading_future is not None:
            return
        try:
            _replace_self_pipe(loop)
        except OSError as exc:
            log.warning('Windows event loop wakeup socket recovery failed: %s; '
                        'retry in 1s', exc)
            loop.call_later(1, self._recover, reason)
            return
        log.warning('Windows event loop wakeup socket lost (%s); recreated', reason)
        self.read_self_pipe()


def install_self_pipe_guard(loop):
    if sys.platform != 'win32' or not isinstance(loop, asyncio.ProactorEventLoop):
        return
    if not isinstance(loop._loop_self_reading, _SelfPipeGuard):
        guard = _SelfPipeGuard(loop)
        loop._loop_self_reading = guard
        future = loop._self_reading_future
        if future is not None:
            future.remove_done_callback(guard.read_self_pipe)
            future.add_done_callback(guard)
