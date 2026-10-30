package com.tgwsproxy

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val io = Executors.newSingleThreadExecutor()
    private val ui = android.os.Handler(android.os.Looper.getMainLooper())

    private lateinit var root: LinearLayout
    private lateinit var statusView: TextView
    private lateinit var toggleButton: Button
    private lateinit var linkView: TextView
    private lateinit var hostEdit: EditText
    private lateinit var portEdit: EditText
    private lateinit var secretEdit: EditText
    private lateinit var dcEdit: EditText
    private lateinit var bufEdit: EditText
    private lateinit var poolEdit: EditText
    private lateinit var cfproxyCheck: CheckBox
    private lateinit var h2Check: CheckBox
    private lateinit var userDomainCheck: CheckBox
    private lateinit var userDomainEdit: EditText
    private lateinit var workerCheck: CheckBox
    private lateinit var workerDomainEdit: EditText
    private lateinit var noSecureCheck: CheckBox
    private lateinit var verboseCheck: CheckBox
    private lateinit var autostartCheck: CheckBox

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { startProxy() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppPrefs.applyTheme(this)
        S.init(this)
        showMain()
    }

    override fun onResume() {
        super.onResume()
        if (::statusView.isInitialized) refresh()
    }

    private fun showMain() {
        val cfg = ProxyConfig.load(this)

        statusView = text("", 16f, bold = true, center = true)
        toggleButton = Button(this).apply { setOnClickListener { onToggle() } }
        linkView = text("", 13f, center = true).apply { setTextIsSelectable(true) }

        hostEdit = edit(cfg.host)
        portEdit = edit(cfg.port.toString(), numeric = true)
        secretEdit = edit(cfg.secret)
        dcEdit = edit(cfg.dcIp.joinToString("\n"), multiline = true)
        bufEdit = edit(cfg.bufKb.toString(), numeric = true)
        poolEdit = edit(cfg.poolSize.toString(), numeric = true)
        cfproxyCheck = check(S.t("cfproxy"), cfg.cfproxy)
        h2Check = check(S.t("h2"), cfg.h2)
        userDomainCheck = check(S.t("user_domain"), cfg.cfproxyUserDomainEnabled)
        userDomainEdit = edit(cfg.cfproxyUserDomain)
        workerCheck = check(S.t("worker"), cfg.cfproxyWorkerEnabled)
        workerDomainEdit = edit(cfg.cfproxyWorkerDomain)
        noSecureCheck = check(S.t("no_secure"), cfg.noSecure)
        verboseCheck = check(S.t("verbose"), cfg.verbose)
        autostartCheck = check(S.t("autostart"), AppPrefs.autostart(this)).apply {
            setOnCheckedChangeListener { _, checked -> AppPrefs.setAutostart(this@MainActivity, checked) }
        }

        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(20), dp(32), dp(20), dp(32))
            addView(text(S.t("app"), 26f, bold = true, center = true), matchWidth())
            addView(statusView, matchWidth(top = 8))
            addView(toggleButton, wrapWidth(top = 16))
            addView(linkView, matchWidth(top = 20))
            addView(button(S.t("copy_link")) { copyLink() }, matchWidth(top = 8))
            addView(button(S.t("open_telegram")) { openInTelegram() }, matchWidth(top = 8))
            addView(button(S.t("tests")) { showTests() }, matchWidth(top = 8))
            addView(button(S.t("logs")) { showLogs() }, matchWidth(top = 8))
            addView(button(S.t("updates")) { showUpdates() }, matchWidth(top = 8))

            addView(text(S.t("settings"), 18f, bold = true, center = false), matchWidth(top = 32))
            addView(text(S.t("appearance"), 13f), matchWidth(top = 12))
            addView(themeGroup(), matchWidth(top = 4))
            addView(text(S.t("language"), 13f), matchWidth(top = 12))
            addView(languageGroup(), matchWidth(top = 4))
            addView(autostartCheck, matchWidth(top = 8))

            addField(this, S.t("host"), hostEdit)
            addField(this, S.t("port"), portEdit)
            addField(this, S.t("secret"), secretEdit)
            addView(button(S.t("gen_secret")) { secretEdit.setText(ProxyConfig.newSecret()) }, matchWidth(top = 4))
            addField(this, S.t("dc_ip"), dcEdit)
            addField(this, S.t("buf_kb"), bufEdit)
            addField(this, S.t("pool"), poolEdit)
            addView(cfproxyCheck, matchWidth(top = 12))
            addView(h2Check, matchWidth())
            addView(userDomainCheck, matchWidth(top = 8))
            addView(userDomainEdit, matchWidth(top = 4))
            addView(workerCheck, matchWidth(top = 8))
            addView(workerDomainEdit, matchWidth(top = 4))
            addView(noSecureCheck, matchWidth(top = 12))
            addView(verboseCheck, matchWidth())

            addView(button(S.t("save")) { onSave() }, matchWidth(top = 24))
        }
        root = column
        setScreen(column)
    }

    private fun setScreen(content: LinearLayout) {
        val maxWidth = minOf(dp(520), resources.displayMetrics.widthPixels)
        val wrapper = FrameLayout(this).apply {
            addView(
                content,
                FrameLayout.LayoutParams(maxWidth, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL),
            )
        }
        setContentView(ScrollView(this).apply {
            addView(wrapper, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        })
    }

    private fun themeGroup(): RadioGroup {
        val group = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        val options = listOf(
            AppPrefs.THEME_AUTO to S.t("theme_auto"),
            AppPrefs.THEME_LIGHT to S.t("theme_light"),
            AppPrefs.THEME_DARK to S.t("theme_dark"),
        )
        options.forEach { (value, label) ->
            group.addView(RadioButton(this).apply {
                text = label
                id = View.generateViewId()
                isChecked = AppPrefs.theme(this@MainActivity) == value
                setOnClickListener {
                    AppPrefs.setTheme(this@MainActivity, value)
                    AppPrefs.applyTheme(this@MainActivity)
                }
            })
        }
        return group
    }

    private fun languageGroup(): RadioGroup {
        val group = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        listOf("ru" to "Русский", "en" to "English").forEach { (value, label) ->
            group.addView(RadioButton(this).apply {
                text = label
                id = View.generateViewId()
                isChecked = AppPrefs.language(this@MainActivity) == value
                setOnClickListener {
                    AppPrefs.setLanguage(this@MainActivity, value)
                    S.init(this@MainActivity)
                    showMain()
                }
            })
        }
        return group
    }

    private fun refresh() {
        val cfg = ProxyConfig.load(this)
        val running = ProxyService.running
        statusView.text = when {
            ProxyService.lastError.isNotEmpty() -> S.t("status_error", ProxyService.lastError)
            running -> S.t("status_running", "${cfg.host}:${cfg.port}")
            else -> S.t("status_stopped")
        }
        toggleButton.text = if (running) S.t("stop") else S.t("start")
        linkView.text = cfg.telegramLink()
    }

    private fun onToggle() {
        if (ProxyService.running) {
            stopService(Intent(this, ProxyService::class.java))
            ProxyService.running = false
            refresh()
            return
        }
        val cfg = readForm() ?: return
        cfg.save(this)
        requestNotificationAndStart()
    }

    private fun onSave() {
        val cfg = readForm() ?: return
        cfg.save(this)
        if (ProxyService.running) startProxy()
        Toast.makeText(this, S.t("saved"), Toast.LENGTH_SHORT).show()
        refresh()
    }

    private fun readForm(): ProxyConfig? {
        val port = portEdit.text.toString().toIntOrNull()
        val buf = bufEdit.text.toString().toIntOrNull()
        val pool = poolEdit.text.toString().toIntOrNull()
        val secret = secretEdit.text.toString().trim().lowercase()
        val dc = dcEdit.text.toString().lines().map { it.trim() }.filter { it.isNotEmpty() }
        val error = when {
            port == null || port !in 1..65535 -> S.t("err_port")
            !Regex("[0-9a-f]{32}").matches(secret) -> S.t("err_secret")
            buf == null || buf < 4 -> S.t("err_buf")
            pool == null || pool < 0 -> S.t("err_pool")
            dc.isEmpty() || dc.any { !it.contains(':') } -> S.t("err_dc")
            else -> null
        }
        if (error != null) {
            Toast.makeText(this, error, Toast.LENGTH_LONG).show()
            return null
        }
        return ProxyConfig(
            host = hostEdit.text.toString().trim().ifEmpty { "127.0.0.1" },
            port = port!!,
            secret = secret,
            dcIp = dc,
            bufKb = buf!!,
            poolSize = pool!!,
            cfproxy = cfproxyCheck.isChecked,
            h2 = h2Check.isChecked,
            cfproxyUserDomainEnabled = userDomainCheck.isChecked,
            cfproxyUserDomain = userDomainEdit.text.toString(),
            cfproxyWorkerEnabled = workerCheck.isChecked,
            cfproxyWorkerDomain = workerDomainEdit.text.toString(),
            noSecure = noSecureCheck.isChecked,
            verbose = verboseCheck.isChecked,
        )
    }

    private fun copyLink() {
        getSystemService(ClipboardManager::class.java)
            .setPrimaryClip(ClipData.newPlainText("tg proxy", ProxyConfig.load(this).telegramLink()))
        Toast.makeText(this, S.t("link_copied"), Toast.LENGTH_SHORT).show()
    }

    private fun openInTelegram() {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(ProxyConfig.load(this).telegramLink())))
    }

    private fun requestNotificationAndStart() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startProxy()
        }
    }

    private fun startProxy() {
        ContextCompat.startForegroundService(this, Intent(this, ProxyService::class.java))
        statusView.postDelayed({ refresh() }, 800)
    }

    private fun showTests() {
        val column = screenColumn()
        val out = text("", 13f)
        column.addView(text(S.t("tests"), 22f, bold = true, center = true), matchWidth())
        column.addView(text(S.t("test_hint"), 12f, center = true), matchWidth(top = 8))
        column.addView(out, matchWidth(top = 16))
        column.addView(button(S.t("test_auto")) { runTest(ConnectivityTest.CFPROXY_AUTO, out) }, matchWidth(top = 12))
        column.addView(button(S.t("test_multi")) { runTest(ConnectivityTest.CFPROXY_MULTI, out) }, matchWidth(top = 8))
        column.addView(button(S.t("test_worker")) { runWorkerTest(out) }, matchWidth(top = 8))
        column.addView(button(S.t("back")) { showMain() }, matchWidth(top = 24))
        setScreen(column)
    }

    private fun runWorkerTest(out: TextView) {
        val domain = ProxyConfig.load(this).cfproxyWorkerDomain.trim()
        if (domain.isEmpty()) {
            Toast.makeText(this, S.t("no_worker_domain"), Toast.LENGTH_LONG).show()
            return
        }
        runTest(ConnectivityTest.CFWORKER_MULTI, out, listOf(domain))
    }

    private fun runTest(kind: String, out: TextView, domains: List<String> = emptyList()) {
        out.text = S.t("testing")
        io.execute {
            val text = try {
                val res = ConnectivityTest.run(kind, domains)
                buildString {
                    if (res.best.isNotEmpty()) append(res.best).append("\n\n")
                    res.results.forEach { (domain, dcs) ->
                        append(domain).append('\n')
                        dcs.forEach { (dc, v) -> append("  DC$dc: ").append(if (v == true) "ok" else v.toString()).append('\n') }
                        append('\n')
                    }
                }
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
            ui.post { out.text = text }
        }
    }

    private fun showLogs() {
        val column = screenColumn()
        val out = text("", 11f).apply { setTextIsSelectable(true) }
        val refreshAction = {
            io.execute {
                val content = LogFiles.tail(this).ifEmpty { S.t("logs_empty") }
                ui.post { out.text = content }
            }
        }
        column.addView(text(S.t("logs"), 22f, bold = true, center = true), matchWidth())
        column.addView(out, matchWidth(top = 16))
        column.addView(button(S.t("log_refresh")) { refreshAction() }, matchWidth(top = 12))
        column.addView(button(S.t("log_export")) { exportLog() }, matchWidth(top = 8))
        column.addView(button(S.t("back")) { showMain() }, matchWidth(top = 24))
        setScreen(column)
        refreshAction()
    }

    private fun exportLog() {
        if (!LogFiles.file(this).exists()) {
            Toast.makeText(this, S.t("logs_empty"), Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(Intent.createChooser(LogFiles.exportIntent(this), S.t("log_export")))
    }

    private fun showUpdates() {
        val column = screenColumn()
        val status = text(S.t("testing"), 14f, center = true)
        column.addView(text(S.t("updates"), 22f, bold = true, center = true), matchWidth())
        column.addView(status, matchWidth(top = 16))
        val action = button(S.t("check_updates")) {}
        column.addView(action, matchWidth(top = 12))
        column.addView(button(S.t("back")) { showMain() }, matchWidth(top = 24))
        setScreen(column)

        val runCheck = {
            status.text = S.t("testing")
            io.execute {
                val current = Updater.currentVersion(this)
                try {
                    val latest = Updater.fetchLatest()
                    ui.post { applyUpdateResult(current, latest, status, action) }
                } catch (e: Exception) {
                    ui.post { status.text = S.t("update_failed", e.message ?: e.toString()) }
                }
            }
        }
        action.setOnClickListener { runCheck() }
    }

    private fun applyUpdateResult(current: String, latest: Updater.Release, status: TextView, action: Button) {
        if (Updater.isNewer(latest.version, current)) {
            status.text = S.t("update_available", latest.version)
            action.text = S.t("download_install")
            action.setOnClickListener { downloadAndInstall(latest, status) }
        } else {
            status.text = S.t("up_to_date", current)
            action.text = S.t("check_updates")
            action.setOnClickListener { showUpdates() }
        }
    }

    private fun downloadAndInstall(release: Updater.Release, status: TextView) {
        if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            Toast.makeText(this, S.t("unknown_source"), Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            return
        }
        status.text = S.t("testing")
        io.execute {
            try {
                val apk = Updater.download(this, release)
                ui.post { startActivity(Updater.installIntent(this, apk)) }
            } catch (e: Exception) {
                ui.post { status.text = S.t("update_failed", e.message ?: e.toString()) }
            }
        }
    }

    private fun screenColumn(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setPadding(dp(20), dp(32), dp(20), dp(32))
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun text(value: String, size: Float, bold: Boolean = false, center: Boolean = false): TextView =
        TextView(this).apply {
            text = value
            textSize = size
            if (bold) setTypeface(null, android.graphics.Typeface.BOLD)
            if (center) gravity = Gravity.CENTER_HORIZONTAL
        }

    private fun button(label: String, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            setOnClickListener { onClick() }
        }

    private fun edit(value: String, multiline: Boolean = false, numeric: Boolean = false): EditText =
        EditText(this).apply {
            setText(value)
            inputType = when {
                multiline -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
                numeric -> InputType.TYPE_CLASS_NUMBER
                else -> InputType.TYPE_CLASS_TEXT
            }
            minLines = if (multiline) 3 else 1
        }

    private fun check(label: String, checked: Boolean): CheckBox =
        CheckBox(this).apply {
            text = label
            isChecked = checked
        }

    private fun addField(parent: LinearLayout, title: String, field: EditText) {
        parent.addView(text(title, 13f), matchWidth(top = 12))
        parent.addView(field, matchWidth(top = 4))
    }

    private fun matchWidth(top: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(top) }

    private fun wrapWidth(top: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(top); gravity = Gravity.CENTER_HORIZONTAL }
}
