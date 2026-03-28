package org.flowseal.tgwsproxy

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

data class AndroidSystemStatus(
    val ignoringBatteryOptimizations: Boolean,
    val backgroundRestricted: Boolean,
) {
    val canKeepRunningReliably: Boolean
        get() = ignoringBatteryOptimizations && !backgroundRestricted

    companion object {
        fun read(context: Context): AndroidSystemStatus {
            val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager

            val ignoringBatteryOptimizations = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                powerManager.isIgnoringBatteryOptimizations(context.packageName)
            } else {
                true
            }

            val backgroundRestricted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                activityManager.isBackgroundRestricted
            } else {
                false
            }

            return AndroidSystemStatus(
                ignoringBatteryOptimizations = ignoringBatteryOptimizations,
                backgroundRestricted = backgroundRestricted,
            )
        }

        fun openBatteryOptimizationSettings(context: Context) {
            val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:${context.packageName}")
                }
            } else {
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.fromParts("package", context.packageName, null)
                }
            }

            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }

        fun openAppSettings(context: Context) {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.fromParts("package", context.packageName, null)
            }
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
