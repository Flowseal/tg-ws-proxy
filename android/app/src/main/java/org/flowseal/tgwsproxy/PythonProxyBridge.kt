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
            File(context.filesDir, "tg-ws-proxy").absolutePath,
            config.host,
            config.port,
            config.secret,
            config.dcIpList,
            config.logMaxMb,
            config.bufferKb,
            config.poolSize,
            config.verbose,
        ).toString()
    }

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
            lastError = json.optString("last_error").ifBlank { null },
        )
    }

    fun getUpdateStatus(context: Context, checkNow: Boolean = false): ProxyUpdateStatus {
        val payload = getModule(context).callAttr("get_update_status_json", checkNow).toString()
        val json = JSONObject(payload)
        return ProxyUpdateStatus(
            currentVersion = json.optString("current_version").ifBlank { "unknown" },
            latestVersion = json.optString("latest").ifBlank { null },
            hasUpdate = json.optBoolean("has_update", false),
            aheadOfRelease = json.optBoolean("ahead_of_release", false),
            checked = json.optBoolean("checked", false),
            htmlUrl = json.optString("html_url").ifBlank { null },
            error = json.optString("error").ifBlank { null },
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
