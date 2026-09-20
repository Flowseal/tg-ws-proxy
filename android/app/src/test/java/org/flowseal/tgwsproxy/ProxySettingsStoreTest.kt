package org.flowseal.tgwsproxy

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class ProxySettingsStoreTest {
    @Test
    fun save_and_load_preserve_cfproxy_fields() {
        val context = TestContext()
        val store = ProxySettingsStore(context)

        store.save(
            NormalizedProxyConfig(
                host = "127.0.0.1",
                port = 1443,
                secret = "0123456789abcdef0123456789abcdef",
                dcIpList = listOf("2:149.154.167.220", "4:149.154.167.220"),
                appearance = "dark",
                logMaxMb = 5.0,
                bufferKb = 256,
                poolSize = 4,
                cfproxy = false,
                cfproxyPriority = false,
                cfproxyUserDomain = "cdn.example.com",
                checkUpdates = true,
                verbose = false,
            ),
        )

        val restored = store.load()

        assertFalse(restored.cfproxy)
        assertFalse(restored.cfproxyPriority)
        assertEquals("cdn.example.com", restored.cfproxyUserDomainText)
        assertEquals("dark", restored.appearance)
    }

    @Test
    fun save_clears_legacy_relay_preferences() {
        val context = TestContext()
        val legacyPrefs = context.getSharedPreferences("proxy_settings", Context.MODE_PRIVATE)
        legacyPrefs.edit()
            .putString("upstream_mode", "relay_ws")
            .putString("relay_url", "wss://relay.example.com/connect")
            .putString("relay_token", "relay-token")
            .putFloat("direct_ws_timeout_seconds", 7.5f)
            .apply()

        val store = ProxySettingsStore(context)
        store.save(
            NormalizedProxyConfig(
                host = "127.0.0.1",
                port = 1443,
                secret = "0123456789abcdef0123456789abcdef",
                dcIpList = listOf("2:149.154.167.220", "4:149.154.167.220"),
                appearance = "auto",
                logMaxMb = 5.0,
                bufferKb = 256,
                poolSize = 4,
                cfproxy = true,
                cfproxyPriority = true,
                cfproxyUserDomain = "",
                checkUpdates = false,
                verbose = false,
            ),
        )

        assertFalse(legacyPrefs.contains("upstream_mode"))
        assertFalse(legacyPrefs.contains("relay_url"))
        assertFalse(legacyPrefs.contains("relay_token"))
        assertFalse(legacyPrefs.contains("direct_ws_timeout_seconds"))
        assertNull(legacyPrefs.getString("relay_url", null))
    }

    private class TestContext : ContextWrapper(null) {
        private val sharedPreferences = mutableMapOf<String, SharedPreferences>()

        override fun getSharedPreferences(name: String?, mode: Int): SharedPreferences {
            requireNotNull(name)
            return sharedPreferences.getOrPut(name) { InMemorySharedPreferences() }
        }
    }

    private class InMemorySharedPreferences : SharedPreferences {
        private val values = linkedMapOf<String, Any?>()
        private val listeners = linkedSetOf<SharedPreferences.OnSharedPreferenceChangeListener>()

        override fun getAll(): MutableMap<String, *> = LinkedHashMap(values)

        override fun getString(key: String?, defValue: String?): String? {
            return values[key] as? String ?: defValue
        }

        @Suppress("UNCHECKED_CAST")
        override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? {
            val value = values[key] as? Set<String> ?: return defValues
            return value.toMutableSet()
        }

        override fun getInt(key: String?, defValue: Int): Int {
            return values[key] as? Int ?: defValue
        }

        override fun getLong(key: String?, defValue: Long): Long {
            return values[key] as? Long ?: defValue
        }

        override fun getFloat(key: String?, defValue: Float): Float {
            return values[key] as? Float ?: defValue
        }

        override fun getBoolean(key: String?, defValue: Boolean): Boolean {
            return values[key] as? Boolean ?: defValue
        }

        override fun contains(key: String?): Boolean {
            return values.containsKey(key)
        }

        override fun edit(): SharedPreferences.Editor {
            return EditorImpl()
        }

        override fun registerOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) {
            if (listener != null) {
                listeners += listener
            }
        }

        override fun unregisterOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) {
            if (listener != null) {
                listeners -= listener
            }
        }

        private inner class EditorImpl : SharedPreferences.Editor {
            private val pending = linkedMapOf<String, Any?>()
            private var clearRequested = false

            override fun putString(key: String?, value: String?): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = value
            }

            override fun putStringSet(
                key: String?,
                values: MutableSet<String>?,
            ): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = values?.toSet()
            }

            override fun putInt(key: String?, value: Int): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = value
            }

            override fun putLong(key: String?, value: Long): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = value
            }

            override fun putFloat(key: String?, value: Float): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = value
            }

            override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = value
            }

            override fun remove(key: String?): SharedPreferences.Editor = apply {
                pending[requireNotNull(key)] = null
            }

            override fun clear(): SharedPreferences.Editor = apply {
                clearRequested = true
                pending.clear()
            }

            override fun commit(): Boolean {
                applyChanges()
                return true
            }

            override fun apply() {
                applyChanges()
            }

            private fun applyChanges() {
                if (clearRequested) {
                    values.clear()
                }
                pending.forEach { (key, value) ->
                    if (value == null) {
                        values.remove(key)
                    } else {
                        values[key] = value
                    }
                    listeners.forEach { listener ->
                        listener.onSharedPreferenceChanged(this@InMemorySharedPreferences, key)
                    }
                }
            }
        }
    }
}
