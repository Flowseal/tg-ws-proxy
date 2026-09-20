package org.flowseal.tgwsproxy

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PythonProxyBridgeCfProxyTest {
    @Test
    fun diagnosticArgumentsChooseAutoCustomWorkerAndSecurity() {
        val auto = CfProxyDiagnosticRequest.fromForm(worker = false,
            customEnabled = false, customText = "saved.example", workerEnabled = false,
            workerText = "", noSecure = true).request!!
        assertEquals(listOf("auto", emptyList<String>(), true),
            PythonProxyBridge.diagnosticArguments(auto))
        val custom = CfProxyDiagnosticRequest.fromForm(worker = false,
            customEnabled = true, customText = "one.example\ntwo.example",
            workerEnabled = false, workerText = "", noSecure = false).request!!
        assertEquals(listOf("custom", listOf("one.example", "two.example"), false),
            PythonProxyBridge.diagnosticArguments(custom))
        val worker = CfProxyDiagnosticRequest.fromForm(worker = true,
            customEnabled = false, customText = "", workerEnabled = true,
            workerText = "w1.example\nw2.example", noSecure = true).request!!
        assertEquals(listOf("worker", listOf("w1.example", "w2.example"), true),
            PythonProxyBridge.diagnosticArguments(worker))
    }
    @Test
    fun diagnosticMapKeepsPerDomainPartialFailureDetails() {
        val result = PythonProxyBridge.cfProxyTestResultFromMap(mapOf(
            "ok" to true, "mode" to "worker", "secure" to false,
            "success_count" to 1, "total_count" to 12,
            "per_domain" to mapOf(
                "one.example" to mapOf("1" to "ok", "2" to "HTTP 403"),
                "two.example" to mapOf("1" to "timeout"),
            ),
        ))
        assertEquals("worker", result.mode)
        assertEquals(1, result.successCount)
        assertEquals(12, result.totalCount)
        assertEquals(false, result.secure)
        assertTrue(result.detailLines().contains("one.example: 1/2"))
        assertTrue(result.detailLines().contains("DC2: HTTP 403"))
        assertTrue(result.detailLines().contains("two.example: 0/1"))
    }
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
        assertTrue(kotlin.contains("run_cfproxy_test_json"))
    }
}
