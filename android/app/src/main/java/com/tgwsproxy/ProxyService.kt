package com.tgwsproxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import chaquo.chaquopy.Python
import java.security.SecureRandom

class ProxyService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannel()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
        )
        if (!running) {
            running = true
            val secret = secret(this)
            Thread {
                Python.getInstance().getModule("tg_android")
                    .callAttr("start", secret, PORT)
            }.start()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Python.getInstance().getModule("tg_android").callAttr("stop")
        running = false
        super.onDestroy()
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Прокси",
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("TG WS Proxy работает")
            .setContentText("127.0.0.1:$PORT")
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setOngoing(true)
            .build()

    companion object {
        const val PORT = 1443
        private const val CHANNEL_ID = "proxy"
        private const val NOTIFICATION_ID = 1
        private const val PREFS = "proxy"
        private const val KEY_SECRET = "secret"

        @Volatile
        var running = false

        fun secret(context: Context): String {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            prefs.getString(KEY_SECRET, null)?.let { return it }
            val bytes = ByteArray(16).also { SecureRandom().nextBytes(it) }
            val secret = bytes.joinToString("") { "%02x".format(it) }
            prefs.edit().putString(KEY_SECRET, secret).apply()
            return secret
        }
    }
}
