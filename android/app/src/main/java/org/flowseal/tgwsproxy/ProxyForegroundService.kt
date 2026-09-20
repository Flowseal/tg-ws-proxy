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
import java.util.concurrent.Executors

class ProxyForegroundService : Service() {
    private lateinit var settingsStore: ProxySettingsStore
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val commandExecutor = Executors.newSingleThreadExecutor()
    private lateinit var lifecycle: ProxyLifecycleCoordinator
    @Volatile
    private var destroyed = false
    private val trafficGate = TrafficPollGate()
    private var trafficJob: Job? = null
    private var lastTrafficSample: TrafficSample? = null
    @Volatile
    private var lastTrafficState = TrafficState()

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AndroidLanguageContext.wrap(newBase))
    }

    override fun onCreate() {
        super.onCreate()
        settingsStore = ProxySettingsStore(this)
        createNotificationChannel()
        lifecycle = ProxyLifecycleCoordinator(
            commandExecutor,
            object : ProxyLifecycleCoordinator.Backend {
                override fun start(config: NormalizedProxyConfig) {
                    PythonProxyBridge.start(this@ProxyForegroundService, config)
                }

                override fun stop() {
                    PythonProxyBridge.stop(this@ProxyForegroundService)
                }
            },
            object : ProxyLifecycleCoordinator.Listener {
                override fun started(config: NormalizedProxyConfig) {
                    onRuntimeStarted(config)
                }

                override fun stopped() {
                    ProxyServiceState.markStopped()
                    finishService()
                }

                override fun failed(error: Throwable) {
                    ProxyServiceState.markFailed(
                        error.message ?: getString(R.string.proxy_start_failed_generic),
                    )
                    finishService()
                }
            },
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return when (intent?.action) {
            ACTION_STOP -> {
                ProxyServiceState.clearError()
                stopTrafficUpdates()
                lifecycle.stop()
                START_NOT_STICKY
            }

            ACTION_RESTART -> {
                val config = loadValidatedConfig() ?: return START_NOT_STICKY
                ProxyRestartSequence.run(
                    config,
                    clearError = ProxyServiceState::clearError,
                    invalidateTraffic = ::stopTrafficUpdates,
                    publishStarting = ::beginProxyStart,
                    requestRestart = { lifecycle.start(it, restart = true) },
                )
                START_STICKY
            }

            ACTION_REFRESH_LOCALE -> {
                refreshLocaleNotification()
                if (ProxyServiceState.isRunning.value || ProxyServiceState.isStarting.value) {
                    START_STICKY
                } else {
                    stopSelf()
                    START_NOT_STICKY
                }
            }

            else -> {
                val config = loadValidatedConfig() ?: return START_NOT_STICKY
                beginProxyStart(config)
                lifecycle.start(config)
                START_STICKY
            }
        }
    }

    override fun onDestroy() {
        destroyed = true
        stopTrafficUpdates()
        serviceScope.cancel()
        lifecycle.destroy()
        commandExecutor.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(payload: NotificationPayload): Notification {
        val localized = AndroidLanguageContext.wrap(this)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(localized.getString(R.string.notification_title))
            .setContentText(payload.statusText)
            .setSubText(payload.endpointText)
            .setStyle(
                NotificationCompat.BigTextStyle().bigText(payload.detailsText),
            )
            .setSmallIcon(R.drawable.ic_proxy_notification)
            .setContentIntent(createOpenAppPendingIntent())
            .addAction(
                0,
                localized.getString(R.string.notification_action_stop),
                createStopPendingIntent(),
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun onRuntimeStarted(config: NormalizedProxyConfig) {
        if (destroyed) return
        ProxyServiceState.markStarted(config)
        updateNotification(
            buildNotificationPayload(
                config = config,
                trafficState = TrafficState(running = true),
                statusText = AndroidLanguageContext.wrap(this).getString(
                    R.string.notification_running, config.host, config.port),
            ),
        )
        startTrafficUpdates(config)
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
                    statusText = AndroidLanguageContext.wrap(this).getString(
                        R.string.notification_starting,
                        config.host,
                        config.port,
                    ),
                ),
            ),
        )
    }

    private fun finishService() {
        stopTrafficUpdates()
        if (!destroyed) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun updateNotification(payload: NotificationPayload) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(payload))
    }

    private fun refreshLocaleNotification() {
        val config = ProxyServiceState.activeConfig.value ?: return
        val starting = ProxyServiceState.isStarting.value
        if (!starting && !ProxyServiceState.isRunning.value) return
        val localized = AndroidLanguageContext.wrap(this)
        createNotificationChannel()
        val status = if (starting) R.string.notification_starting else R.string.notification_running
        updateNotification(buildNotificationPayload(
            config,
            if (starting) TrafficState() else lastTrafficState,
            localized.getString(status, config.host, config.port),
        ))
    }

    private fun buildNotificationPayload(
        config: NormalizedProxyConfig,
        trafficState: TrafficState,
        statusText: String,
    ): NotificationPayload {
        val localized = AndroidLanguageContext.wrap(this)
        val endpointText = localized.getString(R.string.notification_endpoint, config.host, config.port)
        val fallbackSummary = localized.getString(when {
            !config.cfproxy -> R.string.notification_fallback_tcp
            config.cfproxyUserDomainEnabled && config.cfproxyUserDomains.isNotEmpty() ->
                R.string.notification_fallback_cfproxy_custom
            else -> R.string.notification_fallback_cfproxy
        })
        val detailsText = localized.getString(
            R.string.notification_details,
            routeLabel(localized, trafficState.lastTransportRoute),
            fallbackSummary,
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
        trafficGate.activate { ticket ->
            trafficJob = serviceScope.launch {
                while (isActive && ProxyServiceState.isRunning.value) {
                    val keepPolling = trafficGate.poll(
                        ticket,
                        { isActive },
                        { PythonProxyBridge.getTrafficStats(this@ProxyForegroundService) },
                    ) { result ->
                        val error = result.exceptionOrNull()
                        if (error != null) {
                            ProxyServiceState.markFailed(
                                error.message ?: getString(R.string.proxy_runtime_stopped_unexpectedly),
                            )
                            stopForeground(STOP_FOREGROUND_REMOVE)
                            stopSelf()
                            false
                        } else {
                            val trafficState = readTrafficState(result.getOrThrow())
                            lastTrafficState = trafficState
                            if (!trafficState.running) {
                                ProxyServiceState.markFailed(
                                    trafficState.lastError
                                        ?: getString(R.string.proxy_runtime_stopped_unexpectedly),
                                )
                                stopForeground(STOP_FOREGROUND_REMOVE)
                                stopSelf()
                                false
                            } else {
                                updateNotification(
                                    buildNotificationPayload(
                                        config = config,
                                        trafficState = trafficState,
                                        statusText = AndroidLanguageContext.wrap(this@ProxyForegroundService).getString(
                                            R.string.notification_running,
                                            config.host,
                                            config.port,
                                        ),
                                    ),
                                )
                                true
                            }
                        }
                    } ?: break
                    if (!keepPolling) break
                    delay(1000)
                }
            }
        }
    }

    private fun stopTrafficUpdates() {
        trafficGate.invalidate {
            trafficJob?.cancel()
            trafficJob = null
            lastTrafficSample = null
            lastTrafficState = TrafficState()
        }
    }

    private fun readTrafficState(current: ProxyTrafficStats): TrafficState {
        val nowMillis = System.currentTimeMillis()
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
                lastTransportRoute = current.lastTransportRoute,
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
            lastTransportRoute = current.lastTransportRoute,
            lastError = current.lastError,
        )
    }

    private fun routeLabel(localized: Context, lastTransportRoute: String?): String {
        return when (lastTransportRoute) {
            "telegram_ws_direct" -> localized.getString(R.string.notification_route_direct)
            "cfproxy_fallback" -> localized.getString(R.string.notification_route_cfproxy)
            "tcp_fallback" -> localized.getString(R.string.notification_route_tcp)
            else -> localized.getString(R.string.notification_route_unknown)
        }
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
            AndroidLanguageContext.wrap(this).getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = AndroidLanguageContext.wrap(this@ProxyForegroundService)
                .getString(R.string.notification_channel_description)
        }
        manager.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "proxy_service"
        private const val NOTIFICATION_ID = 1001
        private const val ACTION_START = "org.flowseal.tgwsproxy.action.START"
        private const val ACTION_STOP = "org.flowseal.tgwsproxy.action.STOP"
        private const val ACTION_RESTART = "org.flowseal.tgwsproxy.action.RESTART"
        private const val ACTION_REFRESH_LOCALE = "org.flowseal.tgwsproxy.action.REFRESH_LOCALE"

        @JvmStatic
        fun formatNotificationDetailsForTest(
            routeLabel: String,
            fallbackSummary: String,
            upRate: String,
            downRate: String,
            totalUp: String,
            totalDown: String,
        ): String {
            return "Route: $routeLabel\n$fallbackSummary\nTraffic: ↑ $upRate/s ↓ $downRate/s\nTransferred: ↑ $totalUp ↓ $totalDown"
        }

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

        fun refreshLocale(context: Context) {
            if (!ProxyServiceState.isRunning.value && !ProxyServiceState.isStarting.value) return
            context.startService(Intent(context, ProxyForegroundService::class.java).apply {
                action = ACTION_REFRESH_LOCALE
            })
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
    val lastTransportRoute: String? = null,
    val lastError: String? = null,
)
