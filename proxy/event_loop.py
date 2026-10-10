import asyncio
import logging
import signal
import socket
import sys
import threading


log = logging.getLogger('tg-mtproto-proxy')


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
