package com.amurcanov.tgwsproxy

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.amurcanov.tgwsproxy.ui.FloatingToolbar
import com.amurcanov.tgwsproxy.ui.InfoTab
import com.amurcanov.tgwsproxy.ui.LogsTab
import com.amurcanov.tgwsproxy.ui.SettingsTab
import com.amurcanov.tgwsproxy.ui.TgWsProxyTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.Channel.Factory.BUFFERED
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.atomic.AtomicLong

class MainActivity : ComponentActivity() {

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        checkBatteryOptimizations()
        
        androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, false)

        setContent {
            val context = LocalContext.current
            val settingsStore = remember { SettingsStore(context) }
            val themeMode by settingsStore.themeMode
                .collectAsStateWithLifecycle(initialValue = "system")
            val scope = rememberCoroutineScope()

            TgWsProxyTheme(themeMode = themeMode) {
                androidx.compose.runtime.CompositionLocalProvider(
                    androidx.compose.ui.platform.LocalDensity provides androidx.compose.ui.unit.Density(
                        density = androidx.compose.ui.platform.LocalDensity.current.density,
                        fontScale = 1f
                    )
                ) {
                    Surface(
                        modifier = Modifier.fillMaxSize(),
                        color = MaterialTheme.colorScheme.background
                    ) {
                    Box {
                        MainContent(settingsStore)

                        FloatingToolbar(
                            currentTheme = themeMode,
                            onThemeChange = { mode ->
                                scope.launch { settingsStore.saveThemeMode(mode) }
                            }
                        )
                    }
                }
                }
            }
        }
    }

    private fun checkBatteryOptimizations() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                try {
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    intent.data = Uri.parse("package:$packageName")
                    startActivity(intent)
                } catch (_: Exception) {
                    Toast.makeText(this, "Не удалось запросить работу в фоне", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
}

private data class NavItem(
    val label: String,
    val iconRes: androidx.compose.ui.graphics.vector.ImageVector
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainContent(settingsStore: SettingsStore) {
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    val navItems = remember {
        listOf(
            NavItem("Настройки", Icons.Default.Settings),
            NavItem("Логи", Icons.Default.Terminal),
            NavItem("Информация", Icons.Default.Info)
        )
    }

    LaunchedEffect(Unit) {
        LogManager.startListening()
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 3.dp,
            ) {
                navItems.forEachIndexed { index, item ->
                    val selected = selectedTab == index
                    NavigationBarItem(
                        selected = selected,
                        onClick = { selectedTab = index },
                        icon = {
                            Icon(
                                imageVector = item.iconRes,
                                contentDescription = item.label,
                                modifier = Modifier.size(24.dp)
                            )
                        },
                        label = { Text(item.label, style = MaterialTheme.typography.labelSmall) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MaterialTheme.colorScheme.primary,
                            selectedTextColor = MaterialTheme.colorScheme.primary,
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            indicatorColor = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.5f),
                        ),
                    )
                }
            }
        }
    ) { padding ->
        AnimatedContent(
            targetState = selectedTab,
            transitionSpec = {
                fadeIn(tween(250)) togetherWith fadeOut(tween(200))
            },
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            label = "tab_content"
        ) { page ->
            when (page) {
                0 -> SettingsTab(settingsStore)
                1 -> LogsTab()
                2 -> InfoTab()
            }
        }
    }
}

/**
 * Optimized LogManager: uses a Channel + batching approach to avoid
 * creating a new list on every single log line — reduces GC pressure
 * and eliminates UI jank caused by high-frequency log updates.
 *
 * Key optimizations:
 * - Channel-based buffering: log lines are queued, not applied immediately
 * - Batch processing: up to 20 lines applied per tick (every 150ms)
 * - Array-backed list with cap of 50: avoids growing/shrinking allocations
 * - Duplicate merging: last-entry count increment done in-place conceptually
 */
object LogManager {
    val logs = MutableStateFlow<List<LogEntry>>(emptyList())
    private var job: Job? = null
    private var logcatProcess: Process? = null
    private val nextKey = AtomicLong(0)

    // Buffered channel — absorbs bursts of log lines without blocking the reader
    private val logChannel = Channel<LogEntry>(capacity = BUFFERED)

    fun startListening() {
        if (job?.isActive == true) return
        job = CoroutineScope(Dispatchers.IO).launch {
            // Start logcat reader coroutine
            val readerJob = launch {
                try {
                    val pid = android.os.Process.myPid()
                    val process = Runtime.getRuntime().exec(
                        arrayOf("logcat", "-v", "tag", "--pid", pid.toString())
                    )
                    logcatProcess = process
                    val reader = BufferedReader(InputStreamReader(process.inputStream), 8192)

                    while (isActive) {
                        val line = reader.readLine() ?: break
                        val entry = parseLine(line) ?: continue
                        logChannel.trySend(entry) // non-blocking send
                    }
                } catch (_: Exception) {
                } finally {
                    logcatProcess?.destroy()
                    logcatProcess = null
                }
            }

            // Batch consumer: collects queued entries and applies in batches
            launch {
                val pendingBatch = mutableListOf<LogEntry>()
                while (isActive) {
                    // Drain the channel (non-blocking)
                    var received = logChannel.tryReceive()
                    while (received.isSuccess) {
                        pendingBatch.add(received.getOrThrow())
                        if (pendingBatch.size >= 20) break // cap batch size
                        received = logChannel.tryReceive()
                    }

                    if (pendingBatch.isNotEmpty()) {
                        // Apply batch to state — single list mutation
                        logs.value = applyBatch(logs.value, pendingBatch)
                        pendingBatch.clear()
                    }

                    // Throttle updates — 150ms between UI refreshes
                    delay(150)
                }
            }

            readerJob.join()
        }
    }

    /**
     * Efficiently applies a batch of new entries to the current log list.
     * Merges consecutive duplicates and caps at 50 entries.
     */
    private fun applyBatch(current: List<LogEntry>, batch: List<LogEntry>): List<LogEntry> {
        // Use a pre-sized ArrayList to avoid re-allocation
        val result = ArrayList<LogEntry>(minOf(current.size + batch.size, 50))
        result.addAll(current)

        for (entry in batch) {
            var merged = false
            val searchDepth = minOf(result.size, 10)
            for (i in result.lastIndex downTo result.size - searchDepth) {
                if (result[i].message == entry.message) {
                    val existing = result.removeAt(i)
                    result.add(existing.copy(count = existing.count + 1))
                    merged = true
                    break
                }
            }
            if (!merged) {
                result.add(entry)
            }
        }

        // Trim to 50 entries from the end
        return if (result.size > 50) {
            result.subList(result.size - 50, result.size).toList()
        } else {
            result
        }
    }

    fun stopListening() {
        job?.cancel()
        job = null
        logcatProcess?.destroy()
        logcatProcess = null
    }

    fun clearLogs() {
        logs.value = emptyList()
    }

    private fun parseLine(raw: String): LogEntry? {
        val message: String
        val isError: Boolean
        val priority: Int

        when {
            raw.contains("[ERROR]") -> {
                message = raw.substringAfter("[ERROR]").trim()
                isError = true
                priority = 6 // Log.ERROR
            }
            raw.contains("[WARN]") -> {
                message = raw.substringAfter("[WARN]").trim()
                isError = false // WARN is not ERROR, but distinctive
                priority = 5 // Log.WARN
            }
            raw.contains("[DEBUG]") -> {
                return null // DEBUG lines are hidden from UI
            }
            raw.contains("TgWsProxy") -> {
                // Info doesn't have a prefix, so we strip basically everything up to the actual message
                var msg = raw.substringAfter("TgWsProxy:").trim()
                if (msg.startsWith("[ERROR]") || msg.startsWith("[WARN]") || msg.startsWith("[DEBUG]")) {
                     return null // Handled above, but just in case
                }
                
                // Strip dynamic metrics like ↑3.3KB ↓1.1KB 0.3с so that lines can collapse
                if (msg.contains("↑")) {
                    msg = msg.substringBefore("↑").trim()
                }
                if (msg.contains("↓")) {
                    msg = msg.substringBefore("↓").trim()
                }

                message = msg
                isError = false
                priority = 4 // Log.INFO
            }
            else -> return null
        }

        return LogEntry(
            key = "log_${nextKey.getAndIncrement()}",
            message = message,
            count = 1,
            isError = isError,
            priority = priority
        )
    }
}
