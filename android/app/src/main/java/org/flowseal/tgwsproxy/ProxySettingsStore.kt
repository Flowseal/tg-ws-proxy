package org.flowseal.tgwsproxy

import android.content.Context

class ProxySettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): ProxyConfig {
        return ProxyConfig(
            host = preferences.getString(KEY_HOST, ProxyConfig.DEFAULT_HOST).orEmpty(),
            portText = preferences.getInt(KEY_PORT, ProxyConfig.DEFAULT_PORT).toString(),
            secretText = preferences.getString(KEY_SECRET, ProxyConfig.DEFAULT_SECRET).orEmpty(),
            dcIpText = preferences.getString(
                KEY_DC_IP_TEXT,
                ProxyConfig.DEFAULT_DC_IP_LINES.joinToString("\n"),
            ).orEmpty(),
            appearance = ProxyConfig.normalizeAppearance(
                preferences.getString(
                    KEY_APPEARANCE,
                    ProxyConfig.DEFAULT_APPEARANCE,
                )
            ),
            logMaxMbText = ProxyConfig.formatDecimal(
                preferences.getFloat(
                    KEY_LOG_MAX_MB,
                    ProxyConfig.DEFAULT_LOG_MAX_MB.toFloat(),
                ).toDouble()
            ),
            bufferKbText = preferences.getInt(
                KEY_BUFFER_KB,
                ProxyConfig.DEFAULT_BUFFER_KB,
            ).toString(),
            poolSizeText = preferences.getInt(
                KEY_POOL_SIZE,
                ProxyConfig.DEFAULT_POOL_SIZE,
            ).toString(),
            cfproxy = preferences.getBoolean(KEY_CFPROXY, ProxyConfig.DEFAULT_CFPROXY),
            cfproxyPriority = preferences.getBoolean(
                KEY_CFPROXY_PRIORITY,
                ProxyConfig.DEFAULT_CFPROXY_PRIORITY,
            ),
            cfproxyUserDomainText = preferences.getString(
                KEY_CFPROXY_USER_DOMAINS,
                preferences.getString(KEY_CFPROXY_USER_DOMAIN, ProxyConfig.DEFAULT_CFPROXY_USER_DOMAIN),
            ).orEmpty(),
            cfproxyUserDomainEnabled = preferences.getBoolean(
                KEY_CFPROXY_USER_DOMAIN_ENABLED,
                !preferences.getString(KEY_CFPROXY_USER_DOMAIN, "").isNullOrBlank(),
            ),
            cfproxyWorkerDomainText = preferences.getString(KEY_CFPROXY_WORKER_DOMAINS, "").orEmpty(),
            cfproxyWorkerEnabled = preferences.getBoolean(KEY_CFPROXY_WORKER_ENABLED, false),
            noSecure = preferences.getBoolean(KEY_NO_SECURE, false),
            fakeTlsDomain = preferences.getString(KEY_FAKE_TLS_DOMAIN, "").orEmpty(),
            forceTestDc = preferences.getBoolean(KEY_FORCE_TEST_DC, false),
            proxyProtocol = preferences.getBoolean(KEY_PROXY_PROTOCOL, false),
            language = preferences.getString(KEY_LANGUAGE, "ru").orEmpty(),
            checkUpdates = preferences.getBoolean(KEY_CHECK_UPDATES, false),
            verbose = preferences.getBoolean(KEY_VERBOSE, false),
        )
    }

    fun save(config: NormalizedProxyConfig) {
        preferences.edit()
            .putString(KEY_HOST, config.host)
            .putInt(KEY_PORT, config.port)
            .putString(KEY_SECRET, config.secret)
            .putString(KEY_DC_IP_TEXT, config.dcIpList.joinToString("\n"))
            .putString(KEY_APPEARANCE, config.appearance)
            .remove(KEY_UPSTREAM_MODE)
            .remove(KEY_RELAY_URL)
            .remove(KEY_RELAY_TOKEN)
            .remove(KEY_DIRECT_WS_TIMEOUT_SECONDS)
            .putFloat(KEY_LOG_MAX_MB, config.logMaxMb.toFloat())
            .putInt(KEY_BUFFER_KB, config.bufferKb)
            .putInt(KEY_POOL_SIZE, config.poolSize)
            .putBoolean(KEY_CFPROXY, config.cfproxy)
            .putBoolean(KEY_CFPROXY_PRIORITY, config.cfproxyPriority)
            .putString(KEY_CFPROXY_USER_DOMAIN, config.cfproxyUserDomain)
            .putString(KEY_CFPROXY_USER_DOMAINS,
                (config.cfproxyUserDomains.ifEmpty { listOfNotNull(config.cfproxyUserDomain.takeIf { it.isNotBlank() }) }).joinToString("\n"))
            .putBoolean(KEY_CFPROXY_USER_DOMAIN_ENABLED, config.cfproxyUserDomainEnabled)
            .putString(KEY_CFPROXY_WORKER_DOMAINS, config.cfproxyWorkerDomains.joinToString("\n"))
            .putBoolean(KEY_CFPROXY_WORKER_ENABLED, config.cfproxyWorkerEnabled)
            .putBoolean(KEY_NO_SECURE, config.noSecure)
            .putString(KEY_FAKE_TLS_DOMAIN, config.fakeTlsDomain)
            .putBoolean(KEY_FORCE_TEST_DC, config.forceTestDc)
            .putBoolean(KEY_PROXY_PROTOCOL, config.proxyProtocol)
            .putString(KEY_LANGUAGE, config.language)
            .putBoolean(KEY_CHECK_UPDATES, config.checkUpdates)
            .putBoolean(KEY_VERBOSE, config.verbose)
            .apply()
    }

    companion object {
        private const val PREFS_NAME = "proxy_settings"
        private const val KEY_HOST = "host"
        private const val KEY_PORT = "port"
        private const val KEY_SECRET = "secret"
        private const val KEY_DC_IP_TEXT = "dc_ip_text"
        private const val KEY_APPEARANCE = "appearance"
        private const val KEY_UPSTREAM_MODE = "upstream_mode"
        private const val KEY_RELAY_URL = "relay_url"
        private const val KEY_RELAY_TOKEN = "relay_token"
        private const val KEY_DIRECT_WS_TIMEOUT_SECONDS = "direct_ws_timeout_seconds"
        private const val KEY_LOG_MAX_MB = "log_max_mb"
        private const val KEY_BUFFER_KB = "buf_kb"
        private const val KEY_POOL_SIZE = "pool_size"
        private const val KEY_CFPROXY = "cfproxy"
        private const val KEY_CFPROXY_PRIORITY = "cfproxy_priority"
        private const val KEY_CFPROXY_USER_DOMAIN = "cfproxy_user_domain"
        private const val KEY_CFPROXY_USER_DOMAINS = "cfproxy_user_domains"
        private const val KEY_CFPROXY_USER_DOMAIN_ENABLED = "cfproxy_user_domain_enabled"
        private const val KEY_CFPROXY_WORKER_DOMAINS = "cfproxy_worker_domains"
        private const val KEY_CFPROXY_WORKER_ENABLED = "cfproxy_worker_enabled"
        private const val KEY_NO_SECURE = "no_secure"
        private const val KEY_FAKE_TLS_DOMAIN = "fake_tls_domain"
        private const val KEY_FORCE_TEST_DC = "force_test_dc"
        private const val KEY_PROXY_PROTOCOL = "proxy_protocol"
        private const val KEY_LANGUAGE = "language"
        private const val KEY_CHECK_UPDATES = "check_updates"
        private const val KEY_VERBOSE = "verbose"
    }
}
