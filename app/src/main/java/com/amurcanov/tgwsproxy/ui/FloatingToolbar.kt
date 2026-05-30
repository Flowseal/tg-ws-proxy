package com.amurcanov.tgwsproxy.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.amurcanov.tgwsproxy.R
import kotlin.math.roundToInt
import androidx.compose.ui.draw.scale
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.border
import androidx.compose.ui.graphics.Color
import android.os.Build

@Composable
fun FloatingToolbar(
    currentTheme: String,
    onThemeChange: (String) -> Unit,
    isDynamicColor: Boolean,
    onDynamicColorChange: (Boolean) -> Unit,
    currentPalette: String,
    onPaletteChange: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val screenHeightPx = remember(configuration.screenHeightDp, density) {
        with(density) { configuration.screenHeightDp.dp.toPx() }
    }
    val screenWidthPx = remember(configuration.screenWidthDp, density) {
        with(density) { configuration.screenWidthDp.dp.toPx() }
    }

    var offsetY by rememberSaveable { mutableFloatStateOf(-1f) }
    var isRightSide by rememberSaveable { mutableStateOf(true) }
    var isExpanded by rememberSaveable { mutableStateOf(false) }
    var tabHeightPx by remember { mutableFloatStateOf(0f) }
    var panelHeightPx by remember { mutableFloatStateOf(0f) }

    val tabWidthDp = 42.dp
    val tabHeightDp = 52.dp
    val panelWidthDp = 220.dp

    val tabWidthPx = remember(density) { with(density) { tabWidthDp.toPx() } }
    val fallbackTabHeightPx = remember(density) { with(density) { tabHeightDp.toPx() } }
    val edgePaddingPx = remember(density) { with(density) { 8.dp.toPx() } }
    val safeTopPx = WindowInsets.safeDrawing.getTop(density).toFloat()
    val safeBottomPx = WindowInsets.safeDrawing.getBottom(density).toFloat()
    val effectiveTabHeightPx = maxOf(tabHeightPx, fallbackTabHeightPx)
    val floatingHeightPx = if (isExpanded && panelHeightPx > 0f) {
        maxOf(effectiveTabHeightPx, panelHeightPx)
    } else {
        effectiveTabHeightPx
    }
    val minOffsetY = safeTopPx + edgePaddingPx
    val maxOffsetY = (screenHeightPx - safeBottomPx - floatingHeightPx - edgePaddingPx)
        .coerceAtLeast(minOffsetY)
    val defaultOffsetY = (screenHeightPx * 0.24f).coerceIn(minOffsetY, maxOffsetY)

    val targetXPx = if (isRightSide) screenWidthPx - tabWidthPx else 0f

    val animatedTabXPx by animateFloatAsState(
        targetValue = targetXPx,
        animationSpec = spring(stiffness = Spring.StiffnessLow),
        label = "tab_shift"
    )

    LaunchedEffect(minOffsetY, maxOffsetY) {
        offsetY = if (offsetY < 0f) {
            defaultOffsetY
        } else {
            offsetY.coerceIn(minOffsetY, maxOffsetY)
        }
    }

    Box(modifier = modifier.fillMaxSize()) {
        Surface(
            onClick = { isExpanded = !isExpanded },
            modifier = Modifier
                .offset { IntOffset(animatedTabXPx.roundToInt(), offsetY.roundToInt()) }
                .onGloballyPositioned { coordinates ->
                    tabHeightPx = coordinates.size.height.toFloat()
                }
                .pointerInput(minOffsetY, maxOffsetY) {
                    detectDragGestures(
                        onDrag = { change, dragAmount ->
                            change.consume()
                            offsetY = (offsetY + dragAmount.y).coerceIn(minOffsetY, maxOffsetY)
                        }
                    )
                },
            shape = if (isRightSide)
                RoundedCornerShape(topStart = 14.dp, bottomStart = 14.dp)
            else
                RoundedCornerShape(topEnd = 14.dp, bottomEnd = 14.dp),
            color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.9f),
            shadowElevation = 6.dp,
            tonalElevation = 4.dp,
        ) {
            Box(
                modifier = Modifier.size(tabWidthDp, tabHeightDp),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    painter = painterResource(id = R.drawable.ic_palette),
                    contentDescription = stringResource(R.string.theme),
                    modifier = Modifier.size(22.dp),
                    tint = MaterialTheme.colorScheme.onPrimaryContainer
                )
            }
        }

        AnimatedVisibility(
            visible = isExpanded,
            enter = fadeIn(),
            exit = fadeOut(),
            modifier = Modifier.offset {
                val panelWidthPx = with(density) { panelWidthDp.toPx() }
                val gap = with(density) { 8.dp.toPx() }
                val panelX = if (isRightSide) {
                    (targetXPx - panelWidthPx - gap).roundToInt()
                } else {
                    (tabWidthPx + gap).roundToInt()
                }
                IntOffset(panelX, offsetY.roundToInt())
            }
        ) {
            Surface(
                modifier = Modifier.onGloballyPositioned { coordinates ->
                    panelHeightPx = coordinates.size.height.toFloat()
                },
                shape = RoundedCornerShape(32.dp),
                color = MaterialTheme.colorScheme.surface,
                shadowElevation = 8.dp,
                tonalElevation = 4.dp,
            ) {
                Column(
                    modifier = Modifier.padding(12.dp).width(panelWidthDp - 24.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    Text(
                        stringResource(R.string.theme),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(start = 4.dp, bottom = 4.dp)
                    )

                    ThemeOption(
                        icon = R.drawable.ic_auto,
                        label = stringResource(R.string.theme_system),
                        selected = currentTheme == "system",
                        onClick = { onThemeChange("system"); isExpanded = false }
                    )
                    ThemeOption(
                        icon = R.drawable.ic_light_mode,
                        label = stringResource(R.string.theme_light),
                        selected = currentTheme == "light",
                        onClick = { onThemeChange("light"); isExpanded = false }
                    )
                    ThemeOption(
                        icon = R.drawable.ic_dark_mode,
                        label = stringResource(R.string.theme_dark),
                        selected = currentTheme == "dark",
                        onClick = { onThemeChange("dark"); isExpanded = false }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))

                    val supportsDynamicColor = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                    val showDynamicColorOn = isDynamicColor && supportsDynamicColor
                    val showPalettes = !showDynamicColorOn

                    Row(
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            stringResource(R.string.dynamic_colors),
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.Medium,
                            color = if (supportsDynamicColor) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                        )
                        Switch(
                            checked = showDynamicColorOn,
                            onCheckedChange = { onDynamicColorChange(it) },
                            enabled = supportsDynamicColor,
                            modifier = Modifier.scale(0.8f) 
                        )
                    }

                    AnimatedVisibility(visible = showPalettes) {
                        Column {
                            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                            Text(
                                stringResource(R.string.palette),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(bottom = 6.dp, start = 4.dp)
                            )
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceEvenly
                            ) {
                                PaletteCircle("indigo", 0xFF5B588D, currentPalette, onPaletteChange)
                                PaletteCircle("forest", 0xFF5F5D68, currentPalette, onPaletteChange)
                                PaletteCircle("espresso", 0xFF6D4C41, currentPalette, onPaletteChange)
                            }
                            Spacer(modifier = Modifier.height(6.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ThemeOption(
    icon: Int,
    label: String,
    selected: Boolean,
    onClick: () -> Unit
) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(24.dp),
        color = if (selected) MaterialTheme.colorScheme.primaryContainer
        else MaterialTheme.colorScheme.surface,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Icon(
                painter = painterResource(id = icon),
                contentDescription = null,
                modifier = Modifier.size(18.dp),
                tint = if (selected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                color = if (selected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurface,
                fontSize = 13.sp
            )
        }
    }
}
@Composable
fun PaletteCircle(
    paletteId: String,
    colorHex: Long,
    selectedId: String,
    onClick: (String) -> Unit
) {
    val isSelected = paletteId == selectedId
    Box(
        modifier = Modifier
            .size(30.dp)
            .clip(CircleShape)
            .background(Color(colorHex))
            .clickable { onClick(paletteId) }
            .then(
                if (isSelected) Modifier.border(3.dp, MaterialTheme.colorScheme.primary, CircleShape)
                else Modifier
            )
    )
}
