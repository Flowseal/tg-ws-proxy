package com.amurcanov.tgwsproxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class ProxyService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var statsJob: Job? = null

    companion object {
        const val ACTION_START = "com.amurcanov.tgwsproxy.START"
        const val ACTION_STOP = "com.amurcanov.tgwsproxy.STOP"
        const val EXTRA_PORT = "EXTRA_PORT"
        const val EXTRA_IPS = "EXTRA_IPS"
        const val EXTRA_POOL_SIZE = "EXTRA_POOL_SIZE"
        const val EXTRA_CFPROXY_ENABLED = "EXTRA_CFPROXY_ENABLED"
        const val EXTRA_CFPROXY_PRIORITY = "EXTRA_CFPROXY_PRIORITY"
        const val EXTRA_CFPROXY_DOMAIN = "EXTRA_CFPROXY_DOMAIN"
        const val EXTRA_SECRET_KEY = "EXTRA_SECRET_KEY"
        
        private const val NOTIFICATION_ID = 1
        private const val CHANNEL_ID = "ProxyServiceChannel"

        private val _isRunning = MutableStateFlow(false)
        val isRunning: StateFlow<Boolean> = _isRunning
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                LogManager.clearLogs()
                val port = intent.getIntExtra(EXTRA_PORT, 8080)
                val ips = intent.getStringExtra(EXTRA_IPS) ?: ""
                val poolSize = intent.getIntExtra(EXTRA_POOL_SIZE, 4)
                val cfEnabled = intent.getBooleanExtra(EXTRA_CFPROXY_ENABLED, true)
                val cfPriority = intent.getBooleanExtra(EXTRA_CFPROXY_PRIORITY, true)
                val cfDomain = intent.getStringExtra(EXTRA_CFPROXY_DOMAIN) ?: ""
                val secretKey = intent.getStringExtra(EXTRA_SECRET_KEY) ?: ""
                startProxy(port, ips, poolSize, cfEnabled, cfPriority, cfDomain, secretKey)
            }
            ACTION_STOP -> {
                stopProxy()
            }
        }
        return START_STICKY
    }

    private fun startProxy(port: Int, ips: String, poolSize: Int = 4,
                           cfEnabled: Boolean = true, cfPriority: Boolean = true,
                           cfDomain: String = "", secretKey: String = "") {
        if (_isRunning.value) return
        
        val notification = createNotification("Запуск прокси...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        acquireWakeLock()
        
        Thread {
            NativeProxy.setPoolSize(poolSize)
            NativeProxy.setCfProxyConfig(cfEnabled, cfPriority, cfDomain)
            NativeProxy.setSecret(secretKey)
            NativeProxy.startProxy("127.0.0.1", port, ips, 1)
        }.start()

        _isRunning.value = true

        statsJob = CoroutineScope(Dispatchers.IO).launch {
            while (isActive) {
                delay(2000)
                if (_isRunning.value) {
                    val rawStats = NativeProxy.getStats() ?: continue
                    val upRaw = extractStat(rawStats, "up=")
                    val downRaw = extractStat(rawStats, "down=")
                    
                    val totalBytes = parseHumanBytes(upRaw) + parseHumanBytes(downRaw)
                    val text = "Трафик: ${formatBytes(totalBytes)}"
                    val manager = getSystemService(NotificationManager::class.java)
                    manager?.notify(NOTIFICATION_ID, createNotification(text))
                }
            }
        }
    }

    private fun extractStat(stats: String, key: String): String {
        val idx = stats.indexOf(key)
        if (idx == -1) return "0B"
        val start = idx + key.length
        val end = stats.indexOf(" ", start)
        return if (end == -1) stats.substring(start) else stats.substring(start, end)
    }
    
    private fun parseHumanBytes(s: String): Double {
        val num = s.replace(Regex("[^0-9.]"), "").toDoubleOrNull() ?: 0.0
        return when {
            s.endsWith("TB") -> num * 1024.0 * 1024 * 1024 * 1024
            s.endsWith("GB") -> num * 1024.0 * 1024 * 1024
            s.endsWith("MB") -> num * 1024.0 * 1024
            s.endsWith("KB") -> num * 1024.0
            else -> num
        }
    }
    
    private fun formatBytes(bytes: Double): String {
        if (bytes < 1024) return "%.0fB".format(bytes)
        if (bytes < 1024 * 1024) return "%.1fKB".format(bytes / 1024)
        if (bytes < 1024 * 1024 * 1024) return "%.1fMB".format(bytes / (1024 * 1024))
        return "%.2fGB".format(bytes / (1024 * 1024 * 1024))
    }

    private fun stopProxy() {
        statsJob?.cancel()
        statsJob = null
        Thread { NativeProxy.stopProxy() }.start()
        releaseWakeLock()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            stopForeground(true)
        }
        stopSelf()
        _isRunning.value = false
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "TgWsProxy::ServiceWakeLock"
        )
        wakeLock?.acquire()
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.let {
                if (it.isHeld) {
                    it.release()
                }
            }
        } catch (e: Exception) {
            // Ignore wakelock exception
        }
        wakeLock = null
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val serviceChannel = NotificationChannel(
                CHANNEL_ID,
                "Фоновый Прокси",
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(serviceChannel)
        }
    }

    private fun createNotification(content: String): Notification {
        val stopIntent = Intent(this, ProxyService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Telegram WS Proxy")
            .setContentText(content)
            .setSmallIcon(R.drawable.ic_notification) // Local pure vector for Android 16 compatibility
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Отключить", stopPendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true) // prevent vibrate/sound on updates
            .build()
    }

    override fun onDestroy() {
        if (_isRunning.value) {
            stopProxy()
        }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }
}
