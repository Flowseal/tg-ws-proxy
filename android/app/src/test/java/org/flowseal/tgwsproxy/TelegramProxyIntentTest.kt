package org.flowseal.tgwsproxy

import android.content.ActivityNotFoundException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class TelegramProxyIntentTest {
    @Test
    fun fakeTlsUriUsesUpstreamEeSecretWithHexDomain() {
        val fakeTls = config.copy(fakeTlsDomain = "tls.example")
        assertEquals(
            "tg://proxy?server=127.0.0.1&port=1443&secret=ee0123456789abcdef0123456789abcdef746c732e6578616d706c65",
            TelegramProxyIntent.proxyUri(fakeTls),
        )
    }
    private val config = ProxyConfig(
        host = "127.0.0.1", portText = "1443",
        secretText = "0123456789abcdef0123456789abcdef",
    ).validate().normalized!!

    @Test
    fun proxy_uri_encodes_parameters() {
        assertEquals(
            "tg://proxy?server=edge%2Bone.example&port=1443&secret=dda%26b",
            TelegramProxyIntent.proxyUri(config.copy(host = "edge+one.example", secret = "a&b")),
        )
    }

    @Test
    fun missing_handler_returns_false() {
        assertFalse(TelegramProxyIntent.open(config) { throw ActivityNotFoundException() })
    }
}
