package org.flowseal.tgwsproxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.TaskStackBuilder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.Locale

class ProxyForegroundService : Service() {
    private lateinit var settingsStore: ProxySettingsStore
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var trafficJob: Job? = null
    private var lastTrafficSample: TrafficSample? = null

    override fun onCreate() {
        super.onCreate()
        settingsStore = ProxySettingsStore(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return when (intent?.action) {
            ACTION_STOP -> {
                ProxyServiceState.clearError()
                serviceScope.launch {
                    stopProxyRuntime(removeNotification = true, stopService = true)
                }
                START_NOT_STICKY
            }

            ACTION_RESTART -> {
                val config = loadValidatedConfig() ?: return START_NOT_STICKY
                ProxyServiceState.clearError()
                beginProxyStart(config)
                serviceScope.launch {
                    stopRuntimeOnly()
                    startProxyRuntime(config)
                }
                START_STICKY
            }

            else -> {
                val config = loadValidatedConfig() ?: return START_NOT_STICKY
                beginProxyStart(config)
                serviceScope.launch {
                    startProxyRuntime(config)
                }
                START_STICKY
            }
        }
    }

    override fun onDestroy() {
        stopTrafficUpdates()
        serviceScope.cancel()
        runCatching { PythonProxyBridge.stop(this) }
        ProxyServiceState.markStopped()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(payload: NotificationPayload): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(payload.statusText)
            .setSubText(payload.endpointText)
            .setStyle(
                NotificationCompat.BigTextStyle().bigText(payload.detailsText),
            )
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(createOpenAppPendingIntent())
            .addAction(
                0,
                getString(R.string.notification_action_stop),
                createStopPendingIntent(),
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private suspend fun startProxyRuntime(config: NormalizedProxyConfig) {
        val result = runCatching {
            PythonProxyBridge.start(this, config)
        }

        result.onSuccess {
            ProxyServiceState.markStarted(config)
            lastTrafficSample = null
            updateNotification(
                buildNotificationPayload(
                    config = config,
                    statusText = getString(
                        R.string.notification_running,
                        config.host,
                        config.port,
                    ),
                ),
            )
            startTrafficUpdates(config)
        }.onFailure { error ->
            ProxyServiceState.markFailed(
                error.message ?: getString(R.string.proxy_start_failed_generic),
            )
            stopTrafficUpdates()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun loadValidatedConfig(): NormalizedProxyConfig? {
        val config = settingsStore.load().validate().normalized
        if (config == null) {
            ProxyServiceState.markFailed(getString(R.string.saved_config_invalid))
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
        return config
    }

    private fun beginProxyStart(config: NormalizedProxyConfig) {
        ProxyServiceState.markStarting(config)
        startForeground(
            NOTIFICATION_ID,
            buildNotification(
                buildNotificationPayload(
                    config = config,
                    trafficState = TrafficState(),
                    statusText = getString(
                        R.string.notification_starting,
                        config.host,
                        config.port,
                    ),
                ),
            ),
        )
    }

    private fun stopProxyRuntime(removeNotification: Boolean, stopService: Boolean) {
        stopRuntimeOnly()
        ProxyServiceState.markStopped()

        if (removeNotification) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        }
        if (stopService) {
            stopSelf()
        }
    }

    private fun stopRuntimeOnly() {
        stopTrafficUpdates()
        runCatching { PythonProxyBridge.stop(this) }
    }

    private fun updateNotification(payload: NotificationPayload) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(payload))
    }

    private fun buildNotificationPayload(
        config: NormalizedProxyConfig,
        trafficState: TrafficState,
        statusText: String,
    ): NotificationPayload {
        val endpointText = getString(R.string.notification_endpoint, config.host, config.port)
        val detailsText = getString(
            R.string.notification_details,
            config.dcIpList.size,
            formatRate(trafficState.upBytesPerSecond),
            formatRate(trafficState.downBytesPerSecond),
            formatBytes(trafficState.totalBytesUp),
            formatBytes(trafficState.totalBytesDown),
        )
        return NotificationPayload(
            statusText = statusText,
            endpointText = endpointText,
            detailsText = detailsText,
        )
    }

    private fun startTrafficUpdates(config: NormalizedProxyConfig) {
        stopTrafficUpdates()
        trafficJob = serviceScope.launch {
            while (isActive && ProxyServiceState.isRunning.value) {
                val trafficState = readTrafficState()
                if (!trafficState.running) {
                    ProxyServiceState.markFailed(
                        trafficState.lastError ?: getString(R.string.proxy_runtime_stopped_unexpectedly),
                    )
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                    break
                }
                updateNotification(
                    buildNotificationPayload(
                        config = config,
                        trafficState = trafficState,
                        statusText = getString(
                            R.string.notification_running,
                            config.host,
                            config.port,
                        ),
                    ),
                )
                delay(1000)
            }
        }
    }

    private fun stopTrafficUpdates() {
        trafficJob?.cancel()
        trafficJob = null
        lastTrafficSample = null
    }

    private fun readTrafficState(): TrafficState {
        val nowMillis = System.currentTimeMillis()
        val current = PythonProxyBridge.getTrafficStats(this)
        val previous = lastTrafficSample
        lastTrafficSample = TrafficSample(
            bytesUp = current.bytesUp,
            bytesDown = current.bytesDown,
            timestampMillis = nowMillis,
        )

        if (!current.running || previous == null) {
            return TrafficState(
                upBytesPerSecond = 0L,
                downBytesPerSecond = 0L,
                totalBytesUp = current.bytesUp,
                totalBytesDown = current.bytesDown,
                running = current.running,
                lastError = current.lastError,
            )
        }

        val elapsedMillis = (nowMillis - previous.timestampMillis).coerceAtLeast(1L)
        val upDelta = (current.bytesUp - previous.bytesUp).coerceAtLeast(0L)
        val downDelta = (current.bytesDown - previous.bytesDown).coerceAtLeast(0L)
        return TrafficState(
            upBytesPerSecond = (upDelta * 1000L) / elapsedMillis,
            downBytesPerSecond = (downDelta * 1000L) / elapsedMillis,
            totalBytesUp = current.bytesUp,
            totalBytesDown = current.bytesDown,
            running = current.running,
            lastError = current.lastError,
        )
    }

    private fun formatRate(bytesPerSecond: Long): String = formatBytes(bytesPerSecond)

    private fun formatBytes(bytes: Long): String {
        val units = arrayOf("B", "KB", "MB", "GB")
        var value = bytes.toDouble().coerceAtLeast(0.0)
        var unitIndex = 0

        while (value >= 1024.0 && unitIndex < units.lastIndex) {
            value /= 1024.0
            unitIndex += 1
        }

        return if (unitIndex == 0) {
            String.format(Locale.US, "%.0f %s", value, units[unitIndex])
        } else {
            String.format(Locale.US, "%.1f %s", value, units[unitIndex])
        }
    }

    private fun createOpenAppPendingIntent(): PendingIntent {
        val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
            ?.apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                )
            }
            ?: Intent(this, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                )
            }

        return TaskStackBuilder.create(this)
            .addNextIntentWithParentStack(launchIntent)
            .getPendingIntent(
                1,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            ?: PendingIntent.getActivity(
                this,
                1,
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
    }

    private fun createStopPendingIntent(): PendingIntent {
        val intent = Intent(this, ProxyForegroundService::class.java).apply {
            action = ACTION_STOP
        }
        return PendingIntent.getService(
            this,
            2,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }

        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notification_channel_description)
        }
        manager.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "proxy_service"
        private const val NOTIFICATION_ID = 1001
        private const val ACTION_START = "org.flowseal.tgwsproxy.action.START"
        private const val ACTION_STOP = "org.flowseal.tgwsproxy.action.STOP"
        private const val ACTION_RESTART = "org.flowseal.tgwsproxy.action.RESTART"

        fun start(context: Context) {
            val intent = Intent(context, ProxyForegroundService::class.java).apply {
                action = ACTION_START
            }
            androidx.core.content.ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, ProxyForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }

        fun restart(context: Context) {
            val intent = Intent(context, ProxyForegroundService::class.java).apply {
                action = ACTION_RESTART
            }
            androidx.core.content.ContextCompat.startForegroundService(context, intent)
        }
    }
}

private data class NotificationPayload(
    val statusText: String,
    val endpointText: String,
    val detailsText: String,
)

private data class TrafficSample(
    val bytesUp: Long,
    val bytesDown: Long,
    val timestampMillis: Long,
)

private data class TrafficState(
    val upBytesPerSecond: Long = 0L,
    val downBytesPerSecond: Long = 0L,
    val totalBytesUp: Long = 0L,
    val totalBytesDown: Long = 0L,
    val running: Boolean = false,
    val lastError: String? = null,
)
