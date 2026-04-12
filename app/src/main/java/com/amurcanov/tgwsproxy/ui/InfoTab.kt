package com.amurcanov.tgwsproxy.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.amurcanov.tgwsproxy.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

private val BROWSER_PACKAGES = listOf(
    "com.android.chrome",
    "com.google.android.googlequicksearchbox",
    "org.mozilla.firefox",
    "com.yandex.browser",
    "ru.yandex.searchplugin",
    "com.yandex.browser.lite",
    "com.opera.browser",
    "com.opera.mini.native",
    "com.microsoft.emmx",
    "com.brave.browser",
    "com.duckduckgo.mobile.android",
    "com.sec.android.app.sbrowser",
    "com.vivaldi.browser",
    "com.kiwibrowser.browser",
)

private fun openUrlInBrowser(context: Context, url: String) {
    try {
        val pm = context.packageManager
        val uri = Uri.parse(url)

        for (pkg in BROWSER_PACKAGES) {
            try {
                val intent = Intent(Intent.ACTION_VIEW, uri)
                intent.setPackage(pkg)
                if (intent.resolveActivity(pm) != null) {
                    context.startActivity(intent)
                    return
                }
            } catch (_: Exception) {}
        }
        val intent = Intent(Intent.ACTION_VIEW, uri)
        if (intent.resolveActivity(pm) != null) {
            context.startActivity(intent)
        }
    } catch (_: Exception) {}
}

/**
 * Sealed interface for update check result — prevents incorrect state combinations.
 */
private sealed interface UpdateCheckResult {
    data object Idle : UpdateCheckResult
    data object Loading : UpdateCheckResult
    data class UpToDate(val version: String) : UpdateCheckResult
    data class NewVersion(val version: String) : UpdateCheckResult
    data class Error(val message: String) : UpdateCheckResult
}

/**
 * Fetch latest tag from GitHub releases via API.
 * Uses redirect-following GET to the tags page on the GitHub API.
 */
private suspend fun checkLatestVersion(): String? = withContext(Dispatchers.IO) {
    try {
        // Use GitHub API to get latest release / tags
        val url = URL("https://api.github.com/repos/amurcanov/tg-ws-proxy-android/tags?per_page=1")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.setRequestProperty("Accept", "application/vnd.github.v3+json")
        conn.connectTimeout = 8000
        conn.readTimeout = 8000

        if (conn.responseCode == 200) {
            val body = conn.inputStream.bufferedReader().readText()
            // Parse the first tag name from JSON array: [{"name":"v1.0.6",...}]
            val regex = """"name"\s*:\s*"([^"]+)"""".toRegex()
            val match = regex.find(body)
            match?.groupValues?.get(1)
        } else {
            null
        }
    } catch (_: Exception) {
        null
    }
}

/**
 * Compare two version strings like "v1.0.6" and "v1.0.7"
 * Returns true if remote is strictly newer than local.
 */
private fun isNewerVersion(local: String, remote: String): Boolean {
    val localParts = local.removePrefix("v").split(".").mapNotNull { it.toIntOrNull() }
    val remoteParts = remote.removePrefix("v").split(".").mapNotNull { it.toIntOrNull() }
    val maxLen = maxOf(localParts.size, remoteParts.size)
    for (i in 0 until maxLen) {
        val l = localParts.getOrElse(i) { 0 }
        val r = remoteParts.getOrElse(i) { 0 }
        if (r > l) return true
        if (r < l) return false
    }
    return false
}

@Composable
fun InfoTab() {
    val currentVersion = "v1.0.6"
    val scope = rememberCoroutineScope()
    var updateResult by remember { mutableStateOf<UpdateCheckResult>(UpdateCheckResult.Idle) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "Дополнительная информация",
            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Start
        )

        // ═══ Версия ═══
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 1.dp)
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "Установлена версия $currentVersion",
                    style = MaterialTheme.typography.titleMedium.copy(
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    ),
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth()
                )

                // ═══ Проверить обновление ═══
                Button(
                    onClick = {
                        updateResult = UpdateCheckResult.Loading
                        scope.launch {
                            val latestTag = checkLatestVersion()
                            updateResult = if (latestTag != null) {
                                if (isNewerVersion(currentVersion, latestTag)) {
                                    UpdateCheckResult.NewVersion(latestTag)
                                } else {
                                    UpdateCheckResult.UpToDate(currentVersion)
                                }
                            } else {
                                UpdateCheckResult.Error("Не удалось проверить")
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(0.9f).height(48.dp),
                    shape = RoundedCornerShape(16.dp),
                    enabled = updateResult !is UpdateCheckResult.Loading,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                        contentColor = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                ) {
                    when (updateResult) {
                        is UpdateCheckResult.Loading -> {
                            Icon(
                                Icons.Default.Refresh,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                                tint = MaterialTheme.colorScheme.onPrimaryContainer
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Проверяем...", fontWeight = FontWeight.SemiBold)
                        }
                        is UpdateCheckResult.UpToDate -> {
                            Icon(
                                Icons.Default.CheckCircle,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                                tint = AppColors.terminalGreen
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Последняя версия ✓", fontWeight = FontWeight.SemiBold)
                        }
                        is UpdateCheckResult.NewVersion -> {
                            val ver = (updateResult as UpdateCheckResult.NewVersion).version
                            Icon(
                                Icons.Default.NewReleases,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                                tint = AppColors.terminalOrange
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Вышла $ver", fontWeight = FontWeight.Bold)
                        }
                        is UpdateCheckResult.Error -> {
                            Text("Проверить обновление", fontWeight = FontWeight.SemiBold)
                        }
                        is UpdateCheckResult.Idle -> {
                            Text("Проверить обновление", fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }

        // ═══ GitHub ═══
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                GitHubSection(
                    title = "Актуальные релизы",
                    buttonText = "tg-ws-proxy-android",
                    url = "https://github.com/amurcanov/tg-ws-proxy-android/releases"
                )

                Divider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))

                GitHubSection(
                    title = "Страница разработчика",
                    buttonText = "GitHub Amurcanov",
                    url = "https://github.com/amurcanov"
                )

                Divider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))

                GitHubSection(
                    title = "Если возникли проблемы",
                    buttonText = "Поднять вопрос",
                    url = "https://github.com/amurcanov/tg-ws-proxy-android/issues/new"
                )
            }
        }



        Spacer(modifier = Modifier.height(16.dp))
    }
}

@Composable
private fun GitHubSection(
    title: String,
    buttonText: String,
    url: String
) {
    val context = LocalContext.current
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium.copy(
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )
        )
        Button(
            onClick = { openUrlInBrowser(context, url) },
            modifier = Modifier.fillMaxWidth(0.9f).height(48.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer,
                contentColor = MaterialTheme.colorScheme.onPrimaryContainer
            )
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    painter = painterResource(id = R.drawable.ic_github),
                    contentDescription = null,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = buttonText,
                    style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold, fontSize = 15.sp)
                )
            }
        }
    }
}
