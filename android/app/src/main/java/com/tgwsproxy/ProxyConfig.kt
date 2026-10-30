package com.tgwsproxy

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom

data class ProxyConfig(
    val host: String,
    val port: Int,
    val secret: String,
    val dcIp: List<String>,
    val bufKb: Int,
    val poolSize: Int,
    val cfproxy: Boolean,
    val h2: Boolean,
    val cfproxyUserDomainEnabled: Boolean,
    val cfproxyUserDomain: String,
    val cfproxyWorkerEnabled: Boolean,
    val cfproxyWorkerDomain: String,
    val noSecure: Boolean,
    val verbose: Boolean,
) {
    fun toJson(): String = JSONObject().apply {
        put("host", host)
        put("port", port)
        put("secret", secret)
        put("dc_ip", JSONArray(dcIp))
        put("buf_kb", bufKb)
        put("pool_size", poolSize)
        put("cfproxy", cfproxy)
        put("h2", h2)
        put("cfproxy_user_domain_enabled", cfproxyUserDomainEnabled)
        put("cfproxy_user_domain", cfproxyUserDomain)
        put("cfproxy_worker_enabled", cfproxyWorkerEnabled)
        put("cfproxy_worker_domain", cfproxyWorkerDomain)
        put("no_secure", noSecure)
        put("verbose", verbose)
    }.toString()

    fun telegramLink(): String = "tg://proxy?server=$host&port=$port&secret=$secret"

    fun save(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("host", host)
            .putInt("port", port)
            .putString("secret", secret)
            .putString("dc_ip", dcIp.joinToString("\n"))
            .putInt("buf_kb", bufKb)
            .putInt("pool_size", poolSize)
            .putBoolean("cfproxy", cfproxy)
            .putBoolean("h2", h2)
            .putBoolean("cfproxy_user_domain_enabled", cfproxyUserDomainEnabled)
            .putString("cfproxy_user_domain", cfproxyUserDomain)
            .putBoolean("cfproxy_worker_enabled", cfproxyWorkerEnabled)
            .putString("cfproxy_worker_domain", cfproxyWorkerDomain)
            .putBoolean("no_secure", noSecure)
            .putBoolean("verbose", verbose)
            .apply()
    }

    companion object {
        private const val PREFS = "proxy"
        private const val DEFAULT_DC = "2:149.154.167.220\n4:149.154.167.220"

        fun load(context: Context): ProxyConfig {
            val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val secret = p.getString("secret", null) ?: newSecret().also {
                p.edit().putString("secret", it).apply()
            }
            return ProxyConfig(
                host = p.getString("host", "127.0.0.1")!!,
                port = p.getInt("port", 1443),
                secret = secret,
                dcIp = p.getString("dc_ip", DEFAULT_DC)!!.lines().map { it.trim() }.filter { it.isNotEmpty() },
                bufKb = p.getInt("buf_kb", 256),
                poolSize = p.getInt("pool_size", 4),
                cfproxy = p.getBoolean("cfproxy", true),
                h2 = p.getBoolean("h2", true),
                cfproxyUserDomainEnabled = p.getBoolean("cfproxy_user_domain_enabled", false),
                cfproxyUserDomain = p.getString("cfproxy_user_domain", "")!!,
                cfproxyWorkerEnabled = p.getBoolean("cfproxy_worker_enabled", false),
                cfproxyWorkerDomain = p.getString("cfproxy_worker_domain", "")!!,
                noSecure = p.getBoolean("no_secure", false),
                verbose = p.getBoolean("verbose", false),
            )
        }

        fun newSecret(): String =
            ByteArray(16).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
    }
}
