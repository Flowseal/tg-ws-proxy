package com.tgwsproxy

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

object Updater {
    const val REPO = "Flowseal/tg-ws-proxy"
    private const val LATEST_API = "https://api.github.com/repos/$REPO/releases/latest"
    private const val ASSET_NAME = "TgWsProxy_android.apk"

    data class Release(val tag: String, val version: String, val apkUrl: String, val sha256: String?)

    fun currentVersion(context: Context): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "0"

    fun fetchLatest(): Release {
        val conn = (URL(LATEST_API).openConnection() as HttpURLConnection).apply {
            setRequestProperty("User-Agent", "tg-ws-proxy-update-check")
            setRequestProperty("Accept", "application/vnd.github+json")
            connectTimeout = 10_000
            readTimeout = 10_000
        }
        val body = conn.inputStream.bufferedReader().use { it.readText() }
        val root = JSONObject(body)
        val tag = root.getString("tag_name")
        val assets = root.getJSONArray("assets")
        for (i in 0 until assets.length()) {
            val a = assets.getJSONObject(i)
            if (a.getString("name") == ASSET_NAME) {
                val digest = a.optString("digest", "").removePrefix("sha256:").ifEmpty { null }
                return Release(tag, tag.removePrefix("v"), a.getString("browser_download_url"), digest)
            }
        }
        throw IllegalStateException("в релизе $tag нет $ASSET_NAME")
    }

    fun isNewer(latest: String, current: String): Boolean {
        val a = latest.split(".").map { it.toIntOrNull() ?: 0 }
        val b = current.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    fun download(context: Context, release: Release): File {
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        val target = File(dir, "tg-ws-proxy-${release.version}.apk")
        val conn = (URL(release.apkUrl).openConnection() as HttpURLConnection).apply {
            setRequestProperty("User-Agent", "tg-ws-proxy-update-check")
            instanceFollowRedirects = true
            connectTimeout = 15_000
            readTimeout = 60_000
        }
        conn.inputStream.use { input -> target.outputStream().use { input.copyTo(it) } }
        release.sha256?.let { expected ->
            val actual = sha256(target)
            if (!actual.equals(expected, ignoreCase = true)) {
                target.delete()
                throw IllegalStateException("sha256 mismatch")
            }
        }
        return target
    }

    fun installIntent(context: Context, apk: File): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apk)
        return Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    private fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n <= 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
