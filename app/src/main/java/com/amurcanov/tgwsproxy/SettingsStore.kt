package com.amurcanov.tgwsproxy

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "proxy_settings")

class SettingsStore(private val context: Context) {

    private object Keys {
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val IS_DC_AUTO = booleanPreferencesKey("is_dc_auto")
        val DC2 = stringPreferencesKey("dc2")
        val DC4 = stringPreferencesKey("dc4")
        val PORT = stringPreferencesKey("port")
        val POOL_SIZE = intPreferencesKey("pool_size")
        val CFPROXY_ENABLED = booleanPreferencesKey("cfproxy_enabled")
        val SECRET_KEY = stringPreferencesKey("secret_key")
    }

    val themeMode: Flow<String> = context.dataStore.data.map { it[Keys.THEME_MODE] ?: "system" }
    val isDcAuto: Flow<Boolean> = context.dataStore.data.map { it[Keys.IS_DC_AUTO] ?: true }
    val dc2: Flow<String> = context.dataStore.data.map { it[Keys.DC2] ?: "" }
    val dc4: Flow<String> = context.dataStore.data.map { it[Keys.DC4] ?: "149.154.167.220" }
    val port: Flow<String> = context.dataStore.data.map { it[Keys.PORT] ?: "1443" }
    val poolSize: Flow<Int> = context.dataStore.data.map { it[Keys.POOL_SIZE] ?: 4 }
    val cfproxyEnabled: Flow<Boolean> = context.dataStore.data.map { it[Keys.CFPROXY_ENABLED] ?: true }
    val secretKey: Flow<String> = context.dataStore.data.map { it[Keys.SECRET_KEY] ?: "" }

    suspend fun saveSecretKey(key: String) {
        context.dataStore.edit { it[Keys.SECRET_KEY] = key }
    }

    suspend fun saveThemeMode(mode: String) {
        context.dataStore.edit { it[Keys.THEME_MODE] = mode }
    }

    suspend fun saveAll(isDcAuto: Boolean, dc2: String, dc4: String, port: String, poolSize: Int,
                        cfproxyEnabled: Boolean, secretKey: String) {
        context.dataStore.edit {
            it[Keys.IS_DC_AUTO] = isDcAuto
            it[Keys.DC2] = dc2
            it[Keys.DC4] = dc4
            it[Keys.PORT] = port
            it[Keys.POOL_SIZE] = poolSize
            it[Keys.CFPROXY_ENABLED] = cfproxyEnabled
            it[Keys.SECRET_KEY] = secretKey
        }
    }
}
