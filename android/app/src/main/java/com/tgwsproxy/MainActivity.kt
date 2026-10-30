package com.tgwsproxy

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat

class MainActivity : ComponentActivity() {

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { startProxy() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val secret = ProxyService.secret(this)
        val link = "tg://proxy?server=127.0.0.1&port=${ProxyService.PORT}&secret=$secret"

        val status = TextView(this).apply { text = "Прокси остановлен" }
        val linkView = TextView(this).apply { text = link }

        val startButton = Button(this).apply {
            text = "Запустить"
            setOnClickListener { requestNotificationAndStart() }
        }
        val stopButton = Button(this).apply {
            text = "Остановить"
            setOnClickListener {
                stopService(Intent(this@MainActivity, ProxyService::class.java))
                status.text = "Прокси остановлен"
            }
        }
        val openButton = Button(this).apply {
            text = "Открыть в Telegram"
            setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link))) }
        }

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
            addView(status)
            addView(linkView)
            addView(startButton)
            addView(stopButton)
            addView(openButton)
        })

        status.text = if (ProxyService.running) "Прокси работает" else "Прокси остановлен"
    }

    private fun requestNotificationAndStart() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startProxy()
        }
    }

    private fun startProxy() {
        ContextCompat.startForegroundService(this, Intent(this, ProxyService::class.java))
    }
}
