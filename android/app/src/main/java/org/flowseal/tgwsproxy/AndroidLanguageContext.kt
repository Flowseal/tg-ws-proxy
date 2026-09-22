package org.flowseal.tgwsproxy

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

object AndroidLanguageContext {
    fun wrap(base: Context): Context {
        val language = ProxySettingsStore(base).load().language
        val configuration = Configuration(base.resources.configuration)
        configuration.setLocale(Locale.forLanguageTag(language))
        return base.createConfigurationContext(configuration)
    }
}
