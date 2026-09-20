package org.flowseal.tgwsproxy

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import java.net.URLEncoder

object TelegramProxyIntent {
    private fun encode(value: String) = URLEncoder.encode(value, "UTF-8").replace("+", "%20")

    fun proxyUri(config: NormalizedProxyConfig): String =
            "tg://proxy?server=${encode(config.host)}&port=${config.port}&secret=dd${encode(config.secret)}"

    fun open(context: Context, config: NormalizedProxyConfig): Boolean {
        return open(config) { uri ->
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    internal fun open(config: NormalizedProxyConfig, launch: (String) -> Unit): Boolean {
        return try {
            launch(proxyUri(config))
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }
}
