package com.dwu.fomocontroller.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.dwu.fomocontroller.strategy.TraderGuardAssessment
import com.dwu.fomocontroller.strategy.TraderGuardMode
import com.dwu.fomocontroller.strategy.TraderGuardOutcome
import com.dwu.fomocontroller.strategy.TraderWinRateGuard as Guard
import java.io.File

/** Separate append-only outcome/audit history; never alters the strategy wallet. */
class TraderGuardDatabase(context: Context) : SQLiteOpenHelper(context, "trader_guard.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""CREATE TABLE buys (
            opportunity_id TEXT PRIMARY KEY, notification_key TEXT NOT NULL,
            trader TEXT NOT NULL, token TEXT NOT NULL, entry_cap REAL NOT NULL,
            source_usd REAL NOT NULL, opened_time INTEGER NOT NULL, deadline INTEGER NOT NULL,
            episode_id TEXT, closed_time INTEGER)""")
        db.execSQL("CREATE INDEX idx_guard_open ON buys(episode_id,deadline,trader,token)")
        db.execSQL("""CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id TEXT NOT NULL UNIQUE,
            trader TEXT NOT NULL, token TEXT NOT NULL, known_time INTEGER NOT NULL,
            outcome_kind TEXT NOT NULL, net_reference_usd REAL NOT NULL,
            lot_count INTEGER NOT NULL, first_buy_time INTEGER NOT NULL, last_buy_time INTEGER NOT NULL)""")
        db.execSQL("CREATE INDEX idx_guard_trader_time ON episodes(trader,known_time,id)")
        db.execSQL("""CREATE TABLE assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id TEXT NOT NULL,
            notification_key TEXT NOT NULL, trader TEXT NOT NULL, assessed_time INTEGER NOT NULL,
            guard_mode TEXT NOT NULL, status TEXT NOT NULL, samples INTEGER NOT NULL,
            wins INTEGER NOT NULL, win_rate REAL, threshold REAL NOT NULL,
            allowed INTEGER NOT NULL, guard_version TEXT NOT NULL)""")
    }
    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    private fun <T> transaction(block: (SQLiteDatabase) -> T): T {
        val sql = writableDatabase
        sql.beginTransaction()
        return try { val result = block(sql); sql.setTransactionSuccessful(); result }
        finally { sql.endTransaction() }
    }

    fun observeBuy(key: String, sourceTime: Long, trader: String, token: String,
                   entryCap: Double, sourceUsd: Double, observedTime: Long,
                   mode: TraderGuardMode, threshold: Double): TraderGuardAssessment = transaction { sql ->
        val normalized = Guard.normalize(trader)
        val id = "$key@$sourceTime"
        sql.insertWithOnConflict("buys", null, ContentValues().apply {
            put("opportunity_id", id); put("notification_key", key)
            put("trader", normalized); put("token", Guard.normalize(token))
            put("entry_cap", entryCap); put("source_usd", sourceUsd)
            put("opened_time", observedTime); put("deadline", Math.addExact(observedTime, Guard.HOLD_MS))
        }, SQLiteDatabase.CONFLICT_IGNORE)
        val outcomes = mutableListOf<TraderGuardOutcome>()
        sql.rawQuery("SELECT known_time,net_reference_usd FROM episodes " +
            "WHERE trader=? AND known_time<? ORDER BY known_time DESC,id DESC LIMIT ?",
            arrayOf(normalized, observedTime.toString(), Guard.WINDOW.toString())).use { c ->
            while (c.moveToNext()) outcomes += TraderGuardOutcome(c.getLong(0), c.getDouble(1))
        }
        val assessment = Guard.assess(mode, Guard.statistics(outcomes, observedTime), threshold)
        sql.insertOrThrow("assessments", null, ContentValues().apply {
            put("opportunity_id", id); put("notification_key", key); put("trader", normalized)
            put("assessed_time", observedTime); put("guard_mode", mode.name)
            put("status", assessment.status); put("samples", assessment.stats.samples)
            put("wins", assessment.stats.wins); put("win_rate", assessment.stats.winRate)
            put("threshold", threshold); put("allowed", if (assessment.allowed) 1 else 0)
            put("guard_version", Guard.VERSION)
        })
        assessment
    }

    private data class Buy(val id: String, val trader: String, val token: String,
                           val entryCap: Double, val sourceUsd: Double, val opened: Long)
    private fun openBuys(sql: SQLiteDatabase, where: String, args: Array<String>): List<Buy> {
        val rows = mutableListOf<Buy>()
        sql.rawQuery("SELECT opportunity_id,trader,token,entry_cap,source_usd,opened_time FROM buys " +
            "WHERE episode_id IS NULL AND $where ORDER BY opened_time,opportunity_id", args).use { c ->
            while (c.moveToNext()) rows += Buy(c.getString(0), c.getString(1), c.getString(2),
                c.getDouble(3), c.getDouble(4), c.getLong(5))
        }
        return rows
    }
    private fun settle(sql: SQLiteDatabase, buys: List<Buy>, key: String,
                       time: Long, cap: Double?, kind: String) {
        if (buys.isEmpty()) return
        val id = "$key|${buys.joinToString(",") { it.id }}"
        val net = buys.sumOf { Guard.netReferenceUsd(it.sourceUsd, it.entryCap, cap) }
        check(net.isFinite())
        sql.insertOrThrow("episodes", null, ContentValues().apply {
            put("episode_id", id); put("trader", buys.first().trader); put("token", buys.first().token)
            put("known_time", time); put("outcome_kind", kind); put("net_reference_usd", net)
            put("lot_count", buys.size); put("first_buy_time", buys.minOf { it.opened })
            put("last_buy_time", buys.maxOf { it.opened })
        })
        for (buy in buys) check(sql.update("buys", ContentValues().apply {
            put("episode_id", id); put("closed_time", time)
        }, "opportunity_id=? AND episode_id IS NULL", arrayOf(buy.id)) == 1)
    }

    /** Called after expiry and before source-sell strategy processing; never gates sells. */
    fun observeSell(key: String, trader: String, token: String, cap: Double, now: Long) = transaction { sql ->
        val rows = openBuys(sql, "trader=? AND token=? AND opened_time<? AND deadline>=?",
            arrayOf(Guard.normalize(trader), Guard.normalize(token), now.toString(), now.toString()))
        settle(sql, rows, "source:$key", now, cap, "SOURCE_SELL")
    }

    /** All controller modes learn timeouts. Actual sweep time is the availability time. */
    fun expire(now: Long, freshQuote: (String, String) -> Double?): Int = transaction { sql ->
        val rows = openBuys(sql, "deadline<?", arrayOf(now.toString()))
        for (group in rows.groupBy { it.trader to it.token }.values) {
            val cap = freshQuote(group.first().trader, group.first().token)
            settle(sql, group, "timeout:$now", now, cap,
                if (cap == null) "TIMEOUT_ASSUMED_LOSS" else "TIMEOUT_MARK")
        }
        rows.size
    }

    fun export(directory: File): Pair<Int, Int> {
        directory.mkdirs()
        return exportTable("assessments", File(directory, "trader_guard_assessments.csv")) to
            exportTable("episodes", File(directory, "trader_guard_episodes.csv"))
    }

    fun reset() = transaction { sql ->
        sql.delete("assessments", null, null)
        sql.delete("episodes", null, null)
        sql.delete("buys", null, null)
    }
    private fun exportTable(table: String, file: File): Int {
        require(table == "assessments" || table == "episodes")
        var rows = 0
        readableDatabase.rawQuery("SELECT * FROM $table ORDER BY id", null).use { c ->
            file.bufferedWriter().use { out ->
                fun csv(s: String) = "\"" + s.replace("\"", "\"\"") + "\""
                out.appendLine(c.columnNames.joinToString(",") { csv(it) })
                while (c.moveToNext()) {
                    out.appendLine((0 until c.columnCount).joinToString(",") {
                        csv(if (c.isNull(it)) "" else c.getString(it))
                    }); rows++
                }
            }
        }
        return rows
    }
}
