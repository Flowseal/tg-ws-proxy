package com.amurcanov.tgwsproxy

import android.content.ComponentName
import android.content.Context
import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class ProxyTileService : TileService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var listenJob: Job? = null

    override fun onStartListening() {
        super.onStartListening()
        listenJob?.cancel()
        listenJob = scope.launch {
            ProxyService.isRunning.collectLatest { isRunning ->
                renderTile(
                    if (isRunning) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
                )
            }
        }
        renderTile()
    }

    override fun onStopListening() {
        listenJob?.cancel()
        listenJob = null
        super.onStopListening()
    }

    override fun onClick() {
        super.onClick()

        val toggleAction: () -> Unit = {
            scope.launch {
                val wasRunning = ProxyService.isRunning.value
                if (wasRunning) {
                    renderTile(Tile.STATE_INACTIVE)
                    ProxyController.stop(this@ProxyTileService)
                } else {
                    val started = ProxyController.startFromSavedSettings(
                        context = this@ProxyTileService,
                        showInvalidPortToast = true
                    )
                    renderTile(
                        if (started) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
                    )
                }
            }
        }

        if (isLocked) {
            unlockAndRun(toggleAction)
        } else {
            toggleAction()
        }
    }

    override fun onDestroy() {
        listenJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private fun renderTile(overrideState: Int? = null) {
        qsTile?.apply {
            label = "Telegram WS Proxy"
            icon = Icon.createWithResource(this@ProxyTileService, R.drawable.ic_qs_proxy_t)
            state = overrideState ?: if (ProxyService.isRunning.value) {
                Tile.STATE_ACTIVE
            } else {
                Tile.STATE_INACTIVE
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                subtitle = if (state == Tile.STATE_ACTIVE) getString(R.string.tile_connected) else getString(R.string.tile_disconnected)
            }
            contentDescription = label
            updateTile()
        }
    }

    companion object {
        fun requestSync(context: Context) {
            runCatching {
                requestListeningState(context, ComponentName(context, ProxyTileService::class.java))
            }
        }
    }
}
