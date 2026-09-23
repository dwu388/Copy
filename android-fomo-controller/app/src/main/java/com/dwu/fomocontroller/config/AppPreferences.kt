package com.dwu.fomocontroller.config

import android.content.Context
import com.dwu.fomocontroller.model.ControllerMode

class AppPreferences(context: Context) {
    private val prefs = context.getSharedPreferences("fomo_controller", Context.MODE_PRIVATE)

    var mode: ControllerMode
        get() = ControllerMode.from(prefs.getString("mode", ControllerMode.OBSERVE.name))
        set(value) = prefs.edit().putString("mode", value.name).apply()

    var maxMarketCap: Double
        get() = prefs.getString("maxMarketCap", "50000")?.toDoubleOrNull() ?: 50000.0
        set(value) = prefs.edit().putString("maxMarketCap", value.toString()).apply()

    var maxSourceAmount: Double
        get() = prefs.getString("maxSourceAmount", "1000")?.toDoubleOrNull() ?: 1000.0
        set(value) = prefs.edit().putString("maxSourceAmount", value.toString()).apply()

    var copyRatio: Double
        get() = prefs.getString("copyRatio", "0.10")?.toDoubleOrNull() ?: 0.10
        set(value) = prefs.edit().putString("copyRatio", value.toString()).apply()

    var maxEventAgeSeconds: Long
        get() = prefs.getLong("maxEventAgeSeconds", 120L)
        set(value) = prefs.edit().putLong("maxEventAgeSeconds", value).apply()
}
