package com.dwu.fomocontroller.strategy

import org.json.JSONObject
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

data class ModeConfig(
    val confidenceThreshold: Double,
    val ratioFloor: Double,
    val reserveFloorUsd: Double,
    val reserveFraction: Double,
    val maxOpenExposure: Double,
    val maxTraderExposure: Double,
    val maxTokenExposure: Double,
    val max15mNewExposure: Double,
    val feeBurdenTrigger: Double,
    val feeMarginMultiple: Double,
    val uncertaintyMultiplier: Double
) {
    companion object {
        fun fromJson(j: JSONObject) = ModeConfig(
            j.getDouble("confidence_threshold"), j.getDouble("ratio_floor"),
            j.getDouble("reserve_floor_usd"), j.getDouble("reserve_fraction"),
            j.getDouble("max_open_exposure"), j.getDouble("max_trader_exposure"),
            j.getDouble("max_token_exposure"), j.getDouble("max_15m_new_exposure"),
            j.getDouble("fee_burden_trigger"), j.getDouble("fee_margin_multiple"),
            j.getDouble("uncertainty_multiplier")
        )
    }
}

data class RegimeRules(
    val cautionDrawdown: Double,
    val defensiveDrawdown: Double,
    val cautionShadowN: Int,
    val defensiveShadowN: Int,
    val cautionShadowMean: Double,
    val defensiveShadowMean: Double,
    val cautionShadowWinRate: Double,
    val defensiveShadowWinRate: Double,
    val cautionLosingStreak: Int,
    val defensiveLosingStreak: Int,
    val cautionCashFraction: Double,
    val defensiveCashFraction: Double,
    val cautionExposure: Double,
    val defensiveExposure: Double,
    val recoveryExits: Int,
    val defensiveRecoveryDrawdown: Double,
    val cautionRecoveryDrawdown: Double,
    val defensiveRecoveryMean: Double,
    val cautionRecoveryMean: Double,
    val defensiveRecoveryWinRate: Double,
    val cautionRecoveryWinRate: Double
) {
    companion object {
        fun fromJson(j: JSONObject) = RegimeRules(
            j.getDouble("caution_drawdown"), j.getDouble("defensive_drawdown"),
            j.getInt("caution_shadow_n"), j.getInt("defensive_shadow_n"),
            j.getDouble("caution_shadow_mean"), j.getDouble("defensive_shadow_mean"),
            j.getDouble("caution_shadow_win_rate"), j.getDouble("defensive_shadow_win_rate"),
            j.getInt("caution_losing_streak"), j.getInt("defensive_losing_streak"),
            j.getDouble("caution_cash_fraction"), j.getDouble("defensive_cash_fraction"),
            j.getDouble("caution_exposure"), j.getDouble("defensive_exposure"),
            j.getInt("recovery_exits"), j.getDouble("defensive_recovery_drawdown"),
            j.getDouble("caution_recovery_drawdown"), j.getDouble("defensive_recovery_mean"),
            j.getDouble("caution_recovery_mean"), j.getDouble("defensive_recovery_win_rate"),
            j.getDouble("caution_recovery_win_rate")
        )
    }
}

data class StrategyConfig(
    val name: String,
    val modes: Map<String, ModeConfig>,
    val regimes: RegimeRules,
    val baseConfidenceThreshold: Double,
    val confirmationExits: Int,
    val dynamicBurstSignalThreshold: Int,
    val dynamicBurstReserveFraction: Double,
    val maxDynamicBurstReserveFraction: Double,
    val losingStreakReserveStep: Double,
    val maxLosingStreakReserveFraction: Double,
    val recentCandidateReserveMultiple: Double,
    val recentCandidateReserveCapFraction: Double,
    val slippagePerSide: Double
) {
    companion object {
        fun fromJson(j: JSONObject): StrategyConfig {
            val modesJson = j.getJSONObject("modes")
            return StrategyConfig(
                name = j.getString("name"),
                modes = listOf("NORMAL", "CAUTION", "DEFENSIVE").associateWith {
                    ModeConfig.fromJson(modesJson.getJSONObject(it))
                },
                regimes = RegimeRules.fromJson(j.getJSONObject("regimes")),
                baseConfidenceThreshold = j.getDouble("base_confidence_threshold"),
                confirmationExits = j.getInt("confirmation_exits"),
                dynamicBurstSignalThreshold = j.getInt("dynamic_burst_signal_threshold"),
                dynamicBurstReserveFraction = j.getDouble("dynamic_burst_reserve_fraction"),
                maxDynamicBurstReserveFraction = j.getDouble("max_dynamic_burst_reserve_fraction"),
                losingStreakReserveStep = j.getDouble("losing_streak_reserve_step"),
                maxLosingStreakReserveFraction = j.getDouble("max_losing_streak_reserve_fraction"),
                recentCandidateReserveMultiple = j.getDouble("recent_candidate_reserve_multiple"),
                recentCandidateReserveCapFraction = j.getDouble("recent_candidate_reserve_cap_fraction"),
                slippagePerSide = j.getDouble("slippage_per_side")
            )
        }
    }
}

data class RuntimeSnapshot(
    var cash: Double = 1_000.0,
    var openCost: Double = 0.0,
    var peakEquity: Double = 1_000.0,
    var stageIndex: Int = 0,
    var promotionCounter: Int = 0,
    var mode: String = "NORMAL",
    var exitsSinceModeChange: Int = 0,
    val shadowReturns: MutableList<Double> = mutableListOf(),
    var shadowLosingStreak: Int = 0,
    val qualifyingSignalTimes: MutableList<Long> = mutableListOf(),
    val recentCandidateSizes: MutableList<Double> = mutableListOf()
) {
    fun equity() = cash + openCost
    fun exposure() = if (equity() > 0.0) openCost / equity() else Double.POSITIVE_INFINITY
    fun cashFraction() = if (equity() > 0.0) cash / equity() else 0.0
    fun drawdown() = if (peakEquity > 0.0) max(0.0, (peakEquity - equity()) / peakEquity) else 0.0
}

data class RecentEntry(val timeMs: Long, val principal: Double)

data class PolicyDecision(
    val execute: Boolean,
    val reason: String,
    val mode: String,
    val stageRatio: Double,
    val effectiveRatio: Double,
    val actualRatio: Double? = null,
    val copyUsd: Double? = null,
    val buyFee: Double? = null,
    val reserveRequired: Double? = null,
    val conservativePredictedRoi: Double? = null
)

object AdaptiveHybridPolicy {
    private val ratioLadder = doubleArrayOf(1000.0, 500.0, 250.0, 100.0, 75.0, 50.0, 40.0, 35.0, 30.0, 25.0, 20.0, 15.0, 10.0)
    private val promotionRatios = doubleArrayOf(20.0, 15.0, 10.0)
    private val promotionFloors = doubleArrayOf(0.0, 2_000.0, 4_000.0)

    fun orderFee(orderValue: Double, feeDiscount: Double): Double =
        if (orderValue <= 0.0) 0.0
        else max(0.95 * (1.0 - feeDiscount), 0.005 * (1.0 - feeDiscount) * orderValue)

    fun roundTripBurden(copyUsd: Double, feeDiscount: Double, slippagePerSide: Double): Double =
        if (copyUsd <= 0.0) Double.POSITIVE_INFINITY
        else 2.0 * orderFee(copyUsd, feeDiscount) / copyUsd + 2.0 * slippagePerSide

    fun updateMode(state: RuntimeSnapshot, cfg: StrategyConfig) {
        val target = desiredMode(state, cfg)
        val order = mapOf("NORMAL" to 0, "CAUTION" to 1, "DEFENSIVE" to 2)
        if (order.getValue(target) > order.getValue(state.mode)) {
            state.mode = target
            state.exitsSinceModeChange = 0
            return
        }
        if (target == state.mode || state.exitsSinceModeChange < cfg.regimes.recoveryExits) return

        val r = cfg.regimes
        if (state.mode == "DEFENSIVE") {
            val stats = recentStats(state.shadowReturns, max(r.defensiveShadowN, 8))
            if (state.drawdown() < r.defensiveRecoveryDrawdown && stats.count >= r.defensiveShadowN &&
                stats.mean > r.defensiveRecoveryMean && stats.winRate >= r.defensiveRecoveryWinRate &&
                state.cashFraction() >= cfg.modes.getValue("CAUTION").reserveFraction &&
                state.exposure() <= cfg.modes.getValue("CAUTION").maxOpenExposure
            ) {
                state.mode = "CAUTION"
                state.exitsSinceModeChange = 0
            }
        } else if (state.mode == "CAUTION") {
            val stats = recentStats(state.shadowReturns, max(r.cautionShadowN, 10))
            if (state.drawdown() < r.cautionRecoveryDrawdown && stats.count >= r.cautionShadowN &&
                stats.mean > r.cautionRecoveryMean && stats.winRate >= r.cautionRecoveryWinRate &&
                state.cashFraction() >= 0.20 && state.exposure() <= 0.70
            ) {
                state.mode = "NORMAL"
                state.exitsSinceModeChange = 0
            }
        }
    }

    fun updateStage(state: RuntimeSnapshot, cfg: StrategyConfig, afterExit: Boolean) {
        while (state.stageIndex > 0 && state.equity() < promotionFloors[state.stageIndex] - 1e-9) {
            state.stageIndex--
            state.promotionCounter = 0
        }
        if (state.mode != "NORMAL") {
            state.promotionCounter = 0
            return
        }
        if (state.stageIndex < promotionRatios.lastIndex) {
            val nextFloor = promotionFloors[state.stageIndex + 1]
            if (state.equity() >= nextFloor - 1e-9) {
                if (afterExit) state.promotionCounter++
                if (state.promotionCounter >= cfg.confirmationExits) {
                    state.stageIndex++
                    state.promotionCounter = 0
                }
            } else state.promotionCounter = 0
        }
    }

    fun chooseBuy(
        nowMs: Long,
        trader: String,
        token: String,
        sourceBuyUsd: Double,
        predictedRoi: Double,
        confidence: Double,
        state: RuntimeSnapshot,
        cfg: StrategyConfig,
        feeDiscount: Double,
        roiUncertaintyScale: Double,
        openByTrader: Map<String, Double>,
        openByToken: Map<String, Double>,
        recentEntries: List<RecentEntry>
    ): PolicyDecision {
        val mode = cfg.modes.getValue(state.mode)
        val stageRatio = promotionRatios[state.stageIndex]
        if (confidence < mode.confidenceThreshold) {
            return PolicyDecision(false, "MODE_CONFIDENCE", state.mode, stageRatio, max(stageRatio, mode.ratioFloor))
        }
        val effectiveRatio = max(stageRatio, mode.ratioFloor)
        val intended = sourceBuyUsd / effectiveRatio
        state.recentCandidateSizes += intended
        while (state.recentCandidateSizes.size > 10) state.recentCandidateSizes.removeAt(0)
        val reserve = dynamicReserve(state, cfg, nowMs)
        val recentPrincipal = recentEntries.filter { it.timeMs >= nowMs - 15 * 60_000L }.sumOf { it.principal }
        val equity = state.equity()
        val candidates = (listOf(effectiveRatio) + ratioLadder.filter { it > effectiveRatio }.sorted()).distinct()
        var sawFee = false
        var sawLiquidity = false
        var sawConcentration = false

        for (ratio in candidates) {
            val size = sourceBuyUsd / ratio
            val fee = orderFee(size, feeDiscount)
            val burden = roundTripBurden(size, feeDiscount, cfg.slippagePerSide)
            val conservativeRoi = predictedRoi - mode.uncertaintyMultiplier * roiUncertaintyScale
            if (burden > mode.feeBurdenTrigger && conservativeRoi < mode.feeMarginMultiple * burden) {
                sawFee = true
                continue
            }
            val cost = size + fee
            val postCash = state.cash - cost
            val postEquity = equity - fee
            if (postEquity <= 0.0) {
                sawLiquidity = true
                continue
            }
            val totalExposure = (state.openCost + size) / postEquity
            val traderExposure = (openByTrader.getOrDefault(trader, 0.0) + size) / postEquity
            val tokenExposure = (openByToken.getOrDefault(token, 0.0) + size) / postEquity
            val burstExposure = (recentPrincipal + size) / postEquity
            if (cost > state.cash + 1e-9 || postCash < reserve - 1e-9 ||
                totalExposure > mode.maxOpenExposure + 1e-12
            ) {
                sawLiquidity = true
                continue
            }
            if (traderExposure > mode.maxTraderExposure + 1e-12 ||
                tokenExposure > mode.maxTokenExposure + 1e-12 ||
                burstExposure > mode.max15mNewExposure + 1e-12
            ) {
                sawConcentration = true
                continue
            }
            return PolicyDecision(
                true, "PASS", state.mode, stageRatio, effectiveRatio, ratio, size, fee,
                reserve, conservativeRoi
            )
        }
        val reasons = buildList {
            if (sawFee) add("FEE_EV")
            if (sawLiquidity) add("LIQUIDITY")
            if (sawConcentration) add("CONCENTRATION")
        }
        return PolicyDecision(false, reasons.joinToString("+").ifBlank { "NO_FIT" }, state.mode,
            stageRatio, effectiveRatio, reserveRequired = reserve)
    }

    private fun desiredMode(state: RuntimeSnapshot, cfg: StrategyConfig): String {
        val r = cfg.regimes
        val defensive = recentStats(state.shadowReturns, r.defensiveShadowN)
        if (state.drawdown() >= r.defensiveDrawdown || state.cashFraction() < r.defensiveCashFraction ||
            state.exposure() > r.defensiveExposure || state.shadowLosingStreak >= r.defensiveLosingStreak ||
            (defensive.count >= r.defensiveShadowN &&
                (defensive.mean <= r.defensiveShadowMean || defensive.winRate <= r.defensiveShadowWinRate))
        ) return "DEFENSIVE"

        val caution = recentStats(state.shadowReturns, r.cautionShadowN)
        if (state.drawdown() >= r.cautionDrawdown || state.cashFraction() < r.cautionCashFraction ||
            state.exposure() > r.cautionExposure || state.shadowLosingStreak >= r.cautionLosingStreak ||
            (caution.count >= r.cautionShadowN &&
                (caution.mean <= r.cautionShadowMean || caution.winRate <= r.cautionShadowWinRate))
        ) return "CAUTION"
        return "NORMAL"
    }

    private fun dynamicReserve(state: RuntimeSnapshot, cfg: StrategyConfig, nowMs: Long): Double {
        val mode = cfg.modes.getValue(state.mode)
        val equity = state.equity()
        var reserve = max(mode.reserveFloorUsd, mode.reserveFraction * equity)
        state.qualifyingSignalTimes.removeAll { it < nowMs - 60 * 60_000L }
        val excess = max(0, state.qualifyingSignalTimes.size - cfg.dynamicBurstSignalThreshold)
        if (excess > 0) {
            reserve += min(cfg.maxDynamicBurstReserveFraction * equity,
                cfg.dynamicBurstReserveFraction * equity * ceil(excess / 3.0))
        }
        if (state.shadowLosingStreak > 2) {
            reserve += min(cfg.maxLosingStreakReserveFraction * equity,
                cfg.losingStreakReserveStep * equity * (state.shadowLosingStreak - 2))
        }
        if (state.recentCandidateSizes.isNotEmpty()) {
            val sorted = state.recentCandidateSizes.sorted()
            val middle = sorted.size / 2
            val median = if (sorted.size % 2 == 0) (sorted[middle - 1] + sorted[middle]) / 2.0 else sorted[middle]
            reserve = max(reserve, min(cfg.recentCandidateReserveCapFraction * equity,
                cfg.recentCandidateReserveMultiple * median))
        }
        return min(reserve, 0.50 * equity)
    }

    private data class Stats(val count: Int, val mean: Double, val winRate: Double)

    private fun recentStats(values: List<Double>, n: Int): Stats {
        val recent = values.takeLast(n)
        if (recent.isEmpty()) return Stats(0, Double.NaN, Double.NaN)
        return Stats(recent.size, recent.average(), recent.count { it > 0.0 }.toDouble() / recent.size)
    }
}
