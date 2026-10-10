from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import httpx

from .balancer import balancer
from .h2_transport import H2Transport
from .stats import stats
from .utils import (
    DC_DEFAULT_IPS, PROTO_TAG_ABRIDGED,
    PROTO_TAG_SECURE,
)

log = logging.getLogger('tg-mtproto-proxy')
MAX_PACKET = 4 * 1024 * 1024
MAX_CHANNEL_REQUESTS = 8
MAX_CHANNEL_BYTES = 8 * 1024 * 1024
MAX_LANE_REQUESTS = 64
MAX_LANE_BYTES = 32 * 1024 * 1024
MAX_LANE_REPLY_BYTES = 32 * 1024 * 1024
DOMAIN_COOLDOWN = 30.0
REQUEST_TIMEOUT = 65.0
SETUP_TIMEOUT = 8.0
REPLAY_IDLE_SECONDS = 1.0
REPLAY_REQUEST_SECONDS = 3.0
REPLAY_CHECK_SECONDS = 0.25
REPLAY_MAX_AGE = 30.0
REPLAY_HISTORY_BYTES = 64 * 1024
REPLAY_HISTORY_PACKETS = 16
MAX_CHANNEL_REPLAYS = 2
REPLAY_SLOT_SECONDS = 30.0
REPLAY_RETRY_SECONDS = 2.0
REPLAY_MAX_ATTEMPTS = 3
SLOW_RESPONSE_SECONDS = 3.0
DIAGNOSTIC_INTERVAL = 5.0


@dataclass
class _RequestTrace:
    packet: int
    started: float
    replay: bool
    size: int
    request: int = 0
    stream: int = 0
    phase: str = 'queued'
    sent_at: Optional[float] = None
    headers_at: Optional[float] = None
    finished_at: Optional[float] = None
    received: int = 0
    cf_ray: str = '-'

    def summary(self, now: float) -> str:
        end = self.finished_at if self.finished_at is not None else now
        headers = self.headers_at if self.headers_at is not None else end
        send_ms = -1 if self.sent_at is None else (self.sent_at - self.started) * 1000
        headers_ms = -1 if self.sent_at is None else max(0, headers - self.sent_at) * 1000
        return ('req=%d pkt=%d sid=%d replay=%d phase=%s age_ms=%.0f '
                'send_ms=%.0f wait_headers_ms=%.0f up=%d down=%d ray=%s' % (
                    self.request, self.packet, self.stream, self.replay, self.phase,
                    (end - self.started) * 1000,
                    send_ms, headers_ms,
                    self.size, self.received, self.cf_ray))


class _NativeDeliveryError(ConnectionError):
    """The local receiver disconnected"""


class _ReplyBufferFull(BufferError):
    """A local memory limit"""


class _MTProtoTransportError(Exception):
    """An endpoint error that the native client must receive, not just EOF."""

    def __init__(self, code: int, source: str):
        super().__init__('%d (%s)' % (code, source))
        self.code = code


@dataclass
class _ReplayPacket:
    sent_at: float
    body: bytes
    sequence: int
    attempts: int = 0
    last_attempt_at: float = 0.0
    pending: Optional[asyncio.Task] = None
    originals: Set[asyncio.Task] = field(default_factory=set, repr=False)
    retired: bool = False

    @property
    def replayed(self) -> bool:
        return self.attempts > 0


async def _read_packet(reader, decryptor, tag: bytes) -> Tuple[bytes, bool]:
    async def plaintext(count: int) -> bytes:
        return decryptor.update(await reader.readexactly(count))

    if tag == PROTO_TAG_ABRIDGED:
        first = (await plaintext(1))[0]
        quick, words = bool(first & 0x80), first & 0x7f
        if words == 0x7f:
            words = int.from_bytes(await plaintext(3), 'little')
        length = words * 4
    else:
        value, = struct.unpack('<I', await plaintext(4))
        quick, length = bool(value & 0x80000000), value & 0x7fffffff
    if not 24 <= length <= MAX_PACKET + (15 if tag == PROTO_TAG_SECURE else 0):
        raise ValueError('native packet length outside supported range: %d' % length)
    body = await plaintext(length)
    if tag == PROTO_TAG_SECURE:
        if body[:8] == b'\x00' * 8:
            packet_length = 20 + int.from_bytes(body[16:20], 'little')
        else:
            packet_length = 24 + ((length - 24) // 16) * 16
        if not 24 <= packet_length <= length or length - packet_length > 15:
            raise ValueError('invalid padded native packet')
        body = body[:packet_length]
    if len(body) % 4 or len(body) > MAX_PACKET:
        raise ValueError('invalid native packet alignment or size')
    return body, quick


def _encode_reply(body: bytes, tag: bytes) -> bytes:
    if not body or len(body) % 4 or len(body) > MAX_PACKET:
        raise ValueError('invalid HTTP MTProto response length: %d' % len(body))
    if tag == PROTO_TAG_ABRIDGED:
        words = len(body) // 4
        prefix = bytes([words]) if words < 0x7f else b'\x7f' + words.to_bytes(3, 'little')
        return prefix + body
    padding = os.urandom(os.urandom(1)[0] % 4) if tag == PROTO_TAG_SECURE else b''
    return struct.pack('<I', len(body) + len(padding)) + body + padding


class _HttpChannel:
    def __init__(self, lane, channel_id: int, label: str):
        self.lane, self.channel_id, self.label = lane, channel_id, label
        self.closed = False
        self.transport_error: Optional[int] = None
        self.pending: Dict[asyncio.Task, int] = {}
        self.pending_since: Dict[asyncio.Task, float] = {}
        self.sent_since: Dict[asyncio.Task, float] = {}
        self.pending_bytes = 0
        self.reply_bytes = 0
        self.queue = asyncio.Queue(maxsize=16)
        self.capacity = asyncio.Condition()
        self.recovery_wakeup = asyncio.Event()
        self.requests = self.up = self.down = self.quick_requests = 0
        self.started = time.monotonic()
        self.receiving_tasks: Set[asyncio.Task] = set()
        self.delivering_since: Optional[float] = None
        self.replay_pending: Set[asyncio.Task] = set()
        self.replay_history: List[_ReplayPacket] = []
        self.replay_key: Optional[bytes] = None
        self.replay_requests = 0
        self.poll_requests = 0
        self.first_poll_gap = 0.0
        self.replay_rotations = 0
        self.capacity_rotations = 0
        self.last_progress = self.started
        self.last_native_send = self.started
        self.close_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.close_done = False
        self.request_traces: Dict[asyncio.Task, _RequestTrace] = {}
        self.recent_traces = deque(maxlen=16)
        self.upload_wait_since: Optional[float] = None
        self.upload_wait_phase = '-'
        self.upload_wait_bytes = 0
        self.http_bytes = self.small_responses = self.large_responses = 0
        self.flow_sample = (self.started, 0, 0, 0, 0, 0, 0, 0)
        lane.channels.add(self)

    @property
    def receiving_http(self) -> int:
        return len(self.receiving_tasks)

    @property
    def native_backpressure(self) -> bool:
        return bool(self.reply_bytes) or self.delivering_since is not None

    def _has_capacity(self, length: int) -> bool:
        return len(self.pending) < MAX_CHANNEL_REQUESTS and self.pending_bytes + length <= MAX_CHANNEL_BYTES

    def _wake_receiver(self) -> None:
        if not self.closed and not self.pending and not self.native_backpressure:
            self.recovery_wakeup.set()

    def _overdue_original(self, entry: _ReplayPacket, now: float) -> bool:
        return entry.retired or any(task in self.sent_since and task not in self.receiving_tasks
                   and now - self.sent_since[task] >= REPLAY_REQUEST_SECONDS
                   for task in entry.originals)

    def _replay_limit(self, entry: _ReplayPacket) -> int:
        return REPLAY_MAX_ATTEMPTS if entry.originals or entry.retired or entry is self.replay_history[-1] else 0

    def _capacity_candidate(self, now: float):
        for task, sent in sorted(self.sent_since.items(), key=lambda pair: (
                pair[0] not in self.replay_pending, pair[1])):
            if (task.done() or task in self.receiving_tasks
                    or now - sent < REPLAY_REQUEST_SECONDS):
                continue
            if task in self.replay_pending:
                return task, None
            entry = next((entry for entry in self.replay_history if task in entry.originals), None)
            if entry is not None and now - entry.sent_at <= REPLAY_MAX_AGE:
                return task, entry
        return None

    async def _make_upload_room(self, now: float) -> None:
        if self.closed or self.upload_wait_since is None:
            return
        if self.upload_wait_phase == 'channel-capacity':
            if self._has_capacity(self.upload_wait_bytes):
                return
            channels = [self]
        elif self.upload_wait_phase == 'lane-capacity':
            if self.lane._has_capacity(self.upload_wait_bytes):
                return
            channels = sorted(self.lane.channels, key=lambda channel: min(
                channel.sent_since.values(), default=now))
        else:
            return
        for channel in channels:
            candidate = channel._capacity_candidate(now)
            if candidate is None:
                continue
            task, entry = candidate
            channel.sent_since.pop(task, None)
            if entry is not None:
                entry.retired = True
            trace = channel.request_traces.get(task)
            if trace is not None:
                trace.phase = 'capacity-' + trace.phase
            if now >= self.lane.next_capacity_log:
                self.lane.next_capacity_log = now + DIAGNOSTIC_INTERVAL
                log.debug('H2 CAPACITY lane=%d waiting_channel=%d release_channel=%d '
                          'reason=%s replay=%d wait_ms=%.0f inflight=%d',
                          self.lane.lane_id, self.channel_id, channel.channel_id,
                          self.upload_wait_phase, task in channel.replay_pending,
                          (now - self.upload_wait_since) * 1000, self.lane.inflight)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            channel.capacity_rotations += 1
            self.lane.capacity_rotations += 1
            return

    def _replay_blocker(self, entry: _ReplayPacket, now: float) -> Optional[str]:
        if not 0 <= now - entry.sent_at <= REPLAY_MAX_AGE:
            return 'expired'
        if entry.attempts >= self._replay_limit(entry):
            return 'budget-used'
        if self.native_backpressure:
            return 'native-backpressure'
        if entry.originals.intersection(self.receiving_tasks):
            return 'original-body'
        if entry.pending in self.receiving_tasks:
            return 'replay-body'
        if entry.replayed and now - entry.last_attempt_at < REPLAY_RETRY_SECONDS:
            return 'backoff-or-progress'
        if self._overdue_original(entry, now):
            return None
        if (entry is not self.replay_history[-1]
                or any(task is not entry.pending for task in self.pending)):
            return 'receiver-active'
        if entry.replayed and now - self.last_progress < REPLAY_IDLE_SECONDS:
            return 'backoff-or-progress'
        return None

    def _replay_due(self, entry: _ReplayPacket, now: float) -> bool:
        return self._replay_blocker(entry, now) is None

    def _wait_state(self, now: float) -> str:
        oldest = min(self.pending_since.values(), default=now)
        recovery_left = sum(max(0, self._replay_limit(entry) - entry.attempts)
                            for entry in self.replay_history if now - entry.sent_at <= REPLAY_MAX_AGE)
        waiting_headers = sum(task not in self.replay_pending and task not in self.receiving_tasks
                              and now - since >= REPLAY_REQUEST_SECONDS
                              for task, since in self.pending_since.items())
        return ('channel=%d pending=%d replays=%d receiving=%d ready_bytes=%d '
                'native_write=%d idle_ms=%.0f oldest_ms=%.0f native_age_ms=%.0f '
                'recovery_left=%d waiting_headers=%d' % (
                    self.channel_id, len(self.pending), len(self.replay_pending),
                    self.receiving_http, self.reply_bytes, self.delivering_since is not None,
                    (now - self.last_progress) * 1000, (now - oldest) * 1000,
                    (now - self.last_native_send) * 1000, recovery_left, waiting_headers))

    def _request_detail(self, trace: _RequestTrace, now: float) -> str:
        entry = next((entry for entry in self.replay_history if entry.sequence == trace.packet), None)
        recovery = 'not-retained'
        attempts = '-'
        if entry is not None:
            attempts = '%d/%d' % (entry.attempts, self._replay_limit(entry))
            blocker = self._replay_blocker(entry, now)
            if blocker is not None:
                recovery = blocker
            elif entry.pending is not None:
                recovery = ('replay-body' if entry.pending in self.receiving_tasks else
                            'probe-%.0fms' % ((now - self.pending_since.get(entry.pending, now)) * 1000))
            elif len(self.replay_pending) >= MAX_CHANNEL_REPLAYS:
                recovery = 'replay-slots'
            elif not self._has_capacity(len(entry.body)):
                recovery = 'channel-capacity'
            elif not self.lane._has_capacity(len(entry.body)):
                recovery = 'lane-capacity'
            elif self.send_lock.locked():
                recovery = 'send-lock'
            else:
                recovery = 'eligible'
        related = [item for item in self.recent_traces if item.packet == trace.packet][-2:]
        return ('channel=%d %s attempts=%s recovery=%s recent=[%s]' % (
            self.channel_id, trace.summary(now), attempts, recovery,
            '; '.join(item.summary(now) for item in related)))

    def _flow_state(self, now: float) -> str:
        previous = self.flow_sample
        current = (now, self.requests, self.replay_requests, self.http_bytes,
                   self.down, self.small_responses, self.large_responses, self.poll_requests)
        self.flow_sample = current
        poll_gap, self.first_poll_gap = self.first_poll_gap, 0.0
        probes = [trace.summary(now) for trace in self.request_traces.values() if trace.replay][:2]
        return ('channel=%d window_ms=%.0f requests=%d replays=%d http_down=%d native_down=%d '
                'small=%d large=%d pending=%d ready=%d upload_wait=%s/%.0fms native_write_ms=%.0f '
                'capacity_rotations=%d polls=%d first_poll_ms=%.0f probes=[%s]' % (
                    self.channel_id, (now - previous[0]) * 1000,
                    *(current[index] - previous[index] for index in range(1, 7)),
                    len(self.pending), self.reply_bytes, self.upload_wait_phase,
                    0 if self.upload_wait_since is None else (now - self.upload_wait_since) * 1000,
                    0 if self.delivering_since is None else (now - self.delivering_since) * 1000,
                    self.capacity_rotations,
                    current[7] - previous[7], poll_gap * 1000,
                    '; '.join(probes)))

    def _fail(self, error: Exception) -> None:
        if self.closed:
            return
        if isinstance(error, _MTProtoTransportError):
            self.transport_error = error.code
        self.closed = True
        self.recovery_wakeup.set()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.reply_bytes = 0
        self.queue.put_nowait(error)

    async def send(self, body: bytes, quick: bool, replay: bool = False) -> bool:
        if replay and self.send_lock.locked():
            return False
        if not replay:
            self.upload_wait_since = time.monotonic()
            self.upload_wait_phase = 'send-lock'
            self.upload_wait_bytes = len(body)
        try:
            async with self.send_lock:
                return await self._send(body, quick, replay)
        finally:
            if not replay:
                self.upload_wait_since = None
                self.upload_wait_phase = '-'
                self.upload_wait_bytes = 0

    async def _send(self, body: bytes, quick: bool, replay: bool) -> bool:
        async with self.capacity:
            if replay and not self._has_capacity(len(body)):
                return False
            if not replay:
                self.upload_wait_phase = 'channel-capacity'
                await asyncio.wait_for(self.capacity.wait_for(lambda: self.closed or self._has_capacity(len(body))),
                                       REQUEST_TIMEOUT)
        if self.closed:
            raise ConnectionError('H2 channel closed')
        if not replay:
            self.upload_wait_phase = 'lane-capacity'
        if not await self.lane._reserve(len(body), wait=not replay):
            return False
        if self.closed:
            await self.lane._release(len(body))
            raise ConnectionError('H2 channel closed')
        replay_packet = self._find_packet(body) if replay else None
        now = time.monotonic()
        if replay and (replay_packet is None or replay_packet.pending is not None
                       or not self._replay_due(replay_packet, now)):
            await self.lane._release(len(body))
            return False
        self.pending_bytes += len(body)
        self.requests += 1
        self.replay_requests += int(replay)
        self.up += len(body)
        stats.bytes_up += len(body)
        if not replay:
            self.last_native_send = now
        native_packet = self._remember_packet(body, now) if not replay else None
        if quick:
            self.quick_requests += 1
        packet = replay_packet or native_packet
        trace = (_RequestTrace(packet.sequence if packet is not None else self.requests,
                               now, replay, len(body))
                 if log.isEnabledFor(logging.DEBUG) else None)
        task = asyncio.create_task(self._run_request(body, replay, packet, trace))
        self.pending[task] = len(body)
        self.pending_since[task] = now
        if trace is not None:
            self.request_traces[task] = trace
        if native_packet is not None:
            native_packet.originals.add(task)
            self._trim_history(now)
        if replay:
            if not self._overdue_original(replay_packet, now):
                self.poll_requests += 1
                if not replay_packet.replayed:
                    self.first_poll_gap = max(self.first_poll_gap, now - self.last_progress)
            self.replay_pending.add(task)
            replay_packet.attempts += 1
            replay_packet.last_attempt_at = now
            replay_packet.pending = task
        return True

    def _find_packet(self, body: bytes) -> Optional[_ReplayPacket]:
        return next((entry for entry in self.replay_history if entry.body == body), None)

    def _remember_packet(self, body: bytes, now: float) -> Optional[_ReplayPacket]:
        key = body[:8]
        if key == b'\x00' * 8:
            return None
        if self.replay_key != key:
            self.replay_history.clear()
            self.replay_key = key
        if len(body) > REPLAY_HISTORY_BYTES:
            return None
        packet = self._find_packet(body)
        if packet is None:
            packet = _ReplayPacket(now, body, self.requests)
            self.replay_history.append(packet)
        return packet

    def _trim_history(self, now: float) -> None:
        retained_bytes = sum(len(entry.body) for entry in self.replay_history)
        while len(self.replay_history) > REPLAY_HISTORY_PACKETS or retained_bytes > REPLAY_HISTORY_BYTES:
            victim = next((entry for entry in self.replay_history if not entry.originals
                           and not (entry.retired and entry.attempts < REPLAY_MAX_ATTEMPTS
                                    and now - entry.sent_at <= REPLAY_MAX_AGE)),
                          self.replay_history[0])
            self.replay_history.remove(victim)
            retained_bytes -= len(victim.body)

    async def _run_request(self, body: bytes, replay: bool, packet: Optional[_ReplayPacket],
                           trace: Optional[_RequestTrace]) -> None:
        task = asyncio.current_task()
        try:
            reply = await self.lane._post(body, self.channel_id, replay=replay, channel=self)
            self.last_progress = time.monotonic()
            if reply and not self.closed:
                if self.queue.full() or self.reply_bytes + len(reply) > MAX_CHANNEL_BYTES:
                    raise BufferError('H2 client response queue exceeded bound')
                self.reply_bytes += len(reply)
                self.queue.put_nowait(reply)
            if trace is not None:
                trace.phase = 'complete'
        except asyncio.CancelledError:
            if trace is not None:
                trace.phase = 'cancelled-' + trace.phase
            raise
        except _MTProtoTransportError as exc:
            if trace is not None:
                trace.phase = 'transport-error'
            stats.h2_errors += 1
            if not self.closed:
                log.warning('[%s] H2 lane=%d channel=%d host=%s MTProto transport error=%s '
                            'request=%d encrypted=%d bytes=%d replay=%d; reporting to client',
                            self.label, self.lane.lane_id, self.channel_id, self.lane.host, exc,
                            trace.request if trace is not None else 0,
                            body[:8] != b'\x00' * 8, len(body), replay)
            self._fail(exc)
        except Exception as exc:
            if trace is not None:
                trace.phase = 'error-' + type(exc).__name__
            stats.h2_errors += 1
            if isinstance(exc, (httpx.HTTPError, OSError, ValueError, BufferError)):
                log.warning('[%s] H2 lane=%d channel=%d request failed: %s: %s; '
                            'detail=[%s]; '
                            'closing native channel, reconnect may select another domain',
                            self.label, self.lane.lane_id, self.channel_id, type(exc).__name__, exc,
                            trace.summary(time.monotonic()) if trace is not None else '-')
            else:
                log.exception('[%s] H2 lane=%d channel=%d unexpected request failure',
                              self.label, self.lane.lane_id, self.channel_id)
            self._fail(exc)
        finally:
            await self._finish_request(task, packet)

    async def _finish_request(self, task: asyncio.Task, packet: Optional[_ReplayPacket] = None) -> None:
        length = self.pending.pop(task, None)
        if length is None:
            return
        trace = self.request_traces.pop(task, None)
        if trace is not None:
            trace.finished_at = time.monotonic()
            self.recent_traces.append(trace)
        self.pending_bytes -= length
        self.pending_since.pop(task, None)
        self.sent_since.pop(task, None)
        self.replay_pending.discard(task)
        if packet is None:
            packet = next((entry for entry in self.replay_history
                           if task in entry.originals or entry.pending is task), None)
        if packet is not None:
            packet.originals.discard(task)
            if packet.pending is task:
                packet.pending = None
        await self.lane._release(length)
        async with self.capacity:
            self.capacity.notify_all()
        self._wake_receiver()

    async def _recover(self) -> None:
        while not self.closed:
            wakeup = asyncio.create_task(self.recovery_wakeup.wait())
            try:
                # wait_for can swallow cancellation when the wakeup completes
                # at the same time on Python 3.8-3.11 (CPython #86296).
                await asyncio.wait((wakeup,), timeout=REPLAY_CHECK_SECONDS)
            finally:
                wakeup.cancel()
                await asyncio.gather(wakeup, return_exceptions=True)
            self.recovery_wakeup.clear()
            now = time.monotonic()
            self.lane.log_wait(now)
            await self._make_upload_room(now)
            if self.closed or self.native_backpressure:
                continue
            def priority(entry):
                overdue = self._overdue_original(entry, now)
                return (not overdue, entry.sequence if overdue else -entry.sequence)

            candidates = sorted(self.replay_history, key=priority)
            for entry in candidates:
                if not self._replay_due(entry, now):
                    continue
                replace = entry.pending
                if replace is None and len(self.replay_pending) >= MAX_CHANNEL_REPLAYS:
                    replace = next((task for task in self.pending if task in self.replay_pending
                                    and task not in self.receiving_tasks), None)
                    if replace is None:
                        continue
                if replace is not None:
                    sent = self.sent_since.get(replace)
                    if (replace in self.receiving_tasks
                            or sent is None or now - sent < REPLAY_SLOT_SECONDS):
                        continue
                    replace.cancel()
                    await asyncio.gather(replace, return_exceptions=True)
                    self.replay_rotations += 1
                await self.send(entry.body, False, replay=True)
                break

    async def receive(self) -> bytes:
        value = await self.queue.get()
        if isinstance(value, Exception):
            raise value
        self.reply_bytes -= len(value)
        self._wake_receiver()
        return value

    async def close(self) -> None:
        async with self.close_lock:
            if self.close_done:
                return
            waiting = self._wait_state(time.monotonic())
            if log.isEnabledFor(logging.DEBUG) and self.request_traces:
                now = time.monotonic()
                traces = sorted(self.request_traces.values(), key=lambda trace: trace.started)[:3]
                log.debug('H2 DETAIL CLOSE lane=%d [%s]', self.lane.lane_id,
                          '; '.join(self._request_detail(trace, now) for trace in traces))
            self._fail(ConnectionError('H2 channel closed'))
            self.closed = True
            tasks = list(self.pending)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for task in list(self.pending):
                await self._finish_request(task)
            self.replay_pending.clear()
            self.replay_history.clear()
            self.sent_since.clear()
            self.request_traces.clear()
            self.recent_traces.clear()
            self.lane.channels.discard(self)
            self.close_done = True
            log.debug('[%s] H2 channel=%d closed lane=%d requests=%d replays=%d rotations=%d capacity_rotations=%d up=%d down=%d '
                     'quickack=%d duration=%.1fs remaining_channels=%d; %s',
                     self.label, self.channel_id, self.lane.lane_id, self.requests,
                     self.replay_requests, self.replay_rotations, self.capacity_rotations, self.up, self.down, self.quick_requests,
                     time.monotonic() - self.started, len(self.lane.channels), waiting)

class _HttpLane:
    def __init__(self, host: str, lane_id: int):
        self.host, self.lane_id = host, lane_id
        self.channels: Set[_HttpChannel] = set()
        self.failed_until = 0.0
        self.closed = False
        self.tcp_connections = self.requests = self.responses = self.errors = 0
        self.slow_headers = self.slow_bodies = self.replays = 0
        self.max_headers_seconds = self.max_body_seconds = 0.0
        self.next_wait_log = self.next_slow_log = 0.0
        self.next_capacity_log = 0.0
        self.capacity_rotations = 0
        self.next_large_slow_log = 0.0
        self.inflight = self.queued_bytes = self.max_inflight = 0
        self.reply_buffer_bytes = 0
        self.capacity = asyncio.Condition()
        self.client = httpx.AsyncClient(
            transport=H2Transport(max_streams=MAX_LANE_REQUESTS),
            trust_env=False,
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=8, pool=REQUEST_TIMEOUT),
        )

    async def _trace(self, event: str, info: Dict) -> None:
        if event == 'connection.connect_tcp.started':
            self.tcp_connections += 1
            stats.h2_tcp_connections += 1
            log.debug('H2 lane=%d TCP_OPEN connection=%d host=%s channels=%d',
                     self.lane_id, self.tcp_connections, self.host, len(self.channels))

    async def _preflight(self) -> None:
        started = time.monotonic()
        async with self.client.stream('HEAD', 'https://' + self.host + '/api',
                                     extensions={'trace': self._trace}) as response:
            if response.http_version != 'HTTP/2':
                raise ValueError('CF /api preflight negotiated ' + response.http_version)
            if not (200 <= response.status_code < 300 or response.status_code in (405, 501)):
                raise ConnectionError('CF /api preflight HTTP %d cf_ray=%s' % (
                    response.status_code, response.headers.get('cf-ray', '-')))
            log.debug('H2 lane=%d ready host=%s status=%d ms=%.0f',
                     self.lane_id, self.host, response.status_code,
                     (time.monotonic() - started) * 1000)

    def _has_capacity(self, length: int) -> bool:
        return self.inflight < MAX_LANE_REQUESTS and self.queued_bytes + length <= MAX_LANE_BYTES

    async def _reserve(self, length: int, wait: bool = True) -> bool:
        async with self.capacity:
            if not wait and not self._has_capacity(length):
                return False
            if wait:
                await asyncio.wait_for(self.capacity.wait_for(lambda: self.closed or self._has_capacity(length)),
                                       REQUEST_TIMEOUT)
            if self.closed:
                raise ConnectionError('H2 lane closed')
            self.inflight += 1
            self.queued_bytes += length
            self.max_inflight = max(self.max_inflight, self.inflight)
            return True

    async def _release(self, length: int) -> None:
        async with self.capacity:
            self.inflight -= 1
            self.queued_bytes -= length
            self.capacity.notify_all()

    async def _post(self, body: bytes, channel_id: int, replay: bool = False,
                    channel: Optional[_HttpChannel] = None) -> bytes:
        self.requests += 1
        self.replays += int(replay)
        stats.h2_requests += 1
        request_id = self.requests
        started = time.monotonic()
        buffered = 0
        receiving = False
        request_task = asyncio.current_task()
        trace = channel.request_traces.get(request_task) if channel is not None else None
        if trace is not None:
            trace.request = request_id
            trace.phase = 'connect-or-slot'

        async def request_trace(event, info):
            await self._trace(event, info)
            if event == 'http2.send_request_body.complete' and channel is not None:
                channel.sent_since[request_task] = time.monotonic()
            if trace is not None:
                if event == 'connection.connect_tcp.started':
                    trace.phase = 'connect'
                elif event == 'http2.wait_for_stream.started':
                    trace.phase = 'stream-slot'
                elif event == 'http2.send_request_headers.started':
                    trace.stream = info['stream_id']
                    trace.phase = 'upload'
                elif event == 'http2.send_request_body.complete':
                    trace.sent_at = time.monotonic()
                    trace.phase = 'headers' if trace.headers_at is None else 'body'

        def response_started():
            nonlocal receiving
            if trace is not None and trace.headers_at is None:
                trace.headers_at = time.monotonic()
                trace.phase = 'body'
            if channel is not None and not receiving:
                channel.receiving_tasks.add(request_task)
                channel.last_progress = time.monotonic()
                receiving = True

        try:
            async with self.client.stream(
                'POST', 'https://' + self.host + '/api', content=body,
                headers={'content-type': 'application/octet-stream', 'accept-encoding': 'identity'},
                extensions={'trace': request_trace,
                            'h2_response_started': response_started}) as response:
                headers_at = time.monotonic()
                if trace is not None:
                    trace.cf_ray = ''.join(c for c in response.headers.get('cf-ray', '-')[:64]
                                           if c.isascii() and (c.isalnum() or c == '-'))
                self.slow_headers += int(headers_at - started >= SLOW_RESPONSE_SECONDS)
                self.max_headers_seconds = max(self.max_headers_seconds, headers_at - started)
                if response.http_version != 'HTTP/2':
                    raise ValueError('CF /api downgraded to ' + response.http_version)
                if response.status_code in (403, 404, 429, 444):
                    raise _MTProtoTransportError(-response.status_code, 'HTTP %d cf_ray=%s' % (
                        response.status_code, response.headers.get('cf-ray', '-')))
                if response.status_code != 200:
                    raise ConnectionError('CF /api HTTP %d cf_ray=%s' % (
                        response.status_code, response.headers.get('cf-ray', '-')))
                if 'text/html' in response.headers.get('content-type', '').lower():
                    raise ValueError('CF /api returned HTML instead of MTProto')
                response_started()
                length_header = response.headers.get('content-length')
                declared_length = int(length_header) if length_header is not None else None
                if declared_length is not None and not 0 <= declared_length <= MAX_PACKET:
                    raise BufferError('H2 response Content-Length exceeds native packet bound')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_PACKET:
                        raise BufferError('H2 response exceeds native packet bound')
                    if self.reply_buffer_bytes + len(chunk) > MAX_LANE_REPLY_BYTES:
                        raise _ReplyBufferFull('H2 lane response buffers exceeded bound')
                    self.reply_buffer_bytes += len(chunk)
                    buffered += len(chunk)
                    content.extend(chunk)
                    if trace is not None:
                        trace.received += len(chunk)
                    if channel is not None:
                        channel.last_progress = time.monotonic()
                received = len(content)
                if (declared_length is not None
                        and response.headers.get('content-encoding', 'identity') == 'identity'
                        and received != declared_length):
                    raise ValueError('incomplete H2 response body')
                if content and len(content) % 4:
                    raise ValueError('CF /api response is not an aligned MTProto packet')
                if len(content) == 4:
                    code, = struct.unpack('<i', content)
                    if code < 0:
                        raise _MTProtoTransportError(code, 'HTTP 200 payload')
                self.responses += 1
                if channel is not None:
                    channel.http_bytes += received
                    channel.small_responses += int(received <= 4096)
                    channel.large_responses += int(received > 4096)
                finished = time.monotonic()
                body_seconds = finished - headers_at
                self.max_body_seconds = max(self.max_body_seconds, body_seconds)
                self.slow_bodies += int(body_seconds >= SLOW_RESPONSE_SECONDS)
                next_slow = self.next_large_slow_log if received > 4096 else self.next_slow_log
                if finished - started >= SLOW_RESPONSE_SECONDS and finished >= next_slow:
                    if received > 4096:
                        self.next_large_slow_log = finished + DIAGNOSTIC_INTERVAL
                    else:
                        self.next_slow_log = finished + DIAGNOSTIC_INTERVAL
                    log.debug('H2 SLOW lane=%d channel=%d request=%d bytes=%d replay=%d '
                              'headers_ms=%.0f body_ms=%.0f detail=[%s]',
                              self.lane_id, channel_id, request_id, received, replay,
                              (headers_at - started) * 1000, body_seconds * 1000,
                              trace.summary(finished) if trace is not None else '-')
                return bytes(content)
        except _MTProtoTransportError:
            self.errors += 1
            raise
        except _ReplyBufferFull:
            raise
        except (httpx.HTTPError, OSError, ValueError, BufferError):
            self.errors += 1
            self.failed_until = time.monotonic() + DOMAIN_COOLDOWN
            raise
        finally:
            self.reply_buffer_bytes -= buffered
            if receiving:
                channel.receiving_tasks.discard(request_task)

    def log_wait(self, now: float) -> None:
        if not log.isEnabledFor(logging.DEBUG) or now < self.next_wait_log:
            return
        self.next_wait_log = now + 1.0
        waiting = [channel for channel in self.channels
                   if ((channel.delivering_since is not None
                        and now - channel.delivering_since >= SLOW_RESPONSE_SECONDS) or any(
                       task not in channel.replay_pending
                       and task not in channel.receiving_tasks
                       and now - started >= SLOW_RESPONSE_SECONDS
                       for task, started in channel.pending_since.items()))
                   and now - channel.last_native_send <= REPLAY_MAX_AGE]
        if waiting:
            self.next_wait_log = now + DIAGNOSTIC_INTERVAL
            waiting.sort(key=lambda channel: channel.channel_id)
            log.debug('H2 WAIT lane=%d affected=%d inflight=%d body_buffer=%d [%s]',
                      self.lane_id, len(waiting), self.inflight, self.reply_buffer_bytes,
                      '; '.join(channel._wait_state(now) for channel in waiting[:8]))
            details = sorted(((channel, trace) for channel in waiting for trace in channel.request_traces.values()
                              if not trace.replay and now - trace.started >= SLOW_RESPONSE_SECONDS),
                             key=lambda pair: pair[1].started)[:12]
            if details:
                log.debug('H2 DETAIL lane=%d [%s]', self.lane_id,
                          '; '.join(channel._request_detail(trace, now) for channel, trace in details))

    def log_flow(self, now: float) -> None:
        if log.isEnabledFor(logging.DEBUG) and self.channels:
            channels = sorted(self.channels, key=lambda channel: channel.channel_id)
            for offset in range(0, len(channels), 8):
                log.debug('H2 FLOW lane=%d [%s]', self.lane_id,
                          '; '.join(channel._flow_state(now) for channel in channels[offset:offset + 8]))

    def summary(self) -> str:
        return ('lane=%d host=%s channels=%d tcp_connections=%d requests=%d replies=%d '
                'errors=%d inflight=%d max_inflight=%d queued_bytes=%d reply_buffer_bytes=%d '
                'replays=%d capacity_rotations=%d slow_headers=%d slow_bodies=%d max_headers_ms=%.0f max_body_ms=%.0f' % (
                    self.lane_id, self.host, len(self.channels), self.tcp_connections,
                    self.requests, self.responses, self.errors, self.inflight,
                    self.max_inflight, self.queued_bytes, self.reply_buffer_bytes,
                    self.replays, self.capacity_rotations, self.slow_headers, self.slow_bodies,
                    self.max_headers_seconds * 1000, self.max_body_seconds * 1000))

    async def close(self) -> None:
        self.closed = True
        async with self.capacity:
            self.capacity.notify_all()
        await asyncio.gather(*(channel.close() for channel in list(self.channels)))
        await self.client.aclose()
        log.debug('H2 CLOSED %s', self.summary())


class CfH2Pool:
    def __init__(self):
        for name in ('httpx', 'httpcore', 'hpack', 'h2'):
            logging.getLogger(name).setLevel(logging.WARNING)
        self.lanes: Dict[str, _HttpLane] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.failed_until: Dict[str, float] = {}
        self.setup_retry_after: Dict[int, float] = {}
        self.next_lane = self.next_channel = 0
        self.closed = False

    async def open(self, dc: int, label: str) -> Optional[_HttpChannel]:
        if self.closed:
            raise ConnectionError('H2 pool closed')
        if time.monotonic() < self.setup_retry_after.get(dc, 0):
            return None
        try:
            return await asyncio.wait_for(self._open(dc, label), SETUP_TIMEOUT)
        except asyncio.TimeoutError:
            self.setup_retry_after[dc] = time.monotonic() + DOMAIN_COOLDOWN
            stats.h2_errors += 1
            log.warning('[%s] H2 DC%d setup timed out; using WS for %.0fs',
                        label, dc, DOMAIN_COOLDOWN)
            return None

    async def _open(self, dc: int, label: str) -> Optional[_HttpChannel]:
        if self.closed:
            raise ConnectionError('H2 pool closed')
        if dc not in DC_DEFAULT_IPS:
            log.warning('[%s] H2 unsupported DC%d -> existing WS route', label, dc)
            return None
        for base_domain in balancer.get_domains_for_dc(dc):
            host = 'kws%d.%s' % (dc, base_domain)
            async with self.locks.setdefault(host, asyncio.Lock()):
                if self.closed:
                    raise ConnectionError('H2 pool closed')
                lane = self.lanes.get(host)
                if time.monotonic() < max(self.failed_until.get(host, 0),
                                         lane.failed_until if lane else 0):
                    continue
                if lane is None:
                    self.next_lane += 1
                    lane = _HttpLane(host, self.next_lane)
                    try:
                        await lane._preflight()
                    except (httpx.HTTPError, OSError, ValueError) as exc:
                        stats.h2_errors += 1
                        self.failed_until[host] = time.monotonic() + DOMAIN_COOLDOWN
                        log.warning('[%s] H2 lane=%d preflight failed host=%s: %s: %s',
                                    label, lane.lane_id, host, type(exc).__name__, exc)
                        await lane.close()
                        continue
                    except asyncio.CancelledError:
                        await lane.close()
                        raise
                    if self.closed:
                        await lane.close()
                        raise ConnectionError('H2 pool closed during preflight')
                    self.lanes[host] = lane
                self.next_channel += 1
                channel = _HttpChannel(lane, self.next_channel, label)
                balancer.update_domain_for_dc(dc, base_domain)
                log.info('[%s] ROUTE DC%d media -> H2 lane=%d channel=%d host=%s '
                         'channels=%d tcp_connections=%d', label, dc, lane.lane_id,
                         channel.channel_id, host, len(lane.channels), lane.tcp_connections)
                return channel
        log.debug('[%s] H2 DC%d unavailable; using WS', label, dc)
        return None

    def log_stats(self) -> None:
        for lane in self.lanes.values():
            if lane.channels:
                log.debug('H2 STATS %s', lane.summary())

    def log_flow(self, now: float) -> None:
        for lane in self.lanes.values():
            lane.log_flow(now)

    async def close(self) -> None:
        self.closed = True
        await asyncio.gather(*(lane.close() for lane in self.lanes.values()))
        self.lanes.clear()


async def bridge_h2(reader, writer, channel: _HttpChannel, ctx, tag: bytes) -> None:
    async def write_native(data):
        try:
            writer.write(ctx.clt_enc.update(data))
            await writer.drain()
        except OSError as exc:
            raise _NativeDeliveryError(str(exc)) from exc

    async def upload() -> None:
        while True:
            try:
                body, quick = await _read_packet(reader, ctx.clt_dec, tag)
            except OSError as exc:
                raise _NativeDeliveryError(str(exc)) from exc
            await channel.send(body, quick)

    async def download() -> None:
        while True:
            body = await channel.receive()
            if channel.closed:
                raise _NativeDeliveryError('H2 channel closed before native response')
            channel.delivering_since = time.monotonic()
            try:
                await write_native(_encode_reply(body, tag))
            finally:
                channel.delivering_since = None
            channel.down += len(body)
            stats.bytes_down += len(body)
            channel.last_progress = time.monotonic()
            channel._wake_receiver()

    tasks = [asyncio.create_task(upload()), asyncio.create_task(download()),
             asyncio.create_task(channel._recover())]
    try:
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if channel.transport_error is not None:
            await write_native(_encode_reply(struct.pack('<i', channel.transport_error), tag))
            channel.down += 4
            stats.bytes_down += 4
        else:
            for task in done:
                task.result()
    except asyncio.IncompleteReadError:
        log.debug('[%s] H2 channel=%d native EOF', channel.label, channel.channel_id)
    except _NativeDeliveryError as exc:
        log.debug('[%s] H2 channel=%d native closed: %s', channel.label, channel.channel_id, exc)
    except (httpx.HTTPError, OSError, ValueError, BufferError, asyncio.TimeoutError) as exc:
        if not channel.closed:
            log.warning('[%s] H2 channel=%d bridge ended: %s: %s',
                        channel.label, channel.channel_id, type(exc).__name__, exc)
    finally:
        await channel.close()
