package com.amurcanov.tgwsproxy

import android.content.Context
import android.content.Intent
import android.widget.Toast
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.first

object ProxyController {

    suspend fun startFromSavedSettings(
        context: Context,
        showInvalidPortToast: Boolean = false
    ): Boolean {
        val settingsStore = SettingsStore(context)
        settingsStore.migrateLegacyDefaults()
        val portText = settingsStore.port.first()
        val port = portText.toIntOrNull()
        if (port == null) {
            if (showInvalidPortToast) {
                Toast.makeText(context, context.getString(R.string.invalid_port), Toast.LENGTH_SHORT).show()
            }
            ProxyTileService.requestSync(context)
            return false
        }

        val isExperimental = settingsStore.isExperimentalMode.first()
        val isDcAuto = settingsStore.isDcAuto.first()
        val poolSize = settingsStore.poolSize.first()
        val cfEnabled = settingsStore.cfproxyEnabled.first()
        val customCfDomainEnabled = settingsStore.customCfDomainEnabled.first()
        val customCfDomain = settingsStore.customCfDomain.first().trim()
        val secretKey = ensureSecretKey(settingsStore)

        val parsedIps = buildList {
            if (!isDcAuto) {
                appendDc(1, settingsStore.dc1.first())
                appendDc(2, settingsStore.dc2.first())
                appendDc(3, settingsStore.dc3.first())
                appendDc(4, settingsStore.dc4.first())

                if (isExperimental) {
                    appendDc(5, settingsStore.dc5.first())
                    appendDc(203, settingsStore.dc203.first())
                    appendDc(-1, settingsStore.dc1m.first())
                    appendDc(-2, settingsStore.dc2m.first())
                    appendDc(-3, settingsStore.dc3m.first())
                    appendDc(-4, settingsStore.dc4m.first())
                    appendDc(-5, settingsStore.dc5m.first())
                    appendDc(-203, settingsStore.dc203m.first())
                }
            }
        }.joinToString(",")

        ContextCompat.startForegroundService(
            context,
            Intent(context, ProxyService::class.java).apply {
                action = ProxyService.ACTION_START
                putExtra(ProxyService.EXTRA_PORT, port)
                putExtra(ProxyService.EXTRA_IPS, parsedIps)
                putExtra(ProxyService.EXTRA_POOL_SIZE, poolSize)
                putExtra(ProxyService.EXTRA_CFPROXY_ENABLED, cfEnabled)
                putExtra(ProxyService.EXTRA_CFPROXY_PRIORITY, true)
                putExtra(
                    ProxyService.EXTRA_CFPROXY_DOMAIN,
                    if (customCfDomainEnabled && cfEnabled) customCfDomain else ""
                )
                putExtra(ProxyService.EXTRA_SECRET_KEY, secretKey)
            }
        )
        ProxyTileService.requestSync(context)
        return true
    }

    fun stop(context: Context) {
        context.startService(
            Intent(context, ProxyService::class.java).apply {
                action = ProxyService.ACTION_STOP
            }
        )
        ProxyTileService.requestSync(context)
    }

    private suspend fun ensureSecretKey(settingsStore: SettingsStore): String {
        val current = settingsStore.secretKey.first().trim()
        if (isValidSecret(current)) {
            return current
        }

        val generated = generateRandomSecret()
        settingsStore.saveSecretKey(generated)
        return generated
    }

    private fun MutableList<String>.appendDc(dc: Int, value: String) {
        val ip = value.trim()
        if (ip.isNotBlank()) {
            add("$dc:$ip")
        }
    }

    private fun generateRandomSecret(): String {
        val bytes = ByteArray(16)
        java.security.SecureRandom().nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    private fun isValidSecret(value: String): Boolean {
        return value.length == 32 && value.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }
    }
}
