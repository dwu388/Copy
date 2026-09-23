package com.dwu.fomocontroller.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.dwu.fomocontroller.model.TradeEvent

class EventDatabase(context: Context) :
    SQLiteOpenHelper(context, "fomo_controller.db", null, 1) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE events (
                notification_key TEXT PRIMARY KEY,
                notification_id INTEGER NOT NULL,
                notification_tag TEXT,
                package_name TEXT NOT NULL,
                post_time INTEGER NOT NULL,
                captured_time INTEGER NOT NULL,
                title TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                action TEXT,
                trader TEXT,
                coin TEXT,
                market_cap REAL,
                source_amount REAL,
                copy_amount REAL,
                state TEXT NOT NULL,
                failure_reason TEXT,
                updated_time INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_events_state_time ON events(state, captured_time)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun upsert(event: TradeEvent) {
        writableDatabase.insertWithOnConflict(
            "events",
            null,
            event.toValues(),
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    fun updateState(key: String, state: String, failureReason: String? = null) {
        val values = ContentValues().apply {
            put("state", state)
            put("failure_reason", failureReason)
            put("updated_time", System.currentTimeMillis())
        }
        writableDatabase.update(
            "events",
            values,
            "notification_key = ?",
            arrayOf(key)
        )
    }

    fun get(key: String): TradeEvent? {
        readableDatabase.query(
            "events",
            null,
            "notification_key = ?",
            arrayOf(key),
            null,
            null,
            null,
            "1"
        ).use { cursor ->
            return if (cursor.moveToFirst()) cursor.toTradeEvent() else null
        }
    }

    fun recent(limit: Int = 40): List<TradeEvent> {
        readableDatabase.query(
            "events",
            null,
            null,
            null,
            null,
            null,
            "captured_time DESC",
            limit.coerceIn(1, 200).toString()
        ).use { cursor ->
            val out = mutableListOf<TradeEvent>()
            while (cursor.moveToNext()) out += cursor.toTradeEvent()
            return out
        }
    }

    fun clear() {
        writableDatabase.delete("events", null, null)
    }

    private fun TradeEvent.toValues() = ContentValues().apply {
        put("notification_key", notificationKey)
        put("notification_id", notificationId)
        put("notification_tag", notificationTag)
        put("package_name", packageName)
        put("post_time", postTime)
        put("captured_time", capturedTime)
        put("title", title)
        put("raw_text", rawText)
        put("action", action)
        put("trader", trader)
        put("coin", coin)
        put("market_cap", marketCap)
        put("source_amount", sourceAmount)
        put("copy_amount", copyAmount)
        put("state", state)
        put("failure_reason", failureReason)
        put("updated_time", updatedTime)
    }

    private fun Cursor.toTradeEvent() = TradeEvent(
        notificationKey = getString(getColumnIndexOrThrow("notification_key")),
        notificationId = getInt(getColumnIndexOrThrow("notification_id")),
        notificationTag = getStringOrNull("notification_tag"),
        packageName = getString(getColumnIndexOrThrow("package_name")),
        postTime = getLong(getColumnIndexOrThrow("post_time")),
        capturedTime = getLong(getColumnIndexOrThrow("captured_time")),
        title = getString(getColumnIndexOrThrow("title")),
        rawText = getString(getColumnIndexOrThrow("raw_text")),
        action = getStringOrNull("action"),
        trader = getStringOrNull("trader"),
        coin = getStringOrNull("coin"),
        marketCap = getDoubleOrNull("market_cap"),
        sourceAmount = getDoubleOrNull("source_amount"),
        copyAmount = getDoubleOrNull("copy_amount"),
        state = getString(getColumnIndexOrThrow("state")),
        failureReason = getStringOrNull("failure_reason"),
        updatedTime = getLong(getColumnIndexOrThrow("updated_time"))
    )

    private fun Cursor.getStringOrNull(column: String): String? {
        val i = getColumnIndexOrThrow(column)
        return if (isNull(i)) null else getString(i)
    }

    private fun Cursor.getDoubleOrNull(column: String): Double? {
        val i = getColumnIndexOrThrow(column)
        return if (isNull(i)) null else getDouble(i)
    }
}
