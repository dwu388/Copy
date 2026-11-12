package com.dwu.fomocontroller.strategy

import com.dwu.fomocontroller.model.RecordedNotification
import java.util.Locale
import kotlin.math.*

/** Mirrors learning.notification_features. Only strictly earlier captures inform a BUY. */
class NotificationFeatures(private val ratio: Double = 20.0, private val slippage: Double = 0.0) {
    private data class Event(
        val action: String, val trader: String, val token: String, val mc: Double,
        val amount: Double, val post: Long, val captured: Long
    ) { val time: Long get() = max(post, captured) }
    private data class Outcome(val time: Long, val hit: Boolean, val roi: Double)
    private val tokens = mutableMapOf<String, MutableList<Event>>()
    private val traders = mutableMapOf<String, MutableList<Event>>()
    private val pairs = mutableMapOf<Pair<String, String>, MutableList<Event>>()
    private val open = mutableMapOf<Pair<String, String>, MutableList<Event>>()
    private val outcomes = mutableMapOf<String, MutableList<Outcome>>()
    private val seen = mutableMapOf<List<Any>, Long>()
    private var lastTime = Long.MIN_VALUE

    fun consume(raw: RecordedNotification): Map<String, Double>? {
        val trader = raw.trader?.trim()?.lowercase(Locale.ROOT).orEmpty()
        val token = raw.coin?.trim()?.lowercase(Locale.ROOT).orEmpty()
        val mc = raw.marketCap ?: return null
        val amount = raw.sourceAmount ?: return null
        if (raw.packageName != "family.fomo.app" || raw.action !in setOf("bought", "sold") ||
            trader.isBlank() || token.isBlank() || raw.notificationKey.isBlank() ||
            !mc.isFinite() || mc <= 0 || !amount.isFinite() || amount <= 0 ||
            raw.postTime <= 0 || raw.capturedTime <= 0) return null
        val e = Event(raw.action, trader, token, mc, amount, raw.postTime, raw.capturedTime)
        require(e.time >= lastTime) { "Out-of-order capture: rebuild notification feature history" }
        lastTime = e.time
        val fingerprint = listOf(e.action, trader, token, mc, amount)
        val previous = seen[fingerprint]
        if (previous != null && e.time - previous < 180_000L) return null
        seen[fingerprint] = e.time
        val features = if (e.action == "bought") features(e) else null
        val key = trader to token
        if (e.action == "bought") open.getOrPut(key) { mutableListOf() }.add(e)
        else {
            val remaining = mutableListOf<Event>()
            for (buy in open[key].orEmpty()) {
                if (buy.post >= e.post || buy.time >= e.time) { remaining.add(buy); continue }
                val roi = e.mc / buy.mc - 1
                outcomes.getOrPut(trader) { mutableListOf() }.add(
                    Outcome(e.time, netMultiple(1 + roi, buy.amount / ratio, slippage) >= 2,
                        roi.coerceIn(-1.0, 10.0))
                )
            }
            open[key] = remaining
        }
        tokens.getOrPut(token) { mutableListOf() }.add(e)
        traders.getOrPut(trader) { mutableListOf() }.add(e)
        pairs.getOrPut(key) { mutableListOf() }.add(e)
        return features
    }

    private fun features(e: Event): Map<String, Double> {
        val token = tokens[e.token].orEmpty().filter { it.time < e.time }
        val trader = traders[e.trader].orEmpty().filter { it.time < e.time }
        val pair = pairs[e.trader to e.token].orEmpty().filter { it.time < e.time }
        val resolved = outcomes[e.trader].orEmpty().filter { it.time < e.time }
        val recent = token.filter { it.time >= e.time - 3_600_000 }
        val buys = trader.filter { it.action == "bought" }
        val meanBuy = if (buys.isEmpty()) e.amount else buys.sumOf { it.amount } / buys.size
        val hour = (e.time % 86_400_000) / 3_600_000.0
        val weekday = (e.time / 86_400_000 + 3) % 7
        fun count(rows: List<Event>, action: String) = ln1p(rows.count { it.action == action }.toDouble())
        return NAMES.zip(listOf(
            ln1p(e.mc), ln1p(e.amount), ln1p(e.amount / e.mc),
            ln1p(max(0L, e.captured - e.post) / 1000.0),
            sin(2 * PI * hour / 24), cos(2 * PI * hour / 24),
            sin(2 * PI * weekday / 7), cos(2 * PI * weekday / 7),
            count(token, "bought"), count(token, "sold"), ln1p(token.map { it.trader }.toSet().size.toDouble()),
            token.firstOrNull()?.let { ln1p((e.time - it.time) / 3_600_000.0) } ?: 0.0,
            token.lastOrNull()?.let { e.mc / it.mc - 1 } ?: 0.0,
            recent.firstOrNull()?.let { e.mc / it.mc - 1 } ?: 0.0,
            count(recent, "bought"), count(recent, "sold"),
            recent.sumOf { it.amount * if (it.action == "bought") 1 else -1 } / e.mc,
            count(trader, "bought"), count(trader, "sold"), ln1p(meanBuy), ln(e.amount / meanBuy),
            count(pair, "bought"), count(pair, "sold"), ln1p(resolved.size.toDouble()),
            (resolved.count { it.hit } + 1.0) / (resolved.size + 2.0),
            if (resolved.isEmpty()) 0.0 else resolved.sumOf { it.roi } / resolved.size
        )).toMap()
    }

    companion object {
        val NAMES = listOf(
            "log_market_cap", "log_source_buy_usd", "log_source_to_cap", "log_capture_delay_seconds",
            "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "token_prior_buys_log",
            "token_prior_sells_log", "token_traders_log", "token_age_hours_log", "token_return_previous",
            "token_return_1h", "token_buys_1h_log", "token_sells_1h_log", "token_net_flow_1h_to_cap",
            "trader_prior_buys_log", "trader_prior_sells_log", "trader_mean_buy_usd_log",
            "source_vs_trader_mean_log", "pair_prior_buys_log", "pair_prior_sells_log",
            "trader_resolved_log", "trader_prior_2x_rate", "trader_prior_mean_roi"
        )
        fun fee(usd: Double, discount: Double = 0.0) = max(0.95, 0.005 * usd) * (1 - discount)
        fun netMultiple(grossMultiple: Double, principal: Double, slippage: Double = 0.0,
                        discount: Double = 0.0): Double {
            require(principal.isFinite() && principal > 0)
            val proceeds = max(0.0, principal * grossMultiple * (1 - slippage).pow(2))
            return max(0.0, proceeds - fee(proceeds, discount)) / (principal + fee(principal, discount))
        }
    }
}
