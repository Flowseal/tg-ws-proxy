package org.flowseal.tgwsproxy

import android.Manifest
import android.content.pm.PackageManager
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
import kotlinx.coroutines.launch
import org.flowseal.tgwsproxy.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var settingsStore: ProxySettingsStore

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
        binding.saveButton.setOnClickListener { onSaveClicked(showMessage = true) }

        renderConfig(settingsStore.load())
        requestNotificationPermissionIfNeeded()
        observeServiceState()
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
        return config
    }

    private fun onStartClicked() {
        onSaveClicked(showMessage = false) ?: return
        ProxyForegroundService.start(this)
        Snackbar.make(binding.root, R.string.service_start_requested, Snackbar.LENGTH_SHORT).show()
    }

    private fun renderConfig(config: ProxyConfig) {
        binding.hostInput.setText(config.host)
        binding.portInput.setText(config.portText)
        binding.dcIpInput.setText(config.dcIpText)
        binding.verboseSwitch.isChecked = config.verbose
    }

    private fun collectConfigFromForm(): ProxyConfig {
        return ProxyConfig(
            host = binding.hostInput.text?.toString().orEmpty(),
            portText = binding.portInput.text?.toString().orEmpty(),
            dcIpText = binding.dcIpInput.text?.toString().orEmpty(),
            verbose = binding.verboseSwitch.isChecked,
        )
    }

    private fun observeServiceState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                ProxyServiceState.isRunning.collect { isRunning ->
                    binding.statusValue.text = getString(
                        if (isRunning) R.string.status_running else R.string.status_stopped,
                    )
                    binding.startButton.isEnabled = !isRunning
                    binding.stopButton.isEnabled = isRunning
                }
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                ProxyServiceState.activeConfig.collect { config ->
                    binding.serviceHint.text = if (config == null) {
                        getString(R.string.service_hint_idle)
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
