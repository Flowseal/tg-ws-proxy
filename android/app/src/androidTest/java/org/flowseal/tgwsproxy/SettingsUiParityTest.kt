package org.flowseal.tgwsproxy

import android.content.Context
import android.app.NotificationManager
import android.view.View
import android.widget.EditText
import android.widget.TextView
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.MaterialAutoCompleteTextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SettingsUiParityTest {
    @Test
    fun runningNotificationChangesLanguageWithoutProxyRestart() {
        prepare()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val manager = context.getSystemService(NotificationManager::class.java)
        val config = ProxySettingsStore(context).load().validate().normalized!!
        ProxyServiceState.markStarted(config)
        try {
            for ((language, expected) in listOf(
                "en" to "Proxy active",
                "ru" to "Прокси работает",
                "en" to "Proxy active",
            )) {
                ProxySettingsStore(context).save(ProxyConfig(language = language).validate().normalized!!)
                ProxyForegroundService.refreshLocale(context)
                val deadline = System.currentTimeMillis() + 5000
                var text = ""
                while (System.currentTimeMillis() < deadline) {
                    text = manager.activeNotifications.firstOrNull { it.id == 1001 }
                        ?.notification?.extras?.getCharSequence("android.text")?.toString().orEmpty()
                    if (text.contains(expected)) break
                    Thread.sleep(50)
                }
                assertTrue("Wrong notification locale for $language: $text", text.contains(expected))
                assertTrue(ProxyServiceState.isRunning.value)
            }
        } finally {
            ProxyServiceState.markStopped()
            context.stopService(android.content.Intent(context, ProxyForegroundService::class.java))
            manager.cancel(1001)
        }
    }

    private fun prepare() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        instrumentation.uiAutomation.executeShellCommand(
            "pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS",
        ).close()
        context.getSharedPreferences("proxy_settings", Context.MODE_PRIVATE).edit().clear().commit()
    }

    private fun id(activity: MainActivity, name: String): Int {
        val id = activity.resources.getIdentifier(name, "id", activity.packageName)
        assertTrue("Missing view: $name", id != 0)
        return id
    }

    private fun <T : View> view(activity: MainActivity, name: String): T =
        activity.findViewById(id(activity, name))

    @Test
    fun controlsPersistListsAndFlagsWithoutErasingDisabledDomains() {
        prepare()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertEquals(0, activity.resources.getIdentifier("cfProxyPrioritySwitch", "id", activity.packageName))
                val releaseLabel = view<TextView>(activity, "openReleasePageButton").text.toString()
                assertTrue(releaseLabel.contains("Flowseal"))
                assertTrue(releaseLabel.contains("APK"))
                view<EditText>(activity, "cfProxyUserDomainInput").setText("one.example\ntwo.example")
                view<MaterialSwitch>(activity, "cfProxyCustomDomainSwitch").isChecked = false
                view<EditText>(activity, "cfProxyWorkerDomainInput").setText("worker.example")
                view<MaterialSwitch>(activity, "cfProxyWorkerSwitch").isChecked = false
                view<MaterialSwitch>(activity, "noSecureSwitch").isChecked = true
                view<EditText>(activity, "fakeTlsDomainInput").setText("tls.example")
                view<View>(activity, "saveButton").performClick()
            }
            scenario.recreate()
            scenario.onActivity { activity ->
                val saved = ProxySettingsStore(activity).load()
                assertEquals("one.example\ntwo.example", saved.cfproxyUserDomainText)
                assertFalse(saved.cfproxyUserDomainEnabled)
                assertEquals("worker.example", saved.cfproxyWorkerDomainText)
                assertFalse(saved.cfproxyWorkerEnabled)
                assertTrue(saved.noSecure)
                assertEquals("tls.example", saved.fakeTlsDomain)
                assertEquals("one.example\ntwo.example", view<EditText>(activity, "cfProxyUserDomainInput").text.toString())
                assertEquals("worker.example", view<EditText>(activity, "cfProxyWorkerDomainInput").text.toString())
                view<MaterialSwitch>(activity, "cfProxyCustomDomainSwitch").isChecked = true
                view<MaterialSwitch>(activity, "cfProxyWorkerSwitch").isChecked = true
                view<View>(activity, "saveButton").performClick()
            }
            scenario.recreate()
            scenario.onActivity { activity ->
                val saved = ProxySettingsStore(activity).load().validate().normalized!!
                assertTrue(saved.cfproxyUserDomainEnabled)
                assertTrue(saved.cfproxyWorkerEnabled)
                assertEquals(listOf("one.example", "two.example"), saved.cfproxyUserDomains)
                assertEquals(listOf("worker.example"), saved.cfproxyWorkerDomains)
            }
        }
    }

    @Test
    fun themeChangesRepeatedlyKeepCompleteDropdownAfterRecreation() {
        prepare()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            for (mode in listOf("light", "dark", "auto", "light", "dark", "auto")) {
                scenario.onActivity { activity ->
                    val label = when (mode) {
                        "light" -> activity.getString(R.string.appearance_light)
                        "dark" -> activity.getString(R.string.appearance_dark)
                        else -> activity.getString(R.string.appearance_auto)
                    }
                    view<MaterialAutoCompleteTextView>(activity, "appearanceInput").setText(label, false)
                    view<View>(activity, "saveButton").performClick()
                }
                InstrumentationRegistry.getInstrumentation().waitForIdleSync()
                scenario.recreate()
                scenario.onActivity { activity ->
                    assertEquals(mode, ProxySettingsStore(activity).load().appearance)
                    val appearance = view<MaterialAutoCompleteTextView>(activity, "appearanceInput").adapter
                    val language = view<MaterialAutoCompleteTextView>(activity, "languageInput").adapter
                    assertEquals(3, appearance.count)
                    assertEquals(setOf(
                        activity.getString(R.string.appearance_auto),
                        activity.getString(R.string.appearance_light),
                        activity.getString(R.string.appearance_dark),
                    ), (0 until appearance.count).map { appearance.getItem(it).toString() }.toSet())
                    assertEquals(2, language.count)
                    assertEquals(setOf("Русский", "English"),
                        (0 until language.count).map { language.getItem(it).toString() }.toSet())
                    assertTrue(view<View>(activity, "cfProxyWorkerSwitch").isShown)
                }
            }
        }
    }

    @Test
    fun languageSwitchPersistsAndRelabelsControls() {
        prepare()
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            for (language in listOf("en", "ru", "en")) {
                scenario.onActivity { activity ->
                    val resource = activity.resources.getIdentifier(
                        if (language == "ru") "language_russian" else "language_english",
                        "string", activity.packageName,
                    )
                    assertTrue(resource != 0)
                    val label = activity.getString(resource)
                    view<MaterialAutoCompleteTextView>(activity, "languageInput").setText(label, false)
                    view<View>(activity, "saveButton").performClick()
                }
                InstrumentationRegistry.getInstrumentation().waitForIdleSync()
                scenario.recreate()
                scenario.onActivity { activity ->
                    assertEquals(language, ProxySettingsStore(activity).load().language)
                    val expected = if (language == "ru") "Сохранить настройки" else "Save Settings"
                    assertEquals(expected, activity.getString(R.string.save_button))
                    assertEquals(3, view<MaterialAutoCompleteTextView>(activity, "appearanceInput").adapter.count)
                }
            }
        }
    }

    @Test
    fun logViewerUsesPersistedRussianLanguage() {
        prepare()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        ProxySettingsStore(context).save(ProxyConfig(language = "ru").validate().normalized!!)
        ActivityScenario.launch(LogViewerActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertEquals("Закрыть", activity.getString(R.string.close_logs_button))
            }
        }
    }
}
