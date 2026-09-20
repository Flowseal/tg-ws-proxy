package org.flowseal.tgwsproxy

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PythonProxyBridgeCfProxyTest {
    @Test
    fun startArgumentsPreserveListsFlagsAndUpstreamFields() {
        val config = ProxyConfig(
            cfproxyUserDomainText = "one.example\ntwo.example",
            cfproxyUserDomainEnabled = false,
            cfproxyWorkerDomainText = "worker.example",
            cfproxyWorkerEnabled = true,
            noSecure = true,
            fakeTlsDomain = "tls.example",
        ).validate().normalized!!
        val args = PythonProxyBridge.startArguments("/tmp/app", config)
        assertEquals(listOf("one.example", "two.example"), args[10])
        assertEquals(false, args[11])
        assertEquals(listOf("worker.example"), args[12])
        assertEquals(true, args[13])
        assertEquals(true, args[14])
        assertEquals("tls.example", args[16])
    }
    @Test
    fun start_arguments_match_task2_python_signature() {
        val config = ProxyConfig(
            host = "127.0.0.2", portText = "1444",
            secretText = "0123456789abcdef0123456789abcdef",
            dcIpText = "2:149.154.167.220", logMaxMbText = "7",
            bufferKbText = "512", poolSizeText = "3", verbose = true,
            cfproxy = true, cfproxyUserDomainText = "example.com",
        ).validate().normalized!!
        assertEquals(
            listOf("/app", "127.0.0.2", 1444, config.secret,
                listOf("2:149.154.167.220"), 7.0, 512, 3, true,
                true, listOf("example.com"), true, emptyList<String>(), false,
                false, false, "", false),
            PythonProxyBridge.startArguments("/app", config),
        )
    }

    @Test
    fun task2_bridge_is_preserved_without_relay_or_old_helper_calls() {
        val python = File("src/main/python/android_proxy_bridge.py").readText()
        val kotlin = File("src/main/java/org/flowseal/tgwsproxy/PythonProxyBridge.kt").readText()
        assertTrue(python.contains("from proxy.app_runtime import ProxyAppRuntime"))
        assertFalse(kotlin.contains("config.relayUrl"))
        assertFalse(kotlin.contains("get_update_status_json"))
        assertFalse(kotlin.contains("run_cfproxy_test_json"))
    }
}
