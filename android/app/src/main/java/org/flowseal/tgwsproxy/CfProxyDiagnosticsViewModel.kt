package org.flowseal.tgwsproxy

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class CfProxyDiagnosticError {
    CUSTOM_REQUIRED,
    WORKER_REQUIRED,
    INVALID_DOMAIN,
}

data class CfProxyDiagnosticValidation(
    val request: CfProxyDiagnosticRequest? = null,
    val error: CfProxyDiagnosticError? = null,
)

data class CfProxyDiagnosticRequest(
    val mode: String,
    val domains: List<String>,
    val noSecure: Boolean,
) {
    companion object {
        fun fromForm(
            worker: Boolean,
            customEnabled: Boolean,
            customText: String,
            workerEnabled: Boolean,
            workerText: String,
            noSecure: Boolean,
        ): CfProxyDiagnosticValidation {
            val mode = when {
                worker -> "worker"
                customEnabled -> "custom"
                else -> "auto"
            }
            if (worker && !workerEnabled) {
                return CfProxyDiagnosticValidation(error = CfProxyDiagnosticError.WORKER_REQUIRED)
            }
            val domains = when (mode) {
                "worker" -> splitDomains(workerText)
                "custom" -> splitDomains(customText)
                else -> emptyList()
            }
            if (domains.isEmpty() && mode != "auto") {
                return CfProxyDiagnosticValidation(error = if (worker) {
                    CfProxyDiagnosticError.WORKER_REQUIRED
                } else {
                    CfProxyDiagnosticError.CUSTOM_REQUIRED
                })
            }
            if (domains.any { !isHostname(it) }) {
                return CfProxyDiagnosticValidation(error = CfProxyDiagnosticError.INVALID_DOMAIN)
            }
            return CfProxyDiagnosticValidation(
                request = CfProxyDiagnosticRequest(mode, domains, noSecure),
            )
        }

        private fun splitDomains(text: String): List<String> = text
            .split(Regex("[,;\\r\\n]+"))
            .map(String::trim)
            .filter(String::isNotEmpty)
            .distinct()

        private fun isHostname(domain: String): Boolean = domain.length <= 253 &&
            domain.split('.').all { label ->
                label.isNotEmpty() && label.length <= 63 &&
                    label.first().isAsciiAlphanumeric() &&
                    label.last().isAsciiAlphanumeric() &&
                    label.all { it.isAsciiAlphanumeric() || it == '-' }
            }

        private fun Char.isAsciiAlphanumeric(): Boolean =
            this in 'a'..'z' || this in 'A'..'Z' || this in '0'..'9'
    }
}

data class CfProxyDiagnosticsState(
    val running: Boolean = false,
    val request: CfProxyDiagnosticRequest? = null,
    val result: CfProxyTestResult? = null,
    val error: String? = null,
)

class CfProxyDiagnosticsViewModel : ViewModel() {
    private val _state = MutableStateFlow(CfProxyDiagnosticsState())
    val state: StateFlow<CfProxyDiagnosticsState> = _state

    fun reportValidationError(message: String) {
        _state.value = CfProxyDiagnosticsState(error = message)
    }

    fun run(context: Context, request: CfProxyDiagnosticRequest) {
        run(request) { PythonProxyBridge.runCfProxyTest(context.applicationContext, request) }
    }

    internal fun run(request: CfProxyDiagnosticRequest, tester: suspend () -> CfProxyTestResult) {
        if (_state.value.running) return
        _state.value = CfProxyDiagnosticsState(running = true, request = request)
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) { tester() }
            }
            _state.value = result.fold(
                onSuccess = { CfProxyDiagnosticsState(request = request, result = it) },
                onFailure = { CfProxyDiagnosticsState(request = request,
                    error = it.message ?: it.javaClass.simpleName) },
            )
        }
    }
}
