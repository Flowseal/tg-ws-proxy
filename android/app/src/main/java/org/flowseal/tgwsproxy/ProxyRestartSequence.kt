package org.flowseal.tgwsproxy

internal object ProxyRestartSequence {
    fun run(
        config: NormalizedProxyConfig,
        clearError: () -> Unit,
        invalidateTraffic: () -> Unit,
        publishStarting: (NormalizedProxyConfig) -> Unit,
        requestRestart: (NormalizedProxyConfig) -> Unit,
    ) {
        invalidateTraffic()
        clearError()
        publishStarting(config)
        requestRestart(config)
    }
}
