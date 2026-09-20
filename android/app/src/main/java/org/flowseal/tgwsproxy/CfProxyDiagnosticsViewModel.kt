package org.flowseal.tgwsproxy

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class CfProxyDiagnosticsState(
    val running: Boolean = false,
    val result: CfProxyTestResult? = null,
    val error: String? = null,
)

class CfProxyDiagnosticsViewModel : ViewModel() {
    private val _state = MutableStateFlow(CfProxyDiagnosticsState())
    val state: StateFlow<CfProxyDiagnosticsState> = _state

    fun reportValidationError(message: String) {
        _state.value = CfProxyDiagnosticsState(error = message)
    }

    fun run(context: Context, config: NormalizedProxyConfig, worker: Boolean) {
        if (_state.value.running) return
        _state.value = CfProxyDiagnosticsState(running = true)
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    PythonProxyBridge.runCfProxyTest(context.applicationContext, config, worker)
                }
            }
            _state.value = result.fold(
                onSuccess = { CfProxyDiagnosticsState(result = it) },
                onFailure = { CfProxyDiagnosticsState(error = it.message ?: it.javaClass.simpleName) },
            )
        }
    }
}
