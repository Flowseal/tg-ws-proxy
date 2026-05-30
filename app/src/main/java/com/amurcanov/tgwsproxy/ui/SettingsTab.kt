package com.amurcanov.tgwsproxy.ui

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.amurcanov.tgwsproxy.ProxyService
import com.amurcanov.tgwsproxy.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

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

private fun generateRandomSecret(): String {
    val bytes = ByteArray(16)
    java.security.SecureRandom().nextBytes(bytes)
    return bytes.joinToString("") { "%02x".format(it) }
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
        } catch (_: PackageManager.NameNotFoundException) {
        } catch (_: Exception) {
        }
    }
    try {
        val fallbackIntent = Intent(Intent.ACTION_VIEW, uri)
        fallbackIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(fallbackIntent)
    } catch (_: Exception) {
        Toast.makeText(context, context.getString(com.amurcanov.tgwsproxy.R.string.telegram_not_found), Toast.LENGTH_SHORT).show()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsTab(settingsStore: SettingsStore) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val isRunning by ProxyService.isRunning.collectAsStateWithLifecycle()

    val isReady by settingsStore.isReady.collectAsStateWithLifecycle(initialValue = false)
    val isExperimental by settingsStore.isExperimentalMode.collectAsStateWithLifecycle(initialValue = false)

    val savedIsDcAuto by settingsStore.isDcAuto.collectAsStateWithLifecycle(initialValue = true)
    val savedDc1 by settingsStore.dc1.collectAsStateWithLifecycle(initialValue = "")
    val savedDc2 by settingsStore.dc2.collectAsStateWithLifecycle(initialValue = SettingsStore.DEFAULT_DIRECT_DC2_IP)
    val savedDc3 by settingsStore.dc3.collectAsStateWithLifecycle(initialValue = "")
    val savedDc4 by settingsStore.dc4.collectAsStateWithLifecycle(initialValue = SettingsStore.DEFAULT_DIRECT_DC4_IP)
    val savedDc5 by settingsStore.dc5.collectAsStateWithLifecycle(initialValue = "")
    val savedDc203 by settingsStore.dc203.collectAsStateWithLifecycle(initialValue = "")
    val savedDc1m by settingsStore.dc1m.collectAsStateWithLifecycle(initialValue = "")
    val savedDc2m by settingsStore.dc2m.collectAsStateWithLifecycle(initialValue = "")
    val savedDc3m by settingsStore.dc3m.collectAsStateWithLifecycle(initialValue = "")
    val savedDc4m by settingsStore.dc4m.collectAsStateWithLifecycle(initialValue = "")
    val savedDc5m by settingsStore.dc5m.collectAsStateWithLifecycle(initialValue = "")
    val savedDc203m by settingsStore.dc203m.collectAsStateWithLifecycle(initialValue = "")
    val savedPort by settingsStore.port.collectAsStateWithLifecycle(initialValue = "1443")
    val savedPoolSize by settingsStore.poolSize.collectAsStateWithLifecycle(initialValue = 4)
    val savedCfEnabled by settingsStore.cfproxyEnabled.collectAsStateWithLifecycle(initialValue = true)
    val savedCustomDomainEnabled by settingsStore.customCfDomainEnabled.collectAsStateWithLifecycle(initialValue = false)
    val savedCustomDomain by settingsStore.customCfDomain.collectAsStateWithLifecycle(initialValue = "")
    val autoStartOnBoot by settingsStore.autoStartOnBoot.collectAsStateWithLifecycle(initialValue = false)
    val savedSecretKey by settingsStore.secretKey.collectAsStateWithLifecycle(initialValue = "LOADING")

    if (!isReady) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(24.dp),
                strokeWidth = 2.dp
            )
        }
        return
    }

    var isDcAuto by rememberSaveable(savedIsDcAuto) { mutableStateOf(savedIsDcAuto) }
    var experimentalMode by rememberSaveable(isExperimental) { mutableStateOf(isExperimental) }
    var dc1Text by rememberSaveable(savedDc1) { mutableStateOf(savedDc1) }
    var dc2Text by rememberSaveable(savedDc2) { mutableStateOf(savedDc2) }
    var dc3Text by rememberSaveable(savedDc3) { mutableStateOf(savedDc3) }
    var dc4Text by rememberSaveable(savedDc4) { mutableStateOf(savedDc4) }
    var dc5Text by rememberSaveable(savedDc5) { mutableStateOf(savedDc5) }
    var dc203Text by rememberSaveable(savedDc203) { mutableStateOf(savedDc203) }
    var dc1mText by rememberSaveable(savedDc1m) { mutableStateOf(savedDc1m) }
    var dc2mText by rememberSaveable(savedDc2m) { mutableStateOf(savedDc2m) }
    var dc3mText by rememberSaveable(savedDc3m) { mutableStateOf(savedDc3m) }
    var dc4mText by rememberSaveable(savedDc4m) { mutableStateOf(savedDc4m) }
    var dc5mText by rememberSaveable(savedDc5m) { mutableStateOf(savedDc5m) }
    var dc203mText by rememberSaveable(savedDc203m) { mutableStateOf(savedDc203m) }

    var portText by rememberSaveable(savedPort) { mutableStateOf(savedPort) }
    var selectedPoolSize by rememberSaveable(savedPoolSize) { mutableIntStateOf(savedPoolSize) }
    var cfEnabled by rememberSaveable(savedCfEnabled) { mutableStateOf(savedCfEnabled) }
    var customCfDomainEnabled by rememberSaveable(savedCustomDomainEnabled) { mutableStateOf(savedCustomDomainEnabled) }
    var customCfDomain by rememberSaveable(savedCustomDomain) { mutableStateOf(savedCustomDomain) }
    var secretKeyText by remember(savedSecretKey) { mutableStateOf(if (savedSecretKey == "LOADING") "" else savedSecretKey) }

    LaunchedEffect(savedSecretKey) {
        if (savedSecretKey == "") {
            val generated = generateRandomSecret()
            secretKeyText = generated
            settingsStore.saveSecretKey(generated)
        } else if (savedSecretKey != "LOADING") {
            secretKeyText = savedSecretKey
        }
    }

    var saveJob by remember { mutableStateOf<Job?>(null) }

    fun scheduleSave() {
        saveJob?.cancel()
        saveJob = scope.launch {
            delay(300)
            settingsStore.saveAll(
                isDcAuto, dc1Text, dc2Text, dc3Text, dc4Text, dc5Text, dc203Text,
                dc1mText, dc2mText, dc3mText, dc4mText, dc5mText, dc203mText,
                experimentalMode, portText, selectedPoolSize,
                cfEnabled, customCfDomainEnabled, customCfDomain, secretKeyText
            )
        }
    }

    var showIpSetupDialog by rememberSaveable { mutableStateOf(false) }
    val scrollState = rememberScrollState()

    if (showIpSetupDialog) {
        IpSetupDialog(
            isExperimental = experimentalMode,
            onExperimentalChange = { experimentalMode = it; scheduleSave() },
            dc1Text = dc1Text, onDc1Change = { dc1Text = it; scheduleSave() },
            dc2Text = dc2Text, onDc2Change = { dc2Text = it; scheduleSave() },
            dc3Text = dc3Text, onDc3Change = { dc3Text = it; scheduleSave() },
            dc4Text = dc4Text, onDc4Change = { dc4Text = it; scheduleSave() },
            dc5Text = dc5Text, onDc5Change = { dc5Text = it; scheduleSave() },
            dc203Text = dc203Text, onDc203Change = { dc203Text = it; scheduleSave() },
            dc1mText = dc1mText, onDc1mChange = { dc1mText = it; scheduleSave() },
            dc2mText = dc2mText, onDc2mChange = { dc2mText = it; scheduleSave() },
            dc3mText = dc3mText, onDc3mChange = { dc3mText = it; scheduleSave() },
            dc4mText = dc4mText, onDc4mChange = { dc4mText = it; scheduleSave() },
            dc5mText = dc5mText, onDc5mChange = { dc5mText = it; scheduleSave() },
            dc203mText = dc203mText, onDc203mChange = { dc203mText = it; scheduleSave() },
            onDismiss = { showIpSetupDialog = false }
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(start = 16.dp, end = 16.dp, top = 0.dp, bottom = 12.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = stringResource(com.amurcanov.tgwsproxy.R.string.settings),
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                color = MaterialTheme.colorScheme.onSurface
            )
        }

        Spacer(modifier = Modifier.height(8.dp))
        
        AppSectionCard {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Default.Public, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                    Text(
                        stringResource(com.amurcanov.tgwsproxy.R.string.connection),
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                OutlinedTextField(
                    value = portText,
                    onValueChange = { portText = it; scheduleSave() },
                    label = { Text(stringResource(com.amurcanov.tgwsproxy.R.string.port)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().height(56.dp),
                    shape = RoundedCornerShape(24.dp),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
                    )
                )
                OutlinedButton(
                    onClick = { showIpSetupDialog = true },
                    enabled = !cfEnabled && !isRunning,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    shape = RoundedCornerShape(24.dp),
                    colors = ButtonDefaults.outlinedButtonColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                        disabledContainerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f),
                        contentColor = MaterialTheme.colorScheme.primary,
                        disabledContentColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f)
                    ),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = if (cfEnabled || isRunning) 0.2f else 0.5f))
                ) {
                    Icon(Icons.Default.Settings, null, Modifier.size(20.dp))
                    if (cfEnabled) {
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(com.amurcanov.tgwsproxy.R.string.auto_cf_enabled), fontWeight = FontWeight.SemiBold)
                    } else {
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(com.amurcanov.tgwsproxy.R.string.configure_dc_addresses), fontWeight = FontWeight.SemiBold)
                    }
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f))

            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Default.Layers, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                    Text(
                        stringResource(com.amurcanov.tgwsproxy.R.string.ws_pool),
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    val poolOptions = listOf(2, 4, 6)
                    poolOptions.forEach { size ->
                        PoolChip(
                            label = "$size",
                            selected = selectedPoolSize == size,
                            enabled = !isRunning,
                            modifier = Modifier.weight(1f).height(48.dp)
                        ) {
                            selectedPoolSize = size
                            scheduleSave()
                        }
                    }
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f))

            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        Icons.Default.VpnKey, null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(20.dp)
                    )
                    Text(
                        stringResource(com.amurcanov.tgwsproxy.R.string.secret_key),
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                OutlinedTextField(
                    value = secretKeyText,
                    onValueChange = {},
                    readOnly = true,
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().height(56.dp),
                    shape = RoundedCornerShape(24.dp),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                    trailingIcon = {
                        IconButton(
                            onClick = {
                                val newKey = generateRandomSecret()
                                secretKeyText = newKey
                                scope.launch { settingsStore.saveSecretKey(newKey) }
                                scheduleSave()
                            },
                            enabled = !isRunning
                        ) {
                            Icon(Icons.Default.Refresh, null, tint = MaterialTheme.colorScheme.primary)
                        }
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
                    )
                )
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        Icons.Default.Cloud, null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(20.dp)
                    )
                    Text(
                        "CloudFlare CDN",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                Switch(
                    checked = cfEnabled,
                    onCheckedChange = {
                        cfEnabled = it
                        isDcAuto = it
                        scheduleSave()
                    },
                    enabled = !isRunning
                )
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        Icons.Default.PowerSettingsNew, null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(20.dp)
                    )
                    Text(
                        stringResource(com.amurcanov.tgwsproxy.R.string.autostart),
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                Switch(
                    checked = autoStartOnBoot,
                    onCheckedChange = { enabled ->
                        scope.launch { settingsStore.saveAutoStartOnBoot(enabled) }
                    }
                )
            }
        }

        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun PoolChip(
    label: String,
    selected: Boolean,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(24.dp),
        modifier = modifier.height(48.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
            contentColor = if (selected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
        )
    ) {
        Text(
            label,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium
        )
    }
}


@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun IpSetupDialog(
    isExperimental: Boolean,
    onExperimentalChange: (Boolean) -> Unit,
    dc1Text: String, onDc1Change: (String) -> Unit,
    dc2Text: String, onDc2Change: (String) -> Unit,
    dc3Text: String, onDc3Change: (String) -> Unit,
    dc4Text: String, onDc4Change: (String) -> Unit,
    dc5Text: String, onDc5Change: (String) -> Unit,
    dc203Text: String, onDc203Change: (String) -> Unit,
    dc1mText: String, onDc1mChange: (String) -> Unit,
    dc2mText: String, onDc2mChange: (String) -> Unit,
    dc3mText: String, onDc3mChange: (String) -> Unit,
    dc4mText: String, onDc4mChange: (String) -> Unit,
    dc5mText: String, onDc5mChange: (String) -> Unit,
    dc203mText: String, onDc203mChange: (String) -> Unit,
    onDismiss: () -> Unit
) {
    val onIpChange = { newValue: String, update: (String) -> Unit ->
        if (newValue.all { it.isDigit() || it == '.' }) {
            update(newValue)
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Surface(
            shape = RoundedCornerShape(24.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 8.dp,
            modifier = Modifier
                .fillMaxWidth(0.95f)
                .wrapContentHeight()
                .heightIn(max = 560.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    stringResource(com.amurcanov.tgwsproxy.R.string.dc_addresses),
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold
                )

                @Composable
                fun dcInput(label: String, value: String, update: (String) -> Unit) {
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            label,
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.primary
                        )
                        OutlinedTextField(
                            value = value,
                            onValueChange = { onIpChange(it, update) },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth().height(52.dp),
                            shape = RoundedCornerShape(24.dp),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor = MaterialTheme.colorScheme.primary,
                                unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
                            )
                        )
                    }
                }

                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f, fill = false)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    if (isExperimental) {
                        dcInput("DC1", dc1Text, onDc1Change)
                        dcInput("DC2", dc2Text, onDc2Change)
                        dcInput("DC3", dc3Text, onDc3Change)
                        dcInput("DC4", dc4Text, onDc4Change)
                        dcInput("DC5", dc5Text, onDc5Change)
                        dcInput("DC203", dc203Text, onDc203Change)

                        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

                        Text(stringResource(com.amurcanov.tgwsproxy.R.string.media_dcs), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)

                        dcInput("DC1m", dc1mText, onDc1mChange)
                        dcInput("DC2m", dc2mText, onDc2mChange)
                        dcInput("DC3m", dc3mText, onDc3mChange)
                        dcInput("DC4m", dc4mText, onDc4mChange)
                        dcInput("DC5m", dc5mText, onDc5mChange)
                        dcInput("DC203m", dc203mText, onDc203mChange)
                    } else {
                        dcInput("DC2", dc2Text, onDc2Change)
                        dcInput("DC4", dc4Text, onDc4Change)
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        stringResource(com.amurcanov.tgwsproxy.R.string.experimental_mode),
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                    Switch(
                        checked = isExperimental,
                        onCheckedChange = onExperimentalChange
                    )
                }

                Button(
                    onClick = onDismiss,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    shape = RoundedCornerShape(24.dp)
                ) {
                    Text(stringResource(com.amurcanov.tgwsproxy.R.string.done), fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}
