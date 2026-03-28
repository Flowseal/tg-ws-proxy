package com.amurcanov.tgwsproxy

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
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.io.BufferedReader
import java.io.InputStreamReader

// DataCenters list removed

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
    val prefs = context.getSharedPreferences("ProxyPrefs", Context.MODE_PRIVATE)
    val isRunning by ProxyService.isRunning.collectAsStateWithLifecycle()
    var dc2Text by remember { mutableStateOf(prefs.getString("dc2", "149.154.167.220") ?: "149.154.167.220") }
    var dc4Text by remember { mutableStateOf(prefs.getString("dc4", "149.154.167.220") ?: "149.154.167.220") }
    var dc203Text by remember { mutableStateOf(prefs.getString("dc203", "149.154.167.220") ?: "149.154.167.220") }
    var portText by remember { mutableStateOf(prefs.getString("port", "1080") ?: "1080") }
    var selectedPoolSize by remember { mutableStateOf(prefs.getInt("pool", 4)) }
    var showLogs by rememberSaveable { mutableStateOf(true) }
    var showInfoModal by remember { mutableStateOf(false) }
    var showIpSetupModal by remember { mutableStateOf(false) }

    LaunchedEffect(showLogs) {
        if (showLogs) LogManager.startListening() else LogManager.stopListening()
    }

    val startProxyAction by rememberUpdatedState {
        val port = portText.toIntOrNull()
        if (port == null) {
            Toast.makeText(context, "Неверный порт", Toast.LENGTH_SHORT).show()
            return@rememberUpdatedState
        }
        val parsedIps = buildList {
            if (dc2Text.isNotBlank()) add("2:${dc2Text.trim()}")
            if (dc4Text.isNotBlank()) add("4:${dc4Text.trim()}")
            if (dc203Text.isNotBlank()) add("203:${dc203Text.trim()}")
        }.joinToString(",")

        if (parsedIps.isEmpty()) {
            Toast.makeText(context, "Впишите IP хотя бы для одного DC", Toast.LENGTH_SHORT).show()
            return@rememberUpdatedState
        }
        
        val startIntent = Intent(context, ProxyService::class.java).apply {
            action = ProxyService.ACTION_START
            putExtra(ProxyService.EXTRA_PORT, port)
            putExtra(ProxyService.EXTRA_IPS, parsedIps)
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
                    TextButton(
                        onClick = { showInfoModal = true },
                        colors = ButtonDefaults.textButtonColors(
                            containerColor = Color.Transparent,
                            contentColor = MaterialTheme.colorScheme.onSurface
                        ),
                        modifier = Modifier.padding(end = 12.dp)
                    ) {
                        Text("инфо", fontWeight = FontWeight.SemiBold, fontSize = 22.sp)
                    }
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
                    onValueChange = { 
                        portText = it
                        prefs.edit().putString("port", it).apply()
                    },
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

                // DC selection modal button
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp)
                        .clip(RoundedCornerShape(24.dp))
                        .clickable { showIpSetupModal = true }
                ) {
                    OutlinedTextField(
                        value = "Настроить адреса",
                        onValueChange = {},
                        label = { Text("Настройка IP") },
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
                            onClick = { 
                                selectedPoolSize = size
                                prefs.edit().putInt("pool", size).apply()
                            },
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

                Spacer(modifier = Modifier.height(12.dp))

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

    if (showInfoModal) {
        InfoDialog(onDismiss = { showInfoModal = false })
    }

    if (showIpSetupModal) {
        IpSetupDialog(
            dc2Text = dc2Text,
            onDc2Change = { 
                dc2Text = it
                prefs.edit().putString("dc2", it).apply()
            },
            dc4Text = dc4Text,
            onDc4Change = { 
                dc4Text = it
                prefs.edit().putString("dc4", it).apply()
            },
            dc203Text = dc203Text,
            onDc203Change = { 
                dc203Text = it
                prefs.edit().putString("dc203", it).apply()
            },
            onDismiss = { showIpSetupModal = false }
        )
    }
}

@Composable
fun IpSetupDialog(
    dc2Text: String, onDc2Change: (String) -> Unit,
    dc4Text: String, onDc4Change: (String) -> Unit,
    dc203Text: String, onDc203Change: (String) -> Unit,
    onDismiss: () -> Unit
) {
    val onIpChange = { newValue: String, update: (String) -> Unit ->
        if (newValue.all { it.isDigit() || it == '.' }) {
            update(newValue)
        }
    }

    @Composable
    fun dcInput(label: String, value: String, update: (String) -> Unit) {
        OutlinedTextField(
            value = value,
            onValueChange = { onIpChange(it, update) },
            label = { Text(label) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            shape = RoundedCornerShape(24.dp),
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 8.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = MaterialTheme.colorScheme.primary,
                unfocusedBorderColor = MaterialTheme.colorScheme.onSurfaceVariant
            ),
            singleLine = true
        )
    }

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
                
                dcInput("DC2", dc2Text, onDc2Change)
                dcInput("DC4", dc4Text, onDc4Change)
                dcInput("DC203", dc203Text, onDc203Change)
                
                Spacer(modifier = Modifier.height(8.dp))
                
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) {
                        Text("Готово", style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
    }
}

@Composable
fun InfoDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(24.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.widthIn(max = 400.dp).fillMaxWidth()
        ) {
            Column(
                modifier = Modifier
                    .padding(24.dp)
                    .verticalScroll(rememberScrollState())
            ) {
                Text(
                    text = "Версия 1.0.4",
                    style = MaterialTheme.typography.headlineSmall,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(bottom = 16.dp)
                )

                Text(
                    text = "Что нового:",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.padding(bottom = 8.dp)
                )

                Text(
                    text = "1. Убран выбор пула датацентров",
                    color = Color(0xFFD32F2F),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(bottom = 4.dp)
                )

                Text(
                    text = "2. Добавлена возможность ввода IP датацентров вручную",
                    color = Color(0xFF388E3C),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(bottom = 4.dp)
                )

                Text(
                    text = "3. При использовании IP адреса, указанного по умолчанию (149.154.167.220), вспомогательные средства (VPN и прочее) не требуются.",
                    color = MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(bottom = 16.dp)
                )

                Divider(modifier = Modifier.padding(vertical = 12.dp), color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.2f))

                val openLink = { url: String ->
                    try {
                        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                        context.startActivity(intent)
                    } catch (e: Exception) {
                        Toast.makeText(context, "Не удалось открыть ссылку", Toast.LENGTH_SHORT).show()
                    }
                }

                Column(modifier = Modifier.fillMaxWidth()) {
                    Text("Оригинальный автор tg-ws-proxy:", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        text = "→ Flowseal",
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(top = 2.dp, start = 8.dp).clickable { openLink("https://github.com/Flowseal") }
                    )
                }

                Spacer(modifier = Modifier.height(12.dp))

                Column(modifier = Modifier.fillMaxWidth()) {
                    Text("Человек, благодаря кому вышла v1.0.4:", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        text = "→ IMDelewer",
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(top = 2.dp, start = 8.dp).clickable { openLink("https://github.com/IMDelewer") }
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))

                Text(
                    text = buildAnnotatedString {
                        append("Ознакомиться с актуальным списком CIDR датацентров Telegram можно ")
                        withStyle(style = SpanStyle(
                            color = MaterialTheme.colorScheme.primary,
                            textDecoration = TextDecoration.Underline
                        )) {
                            append("тут")
                        }
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .clickable { openLink("https://core.telegram.org/resources/cidr.txt") }
                        .padding(bottom = 16.dp)
                )

                Text(
                    text = "Вероятнее всего, изменение IP адресов в графах DC может нарушить работу прокси без работающего VPN. " +
                           "Не советую ничего менять без необходимости. Однако, если у вас наблюдаются проблемы в Telegram " +
                           "при использовании адреса 149.154.167.220, вы можете заменить его на другие IP из актуальных списков. " +
                           "Помните, что в таком случае вам может потребоваться включённый VPN — этот двойственный способ (Proxy + VPN) " +
                           "зачастую решает проблемы соединения, если Telegram отказывается стабильно работать.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    lineHeight = 16.sp
                )

                Spacer(modifier = Modifier.height(24.dp))

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) {
                        Text("Закрыть", style = MaterialTheme.typography.labelLarge)
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
    val dbgIdx = raw.indexOf("DEBUG ")
    if (dbgIdx >= 0) {
        return "◦ " + raw.substring(dbgIdx + 6).trim()
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
    private var logcatProcess: Process? = null

    fun startListening() {
        if (job?.isActive == true) return
        job = CoroutineScope(Dispatchers.IO).launch {
            try {
                // Clear old logs just to avoid stale
                Runtime.getRuntime().exec("logcat -c").waitFor()
                val process = Runtime.getRuntime().exec(arrayOf("logcat", "-v", "time", "*:D"))
                logcatProcess = process
                val reader = BufferedReader(InputStreamReader(process.inputStream))
                
                val myPid = android.os.Process.myPid().toString()
                while (isActive) {
                    val line = reader.readLine() ?: break
                    if (line.contains(myPid) && (line.contains("INFO") || line.contains("WARN") || line.contains("ERROR") || line.contains("DEBUG"))) {
                        logs.update { current ->
                            val n = current + line
                            if (n.size > 30) n.takeLast(30) else n
                        }
                    }
                }
            } catch (e: Exception) {
            } finally {
                logcatProcess?.destroy()
                logcatProcess = null
            }
        }
    }

    fun stopListening() {
        job?.cancel()
        job = null
        logcatProcess?.destroy()
        logcatProcess = null
        logs.value = emptyList()
    }
}
