package org.flowseal.tgwsproxy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProxyConfigTest {
    @Test
    fun multiDomainOptionsReachNormalizedConfig() {
        val config = ProxyConfig(
            cfproxyUserDomainText = "one.example\ntwo.example",
            cfproxyUserDomainEnabled = false,
            cfproxyWorkerDomainText = "worker.example",
            cfproxyWorkerEnabled = true,
            noSecure = true,
            fakeTlsDomain = "tls.example",
            forceTestDc = true,
            proxyProtocol = true,
        ).validate().normalized!!
        assertEquals(listOf("one.example", "two.example"), config.cfproxyUserDomains)
        assertFalse(config.cfproxyUserDomainEnabled)
        assertEquals(listOf("worker.example"), config.cfproxyWorkerDomains)
        assertTrue(config.cfproxyWorkerEnabled)
        assertTrue(config.noSecure)
        assertEquals("tls.example", config.fakeTlsDomain)
        assertTrue(config.forceTestDc)
        assertTrue(config.proxyProtocol)
    }
    private fun validConfig(
        cfproxy: Boolean = true,
        cfproxyPriority: Boolean = true,
        cfproxyUserDomainText: String = "",
    ): ProxyConfig {
        return ProxyConfig(
            host = "127.0.0.1",
            portText = "1443",
            secretText = "0123456789abcdef0123456789abcdef",
            dcIpText = "2:149.154.167.220\n4:149.154.167.220",
            logMaxMbText = "5",
            bufferKbText = "256",
            poolSizeText = "4",
            cfproxy = cfproxy,
            cfproxyPriority = cfproxyPriority,
            cfproxyUserDomainText = cfproxyUserDomainText,
            checkUpdates = true,
            verbose = false,
        )
    }

    @Test
    fun validate_accepts_blank_cfproxy_domain() {
        val result = validConfig(cfproxyUserDomainText = "").validate()

        assertNull(result.errorMessage)
        assertEquals("", result.normalized?.cfproxyUserDomain)
    }

    @Test
    fun validate_accepts_trimmed_cfproxy_domain() {
        val result = validConfig(
            cfproxyUserDomainText = " cdn.example.com ",
        ).validate()

        assertNull(result.errorMessage)
        assertEquals("cdn.example.com", result.normalized?.cfproxyUserDomain)
    }

    @Test
    fun validate_rejects_cfproxy_domain_with_scheme() {
        val result = validConfig(
            cfproxyUserDomainText = "https://cdn.example.com",
        ).validate()

        assertEquals(
            "CfProxy domain должен быть доменным именем без схемы и пути.",
            result.errorMessage,
        )
    }

    @Test
    fun validate_rejects_cfproxy_domain_with_path_or_spaces() {
        val withPath = validConfig(
            cfproxyUserDomainText = "cdn.example.com/path",
        ).validate()
        val withSpace = validConfig(
            cfproxyUserDomainText = "cdn example.com",
        ).validate()

        assertEquals(
            "CfProxy domain должен быть доменным именем без схемы и пути.",
            withPath.errorMessage,
        )
        assertEquals(
            "CfProxy domain должен быть доменным именем без схемы и пути.",
            withSpace.errorMessage,
        )
    }

    @Test
    fun validate_rejects_invalid_retained_cfproxy_domain_before_python_boundary() {
        val result = validConfig(
            cfproxy = false,
            cfproxyUserDomainText = "https://cdn.example.com/path",
        ).validate()

        assertEquals("CfProxy domain должен быть доменным именем без схемы и пути.", result.errorMessage)
    }

    @Test
    fun android_upstream_mode_normalizes_to_direct_only() {
        assertEquals(UpstreamMode.DIRECT, UpstreamMode.normalize(null))
        assertEquals(UpstreamMode.DIRECT, UpstreamMode.normalize("auto"))
        assertEquals(UpstreamMode.DIRECT, UpstreamMode.normalize("relay_ws"))
    }
}
