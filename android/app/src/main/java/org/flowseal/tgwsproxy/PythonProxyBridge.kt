package org.flowseal.tgwsproxy

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import org.json.JSONObject

object PythonProxyBridge {
    private const val MODULE_NAME = "android_proxy_bridge"
    private val pythonStartLock = Any()

    fun start(context: Context, config: NormalizedProxyConfig): String {
        val module = getModule(context)
        return module.callAttr(
            "start_proxy",
            *startArguments(File(context.filesDir, "tg-ws-proxy").absolutePath, config).toTypedArray(),
        ).toString()
    }

    internal fun startArguments(appDir: String, config: NormalizedProxyConfig): List<Any> = listOf(
            appDir,
            config.host,
            config.port,
            config.secret,
            config.dcIpList,
            config.logMaxMb,
            config.bufferKb,
            config.poolSize,
            config.verbose,
            config.cfproxy,
            config.cfproxyUserDomains,
            config.cfproxyUserDomainEnabled,
            config.cfproxyWorkerDomains,
            config.cfproxyWorkerEnabled,
            config.noSecure,
            config.forceTestDc,
            config.fakeTlsDomain,
            config.proxyProtocol,
        )

    fun stop(context: Context) {
        if (!Python.isStarted()) {
            return
        }
        getModule(context).callAttr("stop_proxy")
    }

    fun getTrafficStats(context: Context): ProxyTrafficStats {
        if (!Python.isStarted()) {
            return ProxyTrafficStats()
        }

        val payload = getModule(context).callAttr("get_runtime_stats_json").toString()
        val json = JSONObject(payload)
        return ProxyTrafficStats(
            bytesUp = json.optLong("bytes_up", 0L),
            bytesDown = json.optLong("bytes_down", 0L),
            running = json.optBoolean("running", false),
            lastTransportRoute = json.optString("last_transport_route").ifBlank { null },
            lastError = json.optString("last_error").ifBlank { null },
        )
    }

    fun getUpdateStatus(context: Context, checkNow: Boolean = false): ProxyUpdateStatus {
        return ProxyUpdateStatus(
            currentVersion = context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "unknown",
            htmlUrl = "https://github.com/Flowseal/tg-ws-proxy/releases/latest",
        )
    }

    fun runCfProxyTest(
        context: Context,
        request: CfProxyDiagnosticRequest,
    ): CfProxyTestResult {
        val payload = getModule(context).callAttr(
            "run_cfproxy_test_json", *diagnosticArguments(request).toTypedArray(),
        ).toString()
        return parseCfProxyTestResult(payload)
    }

    internal fun diagnosticArguments(request: CfProxyDiagnosticRequest): List<Any> =
        listOf(request.mode, request.domains, request.noSecure)

    internal fun parseCfProxyTestResult(payload: String): CfProxyTestResult {
        val json = JSONObject(payload)
        val domains = json.optJSONObject("per_domain") ?: JSONObject()
        val perDomain = linkedMapOf<String, Map<String, String>>()
        for (domain in domains.keys()) {
            val cases = domains.optJSONObject(domain) ?: continue
            val perDc = linkedMapOf<String, String>()
            for (dc in cases.keys()) perDc[dc] = cases.optString(dc)
            perDomain[domain] = perDc
        }
        return cfProxyTestResultFromMap(mapOf(
            "ok" to json.optBoolean("ok"),
            "mode" to json.optString("mode", "auto"),
            "selected_domain" to json.optString("selected_domain"),
            "secure" to json.optBoolean("secure", true),
            "success_count" to json.optInt("success_count"),
            "total_count" to json.optInt("total_count"),
            "per_domain" to perDomain,
        ))
    }

    internal fun cfProxyTestResultFromMap(values: Map<String, Any?>): CfProxyTestResult {
        @Suppress("UNCHECKED_CAST")
        val domains = values["per_domain"] as? Map<String, Map<String, String>> ?: emptyMap()
        return CfProxyTestResult(
            ok = values["ok"] as? Boolean ?: false,
            mode = values["mode"]?.toString().orEmpty().ifBlank { "auto" },
            selectedDomain = values["selected_domain"]?.toString()
                ?.takeUnless { it.isBlank() || it == "null" },
            secure = values["secure"] as? Boolean ?: true,
            successCount = (values["success_count"] as? Number)?.toInt() ?: 0,
            totalCount = (values["total_count"] as? Number)?.toInt() ?: 0,
            perDomain = domains,
        )
    }

    private fun getModule(context: Context) =
        getPython(context.applicationContext).getModule(MODULE_NAME)

    private fun getPython(context: Context): Python {
        if (Python.isStarted()) {
            return Python.getInstance()
        }
        synchronized(pythonStartLock) {
            if (!Python.isStarted()) {
                try {
                    Python.start(AndroidPlatform(context))
                } catch (exc: IllegalStateException) {
                    if (!Python.isStarted()) {
                        throw exc
                    }
                }
            }
        }
        return Python.getInstance()
    }
}

data class ProxyTrafficStats(
    val bytesUp: Long = 0L,
    val bytesDown: Long = 0L,
    val running: Boolean = false,
    val lastTransportRoute: String? = null,
    val lastError: String? = null,
)

data class ProxyUpdateStatus(
    val currentVersion: String = "unknown",
    val latestVersion: String? = null,
    val hasUpdate: Boolean = false,
    val aheadOfRelease: Boolean = false,
    val checked: Boolean = false,
    val htmlUrl: String? = null,
    val error: String? = null,
)

data class CfProxyTestResult(
    val ok: Boolean = false,
    val mode: String = "auto",
    val selectedDomain: String? = null,
    val secure: Boolean = true,
    val successCount: Int = 0,
    val totalCount: Int = 0,
    val perDomain: Map<String, Map<String, String>> = emptyMap(),
) {
    fun detailLines(): String = perDomain.entries.joinToString("\n") { (domain, cases) ->
        val ok = cases.count { it.value == "ok" }
        val failures = cases.entries.filter { it.value != "ok" }
            .joinToString(", ") { (dc, error) -> "DC$dc: $error" }
        "$domain: $ok/${cases.size}" + if (failures.isNotEmpty()) " ($failures)" else ""
    }
}
