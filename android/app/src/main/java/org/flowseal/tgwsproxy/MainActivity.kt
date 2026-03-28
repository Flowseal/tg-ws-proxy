package org.flowseal.tgwsproxy

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.flowseal.tgwsproxy.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var settingsStore: ProxySettingsStore
    private var currentUpdateStatus: ProxyUpdateStatus? = null

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            Toast.makeText(
                this,
                "Без уведомлений Android может скрыть foreground service.",
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        settingsStore = ProxySettingsStore(this)
        setContentView(binding.root)

        binding.startButton.setOnClickListener { onStartClicked() }
        binding.stopButton.setOnClickListener { ProxyForegroundService.stop(this) }
        binding.restartButton.setOnClickListener { onRestartClicked() }
        binding.saveButton.setOnClickListener { onSaveClicked(showMessage = true) }
        binding.openLogsButton.setOnClickListener { onOpenLogsClicked() }
        binding.openTelegramButton.setOnClickListener { onOpenTelegramClicked() }
        binding.openReleasePageButton.setOnClickListener { onOpenReleasePageClicked() }
        binding.checkUpdatesSwitch.setOnCheckedChangeListener { _, _ ->
            renderUpdateStatus(currentUpdateStatus, binding.checkUpdatesSwitch.isChecked)
        }
        binding.disableBatteryOptimizationButton.setOnClickListener {
            AndroidSystemStatus.openBatteryOptimizationSettings(this)
        }
        binding.openAppSettingsButton.setOnClickListener {
            AndroidSystemStatus.openAppSettings(this)
        }

        val config = settingsStore.load()
        renderConfig(config)
        if (config.checkUpdates) {
            refreshUpdateStatus(checkNow = true)
        } else {
            currentUpdateStatus = null
            renderUpdateStatus(null, false)
        }
        requestNotificationPermissionIfNeeded()
        observeServiceState()
        renderSystemStatus()
    }

    override fun onResume() {
        super.onResume()
        renderSystemStatus()
    }

    private fun onSaveClicked(showMessage: Boolean): NormalizedProxyConfig? {
        val validation = collectConfigFromForm().validate()
        val config = validation.normalized
        if (config == null) {
            binding.errorText.text = validation.errorMessage
            binding.errorText.isVisible = true
            return null
        }

        binding.errorText.isVisible = false
        settingsStore.save(config)
        if (showMessage) {
            Snackbar.make(binding.root, R.string.settings_saved, Snackbar.LENGTH_SHORT).show()
        }
        if (config.checkUpdates) {
            refreshUpdateStatus(checkNow = true)
        } else {
            currentUpdateStatus = null
            renderUpdateStatus(null, false)
        }
        return config
    }

    private fun onStartClicked() {
        onSaveClicked(showMessage = false) ?: return
        ProxyForegroundService.start(this)
        Snackbar.make(binding.root, R.string.service_start_requested, Snackbar.LENGTH_SHORT).show()
    }

    private fun onRestartClicked() {
        onSaveClicked(showMessage = false) ?: return
        ProxyForegroundService.restart(this)
        Snackbar.make(binding.root, R.string.service_restart_requested, Snackbar.LENGTH_SHORT).show()
    }

    private fun onOpenLogsClicked() {
        startActivity(Intent(this, LogViewerActivity::class.java))
    }

    private fun onOpenTelegramClicked() {
        val config = onSaveClicked(showMessage = false) ?: return
        if (!TelegramProxyIntent.open(this, config)) {
            Snackbar.make(binding.root, R.string.telegram_not_found, Snackbar.LENGTH_LONG).show()
        }
    }

    private fun renderConfig(config: ProxyConfig) {
        binding.hostInput.setText(config.host)
        binding.portInput.setText(config.portText)
        binding.dcIpInput.setText(config.dcIpText)
        binding.logMaxMbInput.setText(config.logMaxMbText)
        binding.bufferKbInput.setText(config.bufferKbText)
        binding.poolSizeInput.setText(config.poolSizeText)
        binding.checkUpdatesSwitch.isChecked = config.checkUpdates
        binding.verboseSwitch.isChecked = config.verbose
        renderUpdateStatus(currentUpdateStatus, config.checkUpdates)
    }

    private fun collectConfigFromForm(): ProxyConfig {
        return ProxyConfig(
            host = binding.hostInput.text?.toString().orEmpty(),
            portText = binding.portInput.text?.toString().orEmpty(),
            dcIpText = binding.dcIpInput.text?.toString().orEmpty(),
            logMaxMbText = binding.logMaxMbInput.text?.toString().orEmpty(),
            bufferKbText = binding.bufferKbInput.text?.toString().orEmpty(),
            poolSizeText = binding.poolSizeInput.text?.toString().orEmpty(),
            checkUpdates = binding.checkUpdatesSwitch.isChecked,
            verbose = binding.verboseSwitch.isChecked,
        )
    }

    private fun onOpenReleasePageClicked() {
        val url = currentUpdateStatus?.htmlUrl ?: "https://github.com/Flowseal/tg-ws-proxy/releases/latest"
        val opened = runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }.isSuccess
        if (!opened) {
            Snackbar.make(binding.root, R.string.release_page_open_failed, Snackbar.LENGTH_LONG).show()
        }
    }

    private fun refreshUpdateStatus(checkNow: Boolean) {
        lifecycleScope.launch {
            val status = runCatching {
                withContext(Dispatchers.IO) {
                    PythonProxyBridge.getUpdateStatus(this@MainActivity, checkNow)
                }
            }.getOrElse { exc ->
                ProxyUpdateStatus(
                    currentVersion = "unknown",
                    error = exc.message ?: exc.javaClass.simpleName,
                )
            }
            currentUpdateStatus = status
            renderUpdateStatus(status, binding.checkUpdatesSwitch.isChecked)
        }
    }

    private fun renderUpdateStatus(status: ProxyUpdateStatus?, checkUpdatesEnabled: Boolean) {
        val currentVersion = status?.currentVersion?.takeIf { it.isNotBlank() } ?: currentAppVersionName()
        binding.currentVersionValue.text = getString(
            R.string.updates_current_version_format,
            currentVersion,
        )
        binding.updateStatusValue.text = when {
            !checkUpdatesEnabled -> {
                getString(R.string.updates_status_disabled)
            }
            status == null -> {
                getString(R.string.updates_status_initial)
            }
            !status.error.isNullOrBlank() -> {
                getString(R.string.updates_status_error, status.error)
            }
            !status.checked -> {
                getString(R.string.updates_status_idle)
            }
            status.hasUpdate && !status.latestVersion.isNullOrBlank() -> {
                getString(
                    R.string.updates_status_available,
                    status.latestVersion,
                    status.currentVersion,
                )
            }
            status.aheadOfRelease -> {
                getString(R.string.updates_status_newer, status.currentVersion)
            }
            else -> {
                getString(R.string.updates_status_latest, status.currentVersion)
            }
        }
    }

    private fun currentAppVersionName(): String {
        return runCatching {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, 0).versionName
        }.getOrNull().orEmpty().ifBlank { "unknown" }
    }

    private fun observeServiceState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(
                    ProxyServiceState.isStarting,
                    ProxyServiceState.isRunning,
                ) { isStarting, isRunning ->
                    isStarting to isRunning
                }.collect { (isStarting, isRunning) ->
                    binding.statusValue.text = getString(
                        when {
                            isStarting -> R.string.status_starting
                            isRunning -> R.string.status_running
                            else -> R.string.status_stopped
                        },
                    )
                    binding.startButton.isEnabled = !isStarting && !isRunning
                    binding.stopButton.isEnabled = isStarting || isRunning
                    binding.restartButton.isEnabled = !isStarting
                }
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(
                    ProxyServiceState.activeConfig,
                    ProxyServiceState.isStarting,
                ) { config, isStarting ->
                    config to isStarting
                }.collect { (config, isStarting) ->
                    binding.serviceHint.text = if (config == null) {
                        getString(R.string.service_hint_idle)
                    } else if (isStarting) {
                        getString(
                            R.string.service_hint_starting,
                            config.host,
                            config.port,
                        )
                    } else {
                        getString(
                            R.string.service_hint_running,
                            config.host,
                            config.port,
                        )
                    }
                }
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                ProxyServiceState.lastError.collect { error ->
                    if (error.isNullOrBlank()) {
                        binding.lastErrorCard.isVisible = false
                    } else {
                        binding.lastErrorValue.text = error
                        binding.lastErrorCard.isVisible = true
                    }
                }
            }
        }
    }

    private fun renderSystemStatus() {
        val status = AndroidSystemStatus.read(this)

        binding.systemStatusValue.text = getString(
            if (status.canKeepRunningReliably) {
                R.string.system_status_ready
            } else {
                R.string.system_status_attention
            },
        )

        val lines = mutableListOf<String>()
        lines += if (status.ignoringBatteryOptimizations) {
            getString(R.string.system_check_battery_ignored)
        } else {
            getString(R.string.system_check_battery_active)
        }
        lines += if (status.backgroundRestricted) {
            getString(R.string.system_check_background_restricted)
        } else {
            getString(R.string.system_check_background_ok)
        }
        lines += getString(R.string.system_check_oem_note)
        binding.systemStatusHint.text = lines.joinToString("\n")

        binding.disableBatteryOptimizationButton.isVisible = !status.ignoringBatteryOptimizations
        binding.openAppSettingsButton.isVisible = status.backgroundRestricted || !status.ignoringBatteryOptimizations
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return
        }
        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
