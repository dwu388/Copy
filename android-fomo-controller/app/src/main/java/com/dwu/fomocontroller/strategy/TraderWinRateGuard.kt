package com.dwu.fomocontroller.strategy

import java.util.Locale
import kotlin.math.max

enum class TraderGuardMode { OFF, WARN, FILTER;
    companion object {
        fun from(value: String?) = entries.firstOrNull { it.name == value } ?: WARN
    }
}

data class TraderGuardOutcome(val knownTime: Long, val netReferenceUsd: Double)
data class TraderGuardStats(val samples: Int, val wins: Int) {
    val winRate: Double? get() = if (samples == 0) null else wins.toDouble() / samples
}
data class TraderGuardAssessment(
    val mode: TraderGuardMode,
    val status: String,
    val stats: TraderGuardStats,
    val minimumWinRate: Double
) {
    val allowed: Boolean get() = mode != TraderGuardMode.FILTER || status == "PASS"
    val warning: Boolean get() = mode != TraderGuardMode.OFF && status != "PASS"
    fun summary(): String = buildString {
        append("${mode.name}:$status ${stats.wins}/${stats.samples}")
        stats.winRate?.let { append(" (${"%.1f".format(Locale.US, it * 100)}%)") }
        append("; required=${"%.1f".format(Locale.US, minimumWinRate * 100)}%")
    }
}

/** Historical policy outcomes, not a forecast or actual copied-fill performance. */
object TraderWinRateGuard {
    const val VERSION = "trader_guard_reference20_v1"
    const val WINDOW = 20
    const val MIN_SAMPLES = 20
    const val HOLD_MS = 24 * 60 * 60_000L
    const val QUOTE_AGE_MS = 5 * 60_000L

    fun normalize(value: String) = value.trim().lowercase(Locale.ROOT)

    /** Input is newest first. Strict availability excludes outcomes at/after the buy. */
    fun statistics(newestFirst: List<TraderGuardOutcome>, asOf: Long): TraderGuardStats {
        val known = newestFirst.filter { it.knownTime < asOf }.take(WINDOW)
        require(known.all { it.netReferenceUsd.isFinite() })
        return TraderGuardStats(known.size, known.count { it.netReferenceUsd > 0.0 })
    }

    fun assess(mode: TraderGuardMode, stats: TraderGuardStats, minimumWinRate: Double): TraderGuardAssessment {
        require(minimumWinRate.isFinite() && minimumWinRate in 0.0..1.0)
        require(stats.samples in 0..WINDOW && stats.wins in 0..stats.samples)
        val status = when {
            mode == TraderGuardMode.OFF -> "OFF"
            stats.samples < MIN_SAMPLES -> "INSUFFICIENT"
            stats.winRate!! < minimumWinRate -> "LOW"
            else -> "PASS"
        }
        return TraderGuardAssessment(mode, status, stats, minimumWinRate)
    }

    /** Standard fees at source/20, irrespective of the account's current sizing stage. */
    fun netReferenceUsd(sourceBuyUsd: Double, entryCap: Double, exitCap: Double?): Double {
        require(sourceBuyUsd.isFinite() && sourceBuyUsd > 0 && entryCap.isFinite() && entryCap > 0)
        require(exitCap == null || (exitCap.isFinite() && exitCap > 0))
        val principal = sourceBuyUsd / 20.0
        val gross = if (exitCap == null) 0.0 else principal * (exitCap / entryCap)
        require(gross.isFinite()) { "Trader reference valuation overflow" }
        fun fee(value: Double) = if (value > 0) max(0.95, 0.005 * value) else 0.0
        // Per-lot reference fees are intentionally independent of executed fills.
        return max(0.0, gross - fee(gross)) - principal - fee(principal)
    }
}
