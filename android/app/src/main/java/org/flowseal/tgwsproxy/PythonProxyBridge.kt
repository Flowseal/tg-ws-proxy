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

    fun runCfProxyTest(context: Context, customDomain: String): CfProxyTestResult {
        return CfProxyTestResult(detail = "CfProxy diagnostics unavailable in this build")
    }

    internal fun parseCfProxyTestResult(payload: String): CfProxyTestResult {
        val json = JSONObject(payload)
        return cfProxyTestResultFromMap(
            mapOf(
                "ok" to json.opt("ok"),
                "mode" to json.opt("mode"),
                "domain" to json.opt("domain"),
                "selected_domain" to json.opt("selected_domain"),
                "ip" to json.opt("ip"),
                "status" to json.opt("status"),
                "detail" to json.opt("detail"),
            ),
        )
    }

    internal fun cfProxyTestResultFromMap(values: Map<String, Any?>): CfProxyTestResult {
        return CfProxyTestResult(
            ok = values["ok"] as? Boolean ?: false,
            mode = values["mode"]?.toString().orEmpty().ifBlank { "auto" },
            domain = values["domain"]?.toString().orEmpty().ifBlank { null },
            selectedDomain = values["selected_domain"]?.toString().orEmpty().ifBlank { null },
            ip = values["ip"]?.toString().orEmpty().ifBlank { null },
            status = values["status"]?.toString().orEmpty().ifBlank { null },
            detail = values["detail"]?.toString().orEmpty().ifBlank { null },
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
    val domain: String? = null,
    val selectedDomain: String? = null,
    val ip: String? = null,
    val status: String? = null,
    val detail: String? = null,
)
