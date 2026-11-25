package com.dwu.fomocontroller.strategy

import kotlin.math.max

enum class EntryStyle { MODEL, RULE_ONLY }
enum class PaperForecast { CORRECTED, LEGACY }

data class CircuitState(val peak: Double, val equity: Double, val paused: Boolean) {
    val drawdown: Double get() = if (peak > 0) max(0.0, 1 - equity / peak) else 0.0
}

object EntrySafety {
    fun entryReason(cap: Double, sourceUsd: Double, maxCap: Double, minSourceUsd: Double): String? {
        require(maxCap.isFinite() && maxCap > 0 && minSourceUsd.isFinite() && minSourceUsd >= 0)
        return when {
            !cap.isFinite() || cap <= 0 || !sourceUsd.isFinite() || sourceUsd <= 0 -> "INVALID_ENTRY"
            cap >= maxCap -> "ENTRY_MARKET_CAP"
            sourceUsd < minSourceUsd -> "ENTRY_SOURCE_SIZE"
            else -> null
        }
    }

    fun circuit(previousPeak: Double, equity: Double, paused: Boolean, limit: Double): CircuitState {
        require(equity.isFinite() && equity >= 0 && previousPeak.isFinite() && previousPeak >= 0)
        require(limit.isFinite() && limit > 0 && limit < 1)
        val peak = max(previousPeak, equity)
        return CircuitState(peak, equity, paused || (peak > 0 && 1 - equity / peak >= limit - 1e-12))
    }
}
