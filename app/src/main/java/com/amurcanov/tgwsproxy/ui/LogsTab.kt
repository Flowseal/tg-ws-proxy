package com.amurcanov.tgwsproxy.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.core.*
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.amurcanov.tgwsproxy.LogEntry
import com.amurcanov.tgwsproxy.LogManager

@Composable
fun LogsTab() {
    val context = LocalContext.current
    val currentLogs by LogManager.logs.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()

    LaunchedEffect(currentLogs.size) {
        if (currentLogs.isNotEmpty()) {
            listState.scrollToItem(currentLogs.size - 1)
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "Лог событий",
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                color = MaterialTheme.colorScheme.onSurface
            )
            Row {
                IconButton(onClick = { LogManager.clearLogs() }) {
                    Icon(Icons.Default.Delete, contentDescription = "Очистить", tint = MaterialTheme.colorScheme.primary)
                }
                IconButton(onClick = {
                    val text = currentLogs.joinToString("\n") { "${it.message} (x${it.count})" }
                    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    val clip = ClipData.newPlainText("TgWsProxy Logs", text)
                    clipboard.setPrimaryClip(clip)
                    Toast.makeText(context, "Скопировано", Toast.LENGTH_SHORT).show()
                }) {
                    Icon(Icons.Default.ContentCopy, contentDescription = "Копировать", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }

        val isDark = isSystemInDarkTheme()
        val terminalBg = if (isDark) AppColors.terminalBgDark else AppColors.terminalBg

        Card(
            modifier = Modifier.fillMaxSize(),
            colors = CardDefaults.cardColors(containerColor = terminalBg),
            shape = RoundedCornerShape(20.dp),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
        ) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(12.dp),
                contentPadding = PaddingValues(bottom = 12.dp)
            ) {
                items(currentLogs, key = { it.key }) { entry ->
                    LogLine(entry)
                }
            }
        }
    }
}

@Composable
private fun LogLine(entry: LogEntry) {
    val color = when (entry.priority) {
        6 -> AppColors.terminalRed    // ERROR
        5 -> AppColors.terminalOrange // WARN (Нужно убедиться, что Orange есть в AppColors)
        4 -> AppColors.terminalGreen  // INFO
        3 -> AppColors.terminalBlue   // DEBUG
        else -> AppColors.terminalText
    }

    var trigger by remember { mutableIntStateOf(0) }
    LaunchedEffect(entry.count) { trigger++ }

    val animatedScale by animateFloatAsState(
        targetValue = if (trigger > 0) 1.15f else 1.0f,
        animationSpec = spring(dampingRatio = Spring.DampingRatioMediumBouncy),
        label = "scale",
        finishedListener = { trigger = 0 }
    )

    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Surface(
            color = AppColors.terminalCounter.copy(alpha = 0.2f),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier
                .defaultMinSize(minWidth = 24.dp, minHeight = 24.dp)
                .graphicsLayer(scaleX = animatedScale, scaleY = animatedScale)
        ) {
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier.padding(horizontal = 6.dp)
            ) {
                Text(
                    text = "${entry.count}",
                    color = AppColors.terminalBlue,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1
                )
            }
        }

        Spacer(modifier = Modifier.width(12.dp))

        Text(
            text = entry.message,
            color = color,
            fontSize = 13.sp,
            fontFamily = FontFamily.Monospace,
            fontWeight = if (entry.isError) FontWeight.Bold else FontWeight.Normal,
            lineHeight = 18.sp,
            modifier = Modifier.weight(1f)
        )
    }
}
