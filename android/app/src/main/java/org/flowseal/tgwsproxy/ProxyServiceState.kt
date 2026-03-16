package org.flowseal.tgwsproxy

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

object ProxyServiceState {
    private val _isRunning = MutableStateFlow(false)
    val isRunning: StateFlow<Boolean> = _isRunning

    private val _activeConfig = MutableStateFlow<NormalizedProxyConfig?>(null)
    val activeConfig: StateFlow<NormalizedProxyConfig?> = _activeConfig

    fun markStarted(config: NormalizedProxyConfig) {
        _activeConfig.value = config
        _isRunning.value = true
    }

    fun markStopped() {
        _activeConfig.value = null
        _isRunning.value = false
    }
}
