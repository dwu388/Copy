package com.dwu.fomocontroller.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.dwu.fomocontroller.strategy.RecentEntry
import com.dwu.fomocontroller.strategy.RuntimeSnapshot

data class StrategyPlan(
    val notificationKey: String,
    val action: String,
    val trader: String,
    val token: String,
    val marketCap: Double,
    val principal: Double,
    val grossValue: Double,
    val fee: Double,
    val status: String,
    val createdTime: Long
)

data class StrategyPosition(
    val id: Long,
    val trader: String,
    val token: String,
    val entryMarketCap: Double,
    val principal: Double,
    val openedTime: Long
)

data class StrategyExposure(
    val pendingBuyCost: Double,
    val pendingBuyPrincipal: Double,
    val byTrader: Map<String, Double>,
    val byToken: Map<String, Double>,
    val recentEntries: List<RecentEntry>
)

data class StrategyStatus(
    val cash: Double,
    val openCost: Double,
    val equity: Double,
    val mode: String,
    val stageRatio: String,
    val openPositions: Int,
    val pendingPlans: Int,
    val latestPreparedKey: String?
)

class StrategyDatabase(context: Context) :
    SQLiteOpenHelper(context, DB_NAME, null, DB_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE strategy_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                cash REAL NOT NULL,
                open_cost REAL NOT NULL,
                peak_equity REAL NOT NULL,
                stage_index INTEGER NOT NULL,
                promotion_counter INTEGER NOT NULL,
                mode TEXT NOT NULL,
                exits_since_mode_change INTEGER NOT NULL,
                shadow_losing_streak INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            "INSERT INTO strategy_state VALUES (1,1000.0,0.0,1000.0,0,0,'NORMAL',0,0)"
        )
        db.execSQL(
            """
            CREATE TABLE seen_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                observed_time INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_seen_fingerprint_time ON seen_signals(fingerprint, observed_time)")
        db.execSQL(
            """
            CREATE TABLE shadow_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_key TEXT NOT NULL UNIQUE,
                trader TEXT NOT NULL,
                token TEXT NOT NULL,
                entry_market_cap REAL NOT NULL,
                predicted_roi REAL NOT NULL,
                confidence_rank REAL NOT NULL,
                entry_time INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN'
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_shadow_match ON shadow_lots(status, trader, token, entry_time)")
        db.execSQL(
            """
            CREATE TABLE shadow_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shadow_lot_id INTEGER NOT NULL UNIQUE,
                roi REAL NOT NULL,
                resolved_time INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_shadow_outcome_time ON shadow_outcomes(resolved_time)")
        db.execSQL(
            """
            CREATE TABLE decisions (
                notification_key TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                mode TEXT NOT NULL,
                trader TEXT,
                token TEXT,
                market_cap REAL,
                source_amount REAL,
                probability REAL,
                predicted_roi REAL,
                confidence_rank REAL,
                stage_ratio REAL,
                effective_ratio REAL,
                actual_ratio REAL,
                intended_size REAL,
                copy_amount REAL,
                fee REAL,
                reserve_required REAL,
                created_time INTEGER NOT NULL,
                execution_status TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_decisions_time ON decisions(created_time)")
        db.execSQL(
            """
            CREATE TABLE plans (
                notification_key TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                trader TEXT NOT NULL,
                token TEXT NOT NULL,
                market_cap REAL NOT NULL,
                principal REAL NOT NULL,
                gross_value REAL NOT NULL,
                fee REAL NOT NULL,
                status TEXT NOT NULL,
                created_time INTEGER NOT NULL,
                updated_time INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_plans_status_time ON plans(status, created_time)")
        db.execSQL(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buy_notification_key TEXT NOT NULL UNIQUE,
                trader TEXT NOT NULL,
                token TEXT NOT NULL,
                entry_market_cap REAL NOT NULL,
                principal REAL NOT NULL,
                buy_fee REAL NOT NULL,
                opened_time INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                sell_notification_key TEXT,
                closed_time INTEGER
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_positions_match ON positions(status, trader, token, opened_time)")
        db.execSQL(
            """
            CREATE TABLE plan_positions (
                plan_notification_key TEXT NOT NULL,
                position_id INTEGER NOT NULL,
                PRIMARY KEY(plan_notification_key, position_id)
            )
            """.trimIndent()
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun <T> transaction(block: (SQLiteDatabase) -> T): T {
        val db = writableDatabase
        db.beginTransaction()
        return try {
            val result = block(db)
            db.setTransactionSuccessful()
            result
        } finally {
            db.endTransaction()
        }
    }

    fun loadState(db: SQLiteDatabase, nowMs: Long): RuntimeSnapshot {
        val state = db.rawQuery("SELECT * FROM strategy_state WHERE id=1", null).use { c ->
            check(c.moveToFirst()) { "Missing Adaptive Hybrid strategy state" }
            RuntimeSnapshot(
                cash = c.getDouble(c.getColumnIndexOrThrow("cash")),
                openCost = c.getDouble(c.getColumnIndexOrThrow("open_cost")),
                peakEquity = c.getDouble(c.getColumnIndexOrThrow("peak_equity")),
                stageIndex = c.getInt(c.getColumnIndexOrThrow("stage_index")),
                promotionCounter = c.getInt(c.getColumnIndexOrThrow("promotion_counter")),
                mode = c.getString(c.getColumnIndexOrThrow("mode")),
                exitsSinceModeChange = c.getInt(c.getColumnIndexOrThrow("exits_since_mode_change")),
                shadowLosingStreak = c.getInt(c.getColumnIndexOrThrow("shadow_losing_streak"))
            )
        }
        db.rawQuery(
            "SELECT roi FROM shadow_outcomes ORDER BY resolved_time DESC,id DESC LIMIT 20", null
        ).use { c ->
            val reverse = mutableListOf<Double>()
            while (c.moveToNext()) reverse += c.getDouble(0)
            state.shadowReturns += reverse.asReversed()
        }
        db.rawQuery(
            "SELECT entry_time FROM shadow_lots WHERE entry_time>=? ORDER BY entry_time",
            arrayOf((nowMs - 60 * 60_000L).toString())
        ).use { c -> while (c.moveToNext()) state.qualifyingSignalTimes += c.getLong(0) }
        db.rawQuery(
            "SELECT intended_size FROM decisions WHERE intended_size IS NOT NULL " +
                "ORDER BY created_time DESC LIMIT 10", null
        ).use { c ->
            val reverse = mutableListOf<Double>()
            while (c.moveToNext()) reverse += c.getDouble(0)
            state.recentCandidateSizes += reverse.asReversed()
        }
        return state
    }

    fun saveState(db: SQLiteDatabase, state: RuntimeSnapshot) {
        val values = ContentValues().apply {
            put("cash", state.cash)
            put("open_cost", state.openCost)
            put("peak_equity", state.peakEquity)
            put("stage_index", state.stageIndex)
            put("promotion_counter", state.promotionCounter)
            put("mode", state.mode)
            put("exits_since_mode_change", state.exitsSinceModeChange)
            put("shadow_losing_streak", state.shadowLosingStreak)
        }
        check(db.update("strategy_state", values, "id=1", null) == 1)
    }

    fun isDuplicate(db: SQLiteDatabase, fingerprint: String, nowMs: Long, windowMs: Long): Boolean =
        db.rawQuery(
            "SELECT 1 FROM seen_signals WHERE fingerprint=? AND observed_time>=? LIMIT 1",
            arrayOf(fingerprint, (nowMs - windowMs).toString())
        ).use { it.moveToFirst() }

    fun recordSeen(db: SQLiteDatabase, notificationKey: String, fingerprint: String, nowMs: Long) {
        db.insertOrThrow("seen_signals", null, ContentValues().apply {
            put("notification_key", notificationKey)
            put("fingerprint", fingerprint)
            put("observed_time", nowMs)
        })
        db.delete("seen_signals", "observed_time<?", arrayOf((nowMs - 24 * 60 * 60_000L).toString()))
    }

    fun addShadowLot(
        db: SQLiteDatabase, notificationKey: String, trader: String, token: String,
        marketCap: Double, predictedRoi: Double, confidence: Double, entryTime: Long
    ) {
        db.insertWithOnConflict("shadow_lots", null, ContentValues().apply {
            put("notification_key", notificationKey)
            put("trader", trader)
            put("token", token)
            put("entry_market_cap", marketCap)
            put("predicted_roi", predictedRoi)
            put("confidence_rank", confidence)
            put("entry_time", entryTime)
            put("status", "OPEN")
        }, SQLiteDatabase.CONFLICT_IGNORE)
    }

    fun openShadowLots(db: SQLiteDatabase, trader: String, token: String, exitTime: Long): List<Pair<Long, Double>> {
        val out = mutableListOf<Pair<Long, Double>>()
        db.rawQuery(
            "SELECT id,entry_market_cap FROM shadow_lots " +
                "WHERE status='OPEN' AND lower(trader)=lower(?) AND lower(token)=lower(?) AND entry_time<? " +
                "ORDER BY entry_time,id",
            arrayOf(trader, token, exitTime.toString())
        ).use { c -> while (c.moveToNext()) out += c.getLong(0) to c.getDouble(1) }
        return out
    }

    fun resolveShadow(db: SQLiteDatabase, id: Long, roi: Double, timeMs: Long) {
        db.update("shadow_lots", ContentValues().apply { put("status", "RESOLVED") }, "id=?", arrayOf(id.toString()))
        db.insertOrThrow("shadow_outcomes", null, ContentValues().apply {
            put("shadow_lot_id", id); put("roi", roi); put("resolved_time", timeMs)
        })
    }

    fun exposure(db: SQLiteDatabase, nowMs: Long): StrategyExposure {
        val byTrader = mutableMapOf<String, Double>()
        val byToken = mutableMapOf<String, Double>()
        val recent = mutableListOf<RecentEntry>()
        db.rawQuery(
            "SELECT trader,token,principal,opened_time FROM positions WHERE status='OPEN'", null
        ).use { c ->
            while (c.moveToNext()) {
                val trader = c.getString(0); val token = c.getString(1); val principal = c.getDouble(2)
                byTrader[trader] = byTrader.getOrDefault(trader, 0.0) + principal
                byToken[token] = byToken.getOrDefault(token, 0.0) + principal
                if (c.getLong(3) >= nowMs - 15 * 60_000L) recent += RecentEntry(c.getLong(3), principal)
            }
        }
        var pendingCost = 0.0
        var pendingPrincipal = 0.0
        db.rawQuery(
            "SELECT trader,token,principal,fee,created_time FROM plans " +
                "WHERE action='bought' AND status IN ('PLANNED','PREPARED')", null
        ).use { c ->
            while (c.moveToNext()) {
                val trader = c.getString(0); val token = c.getString(1); val principal = c.getDouble(2)
                pendingPrincipal += principal
                pendingCost += principal + c.getDouble(3)
                byTrader[trader] = byTrader.getOrDefault(trader, 0.0) + principal
                byToken[token] = byToken.getOrDefault(token, 0.0) + principal
                if (c.getLong(4) >= nowMs - 15 * 60_000L) recent += RecentEntry(c.getLong(4), principal)
            }
        }
        return StrategyExposure(pendingCost, pendingPrincipal, byTrader, byToken, recent)
    }

    fun openPositions(db: SQLiteDatabase, trader: String, token: String): List<StrategyPosition> {
        val out = mutableListOf<StrategyPosition>()
        db.rawQuery(
            "SELECT id,trader,token,entry_market_cap,principal,opened_time FROM positions " +
                "WHERE status='OPEN' AND lower(trader)=lower(?) AND lower(token)=lower(?) ORDER BY opened_time,id",
            arrayOf(trader, token)
        ).use { c ->
            while (c.moveToNext()) out += StrategyPosition(
                c.getLong(0), c.getString(1), c.getString(2), c.getDouble(3), c.getDouble(4), c.getLong(5)
            )
        }
        return out
    }

    fun hasActiveSellPlan(db: SQLiteDatabase, trader: String, token: String): Boolean =
        db.rawQuery(
            "SELECT 1 FROM plans WHERE action='sold' AND status IN ('PLANNED','PREPARED') " +
                "AND lower(trader)=lower(?) AND lower(token)=lower(?) LIMIT 1",
            arrayOf(trader, token)
        ).use { it.moveToFirst() }

    fun linkPlanPositions(db: SQLiteDatabase, planKey: String, positions: List<StrategyPosition>) {
        for (position in positions) {
            db.insertOrThrow("plan_positions", null, ContentValues().apply {
                put("plan_notification_key", planKey); put("position_id", position.id)
            })
        }
    }

    fun positionsForPlan(db: SQLiteDatabase, planKey: String): List<StrategyPosition> {
        val out = mutableListOf<StrategyPosition>()
        db.rawQuery(
            "SELECT p.id,p.trader,p.token,p.entry_market_cap,p.principal,p.opened_time " +
                "FROM positions p JOIN plan_positions pp ON pp.position_id=p.id " +
                "WHERE pp.plan_notification_key=? AND p.status='OPEN' ORDER BY p.opened_time,p.id",
            arrayOf(planKey)
        ).use { c ->
            while (c.moveToNext()) out += StrategyPosition(
                c.getLong(0), c.getString(1), c.getString(2), c.getDouble(3), c.getDouble(4), c.getLong(5)
            )
        }
        return out
    }

    fun recordDecision(db: SQLiteDatabase, values: ContentValues) {
        db.insertWithOnConflict("decisions", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun addPlan(db: SQLiteDatabase, plan: StrategyPlan) {
        db.insertOrThrow("plans", null, ContentValues().apply {
            put("notification_key", plan.notificationKey); put("action", plan.action)
            put("trader", plan.trader); put("token", plan.token); put("market_cap", plan.marketCap)
            put("principal", plan.principal); put("gross_value", plan.grossValue); put("fee", plan.fee)
            put("status", plan.status); put("created_time", plan.createdTime); put("updated_time", plan.createdTime)
        })
    }

    fun getPlan(db: SQLiteDatabase, key: String): StrategyPlan? = db.rawQuery(
        "SELECT notification_key,action,trader,token,market_cap,principal,gross_value,fee,status,created_time " +
            "FROM plans WHERE notification_key=?", arrayOf(key)
    ).use { c -> if (c.moveToFirst()) c.toPlan() else null }

    fun setPlanStatus(db: SQLiteDatabase, key: String, status: String) {
        db.update("plans", ContentValues().apply {
            put("status", status); put("updated_time", System.currentTimeMillis())
        }, "notification_key=?", arrayOf(key))
        db.update("decisions", ContentValues().apply { put("execution_status", status) },
            "notification_key=?", arrayOf(key))
    }

    fun cancelOrphanedPlanned(db: SQLiteDatabase) {
        val values = ContentValues().apply {
            put("status", "CANCELLED_AFTER_RESTART")
            put("updated_time", System.currentTimeMillis())
        }
        db.update("plans", values, "status='PLANNED'", null)
        db.update(
            "decisions",
            ContentValues().apply { put("execution_status", "CANCELLED_AFTER_RESTART") },
            "execution_status='PLANNED'",
            null
        )
    }

    fun latestPrepared(db: SQLiteDatabase): StrategyPlan? = db.rawQuery(
        "SELECT notification_key,action,trader,token,market_cap,principal,gross_value,fee,status,created_time " +
            "FROM plans WHERE status='PREPARED' ORDER BY updated_time DESC LIMIT 1", null
    ).use { c -> if (c.moveToFirst()) c.toPlan() else null }

    fun insertPosition(db: SQLiteDatabase, plan: StrategyPlan) {
        db.insertOrThrow("positions", null, ContentValues().apply {
            put("buy_notification_key", plan.notificationKey); put("trader", plan.trader); put("token", plan.token)
            put("entry_market_cap", plan.marketCap); put("principal", plan.principal); put("buy_fee", plan.fee)
            put("opened_time", plan.createdTime); put("status", "OPEN")
        })
    }

    fun closePositions(db: SQLiteDatabase, positions: List<StrategyPosition>, sellKey: String, timeMs: Long) {
        for (position in positions) {
            db.update("positions", ContentValues().apply {
                put("status", "CLOSED"); put("sell_notification_key", sellKey); put("closed_time", timeMs)
            }, "id=?", arrayOf(position.id.toString()))
        }
    }

    fun status(): StrategyStatus = transaction { db ->
        val state = loadState(db, System.currentTimeMillis())
        val open = db.rawQuery("SELECT COUNT(*) FROM positions WHERE status='OPEN'", null).use {
            it.moveToFirst(); it.getInt(0)
        }
        val pending = db.rawQuery("SELECT COUNT(*) FROM plans WHERE status IN ('PLANNED','PREPARED')", null).use {
            it.moveToFirst(); it.getInt(0)
        }
        val latest = latestPrepared(db)?.notificationKey
        val ratios = arrayOf("1:20", "1:15", "1:10")
        StrategyStatus(state.cash, state.openCost, state.equity(), state.mode,
            ratios[state.stageIndex.coerceIn(ratios.indices)], open, pending, latest)
    }

    private fun android.database.Cursor.toPlan() = StrategyPlan(
        getString(0), getString(1), getString(2), getString(3), getDouble(4),
        getDouble(5), getDouble(6), getDouble(7), getString(8), getLong(9)
    )

    companion object {
        const val DB_NAME = "adaptive_hybrid_v2.db"
        private const val DB_VERSION = 1
    }
}
