package com.example.tgwsproxy

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.content.ClipData
import android.content.ClipboardManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.NightsStay
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.io.BufferedReader
import java.io.InputStreamReader

data class DataCenter(val name: String, val ips: String)

val datacenters = listOf(
    DataCenter("Нидерланды", "91.108.4.0/22,91.108.8.0/22,149.154.160.0/20"),
    DataCenter("Финляндия", "91.105.192.0/23,185.76.151.0/24"),
    DataCenter("Сингапур", "91.108.56.0/22,91.108.16.0/22"),
    DataCenter("Россия", "91.108.12.0/22,91.108.20.0/22")
)

val telegramApps = listOf(
    "org.telegram.messenger",
    "org.thunderdog.challegram",
    "com.radolyn.ayugram",
    "app.exteragram.messenger",
    "ir.ilmili.telegraph",
    "org.telegram.plus",
    "tw.nekomimi.nekogram",
    "tw.nekomimi.nekogramx",
    "org.telegram.mdgram",
    "com.iMe.android",
    "app.nicegram",
    "org.telegram.bgram",
    "cc.modery.cherrygram",
    "io.github.nextalone.nagram"
)

class MainActivity : ComponentActivity() {

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) {
        // Ignored in this example, but handles Tiramisu+ notifications
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        checkBatteryOptimizations()
        
        setContent {
            var isDarkTheme by remember { mutableStateOf(false) }
            val context = LocalContext.current

            // Dynamic colors logic for Android 12+ (Material You)
            val colorScheme = when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
                    if (isDarkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
                }
                isDarkTheme -> darkColorScheme()
                else -> lightColorScheme()
            }

            MaterialTheme(colorScheme = colorScheme) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    ProxyScreen(
                        isDarkTheme = isDarkTheme,
                        onThemeChange = { isDarkTheme = !isDarkTheme }
                    )
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
                } catch (e: Exception) {
                    Toast.makeText(this, "Не удалось запросить работу в фоне", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProxyScreen(isDarkTheme: Boolean, onThemeChange: () -> Unit) {
    val context = LocalContext.current
    val isRunning by ProxyService.isRunning.collectAsStateWithLifecycle()
    var selectedDc by remember { mutableStateOf<DataCenter?>(datacenters[0]) }
    var showDcModal by remember { mutableStateOf(false) }
    var portText by remember { mutableStateOf("1080") }
    var selectedPoolSize by remember { mutableStateOf(4) }
    var showLogs by rememberSaveable { mutableStateOf(true) }

    LaunchedEffect(showLogs) {
        if (showLogs) LogManager.startListening() else LogManager.stopListening()
    }

    val startProxyAction by rememberUpdatedState {
        val port = portText.toIntOrNull()
        if (port == null) {
            Toast.makeText(context, "Неверный порт", Toast.LENGTH_SHORT).show()
            return@rememberUpdatedState
        }
        if (selectedDc == null) {
            Toast.makeText(context, "Выберите пул датацентров", Toast.LENGTH_SHORT).show()
            return@rememberUpdatedState
        }
        
        val startIntent = Intent(context, ProxyService::class.java).apply {
            action = ProxyService.ACTION_START
            putExtra(ProxyService.EXTRA_PORT, port)
            putExtra(ProxyService.EXTRA_IPS, selectedDc!!.ips)
            putExtra(ProxyService.EXTRA_POOL_SIZE, selectedPoolSize)
        }
        ContextCompat.startForegroundService(context, startIntent)
    }

    val stopProxyAction by rememberUpdatedState {
        val stopIntent = Intent(context, ProxyService::class.java).apply {
            action = ProxyService.ACTION_STOP
        }
        context.startService(stopIntent)
    }

    val applyInTelegramAction by rememberUpdatedState {
        val port = portText.toIntOrNull() ?: 1080
        val proxyUrl = "tg://socks?server=127.0.0.1&port=$port"
        openTelegram(context, proxyUrl)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Telegram WS Proxy", fontWeight = FontWeight.SemiBold) },
                actions = {
                    IconButton(onClick = onThemeChange) {
                        Crossfade(targetState = isDarkTheme, animationSpec = tween(400), label = "themeAnim") { isDark ->
                            if (isDark) {
                                Icon(
                                    imageVector = Icons.Default.WbSunny,
                                    contentDescription = "Светлая тема",
                                    tint = MaterialTheme.colorScheme.onSurface
                                )
                            } else {
                                Icon(
                                    imageVector = Icons.Default.NightsStay,
                                    contentDescription = "Темная тема",
                                    tint = MaterialTheme.colorScheme.onSurface
                                )
                            }
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color.Transparent,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                    actionIconContentColor = MaterialTheme.colorScheme.onSurface
                )
            )
        },
        containerColor = MaterialTheme.colorScheme.background
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
            contentAlignment = Alignment.Center
        ) {
            // Constrain content width for tablets to look good anywhere
            Column(
                modifier = Modifier
                    .fillMaxHeight()
                    .widthIn(max = 600.dp)
                    .padding(horizontal = 24.dp, vertical = 24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Top // Push top fields higher
            ) {

                // Proxy Port Input
                OutlinedTextField(
                    value = portText,
                    onValueChange = { portText = it },
                    label = { Text("Порт прокси") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    shape = RoundedCornerShape(24.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 10.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.onSurfaceVariant
                    ),
                    singleLine = true
                )

                // DC selection
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp)
                        .clip(RoundedCornerShape(24.dp))
                        .clickable { showDcModal = true }
                ) {
                    OutlinedTextField(
                        value = selectedDc?.name ?: "",
                        onValueChange = {},
                        label = { Text("Пул датацентров") },
                        enabled = false,
                        shape = RoundedCornerShape(24.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            disabledTextColor = MaterialTheme.colorScheme.onSurface,
                            disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            disabledBorderColor = MaterialTheme.colorScheme.onSurfaceVariant
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )
                }



                // Pool size selector
                Text(
                    "Размер пула WS",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    listOf(4, 6, 8).forEach { size ->
                        val isSelected = selectedPoolSize == size
                        FilledTonalButton(
                            onClick = { selectedPoolSize = size },
                            enabled = !isRunning,
                            modifier = Modifier.weight(1f).height(48.dp),
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.filledTonalButtonColors(
                                containerColor = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
                                contentColor = if (isSelected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        ) {
                            Text(
                                "$size",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal
                            )
                        }
                    }
                }

                // Proxy Start/Stop Button
                AnimatedContent(
                    targetState = isRunning,
                    transitionSpec = {
                        fadeIn(animationSpec = tween(300)) togetherWith fadeOut(animationSpec = tween(300))
                    },
                    label = "runAnim"
                ) { running ->
                    Button(
                        onClick = {
                            if (running) stopProxyAction() else startProxyAction()
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(64.dp),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (running) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
                        )
                    ) {
                        Text(
                            if (running) "Остановить прокси" else "Запустить прокси",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }

                Spacer(modifier = Modifier.height(20.dp))

                // Apply in Telegram Button
                FilledTonalButton(
                    onClick = applyInTelegramAction,
                    enabled = isRunning,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(64.dp),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text(
                        "Применить в телеграмм", 
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                }

                Spacer(modifier = Modifier.height(12.dp))

                // Logs toggle button — same style as main buttons
                Button(
                    onClick = { showLogs = !showLogs },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(64.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.secondaryContainer,
                        contentColor = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                ) {
                    Text(
                        if (showLogs) "Скрыть логи" else "Показать логи",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                }

                if (showLogs) {
                    val logs by LogManager.logs.collectAsStateWithLifecycle()
                    val scroll = rememberScrollState()
                    val primaryColor = MaterialTheme.colorScheme.primary

                    // Auto-scroll to bottom when new logs arrive
                    LaunchedEffect(logs.size) {
                        scroll.animateScrollTo(scroll.maxValue)
                    }

                    Spacer(modifier = Modifier.height(12.dp))

                    Box(modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                    ) {
                        Text(
                            text = logs.joinToString("\n") { formatLogLine(it) },
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(start = 12.dp, end = 40.dp, top = 12.dp, bottom = 12.dp)
                                .verticalScroll(scroll),
                            color = primaryColor,
                            style = MaterialTheme.typography.bodySmall,
                            lineHeight = MaterialTheme.typography.bodySmall.fontSize * 1.5
                        )
                        IconButton(
                            onClick = {
                                val cm = ContextCompat.getSystemService(context, ClipboardManager::class.java)
                                cm?.setPrimaryClip(ClipData.newPlainText("Logs", logs.joinToString("\n")))
                                Toast.makeText(context, "Логи скопированы!", Toast.LENGTH_SHORT).show()
                            },
                            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp)
                        ) {
                            Icon(
                                Icons.Default.ContentCopy,
                                "Копировать логи",
                                tint = primaryColor.copy(alpha = 0.6f)
                            )
                        }
                    }
                } else {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
    }

    if (showDcModal) {
        DcSelectionDialog(
            currentValue = selectedDc,
            onDismiss = { showDcModal = false },
            onSelect = { 
                selectedDc = it
                showDcModal = false 
            }
        )
    }
}

@Composable
fun DcSelectionDialog(
    currentValue: DataCenter?,
    onDismiss: () -> Unit,
    onSelect: (DataCenter) -> Unit
) {
    val currentOnSelect by rememberUpdatedState(onSelect)

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(28.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.widthIn(max = 400.dp)
        ) {
            Column(modifier = Modifier.padding(24.dp)) {
                Text(
                    text = "Пул датацентров", 
                    style = MaterialTheme.typography.headlineSmall,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(bottom = 20.dp),
                    fontWeight = FontWeight.SemiBold
                )
                LazyColumn(
                    modifier = Modifier.padding(bottom = 8.dp)
                ) {
                    items(datacenters) { dc ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(16.dp))
                                .clickable { currentOnSelect(dc) }
                                .padding(vertical = 16.dp, horizontal = 12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            RadioButton(
                                selected = dc == currentValue,
                                onClick = null
                            )
                            Spacer(modifier = Modifier.width(16.dp))
                            Text(
                                text = dc.name, 
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
                
                Spacer(modifier = Modifier.height(8.dp))
                
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) {
                        Text("Отмена", style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
    }
}

fun formatLogLine(raw: String): String {
    // Raw logcat line example:
    // 03-24 14:30:45.057 I/TgWsProxy(24567): INFO  11:30:45 WS pool warmup started...
    // We want to extract: "11:30:45 WS pool warmup started..."
    val infoIdx = raw.indexOf("INFO  ")
    if (infoIdx >= 0) {
        return "• " + raw.substring(infoIdx + 6).trim()
    }
    val warnIdx = raw.indexOf("WARN  ")
    if (warnIdx >= 0) {
        return "⚠ " + raw.substring(warnIdx + 6).trim()
    }
    val errIdx = raw.indexOf("ERROR ")
    if (errIdx >= 0) {
        return "✖ " + raw.substring(errIdx + 6).trim()
    }
    // Fallback: try to find the message after ): 
    val msgIdx = raw.indexOf("): ")
    if (msgIdx >= 0) {
        return "• " + raw.substring(msgIdx + 3).trim()
    }
    return raw.trim()
}

fun openTelegram(context: Context, url: String) {
    val pm = context.packageManager
    val uri = Uri.parse(url)
    
    for (pkg in telegramApps) {
        try {
            pm.getPackageInfo(pkg, 0)
            val intent = Intent(Intent.ACTION_VIEW, uri)
            intent.setPackage(pkg)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            return
        } catch (e: PackageManager.NameNotFoundException) {
            // App not found, skip
        } catch (e: Exception) {
            // Activity not found or other err
        }
    }
    
    // Fallback: just open any app that handles tg:// link
    try {
        val fallbackIntent = Intent(Intent.ACTION_VIEW, uri)
        fallbackIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(fallbackIntent)
    } catch (e: Exception) {
        Toast.makeText(context, "Telegram не найден!", Toast.LENGTH_SHORT).show()
    }
}

object LogManager {
    val logs = MutableStateFlow<List<String>>(emptyList())
    private var job: Job? = null

    fun startListening() {
        if (job?.isActive == true) return
        job = CoroutineScope(Dispatchers.IO).launch {
            try {
                // Clear old logs just to avoid stale
                Runtime.getRuntime().exec("logcat -c").waitFor()
                val process = Runtime.getRuntime().exec(arrayOf("logcat", "-v", "time", "*:D"))
                val reader = BufferedReader(InputStreamReader(process.inputStream))
                
                val myPid = android.os.Process.myPid().toString()
                while (isActive) {
                    val line = reader.readLine() ?: break
                    if (line.contains(myPid) && (line.contains("INFO") || line.contains("WARN") || line.contains("ERROR"))) {
                        logs.update { current ->
                            val n = current + line
                            if (n.size > 30) n.takeLast(30) else n
                        }
                    }
                }
            } catch (e: Exception) {}
        }
    }

    fun stopListening() {
        job?.cancel()
        job = null
        logs.value = emptyList()
    }
}
