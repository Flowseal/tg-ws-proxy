package org.flowseal.tgwsproxy

import java.io.File
import java.nio.file.Files
import java.nio.file.Paths
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class AndroidAppearanceAndNotificationTest {
    @Test
    fun stalePollCannotPublishAfterLocaleRefresh() {
        val gate = NotificationPublishGate()
        val language = AtomicReference("en")
        val published = AtomicReference("")
        val pollBuilding = CountDownLatch(1)
        val releasePoll = CountDownLatch(1)
        val refreshStarted = CountDownLatch(1)
        val refreshPublished = CountDownLatch(1)
        val poll = Thread {
            gate.publish(
                build = {
                    val captured = language.get()
                    pollBuilding.countDown()
                    check(releasePoll.await(5, TimeUnit.SECONDS))
                    captured
                },
                publish = { published.set(it) },
            )
        }
        poll.start()
        assertTrue(pollBuilding.await(5, TimeUnit.SECONDS))
        language.set("ru")
        val refresh = Thread {
            refreshStarted.countDown()
            gate.publish(build = { language.get() }, publish = {
                published.set(it)
                refreshPublished.countDown()
            })
        }
        refresh.start()
        assertTrue(refreshStarted.await(5, TimeUnit.SECONDS))
        assertFalse(refreshPublished.await(100, TimeUnit.MILLISECONDS))
        releasePoll.countDown()
        poll.join(5000)
        refresh.join(5000)
        assertFalse(poll.isAlive)
        assertFalse(refresh.isAlive)
        assertEquals("ru", published.get())

        // A later poll reads the current locale rather than reusing a stale payload.
        gate.publish(build = { language.get() }, publish = { published.set(it) })
        assertEquals("ru", published.get())
    }

    @Test
    fun localeRefreshUsesDedicatedNotificationOnlyServiceAction() {
        val service = File(findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/ProxyForegroundService.kt",
            "src/main/java/org/flowseal/tgwsproxy/ProxyForegroundService.kt",
        ).toString()).readText()
        val activity = File(findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        ).toString()).readText()

        assertTrue(service.contains("ACTION_REFRESH_LOCALE ->"))
        assertTrue(service.contains("refreshLocaleNotification()"))
        assertTrue(service.contains("AndroidLanguageContext.wrap(this)"))
        assertTrue(service.contains("notificationGate.publish("))
        assertTrue(service.contains("publishNotification(config, trafficState, starting = false)"))
        assertTrue(activity.contains("ProxyForegroundService.refreshLocale(this)"))
    }
    @Test
    fun releaseActionAlwaysOpensUpstreamApkListingEvenForStaleOrFailedStatus() {
        val expected = "https://github.com/Flowseal/tg-ws-proxy/releases/latest"
        for (status in listOf(
            null,
            ProxyUpdateStatus(htmlUrl = expected),
            ProxyUpdateStatus(htmlUrl = ""),
            ProxyUpdateStatus(error = "offline", htmlUrl = "https://github.com/fork/releases/latest"),
            ProxyUpdateStatus(htmlUrl = "https://example.org/windows-installer.exe"),
        )) {
            assertEquals(expected, MainActivity.releasePageUrl(status))
        }
    }
    @Test
    fun settingsLayoutExposesUpstreamControlsWithoutObsoletePriority() {
        val layout = File(findResourcePath(
            "app/src/main/res/layout/activity_main.xml",
            "src/main/res/layout/activity_main.xml",
        ).toString()).readText()
        for (control in listOf(
            "cfProxyCustomDomainSwitch", "cfProxyUserDomainInput",
            "cfProxyWorkerSwitch", "cfProxyWorkerDomainInput",
            "noSecureSwitch", "fakeTlsDomainInput", "languageInput",
        )) {
            assertTrue("Missing $control", layout.contains("@+id/$control"))
        }
        assertFalse(layout.contains("@+id/cfProxyPrioritySwitch"))
    }
    @Test
    fun disabledCustomDomainsDoNotClaimCustomRoute() {
        val config = ProxyConfig(
            cfproxy = true,
            cfproxyUserDomainText = "custom.example",
            cfproxyUserDomainEnabled = false,
        ).validate().normalized!!
        assertEquals(NotificationSummary.FALLBACK_CFPROXY,
            NotificationSummary.formatFallbackSummary(config))
    }
    private fun findResourcePath(vararg candidates: String): java.nio.file.Path {
        return candidates
            .map { Paths.get(it) }
            .firstOrNull { Files.exists(it) }
            ?: error("resource not found from cwd=${System.getProperty("user.dir")}")
    }

    @Test
    fun normalize_appearance_defaults_to_auto() {
        assertEquals("auto", ProxyConfig.normalizeAppearance(""))
        assertEquals("auto", ProxyConfig.normalizeAppearance("weird"))
        assertEquals("light", ProxyConfig.normalizeAppearance("light"))
        assertEquals("dark", ProxyConfig.normalizeAppearance("dark"))
    }

    @Test
    fun fallback_summary_prefers_custom_cfproxy_label() {
        val config = NormalizedProxyConfig(
            host = "127.0.0.1",
            port = 1443,
            secret = "0123456789abcdef0123456789abcdef",
            dcIpList = listOf("2:149.154.167.220"),
            logMaxMb = 5.0,
            bufferKb = 256,
            poolSize = 4,
            cfproxy = true,
            cfproxyUserDomain = "cdn.example.com",
            cfproxyUserDomains = listOf("cdn.example.com"),
            cfproxyUserDomainEnabled = true,
            checkUpdates = true,
            verbose = false,
            appearance = "dark",
        )

        assertEquals(
            NotificationSummary.FALLBACK_CFPROXY_CUSTOM,
            NotificationSummary.formatFallbackSummary(config),
        )
    }

    @Test
    fun notification_details_format_uses_fallback_summary_not_dc_count() {
        val details = ProxyForegroundService.formatNotificationDetailsForTest(
            routeLabel = "Direct Telegram WS",
            fallbackSummary = NotificationSummary.FALLBACK_CFPROXY,
            upRate = "1.0 KB",
            downRate = "2.0 KB",
            totalUp = "3.0 KB",
            totalDown = "4.0 KB",
        )

        assertTrue(details.contains("Fallback: CfProxy"))
        assertFalse(details.contains("relay"))
    }

    @Test
    fun notification_details_resource_uses_fallback_placeholder() {
        val resourcePath = findResourcePath(
            "app/src/main/res/values/strings.xml",
            "src/main/res/values/strings.xml",
            "../app/src/main/res/values/strings.xml",
        )
        val xml = File(resourcePath.toString()).readText()
        val rawValue = Regex(
            """<string name="notification_details">(.*?)</string>""",
            setOf(RegexOption.DOT_MATCHES_ALL),
        ).find(xml)?.groupValues?.get(1)

        assertEquals(
            "Route: %1\$s\\n%2\$s\\nTraffic: ↑ %3\$s/s ↓ %4\$s/s\\nTransferred: ↑ %5\$s ↓ %6\$s",
            rawValue,
        )
    }

    @Test
    fun android_strings_remove_relay_copy() {
        val resourcePath = findResourcePath(
            "app/src/main/res/values/strings.xml",
            "src/main/res/values/strings.xml",
            "../app/src/main/res/values/strings.xml",
        )
        val xml = File(resourcePath.toString()).readText()

        assertFalse(xml.contains("relay_url_hint"))
        assertFalse(xml.contains("relay_token_hint"))
        assertFalse(xml.contains("upstream_mode_auto"))
        assertFalse(xml.contains("upstream_mode_direct"))
        assertFalse(xml.contains("upstream_mode_summary_direct"))
        assertFalse(xml.contains("upstreamStatusValue"))
        assertFalse(xml.contains("relay"))
    }

    @Test
    fun settings_layout_removes_relay_inputs_and_timeout_controls() {
        val resourcePath = findResourcePath(
            "app/src/main/res/layout/activity_main.xml",
            "src/main/res/layout/activity_main.xml",
            "../app/src/main/res/layout/activity_main.xml",
        )
        val xml = File(resourcePath.toString()).readText()

        assertFalse(xml.contains("""android:id="@+id/relayUrlLayout""""))
        assertFalse(xml.contains("""android:id="@+id/relayTokenLayout""""))
        assertFalse(xml.contains("""android:id="@+id/relayUrlInput""""))
        assertFalse(xml.contains("""android:id="@+id/relayTokenInput""""))
        assertFalse(xml.contains("""android:id="@+id/directWsTimeoutLayout""""))
        assertFalse(xml.contains("""android:id="@+id/directWsTimeoutInput""""))
        assertFalse(xml.contains("""android:id="@+id/upstreamModeInput""""))
        assertFalse(xml.contains("""android:id="@+id/upstreamStatusValue""""))
    }

    @Test
    fun release_page_actions_use_upstream_flowseal_releases() {
        val mainActivityPath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val bridgePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/PythonProxyBridge.kt",
            "src/main/java/org/flowseal/tgwsproxy/PythonProxyBridge.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/PythonProxyBridge.kt",
        )
        val mainActivitySource = File(mainActivityPath.toString()).readText()
        val bridgeSource = File(bridgePath.toString()).readText()

        assertTrue(mainActivitySource.contains("https://github.com/Flowseal/tg-ws-proxy/releases/latest"))
        assertTrue(bridgeSource.contains("https://github.com/Flowseal/tg-ws-proxy/releases/latest"))
        assertTrue(mainActivitySource.contains("https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/Funding.md"))
        assertFalse(mainActivitySource.contains("https://github.com/Dark-Avery/tg-ws-proxy/releases/latest"))
        assertFalse(mainActivitySource.contains("https://github.com/Dark-Avery/tg-ws-proxy/blob/main/docs/Funding.md"))
        assertFalse(bridgeSource.contains("https://github.com/Dark-Avery/tg-ws-proxy/releases/latest"))
    }

    @Test
    fun notification_route_mapping_handles_cfproxy_fallback_explicitly() {
        val servicePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/ProxyForegroundService.kt",
            "src/main/java/org/flowseal/tgwsproxy/ProxyForegroundService.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/ProxyForegroundService.kt",
        )
        val stringsPath = findResourcePath(
            "app/src/main/res/values/strings.xml",
            "src/main/res/values/strings.xml",
            "../app/src/main/res/values/strings.xml",
        )
        val serviceSource = File(servicePath.toString()).readText()
        val stringsXml = File(stringsPath.toString()).readText()

        assertTrue(
            serviceSource.contains(
                "\"cfproxy_fallback\" -> localized.getString(R.string.notification_route_cfproxy)",
            ),
        )
        assertTrue(stringsXml.contains("""<string name="notification_route_cfproxy">CfProxy fallback</string>"""))
    }

    @Test
    fun app_theme_uses_daynight_parent() {
        val resourcePath = findResourcePath(
            "app/src/main/res/values/themes.xml",
            "src/main/res/values/themes.xml",
            "../app/src/main/res/values/themes.xml",
        )
        val xml = File(resourcePath.toString()).readText()

        assertTrue(xml.contains("""<style name="Theme.TgWsProxy" parent="Theme.Material3.DayNight.NoActionBar">"""))
    }

    @Test
    fun top_layout_places_appearance_and_donate_before_endpoint_card() {
        val resourcePath = findResourcePath(
            "app/src/main/res/layout/activity_main.xml",
            "src/main/res/layout/activity_main.xml",
            "../app/src/main/res/layout/activity_main.xml",
        )
        val xml = File(resourcePath.toString()).readText()
        val appearanceIndex = xml.indexOf("""android:id="@+id/appearanceInput"""")
        val donateIndex = xml.indexOf("""android:id="@+id/donateButton"""")
        val hostIndex = xml.indexOf("""android:id="@+id/hostInput"""")
        val secretRegenIndex = xml.indexOf("""android:id="@+id/secretRegenerateButton"""")

        assertTrue(appearanceIndex in 0 until hostIndex)
        assertTrue(donateIndex in 0 until hostIndex)
        assertTrue(secretRegenIndex > 0)
    }

    @Test
    fun appearance_logic_uses_global_delegate_without_local_override() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()

        assertFalse(source.contains("delegate.localNightMode"))
        assertTrue(source.contains("AppCompatDelegate.getDefaultNightMode() != nightMode"))
    }

    @Test
    fun appearance_is_applied_from_saved_state_before_ui_inflation_only() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()
        val preCreateApply = source.indexOf("applyAppearance(initialSettingsStore.load().appearance)")
        val superOnCreate = source.indexOf("super.onCreate(savedInstanceState)")
        val renderConfigStart = source.indexOf("private fun renderConfig(config: ProxyConfig) {")
        val collectConfigStart = source.indexOf("private fun collectConfigFromForm(): ProxyConfig {")
        val renderConfigBody = source.substring(renderConfigStart, collectConfigStart)

        assertTrue(preCreateApply in 0 until superOnCreate)
        assertFalse(source.contains("binding.appearanceInput.setOnItemClickListener { _, _, _, _ ->\n            applyAppearance(selectedAppearanceValue())"))
        assertFalse(renderConfigBody.contains("applyAppearance(config.appearance)"))
    }

    @Test
    fun dropdowns_use_non_filtering_adapter_so_current_selection_does_not_collapse_options() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()

        assertTrue(source.contains("private class NonFilteringArrayAdapter("))
        assertTrue(source.contains("override fun getFilter(): Filter = noFilter"))
        assertTrue(source.contains("values = items"))
        assertTrue(source.contains("binding.appearanceInput.setAdapter(adapter)"))
        assertTrue(source.contains("val adapter = NonFilteringArrayAdapter("))
    }

    @Test
    fun startup_side_effects_are_deferred_until_after_first_layout_post() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()
        val postBlockStart = source.indexOf("binding.root.post {")
        val onResumeStart = source.indexOf("override fun onResume() {")
        val postBlock = source.substring(postBlockStart, onResumeStart)

        assertTrue(postBlockStart >= 0)
        assertTrue(postBlock.contains("if (isFinishing || isDestroyed) {"))
        assertTrue(postBlock.contains("refreshUpdateStatus(checkNow = true)"))
        assertTrue(postBlock.contains("renderUpdateStatus(null, false)"))
        assertTrue(postBlock.contains("requestNotificationPermissionIfNeeded()"))
        assertTrue(postBlock.contains("observeServiceState()"))
        assertTrue(postBlock.contains("renderSystemStatus()"))
        assertTrue(postBlock.contains("resumePendingPostRecreateActionIfNeeded()"))
    }

    @Test
    fun save_flow_stops_touching_old_ui_after_theme_switch() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()
        val onSaveStart = source.indexOf(
            "private fun onSaveClicked(\n        showMessage: Boolean,\n        postRecreateAction: PendingPostRecreateAction = PendingPostRecreateAction.NONE,\n    ): NormalizedProxyConfig? {"
        )
        val onStartClicked = source.indexOf("private fun onStartClicked() {")
        val onSaveBody = source.substring(onSaveStart, onStartClicked)

        assertTrue(onSaveBody.contains("if (applyAppearance(config.appearance)) {"))
        assertTrue(onSaveBody.contains("pendingPostRecreateAction = postRecreateAction"))
        assertTrue(onSaveBody.contains("return null"))
        assertTrue(onSaveBody.indexOf("if (applyAppearance(config.appearance)) {") < onSaveBody.indexOf("Snackbar.make(binding.root"))
    }

    @Test
    fun theme_switch_save_preserves_requested_action_for_recreated_activity() {
        val sourcePath = findResourcePath(
            "app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
            "../app/src/main/java/org/flowseal/tgwsproxy/MainActivity.kt",
        )
        val source = File(sourcePath.toString()).readText()

        assertTrue(source.contains("private var pendingPostRecreateAction = PendingPostRecreateAction.NONE"))
        assertTrue(source.contains("resumePendingPostRecreateActionIfNeeded()"))
        assertTrue(source.contains("outState.putString(\n                STATE_PENDING_POST_RECREATE_ACTION,\n                pendingPostRecreateAction.value,"))
        assertTrue(source.contains("PendingPostRecreateAction.START"))
        assertTrue(source.contains("PendingPostRecreateAction.RESTART"))
        assertTrue(source.contains("PendingPostRecreateAction.OPEN_TELEGRAM"))
    }
}
