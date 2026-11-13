package com.dwu.fomocontroller.strategy

/** Conservative paper-only valuation; a missing quote is an assumed write-off. */
object PaperExpiry {
    const val MAX_HOLD_MS = 24 * 60 * 60_000L
    const val QUOTE_MAX_AGE_MS = 5 * 60_000L
    fun overdue(opened: Long, now: Long) = now > opened && now - opened > MAX_HOLD_MS
    fun gross(principal: Double, entryCap: Double, quote: Double?, slippage: Double): Double {
        require(principal.isFinite() && principal > 0 && entryCap.isFinite() && entryCap > 0)
        require(slippage in 0.0..0.25)
        if (quote == null || !quote.isFinite() || quote <= 0) return 0.0
        val value = principal * (quote / entryCap) * (1 - slippage)
        require(value.isFinite()) { "Expiry valuation overflow" }
        return value.coerceAtLeast(0.0)
    }
}
