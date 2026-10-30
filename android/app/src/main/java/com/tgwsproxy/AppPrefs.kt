package com.tgwsproxy

import android.content.Context
import androidx.appcompat.app.AppCompatDelegate

object AppPrefs {
    private const val PREFS = "app"

    const val THEME_AUTO = "auto"
    const val THEME_LIGHT = "light"
    const val THEME_DARK = "dark"

    fun theme(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("theme", THEME_AUTO)!!

    fun setTheme(context: Context, value: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("theme", value).apply()
    }

    fun language(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("language", "ru")!!

    fun setLanguage(context: Context, value: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("language", value).apply()
    }

    fun autostart(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("autostart", false)

    fun setAutostart(context: Context, value: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("autostart", value).apply()
    }

    fun applyTheme(context: Context) {
        AppCompatDelegate.setDefaultNightMode(
            when (theme(context)) {
                THEME_LIGHT -> AppCompatDelegate.MODE_NIGHT_NO
                THEME_DARK -> AppCompatDelegate.MODE_NIGHT_YES
                else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
            }
        )
    }
}
