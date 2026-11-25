package com.dwu.fomocontroller.config

import android.content.Context
import com.dwu.fomocontroller.model.ControllerMode
import com.dwu.fomocontroller.strategy.TraderGuardMode

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

    /** 0.0 for standard fees or 0.10 for the validated 10% code schedule. */
    var feeDiscount: Double
        get() = prefs.getString("feeDiscount", "0.0")?.toDoubleOrNull()
            ?.takeIf { it == 0.0 || it == 0.10 } ?: 0.0
        set(value) {
            require(value == 0.0 || value == 0.10)
            prefs.edit().putString("feeDiscount", value.toString()).apply()
        }

    var traderGuardMode: TraderGuardMode
        get() = TraderGuardMode.from(prefs.getString("traderGuardMode", "WARN"))
        set(value) = prefs.edit().putString("traderGuardMode", value.name).apply()

    var traderMinimumWinRate: Double
        get() = prefs.getString("traderMinimumWinRate", "0.50")?.toDoubleOrNull()
            ?.takeIf { it.isFinite() && it in 0.0..1.0 } ?: 0.50
        set(value) {
            require(value.isFinite() && value in 0.0..1.0)
            prefs.edit().putString("traderMinimumWinRate", value.toString()).apply()
        }

    var traderHistoryInvalid: Boolean
        get() = prefs.getBoolean("traderHistoryInvalid", false)
        set(value) = prefs.edit().putBoolean("traderHistoryInvalid", value).apply()
}
