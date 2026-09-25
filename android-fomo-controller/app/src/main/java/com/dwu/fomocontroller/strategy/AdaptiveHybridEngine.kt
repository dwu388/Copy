package com.dwu.fomocontroller.strategy

import android.content.ContentValues
import android.content.Context
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.StrategyDatabase
import com.dwu.fomocontroller.data.StrategyPlan
import com.dwu.fomocontroller.data.StrategyStatus
import kotlin.math.max

data class AndroidStrategyDecision(
    val execute: Boolean,
    val reason: String,
    val copyAmount: Double?,
    val mode: String,
    val probability: Double? = null,
    val predictedRoi: Double? = null,
    val confidenceRank: Double? = null,
    val actualRatio: Double? = null
) {
    fun auditSummary(): String = buildString {
        append("Adaptive Hybrid v2: ")
        append(reason)
        append("; mode=")
        append(mode)
        confidenceRank?.let { append("; confidence=${"%.4f".format(java.util.Locale.US, it)}") }
        predictedRoi?.let { append("; predicted_roi=${"%.4f".format(java.util.Locale.US, it)}") }
        actualRatio?.let { append("; ratio=1:${"%.0f".format(java.util.Locale.US, it)}") }
    }
}

/**
 * Persistent Android integration for the fitted model and causal controller.
 *
 * Raw notifications remain in EventDatabase. This store contains only strategy
 * decisions, shadow outcomes, reservations, and confirmed/paper positions.
 */
object AdaptiveHybridEngine {
    private const val DEDUPE_WINDOW_MS = 3 * 60_000L

    private lateinit var model: AdaptiveHybridModel
    private lateinit var db: StrategyDatabase
    private lateinit var prefs: AppPreferences
    @Volatile private var initialized = false

    @Synchronized
    fun initialize(context: Context) {
        if (initialized) return
        val app = context.applicationContext
        model = AdaptiveHybridModel.fromAssets(app)
        db = StrategyDatabase(app)
        prefs = AppPreferences(app)
        db.transaction(db::cancelOrphanedPlanned)
        initialized = true
    }

    @Synchronized
    fun evaluate(
        notificationKey: String,
        action: String,
        trader: String,
        token: String,
        marketCap: Double,
        sourceAmount: Double,
        eventTimeMs: Long,
        reserveForExecution: Boolean
    ): AndroidStrategyDecision {
        ensureInitialized()
        require(action == "bought" || action == "sold")
        return if (action == "bought") {
            evaluateBuy(notificationKey, trader, token, marketCap, sourceAmount, eventTimeMs, reserveForExecution)
        } else {
            evaluateSell(notificationKey, trader, token, marketCap, sourceAmount, eventTimeMs, reserveForExecution)
        }
    }

    private fun evaluateBuy(
        key: String, trader: String, token: String, marketCap: Double,
        sourceAmount: Double, nowMs: Long, reserveForExecution: Boolean
    ): AndroidStrategyDecision {
        val score = model.score(trader, marketCap, sourceAmount)
        return db.transaction { sql ->
            val fingerprint = fingerprint("bought", trader, token, marketCap, sourceAmount)
            if (db.isDuplicate(sql, fingerprint, nowMs, DEDUPE_WINDOW_MS)) {
                return@transaction skipAndRecord(sql, key, "bought", "DUPLICATE_3M", trader, token,
                    marketCap, sourceAmount, nowMs, null, null)
            }
            db.recordSeen(sql, key, fingerprint, nowMs)
            var state = db.loadState(sql, nowMs)
            AdaptiveHybridPolicy.updateStage(state, model.config, afterExit = false)
            AdaptiveHybridPolicy.updateMode(state, model.config)

            if (score.excluded) {
                db.saveState(sql, state)
                return@transaction skipAndRecord(sql, key, "bought", "EXCLUDED_TRADER", trader, token,
                    marketCap, sourceAmount, nowMs, score, state.mode)
            }
            if (score.confidenceRank >= model.config.baseConfidenceThreshold) {
                db.addShadowLot(sql, key, trader, token, marketCap, score.predictedRoi,
                    score.confidenceRank, nowMs)
                state.qualifyingSignalTimes += nowMs
            }

            val exposure = db.exposure(sql, nowMs)
            val reservedState = state.copy(
                cash = state.cash - exposure.pendingBuyCost,
                openCost = state.openCost + exposure.pendingBuyPrincipal,
                shadowReturns = state.shadowReturns.toMutableList(),
                qualifyingSignalTimes = state.qualifyingSignalTimes.toMutableList(),
                recentCandidateSizes = state.recentCandidateSizes.toMutableList()
            )
            val policy = AdaptiveHybridPolicy.chooseBuy(
                nowMs, trader, token, sourceAmount, score.predictedRoi, score.confidenceRank,
                reservedState, model.config, prefs.feeDiscount, model.roiUncertaintyScale,
                exposure.byTrader, exposure.byToken, exposure.recentEntries
            )
            val intended = if (policy.reason == "MODE_CONFIDENCE") null else sourceAmount / policy.effectiveRatio
            recordDecision(sql, key, "bought", if (policy.execute) "EXECUTE" else "SKIP", policy.reason,
                state.mode, trader, token, marketCap, sourceAmount, nowMs, score, policy, intended,
                if (policy.execute && reserveForExecution) "PLANNED" else "NOT_APPLICABLE")
            if (policy.execute && reserveForExecution) {
                db.addPlan(sql, StrategyPlan(
                    key, "bought", trader, token, marketCap,
                    principal = policy.copyUsd!!,
                    grossValue = policy.copyUsd,
                    fee = policy.buyFee!!,
                    status = "PLANNED",
                    createdTime = nowMs
                ))
            }
            db.saveState(sql, state)
            AndroidStrategyDecision(
                policy.execute, policy.reason, policy.copyUsd, state.mode,
                score.probabilityProfitableExit, score.predictedRoi, score.confidenceRank,
                policy.actualRatio
            )
        }
    }

    private fun evaluateSell(
        key: String, trader: String, token: String, marketCap: Double,
        sourceAmount: Double, nowMs: Long, reserveForExecution: Boolean
    ): AndroidStrategyDecision = db.transaction { sql ->
        val fingerprint = fingerprint("sold", trader, token, marketCap, sourceAmount)
        if (db.isDuplicate(sql, fingerprint, nowMs, DEDUPE_WINDOW_MS)) {
            return@transaction skipAndRecord(sql, key, "sold", "DUPLICATE_3M", trader, token,
                marketCap, sourceAmount, nowMs, null, null)
        }
        db.recordSeen(sql, key, fingerprint, nowMs)
        val state = db.loadState(sql, nowMs)

        // Every top-20% source opportunity informs the controller, including skipped copies.
        for ((shadowId, entryMarketCap) in db.openShadowLots(sql, trader, token, nowMs)) {
            val roi = marketCap / entryMarketCap - 1.0
            db.resolveShadow(sql, shadowId, roi, nowMs)
            state.shadowReturns += roi
            while (state.shadowReturns.size > 20) state.shadowReturns.removeAt(0)
            state.shadowLosingStreak = if (roi <= 0.0) state.shadowLosingStreak + 1 else 0
            state.exitsSinceModeChange++
            AdaptiveHybridPolicy.updateMode(state, model.config)
        }

        val positions = db.openPositions(sql, trader, token)
        if (positions.isEmpty()) {
            db.saveState(sql, state)
            return@transaction skipAndRecord(sql, key, "sold", "NO_CONFIRMED_POSITION", trader, token,
                marketCap, sourceAmount, nowMs, null, state.mode)
        }
        if (db.hasActiveSellPlan(sql, trader, token)) {
            db.saveState(sql, state)
            return@transaction skipAndRecord(sql, key, "sold", "SELL_ALREADY_PENDING", trader, token,
                marketCap, sourceAmount, nowMs, null, state.mode)
        }
        val principal = positions.sumOf { it.principal }
        val gross = positions.sumOf { position ->
            position.principal * (marketCap / position.entryMarketCap) * (1.0 - model.config.slippagePerSide)
        }.coerceAtLeast(0.0)
        val fee = AdaptiveHybridPolicy.orderFee(gross, prefs.feeDiscount)
        recordDecision(sql, key, "sold", "EXECUTE", "MATCHED_CONFIRMED_POSITION", state.mode,
            trader, token, marketCap, sourceAmount, nowMs, null, null, null,
            if (reserveForExecution) "PLANNED" else "NOT_APPLICABLE",
            copyAmount = gross, fee = fee)
        if (reserveForExecution) {
            db.addPlan(sql, StrategyPlan(key, "sold", trader, token, marketCap, principal, gross,
                fee, "PLANNED", nowMs))
            db.linkPlanPositions(sql, key, positions)
        }
        db.saveState(sql, state)
        AndroidStrategyDecision(true, "MATCHED_CONFIRMED_POSITION", gross, state.mode)
    }

    @Synchronized
    fun markPrepared(notificationKey: String) {
        ensureInitialized()
        db.transaction { sql ->
            val plan = db.getPlan(sql, notificationKey) ?: return@transaction
            if (plan.status == "PLANNED") db.setPlanStatus(sql, notificationKey, "PREPARED")
        }
    }

    @Synchronized
    fun recordPaperExecution(notificationKey: String): String = executePlan(notificationKey, "PAPER_EXECUTED")

    @Synchronized
    fun confirmLatestPrepared(): String {
        ensureInitialized()
        val key = db.transaction { sql -> db.latestPrepared(sql)?.notificationKey }
            ?: return "No prepared trade is awaiting confirmation."
        return executePlan(key, "CONFIRMED")
    }

    @Synchronized
    fun rejectLatestPrepared(): String {
        ensureInitialized()
        return db.transaction { sql ->
            val plan = db.latestPrepared(sql) ?: return@transaction "No prepared trade is awaiting confirmation."
            db.setPlanStatus(sql, plan.notificationKey, "REJECTED")
            "Rejected ${plan.action} plan for ${plan.token}."
        }
    }

    @Synchronized
    fun cancelUnexecuted(notificationKey: String) {
        ensureInitialized()
        db.transaction { sql ->
            val plan = db.getPlan(sql, notificationKey) ?: return@transaction
            if (plan.status == "PLANNED") db.setPlanStatus(sql, notificationKey, "CANCELLED")
        }
    }

    fun status(): StrategyStatus {
        ensureInitialized()
        return db.status()
    }

    fun modelDescription(): String {
        ensureInitialized()
        return "${model.version} (${model.config.name}), trained through ${model.trainedThrough}"
    }

    private fun executePlan(key: String, finalStatus: String): String {
        ensureInitialized()
        return db.transaction { sql ->
            val plan = db.getPlan(sql, key) ?: return@transaction "Strategy plan is missing."
            if (plan.status !in setOf("PLANNED", "PREPARED")) {
                return@transaction "Plan is already ${plan.status}."
            }
            val state = db.loadState(sql, System.currentTimeMillis())
            if (plan.action == "bought") {
                val cost = plan.principal + plan.fee
                if (cost > state.cash + 1e-9) {
                    db.setPlanStatus(sql, key, "FUNDING_FAILED")
                    return@transaction "Buy was not recorded: insufficient strategy cash."
                }
                state.cash -= cost
                state.openCost += plan.principal
                db.insertPosition(sql, plan)
            } else {
                val positions = db.positionsForPlan(sql, key)
                if (positions.isEmpty()) {
                    db.setPlanStatus(sql, key, "POSITION_MISSING")
                    return@transaction "Sell was not recorded: no confirmed position remains."
                }
                val principal = positions.sumOf { it.principal }
                state.cash += max(0.0, plan.grossValue - plan.fee)
                state.openCost = max(0.0, state.openCost - principal)
                db.closePositions(sql, positions, key, System.currentTimeMillis())
                AdaptiveHybridPolicy.updateMode(state, model.config)
                AdaptiveHybridPolicy.updateStage(state, model.config, afterExit = true)
            }
            state.peakEquity = max(state.peakEquity, state.equity())
            db.saveState(sql, state)
            db.setPlanStatus(sql, key, finalStatus)
            "Recorded ${plan.action} for ${plan.token} as $finalStatus."
        }
    }

    private fun skipAndRecord(
        sql: android.database.sqlite.SQLiteDatabase,
        key: String,
        action: String,
        reason: String,
        trader: String,
        token: String,
        marketCap: Double,
        sourceAmount: Double,
        nowMs: Long,
        score: HybridScore?,
        mode: String?
    ): AndroidStrategyDecision {
        val currentMode = mode ?: db.loadState(sql, nowMs).mode
        recordDecision(sql, key, action, "SKIP", reason, currentMode, trader, token,
            marketCap, sourceAmount, nowMs, score, null, null, "NOT_APPLICABLE")
        return AndroidStrategyDecision(false, reason, null, currentMode,
            score?.probabilityProfitableExit, score?.predictedRoi, score?.confidenceRank)
    }

    private fun recordDecision(
        sql: android.database.sqlite.SQLiteDatabase,
        key: String,
        action: String,
        decision: String,
        reason: String,
        mode: String,
        trader: String,
        token: String,
        marketCap: Double,
        sourceAmount: Double,
        nowMs: Long,
        score: HybridScore?,
        policy: PolicyDecision?,
        intendedSize: Double?,
        executionStatus: String,
        copyAmount: Double? = policy?.copyUsd,
        fee: Double? = policy?.buyFee
    ) {
        db.recordDecision(sql, ContentValues().apply {
            put("notification_key", key); put("action", action); put("decision", decision)
            put("reason", reason); put("mode", mode); put("trader", trader); put("token", token)
            put("market_cap", marketCap); put("source_amount", sourceAmount)
            put("probability", score?.probabilityProfitableExit); put("predicted_roi", score?.predictedRoi)
            put("confidence_rank", score?.confidenceRank); put("stage_ratio", policy?.stageRatio)
            put("effective_ratio", policy?.effectiveRatio); put("actual_ratio", policy?.actualRatio)
            put("intended_size", intendedSize); put("copy_amount", copyAmount); put("fee", fee)
            put("reserve_required", policy?.reserveRequired); put("created_time", nowMs)
            put("execution_status", executionStatus)
        })
    }

    private fun fingerprint(
        action: String, trader: String, token: String, marketCap: Double, sourceAmount: Double
    ): String = listOf(
        action, trader.lowercase(), token.lowercase(), marketCap.toBits().toString(), sourceAmount.toBits().toString()
    ).joinToString("|")

    private fun ensureInitialized() = check(initialized) { "AdaptiveHybridEngine.initialize(context) first" }
}
