package com.tgwsproxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.chaquo.python.PyException
import com.chaquo.python.Python

class ProxyService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannel()
        val cfg = ProxyConfig.load(this)
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(cfg.port),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
        )
        running = true
        lastError = ""
        val json = cfg.toJson()
        Thread {
            try {
                val error = Python.getInstance().getModule("tg_android")
                    .callAttr("start", json).toString()
                if (error.isNotEmpty()) fail(error)
            } catch (e: PyException) {
                fail(e.message ?: "ошибка Python")
            }
        }.start()
        return START_STICKY
    }

    private fun fail(message: String) {
        lastError = message
        running = false
        stopSelf()
    }

    override fun onDestroy() {
        running = false
        Thread { Python.getInstance().getModule("tg_android").callAttr("stop") }.start()
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

    private fun buildNotification(port: Int): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("TG WS Proxy работает")
            .setContentText("127.0.0.1:$port")
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setOngoing(true)
            .build()

    companion object {
        private const val CHANNEL_ID = "proxy"
        private const val NOTIFICATION_ID = 1

        @Volatile
        var running = false

        @Volatile
        var lastError = ""
    }
}
