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
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.net.InetSocketAddress
import java.net.Socket

class ProxyService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var statsJob: Job? = null
    private var watchdogJob: Job? = null
    private var restartJob: Job? = null
    private var lastNotificationContent: String = ""
    private var lastNotificationAtMs: Long = 0L
    private var notificationStartedAtMs: Long = 0L
    @Volatile
    private var stopInProgress = false
    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    // Saved intent extras for restart on kill / onTaskRemoved
    private var lastPort: Int = 1443
    private var lastIps: String = ""
    private var lastPoolSize: Int = 4
    private var lastCfEnabled: Boolean = true
    private var lastCfPriority: Boolean = true
    private var lastCfDomain: String = ""
    private var lastSecretKey: String = ""

    companion object {
        const val ACTION_START = "com.amurcanov.tgwsproxy.START"
        const val ACTION_STOP = "com.amurcanov.tgwsproxy.STOP"
        const val ACTION_RESTART = "com.amurcanov.tgwsproxy.RESTART"
        const val EXTRA_PORT = "EXTRA_PORT"
        const val EXTRA_IPS = "EXTRA_IPS"
        const val EXTRA_POOL_SIZE = "EXTRA_POOL_SIZE"
        const val EXTRA_CFPROXY_ENABLED = "EXTRA_CFPROXY_ENABLED"
        const val EXTRA_CFPROXY_PRIORITY = "EXTRA_CFPROXY_PRIORITY"
        const val EXTRA_CFPROXY_DOMAIN = "EXTRA_CFPROXY_DOMAIN"
        const val EXTRA_SECRET_KEY = "EXTRA_SECRET_KEY"
        
        private const val NOTIFICATION_ID = 101
        private const val CHANNEL_ID = "TG_WS_Proxy_Service_v4"
        private const val TAG = "ProxyService"

        // Wakelock refresh interval (25 min, re-acquire before 30-min timeout)
        private const val WAKELOCK_TIMEOUT_MS = 30L * 60 * 1000
        private const val WAKELOCK_REFRESH_MS = 25L * 60 * 1000

        // Stats/notification update interval
        private const val STATS_UPDATE_MS = 3_000L
        private const val NOTIFICATION_MIN_UPDATE_MS = 3_000L
        private const val NATIVE_STOP_WAIT_MS = 3_000L

        // Startup verification timeout
        private const val STARTUP_CHECK_DELAY_MS = 3000L

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
                val port = intent.getIntExtra(EXTRA_PORT, 1443)
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
            ACTION_RESTART -> {
                restartProxy()
            }
            null -> {
                // Service restarted by system after being killed (START_REDELIVER_INTENT)
                // If we had saved params, try to restart
                if (lastPort > 0 && lastSecretKey.isNotEmpty()) {
                    Log.w(TAG, "Service restarted by system, re-starting proxy")
                    startProxy(lastPort, lastIps, lastPoolSize, lastCfEnabled, lastCfPriority, lastCfDomain, lastSecretKey)
                } else {
                    stopSelf()
                }
            }
        }
        // START_REDELIVER_INTENT: if the system kills the service, it will restart it
        // and re-deliver the last intent, so we don't lose the config.
        return START_REDELIVER_INTENT
    }

    private fun startProxy(port: Int, ips: String, poolSize: Int = 4,
                           cfEnabled: Boolean = true, cfPriority: Boolean = true,
                           cfDomain: String = "", secretKey: String = "") {
        if (_isRunning.value || stopInProgress) return

        // Save params for restart
        lastPort = port
        lastIps = ips
        lastPoolSize = poolSize
        lastCfEnabled = cfEnabled
        lastCfPriority = cfPriority
        lastCfDomain = cfDomain
        lastSecretKey = secretKey
        notificationStartedAtMs = System.currentTimeMillis()
        lastNotificationContent = getString(R.string.notification_starting)
        lastNotificationAtMs = notificationStartedAtMs

        val notification = createNotification(lastNotificationContent)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        acquireWakeLock()
        stopInProgress = false
        
        // Start Go proxy in a separate thread with error handling
        Thread({
            try {
                NativeProxy.setPoolSize(poolSize)
                NativeProxy.setCfProxyCacheDir(cacheDir.absolutePath)
                NativeProxy.setCfProxyConfig(cfEnabled, cfPriority, cfDomain)
                val result = NativeProxy.startProxy("127.0.0.1", port, ips, secretKey, 1)
                if (result != 0) {
                    Log.e(TAG, "StartProxy returned error code: $result")
                    serviceScope.launch {
                        updateNotification(getString(R.string.notification_start_error_code, result), force = true)
                        delay(3000)
                        stopProxy()
                    }
                }
            } catch (e: Throwable) {
                Log.e(TAG, "Failed to start proxy via JNA", e)
                serviceScope.launch {
                    updateNotification(getString(R.string.notification_error, e.message ?: ""), force = true)
                    delay(3000)
                    stopProxy()
                }
            }
        }, "ProxyStart").apply {
            isDaemon = true
            start()
        }

        updateRunningState(true)

        // Watchdog: verify the proxy is actually listening after startup
        watchdogJob = serviceScope.launch {
            delay(STARTUP_CHECK_DELAY_MS)
            if (_isRunning.value) {
                val isListening = withContext(Dispatchers.IO) {
                    isPortOpen("127.0.0.1", port, 2000)
                }
                if (isListening) {
                    updateNotification(getString(R.string.notification_running), force = true)
                    Log.i(TAG, "Proxy verified: listening on port $port")
                } else {
                    Log.e(TAG, "Proxy NOT listening on port $port after ${STARTUP_CHECK_DELAY_MS}ms")
                    updateNotification(getString(R.string.notification_not_responding), force = true)
                    // Don't stop — it might start slightly later; let the user decide
                }
            }
        }

        // Stats updater. Notification updates are throttled so the system keeps
        // a stable foreground entry instead of constantly reordering it.
        statsJob = serviceScope.launch {
            // WakeLock refresh sub-job: re-acquire before system timeout
            launch {
                while (isActive) {
                    delay(WAKELOCK_REFRESH_MS)
                    refreshWakeLock()
                }
            }

            while (isActive) {
                delay(STATS_UPDATE_MS)
                if (_isRunning.value && !stopInProgress) {
                    try {
                        val rawStats = NativeProxy.getStats() ?: continue
                        val upRaw = extractStat(rawStats, "up=")
                        val downRaw = extractStat(rawStats, "down=")
                        val activeConns = extractStat(rawStats, "active=")
                        
                        val totalBytes = parseHumanBytes(upRaw) + parseHumanBytes(downRaw)
                        val active = activeConns.toIntOrNull() ?: 0
                        val text = getString(R.string.notification_traffic, formatBytes(totalBytes), active)
                        updateNotification(text)
                    } catch (e: Exception) {
                        Log.w(TAG, "Stats update failed", e)
                    }
                }
            }
        }
    }

    /**
     * Check if a TCP port is reachable (used to verify proxy startup)
     */
    private fun isPortOpen(host: String, port: Int, timeoutMs: Int): Boolean {
        return try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(host, port), timeoutMs)
                true
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun updateNotification(content: String, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force) {
            if (content == lastNotificationContent) return
            if (lastNotificationAtMs != 0L && now - lastNotificationAtMs < NOTIFICATION_MIN_UPDATE_MS) return
        }

        lastNotificationContent = content
        lastNotificationAtMs = now
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager?.notify(NOTIFICATION_ID, createNotification(content))
        } catch (e: Exception) {
            Log.w(TAG, "Failed to update notification", e)
        }
    }

    private fun restartProxy() {
        if (restartJob?.isActive == true) return
        if (lastPort <= 0 || lastSecretKey.isEmpty()) {
            Log.w(TAG, "Restart requested without saved proxy configuration")
            return
        }

        restartJob = serviceScope.launch {
            Log.i(TAG, "Restarting proxy from notification")
            updateNotification(getString(R.string.notification_restarting), force = true)

            watchdogJob?.cancel()
            watchdogJob = null
            statsJob?.cancel()
            statsJob = null

            requestNativeStop("restart")
            releaseWakeLock()
            updateRunningState(false)
            delay(350)

            startProxy(
                port = lastPort,
                ips = lastIps,
                poolSize = lastPoolSize,
                cfEnabled = lastCfEnabled,
                cfPriority = lastCfPriority,
                cfDomain = lastCfDomain,
                secretKey = lastSecretKey
            )
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
        if (stopInProgress) return
        stopInProgress = true
        restartJob?.cancel()
        restartJob = null
        watchdogJob?.cancel()
        watchdogJob = null
        statsJob?.cancel()
        statsJob = null
        serviceScope.launch {
            updateNotification(getString(R.string.notification_stopping), force = true)
            requestNativeStop("stop")
            releaseWakeLock()
            updateRunningState(false)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE)
            } else {
                @Suppress("DEPRECATION")
                stopForeground(true)
            }
            stopSelf()
        }
    }

    private suspend fun requestNativeStop(reason: String): Boolean {
        val completed = CompletableDeferred<Unit>()
        Thread({
            try {
                NativeProxy.stopProxy()
            } catch (e: Exception) {
                Log.w(TAG, "StopProxy failed during $reason", e)
            } finally {
                completed.complete(Unit)
            }
        }, "ProxyStop-$reason").apply {
            isDaemon = true
            start()
        }

        val finished = withTimeoutOrNull(NATIVE_STOP_WAIT_MS) {
            completed.await()
            true
        } ?: false

        if (!finished) {
            Log.w(TAG, "Native stop is still running after ${NATIVE_STOP_WAIT_MS}ms during $reason")
        }
        return finished
    }

    /**
     * Called when the user swipes the app from recents.
     * Without this, the service would be killed on many OEM Androids.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        if (_isRunning.value) {
            Log.w(TAG, "onTaskRemoved: proxy is running, service stays alive")
            // The service continues because stopWithTask=false in manifest
            // No action needed — the service keeps running.
        }
    }

    private fun acquireWakeLock() {
        try {
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "TgWsProxy::ServiceWakeLock"
            ).apply {
                // Acquire with timeout. System may ignore indefinite wakelocks.
                acquire(WAKELOCK_TIMEOUT_MS)
            }
            Log.d(TAG, "WakeLock acquired (${WAKELOCK_TIMEOUT_MS / 60000}min)")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to acquire WakeLock", e)
        }
    }

    /**
     * Periodically refresh wakelock to prevent system from expiring it.
     */
    private fun refreshWakeLock() {
        try {
            wakeLock?.let {
                if (it.isHeld) {
                    it.release()
                }
            }
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "TgWsProxy::ServiceWakeLock"
            ).apply {
                acquire(WAKELOCK_TIMEOUT_MS)
            }
            Log.d(TAG, "WakeLock refreshed")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to refresh WakeLock", e)
        }
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.let {
                if (it.isHeld) {
                    it.release()
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to release WakeLock", e)
        }
        wakeLock = null
    }

    private fun updateRunningState(isRunning: Boolean) {
        _isRunning.value = isRunning
        ProxyTileService.requestSync(this)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val serviceChannel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.notification_channel_description)
                setShowBadge(false)
                setSound(null, null)
                enableVibration(false)
                enableLights(false)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(serviceChannel)
        }
    }

    private fun createNotification(content: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openPendingIntent = PendingIntent.getActivity(
            this, 1, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, ProxyService::class.java).apply {
            action = ACTION_STOP
        }
        val restartIntent = Intent(this, ProxyService::class.java).apply {
            action = ACTION_RESTART
        }
        val restartPendingIntent = PendingIntent.getService(
            this, 2, restartIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Telegram WS Proxy")
            .setContentText(content)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(openPendingIntent) // Tap notification → open app
            .addAction(android.R.drawable.ic_popup_sync, getString(R.string.notification_restart), restartPendingIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, getString(R.string.notification_disconnect), stopPendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setWhen(notificationStartedAtMs.takeIf { it > 0L } ?: System.currentTimeMillis())
            .setShowWhen(false)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    override fun onDestroy() {
        restartJob?.cancel()
        restartJob = null
        watchdogJob?.cancel()
        watchdogJob = null
        statsJob?.cancel()
        statsJob = null
        releaseWakeLock()
        if (_isRunning.value) {
            updateRunningState(false)
        }
        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }
}
