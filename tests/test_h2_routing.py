"""H2 belongs to CF fallback, never to the direct route."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from proxy import bridge, tg_ws_proxy
from proxy.config import proxy_config
from proxy.utils import PROTO_TAG_INTERMEDIATE


class H2RoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_pool_hit_never_opens_h2(self):
        reader = Mock()
        writer = Mock()
        writer.get_extra_info.return_value = None
        writer.wait_closed = AsyncMock()
        ws = Mock(send_init=AsyncMock())
        pool = SimpleNamespace(open=AsyncMock())
        with patch.object(proxy_config, 'dc_redirects', {2: '149.154.167.51'}), \
                patch.object(proxy_config, 'force_test_dc', False), \
                patch.object(tg_ws_proxy, 'cf_h2_pool', pool), \
                patch.object(tg_ws_proxy, 'set_sock_opts'), \
                patch.object(tg_ws_proxy, '_read_client_init', AsyncMock(
                    return_value=(b'', reader, writer, 'test'))), \
                patch.object(tg_ws_proxy, '_try_handshake', return_value=(
                    2, True, PROTO_TAG_INTERMEDIATE, b'')), \
                patch.object(tg_ws_proxy, '_build_crypto_ctx'), \
                patch.object(tg_ws_proxy.ws_pool, 'get', AsyncMock(return_value=ws)), \
                patch.object(tg_ws_proxy, 'bridge_ws_reencrypt', AsyncMock()) as direct, \
                patch.object(tg_ws_proxy, 'do_fallback', AsyncMock()) as fallback:
            await tg_ws_proxy._handle_client(reader, writer, b'')
        direct.assert_awaited_once()
        ws.send_init.assert_awaited_once()
        fallback.assert_not_awaited()
        pool.open.assert_not_awaited()

    async def test_pool_miss_goes_straight_to_fallback_including_test_dcs(self):
        for dc, forced, expected_test in [(2, False, False),
                                          (10002, False, True),
                                          (2, True, True)]:
            with self.subTest(dc=dc, forced=forced):
                reader = Mock()
                writer = Mock(wait_closed=AsyncMock())
                writer.get_extra_info.return_value = None
                with patch.object(proxy_config, 'force_test_dc', forced), \
                        patch.object(tg_ws_proxy, 'set_sock_opts'), \
                        patch.object(tg_ws_proxy, '_read_client_init', AsyncMock(
                            return_value=(b'', reader, writer, 'test'))), \
                        patch.object(tg_ws_proxy, '_try_handshake', return_value=(
                            dc, False, PROTO_TAG_INTERMEDIATE, b'')), \
                        patch.object(tg_ws_proxy, '_build_crypto_ctx'), \
                        patch.object(tg_ws_proxy.ws_pool, 'get', AsyncMock(return_value=None)) as get, \
                        patch('proxy.raw_websocket.RawWebSocket.connect', AsyncMock()) as connect, \
                        patch.object(tg_ws_proxy, 'do_fallback', AsyncMock(return_value=True)) as fallback:
                    await tg_ws_proxy._handle_client(reader, writer, b'')
                get.assert_awaited_once_with(2, False, is_test_dc=expected_test)
                fallback.assert_awaited_once()
                self.assertEqual(fallback.call_args.args[4:7], (2, expected_test, False))
                connect.assert_not_awaited()

    async def test_cf_fallback_order_and_route_guards(self):
        cases = [
            # media, test DC, CF enabled, Worker succeeds, H2 opens, expected
            (True, False, True, False, True, ['h2', 'bridge']),
            (True, False, True, False, False, ['h2', 'ws']),
            (False, False, True, False, True, ['ws']),
            (True, True, True, False, True, ['tcp']),
            (True, False, False, False, True, ['tcp']),
            (True, False, True, True, True, ['worker']),
        ]
        for media, test_dc, cf, worker, opens, expected in cases:
            with self.subTest(expected=expected, media=media, test_dc=test_dc, cf=cf):
                calls = []

                async def open_h2(*args):
                    calls.append('h2')
                    return object() if opens else None

                def route(name):
                    async def run(*args, **kwargs):
                        calls.append(name)
                        return True
                    return run

                pool = SimpleNamespace(open=AsyncMock(side_effect=open_h2))
                with patch.object(proxy_config, 'fallback_cfproxy', cf), \
                        patch.object(proxy_config, 'cfproxy_worker_domains',
                                     ['worker.example'] if worker else []), \
                        patch.object(bridge, 'bridge_h2', side_effect=route('bridge')), \
                        patch.object(bridge, '_cfproxy_fallback', side_effect=route('ws')), \
                        patch.object(bridge, '_cfproxy_worker_fallback', side_effect=route('worker')), \
                        patch.object(bridge, '_tcp_fallback', side_effect=route('tcp')):
                    writer = Mock()
                    writer.is_closing.return_value = False
                    result = await bridge.do_fallback(
                        asyncio.StreamReader(), writer, b'', 'test', 2, test_dc, media, '', Mock(),
                        h2_pool=pool, proto_tag=PROTO_TAG_INTERMEDIATE)
                self.assertTrue(result)
                self.assertEqual(calls, expected)
