package com.tgwsproxy

import android.content.Context
import androidx.core.content.FileProvider
import android.content.Intent
import java.io.File

object LogFiles {
    const val FILE_NAME = "tg-ws-proxy.log"

    fun dir(context: Context): File = File(context.filesDir, "logs").apply { mkdirs() }

    fun file(context: Context): File = File(dir(context), FILE_NAME)

    fun tail(context: Context, maxBytes: Long = 256L * 1024): String {
        val f = file(context)
        if (!f.exists()) return ""
        f.inputStream().use { input ->
            val skip = maxOf(0L, f.length() - maxBytes)
            input.skip(skip)
            return input.readBytes().toString(Charsets.UTF_8)
        }
    }

    fun exportIntent(context: Context): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file(context))
        return Intent(Intent.ACTION_SEND)
            .setType("text/plain")
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
}
