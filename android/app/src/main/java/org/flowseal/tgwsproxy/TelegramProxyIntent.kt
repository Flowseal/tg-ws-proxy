package org.flowseal.tgwsproxy

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

object TelegramProxyIntent {
    fun open(context: Context, config: NormalizedProxyConfig): Boolean {
        val uri = Uri.parse(
            "tg://socks?server=${Uri.encode(config.host)}&port=${config.port}"
        )
        val intent = Intent(Intent.ACTION_VIEW, uri)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        return try {
            context.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }
}
