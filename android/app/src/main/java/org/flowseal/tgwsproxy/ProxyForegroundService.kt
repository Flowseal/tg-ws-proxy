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
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class ProxyForegroundService : Service() {
    private lateinit var settingsStore: ProxySettingsStore
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

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

            else -> {
                val config = settingsStore.load().validate().normalized
                if (config == null) {
                    ProxyServiceState.markFailed(getString(R.string.saved_config_invalid))
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                    START_NOT_STICKY
                } else {
                    ProxyServiceState.markStarting(config)
                    startForeground(
                        NOTIFICATION_ID,
                        buildNotification(
                            buildNotificationPayload(
                                config = config,
                                statusText = getString(
                                    R.string.notification_starting,
                                    config.host,
                                    config.port,
                                ),
                            ),
                        ),
                    )
                    serviceScope.launch {
                        startProxyRuntime(config)
                    }
                    START_STICKY
                }
            }
        }
    }

    override fun onDestroy() {
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
        }.onFailure { error ->
            ProxyServiceState.markFailed(
                error.message ?: getString(R.string.proxy_start_failed_generic),
            )
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun stopProxyRuntime(removeNotification: Boolean, stopService: Boolean) {
        runCatching { PythonProxyBridge.stop(this) }
        ProxyServiceState.markStopped()

        if (removeNotification) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        }
        if (stopService) {
            stopSelf()
        }
    }

    private fun updateNotification(payload: NotificationPayload) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(payload))
    }

    private fun buildNotificationPayload(
        config: NormalizedProxyConfig,
        statusText: String,
    ): NotificationPayload {
        val endpointText = getString(R.string.notification_endpoint, config.host, config.port)
        val detailsText = getString(
            R.string.notification_details,
            config.host,
            config.port,
            config.dcIpList.size,
            if (config.verbose) {
                getString(R.string.notification_verbose_on)
            } else {
                getString(R.string.notification_verbose_off)
            },
        )
        return NotificationPayload(
            statusText = statusText,
            endpointText = endpointText,
            detailsText = detailsText,
        )
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
    }
}

private data class NotificationPayload(
    val statusText: String,
    val endpointText: String,
    val detailsText: String,
)
