package com.dwu.fomocontroller.strategy

import android.content.ContentValues
import android.content.Context
import android.util.Log
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.StrategyDatabase
import com.dwu.fomocontroller.data.StrategyPlan
import com.dwu.fomocontroller.data.StrategyStatus
import com.dwu.fomocontroller.data.EventDatabase
import com.dwu.fomocontroller.data.TraderGuardDatabase
import com.dwu.fomocontroller.model.ControllerMode
import java.io.File
import kotlin.math.max

data class AndroidStrategyDecision(
    val execute: Boolean,
    val reason: String,
    val copyAmount: Double?,
    val mode: String,
    val probability: Double? = null,
    val predictedRoi: Double? = null,
    val confidenceRank: Double? = null,
    val actualRatio: Double? = null,
    val traderGuard: TraderGuardAssessment? = null
) {
    fun auditSummary(): String = buildString {
        append("Adaptive Hybrid v2: ")
        append(reason)
        append("; mode=")
        append(mode)
        confidenceRank?.let { append("; confidence=${"%.4f".format(java.util.Locale.US, it)}") }
        predictedRoi?.let { append("; predicted_roi=${"%.4f".format(java.util.Locale.US, it)}") }
        actualRatio?.let { append("; ratio=1:${"%.0f".format(java.util.Locale.US, it)}") }
        traderGuard?.let { append("; trader_guard=${it.summary()}") }
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

    private lateinit var baseModel: AdaptiveHybridModel
    private lateinit var basePolicy: PolicyArtifact
    private var paperModel: AdaptiveHybridModel? = null
    private var paperPolicy: PolicyArtifact? = null
    private val paperSelected: Boolean get() = prefs.mode == ControllerMode.DRY_RUN && prefs.paperForecast == PaperForecast.CORRECTED
    private val model: AdaptiveHybridModel get() = if (paperSelected) paperModel ?: baseModel else baseModel
    private val policyArtifact: PolicyArtifact get() = if (paperSelected) paperPolicy ?: basePolicy else basePolicy
    private lateinit var championSource: String
    private lateinit var db: StrategyDatabase
    private lateinit var prefs: AppPreferences
    private lateinit var rawDb: EventDatabase
    private lateinit var traderDb: TraderGuardDatabase
    @Volatile private var lastTraderGuard: String? = null
    @Volatile private var lastRiskEquity = 1000.0
    @Volatile private var unpricedPositions = 0
    @Volatile private var initialized = false

    @Synchronized
    fun initialize(context: Context) {
        if (initialized) return
        val app = context.applicationContext
        val champions = ChampionStore.load(app)
        baseModel = champions.forecast
        basePolicy = champions.policy
        championSource = champions.source
        db = StrategyDatabase(app)
        prefs = AppPreferences(app)
        runCatching {
            AdaptiveHybridModel.fromAssets(app, "corrected_paper_v1.json").also {
                it.validateParity()
                require(it.labelContract == "notification_24h_net_reference_v1")
            }
        }.onSuccess {
            paperModel = it
            paperPolicy = PolicyArtifact.bootstrap(requireNotNull(it.config), it.trainedThrough)
                .copy(policyModelId = "paper_mild_v1")
        }.onFailure { Log.e("AdaptiveHybridEngine", "Corrected paper model unavailable; MODEL paper entries will be blocked", it) }
        rawDb = EventDatabase(app)
        traderDb = TraderGuardDatabase(app)
        db.transaction(db::cancelOrphanedPlanned)
        initialized = true
        expirePaperPositions()
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
        expirePaperPositions()
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
            val guard = if (prefs.traderHistoryInvalid) unavailableTraderGuard() else runCatching {
                traderDb.observeBuy(key, nowMs, trader, token, marketCap, sourceAmount,
                    System.currentTimeMillis(), prefs.traderGuardMode, prefs.traderMinimumWinRate)
            }.getOrElse { error -> traderHistoryFailed(error); unavailableTraderGuard() }
            lastTraderGuard = "$trader: ${guard.summary()}"
            var state = db.loadState(sql, nowMs)
            val config = policyArtifact.config
            AdaptiveHybridPolicy.updateStage(state, config, afterExit = false)
            AdaptiveHybridPolicy.updateMode(state, config)

            db.addLearningOpportunity(
                sql, key, trader, token, marketCap, sourceAmount, nowMs,
                model.modelId, policyArtifact.policyModelId,
                score.probabilityProfitableExit, score.predictedRoi, score.confidenceRank
            )

            val useForecast = prefs.entryStyle == EntryStyle.MODEL
            val entryReason = EntrySafety.entryReason(marketCap, sourceAmount, prefs.entryMaxCap, prefs.entryMinSource)
                ?: (if (!useForecast && prefs.mode != ControllerMode.DRY_RUN) "RULE_ONLY_PAPER_ONLY" else null)
                ?: (if (useForecast && paperSelected && paperModel == null) "PAPER_MODEL_UNAVAILABLE" else null)
            if (entryReason != null) {
                db.saveState(sql, state)
                return@transaction skipAndRecord(sql, key, "bought", entryReason, trader, token,
                    marketCap, sourceAmount, nowMs, score, state.mode, guard)
            }

            if (useForecast && score.excluded) {
                db.saveState(sql, state)
                return@transaction skipAndRecord(sql, key, "bought", "EXCLUDED_TRADER", trader, token,
                    marketCap, sourceAmount, nowMs, score, state.mode, guard)
            }
            if (!useForecast || score.confidenceRank >= config.baseConfidenceThreshold) {
                db.addShadowLot(sql, key, trader, token, marketCap, score.predictedRoi,
                    score.confidenceRank, nowMs)
                state.qualifyingSignalTimes += nowMs
            }

            val circuit = updateCircuit(sql, System.currentTimeMillis())
            if (circuit.paused) {
                db.saveState(sql, state)
                return@transaction skipAndRecord(sql, key, "bought", "LOSS_CIRCUIT_PAUSED", trader, token,
                    marketCap, sourceAmount, nowMs, score, state.mode, guard)
            }

            // Still record every opportunity and qualifying shadow before filtering,
            // so a blocked trader can accumulate new evidence and recover eligibility.
            if (!guard.allowed) {
                db.saveState(sql, state)
                return@transaction skipAndRecord(sql, key, "bought", "TRADER_WIN_RATE_${guard.status}",
                    trader, token, marketCap, sourceAmount, nowMs, score, state.mode, guard)
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
                reservedState, config, prefs.feeDiscount, model.roiUncertaintyScale,
                exposure.byTrader, exposure.byToken, exposure.recentEntries, useForecast
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
                policy.actualRatio, guard
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
        // Eligibility only applies to entries; sells always update history and
        // retain the existing confirmed-position exit path.
        if (!prefs.traderHistoryInvalid) runCatching {
            traderDb.observeSell(key, trader, token, marketCap, System.currentTimeMillis())
        }.onFailure(::traderHistoryFailed)
        val state = db.loadState(sql, nowMs)
        val config = policyArtifact.config
        db.resolveLearningOpportunities(sql, key, trader, token, marketCap, nowMs)

        // Every top-20% source opportunity informs the controller, including skipped copies.
        for ((shadowId, entryMarketCap) in db.openShadowLots(sql, trader, token, nowMs)) {
            val roi = marketCap / entryMarketCap - 1.0
            db.resolveShadow(sql, shadowId, roi, nowMs)
            state.shadowReturns += roi
            while (state.shadowReturns.size > 20) state.shadowReturns.removeAt(0)
            state.shadowLosingStreak = if (roi <= 0.0) state.shadowLosingStreak + 1 else 0
            state.exitsSinceModeChange++
            AdaptiveHybridPolicy.updateMode(state, config)
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
            position.principal * (marketCap / position.entryMarketCap) * (1.0 - config.slippagePerSide)
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
        expirePaperPositions()
        db.transaction { updateCircuit(it, System.currentTimeMillis()) }
        return db.status()
    }

    private fun riskEquity(sql: android.database.sqlite.SQLiteDatabase, now: Long): Double {
        val state = db.loadState(sql, now)
        var unknown = 0
        val marked = db.allOpenPositions(sql).sumOf { position ->
            val cap = rawDb.freshMarketCap(position.trader, position.token, now, PaperExpiry.QUOTE_MAX_AGE_MS)
            if (cap == null) { unknown++; position.principal } else {
                val gross = PaperExpiry.gross(position.principal, position.entryMarketCap, cap,
                    policyArtifact.config.slippagePerSide)
                minOf(position.principal, max(0.0, gross - AdaptiveHybridPolicy.orderFee(gross, prefs.feeDiscount)))
            }
        }
        unpricedPositions = unknown
        lastRiskEquity = max(0.0, state.cash + marked)
        return lastRiskEquity
    }

    private fun updateCircuit(sql: android.database.sqlite.SQLiteDatabase, now: Long): CircuitState {
        val state = EntrySafety.circuit(prefs.circuitPeak, riskEquity(sql, now), prefs.entriesPaused, prefs.lossPauseFraction)
        if (!prefs.entriesPaused && state.paused) db.recordCircuit(sql, now, "PAUSED", state.peak,
            state.equity, prefs.lossPauseFraction, unpricedPositions)
        prefs.circuitPeak = state.peak
        prefs.entriesPaused = state.paused
        return state
    }

    fun entrySafetyDescription(): String =
        "${prefs.entryStyle}; MC < ${prefs.entryMaxCap}; source >= ${prefs.entryMinSource}" +
        "\nEntries ${if (prefs.entriesPaused) "PAUSED" else "enabled"}; loss limit ${prefs.lossPauseFraction * 100}%" +
        "; estimated risk equity ${"%.2f".format(java.util.Locale.US, lastRiskEquity)}; unpriced positions=$unpricedPositions"

    @Synchronized
    fun resumePaperEntries() {
        ensureInitialized()
        require(prefs.mode == ControllerMode.DRY_RUN) { "Entry-pause acknowledgement is paper-only." }
        db.transaction { sql ->
            val now = System.currentTimeMillis()
            val equity = riskEquity(sql, now)
            db.recordCircuit(sql, now, "USER_RESUMED_NEW_ANCHOR", equity, equity, prefs.lossPauseFraction, unpricedPositions)
            prefs.circuitPeak = equity
            prefs.entriesPaused = false
        }
    }

    @Synchronized
    fun startNewPaperSession() {
        ensureInitialized()
        require(prefs.mode != ControllerMode.PREPARE) { "Switch to OBSERVE and resolve prepared trades first." }
        if (prefs.paperForecast == PaperForecast.CORRECTED && prefs.entryStyle == EntryStyle.MODEL)
            require(paperModel != null) { "Corrected paper model is unavailable. Build it locally before packaging the APK, or select LEGACY / RULE_ONLY for a separate paper comparison." }
        db.transaction { sql ->
            val now = System.currentTimeMillis()
            db.startPaperSession(sql, now, "style=${prefs.entryStyle}; forecast=${prefs.paperForecast}; MC<${prefs.entryMaxCap}; source>=${prefs.entryMinSource}")
            db.recordCircuit(sql, now, "NEW_PAPER_SESSION", 1000.0, 1000.0, prefs.lossPauseFraction, 0)
        }
        prefs.circuitPeak = 1000.0
        prefs.entriesPaused = false
    }

    @Synchronized
    fun expirePaperPositions(now: Long = System.currentTimeMillis()): Int {
        ensureInitialized()
        // Independent observational history also advances in OBSERVE/PREPARE.
        if (!prefs.traderHistoryInvalid) runCatching {
            traderDb.expire(now) { trader, token ->
                rawDb.freshMarketCap(trader, token, now, TraderWinRateGuard.QUOTE_AGE_MS)
            }
        }.onFailure(::traderHistoryFailed)
        if (prefs.mode != ControllerMode.DRY_RUN) return 0
        return db.transaction { sql ->
            val overdue = db.overduePaperPositions(sql, now - PaperExpiry.MAX_HOLD_MS)
            if (overdue.isEmpty()) {
                updateCircuit(sql, now)
                return@transaction 0
            }
            val state = db.loadState(sql, now)
            for (positions in overdue.groupBy { it.trader.lowercase(java.util.Locale.ROOT) to
                    it.token.lowercase(java.util.Locale.ROOT) }.values) {
                val first = positions.first()
                val quote = rawDb.freshMarketCap(first.trader, first.token, now, PaperExpiry.QUOTE_MAX_AGE_MS)
                val gross = positions.sumOf { PaperExpiry.gross(it.principal, it.entryMarketCap,
                    quote, policyArtifact.config.slippagePerSide) }
                val fee = AdaptiveHybridPolicy.orderFee(gross, prefs.feeDiscount)
                val principal = positions.sumOf { it.principal }
                val key = "paper-expiry-24h:" + positions.joinToString(",") { it.id.toString() }
                db.cancelPlansForExpiry(sql, positions)
                val plan = StrategyPlan(key, "sold", first.trader, first.token, quote ?: 0.0,
                    principal, gross, fee, "PAPER_EXECUTED", now)
                recordDecision(sql, key, "sold", "EXECUTE", if (quote == null)
                    "TIMEOUT_24H_FULL_LOSS_NO_FRESH_QUOTE" else "TIMEOUT_24H_FRESH_NOTIFICATION_MARK",
                    state.mode, first.trader, first.token, quote ?: 0.0, 0.0, now,
                    null, null, null, "PAPER_EXECUTED", copyAmount = gross, fee = fee)
                db.addPlan(sql, plan)
                db.linkPlanPositions(sql, key, positions)
                db.closePositions(sql, positions, key, now)
                state.cash += max(0.0, gross - fee)
                state.openCost = max(0.0, state.openCost - principal)
                state.exitsSinceModeChange++
            }
            AdaptiveHybridPolicy.updateMode(state, policyArtifact.config)
            AdaptiveHybridPolicy.updateStage(state, policyArtifact.config, afterExit = true)
            state.peakEquity = max(state.peakEquity, state.equity())
            db.saveState(sql, state)
            updateCircuit(sql, now)
            overdue.size
        }
    }

    fun exportLearningCsv(destination: File): Int {
        ensureInitialized()
        return db.exportLearningCsv(destination)
    }

    fun exportExecutionCsv(destination: File): Int {
        ensureInitialized()
        return db.exportExecutionCsv(destination)
    }

    @Synchronized
    fun exportTraderGuard(directory: File): Pair<Int, Int> {
        ensureInitialized()
        val result = traderDb.export(directory)
        db.exportSafetyAudit(directory)
        return result
    }

    fun traderGuardDescription(): String =
        "${prefs.traderGuardMode}: last ${TraderWinRateGuard.WINDOW} episodes, minimum " +
            "${"%.1f".format(java.util.Locale.US, prefs.traderMinimumWinRate * 100)}%" +
            (lastTraderGuard?.let { "\nLast trader check: $it" } ?: "\nHistory starts with this update.") +
            (if (prefs.traderHistoryInvalid) "\nHistory unavailable; reset it to collect a new trusted window." else "")

    private fun unavailableTraderGuard() = TraderGuardAssessment(prefs.traderGuardMode,
        "UNAVAILABLE", TraderGuardStats(0, 0), prefs.traderMinimumWinRate)

    private fun traderHistoryFailed(error: Throwable) {
        prefs.traderHistoryInvalid = true
        Log.e("AdaptiveHybridEngine", "Trader history unavailable; entries follow guard mode, sells remain enabled", error)
    }

    @Synchronized
    fun resetTraderHistory() {
        ensureInitialized()
        traderDb.reset()
        prefs.traderHistoryInvalid = false
        lastTraderGuard = null
    }

    fun modelDescription(): String {
        ensureInitialized()
        return "${model.modelId} + ${policyArtifact.policyModelId} " +
            "[${if (paperSelected) if (paperModel == null) "PAPER MODEL UNAVAILABLE" else "PAPER EXPERIMENT" else championSource}], trained through ${model.trainedThrough}"
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
                AdaptiveHybridPolicy.updateMode(state, policyArtifact.config)
                AdaptiveHybridPolicy.updateStage(state, policyArtifact.config, afterExit = true)
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
        mode: String?,
        guard: TraderGuardAssessment? = null
    ): AndroidStrategyDecision {
        val currentMode = mode ?: db.loadState(sql, nowMs).mode
        recordDecision(sql, key, action, "SKIP", reason, currentMode, trader, token,
            marketCap, sourceAmount, nowMs, score, null, null, "NOT_APPLICABLE")
        return AndroidStrategyDecision(false, reason, null, currentMode,
            score?.probabilityProfitableExit, score?.predictedRoi, score?.confidenceRank,
            traderGuard = guard)
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
            put("forecast_model_id", model.modelId)
            put("policy_model_id", policyArtifact.policyModelId)
        })
    }

    private fun fingerprint(
        action: String, trader: String, token: String, marketCap: Double, sourceAmount: Double
    ): String = listOf(
        action, trader.lowercase(), token.lowercase(), marketCap.toBits().toString(), sourceAmount.toBits().toString()
    ).joinToString("|")

    private fun ensureInitialized() = check(initialized) { "AdaptiveHybridEngine.initialize(context) first" }
}
