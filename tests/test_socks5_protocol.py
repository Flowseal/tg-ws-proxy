import asyncio
import socket
import unittest
from unittest.mock import patch

from proxy.tg_ws_proxy import _handle_client, _socks5_reply


class _FakeTransport:
    def get_extra_info(self, name):
        return None

    def get_write_buffer_size(self):
        return 0


class _FakeReader:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    async def readexactly(self, n: int) -> bytes:
        end = self._offset + n
        if end > len(self._payload):
            partial = self._payload[self._offset:]
            self._offset = len(self._payload)
            raise asyncio.IncompleteReadError(partial, n)
        chunk = self._payload[self._offset:end]
        self._offset = end
        return chunk


class _FakeWriter:
    def __init__(self):
        self.transport = _FakeTransport()
        self.writes = []
        self.closed = False
        self.close_calls = 0

    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 50000)
        return None

    def write(self, data: bytes):
        self.writes.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True
        self.close_calls += 1

    async def wait_closed(self):
        return None


def _ipv4_connect_request(ip: str, port: int, cmd: int = 1) -> bytes:
    return bytes([0x05, cmd, 0x00, 0x01]) + socket.inet_aton(ip) + port.to_bytes(2, "big")


def _domain_connect_request(domain: str, port: int, cmd: int = 1) -> bytes:
    encoded = domain.encode("utf-8")
    return (
        bytes([0x05, cmd, 0x00, 0x03, len(encoded)])
        + encoded
        + port.to_bytes(2, "big")
    )


def _ipv6_connect_request(ip: str, port: int) -> bytes:
    return (
        bytes([0x05, 0x01, 0x00, 0x04])
        + socket.inet_pton(socket.AF_INET6, ip)
        + port.to_bytes(2, "big")
    )


class Socks5ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_socks5_greeting(self):
        reader = _FakeReader(b"\x04\x01")
        writer = _FakeWriter()

        await _handle_client(reader, writer)

        self.assertEqual(writer.writes, [])
        self.assertTrue(writer.closed)

    async def test_rejects_unsupported_command(self):
        reader = _FakeReader(b"\x05\x01\x00" + _ipv4_connect_request("1.1.1.1", 443, cmd=2))
        writer = _FakeWriter()

        await _handle_client(reader, writer)

        self.assertEqual(writer.writes, [b"\x05\x00", _socks5_reply(0x07)])
        self.assertTrue(writer.closed)

    async def test_rejects_unsupported_address_type(self):
        reader = _FakeReader(b"\x05\x01\x00" + b"\x05\x01\x00\x02")
        writer = _FakeWriter()

        await _handle_client(reader, writer)

        self.assertEqual(writer.writes, [b"\x05\x00", _socks5_reply(0x08)])
        self.assertTrue(writer.closed)

    async def test_rejects_ipv6_destinations(self):
        reader = _FakeReader(b"\x05\x01\x00" + _ipv6_connect_request("2001:db8::1", 443))
        writer = _FakeWriter()

        await _handle_client(reader, writer)

        self.assertEqual(writer.writes, [b"\x05\x00", _socks5_reply(0x05)])
        self.assertTrue(writer.closed)

    async def test_passthrough_connect_failure_returns_error(self):
        reader = _FakeReader(b"\x05\x01\x00" + _domain_connect_request("example.com", 443))
        writer = _FakeWriter()

        with patch("proxy.tg_ws_proxy.asyncio.open_connection", side_effect=OSError("boom")):
            await _handle_client(reader, writer)

        self.assertEqual(writer.writes, [b"\x05\x00", _socks5_reply(0x05)])
        self.assertTrue(writer.closed)


if __name__ == "__main__":
    unittest.main()
