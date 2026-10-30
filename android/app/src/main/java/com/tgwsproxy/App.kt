package com.tgwsproxy

import android.app.Application
import chaquo.chaquopy.Python
import com.chaquo.python.android.AndroidPlatform

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
    }
}
