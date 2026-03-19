package org.flowseal.tgwsproxy

import android.content.Context

class ProxySettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): ProxyConfig {
        return ProxyConfig(
            host = preferences.getString(KEY_HOST, ProxyConfig.DEFAULT_HOST).orEmpty(),
            portText = preferences.getInt(KEY_PORT, ProxyConfig.DEFAULT_PORT).toString(),
            dcIpText = preferences.getString(
                KEY_DC_IP_TEXT,
                ProxyConfig.DEFAULT_DC_IP_LINES.joinToString("\n"),
            ).orEmpty(),
            verbose = preferences.getBoolean(KEY_VERBOSE, false),
        )
    }

    fun save(config: NormalizedProxyConfig) {
        preferences.edit()
            .putString(KEY_HOST, config.host)
            .putInt(KEY_PORT, config.port)
            .putString(KEY_DC_IP_TEXT, config.dcIpList.joinToString("\n"))
            .putBoolean(KEY_VERBOSE, config.verbose)
            .apply()
    }

    companion object {
        private const val PREFS_NAME = "proxy_settings"
        private const val KEY_HOST = "host"
        private const val KEY_PORT = "port"
        private const val KEY_DC_IP_TEXT = "dc_ip_text"
        private const val KEY_VERBOSE = "verbose"
    }
}
