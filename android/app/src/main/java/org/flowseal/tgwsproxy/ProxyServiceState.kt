package org.flowseal.tgwsproxy

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

object ProxyServiceState {
    private val _isRunning = MutableStateFlow(false)
    val isRunning: StateFlow<Boolean> = _isRunning

    private val _isStarting = MutableStateFlow(false)
    val isStarting: StateFlow<Boolean> = _isStarting

    private val _activeConfig = MutableStateFlow<NormalizedProxyConfig?>(null)
    val activeConfig: StateFlow<NormalizedProxyConfig?> = _activeConfig

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError: StateFlow<String?> = _lastError

    fun markStarting(config: NormalizedProxyConfig) {
        _activeConfig.value = config
        _isStarting.value = true
        _isRunning.value = false
        _lastError.value = null
    }

    fun markStarted(config: NormalizedProxyConfig) {
        _activeConfig.value = config
        _isStarting.value = false
        _isRunning.value = true
        _lastError.value = null
    }

    fun markFailed(message: String) {
        _activeConfig.value = null
        _isStarting.value = false
        _isRunning.value = false
        _lastError.value = message
    }

    fun markStopped() {
        _activeConfig.value = null
        _isStarting.value = false
        _isRunning.value = false
    }

    fun clearError() {
        _lastError.value = null
    }
}
