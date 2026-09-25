package com.dwu.fomocontroller.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.dwu.fomocontroller.model.RecordedNotification
import com.dwu.fomocontroller.model.RecorderStats
import com.dwu.fomocontroller.model.TradeEvent
import java.io.File

class EventDatabase(context: Context) :
    SQLiteOpenHelper(context, DB_NAME, null, DB_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        createControllerEventsTable(db)
        createRawRecorderTable(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            createRawRecorderTable(db)
        }
    }

    private fun createControllerEventsTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS events (
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
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_events_state_time ON events(state, captured_time)")
    }

    private fun createRawRecorderTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS recorded_notifications (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_key TEXT NOT NULL,
                notification_id INTEGER NOT NULL,
                notification_tag TEXT,
                package_name TEXT NOT NULL,
                post_time INTEGER NOT NULL,
                captured_time INTEGER NOT NULL,
                title TEXT NOT NULL,
                normal_text TEXT NOT NULL,
                big_text TEXT,
                selected_text TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('bought', 'sold')),
                trader TEXT,
                coin TEXT,
                market_cap REAL,
                source_amount REAL,
                channel_id TEXT,
                category TEXT,
                group_key TEXT,
                has_content_intent INTEGER NOT NULL,
                notification_action_count INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS idx_recorded_notifications_post_time " +
                "ON recorded_notifications(post_time)"
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS idx_recorded_notifications_action " +
                "ON recorded_notifications(action)"
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS idx_recorded_notifications_trader_coin " +
                "ON recorded_notifications(trader, coin)"
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS idx_recorded_notifications_key " +
                "ON recorded_notifications(notification_key)"
        )
    }

    fun recordNotification(event: RecordedNotification): Long {
        val values = ContentValues().apply {
            put("notification_key", event.notificationKey)
            put("notification_id", event.notificationId)
            put("notification_tag", event.notificationTag)
            put("package_name", event.packageName)
            put("post_time", event.postTime)
            put("captured_time", event.capturedTime)
            put("title", event.title)
            put("normal_text", event.normalText)
            put("big_text", event.bigText)
            put("selected_text", event.selectedText)
            put("action", event.action)
            put("trader", event.trader)
            put("coin", event.coin)
            put("market_cap", event.marketCap)
            put("source_amount", event.sourceAmount)
            put("channel_id", event.channelId)
            put("category", event.category)
            put("group_key", event.groupKey)
            put("has_content_intent", if (event.hasContentIntent) 1 else 0)
            put("notification_action_count", event.notificationActionCount)
        }
        return writableDatabase.insertOrThrow("recorded_notifications", null, values)
    }

    fun recorderStats(): RecorderStats {
        readableDatabase.rawQuery(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN action = 'bought' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN action = 'sold' THEN 1 ELSE 0 END), 0),
                MAX(post_time)
            FROM recorded_notifications
            """.trimIndent(),
            null
        ).use { cursor ->
            cursor.moveToFirst()
            return RecorderStats(
                total = cursor.getLong(0),
                buys = cursor.getLong(1),
                sells = cursor.getLong(2),
                latestPostTime = if (cursor.isNull(3)) null else cursor.getLong(3)
            )
        }
    }

    fun recentRecorded(limit: Int = 40): List<RecordedNotification> {
        readableDatabase.query(
            "recorded_notifications",
            null,
            null,
            null,
            null,
            null,
            "event_id DESC",
            limit.coerceIn(1, 500).toString()
        ).use { cursor ->
            val out = mutableListOf<RecordedNotification>()
            while (cursor.moveToNext()) out += cursor.toRecordedNotification()
            return out
        }
    }

    fun exportRecordedCsv(destination: File): Int {
        destination.parentFile?.mkdirs()
        var rows = 0
        destination.bufferedWriter().use { out ->
            out.appendLine(
                listOf(
                    "event_id",
                    "notification_key",
                    "notification_id",
                    "notification_tag",
                    "package_name",
                    "post_time",
                    "captured_time",
                    "title",
                    "normal_text",
                    "big_text",
                    "selected_text",
                    "action",
                    "trader",
                    "coin",
                    "market_cap",
                    "source_amount",
                    "channel_id",
                    "category",
                    "group_key",
                    "has_content_intent",
                    "notification_action_count"
                ).joinToString(",")
            )
            readableDatabase.rawQuery(
                "SELECT * FROM recorded_notifications ORDER BY event_id",
                null
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val values = (0 until cursor.columnCount).map { index ->
                        if (cursor.isNull(index)) "" else cursor.getString(index)
                    }
                    out.appendLine(values.joinToString(",") { csv(it) })
                    rows += 1
                }
            }
        }
        return rows
    }

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

    private fun Cursor.toRecordedNotification() = RecordedNotification(
        eventId = getLong(getColumnIndexOrThrow("event_id")),
        notificationKey = getString(getColumnIndexOrThrow("notification_key")),
        notificationId = getInt(getColumnIndexOrThrow("notification_id")),
        notificationTag = getStringOrNull("notification_tag"),
        packageName = getString(getColumnIndexOrThrow("package_name")),
        postTime = getLong(getColumnIndexOrThrow("post_time")),
        capturedTime = getLong(getColumnIndexOrThrow("captured_time")),
        title = getString(getColumnIndexOrThrow("title")),
        normalText = getString(getColumnIndexOrThrow("normal_text")),
        bigText = getStringOrNull("big_text"),
        selectedText = getString(getColumnIndexOrThrow("selected_text")),
        action = getString(getColumnIndexOrThrow("action")),
        trader = getStringOrNull("trader"),
        coin = getStringOrNull("coin"),
        marketCap = getDoubleOrNull("market_cap"),
        sourceAmount = getDoubleOrNull("source_amount"),
        channelId = getStringOrNull("channel_id"),
        category = getStringOrNull("category"),
        groupKey = getStringOrNull("group_key"),
        hasContentIntent = getInt(getColumnIndexOrThrow("has_content_intent")) != 0,
        notificationActionCount = getInt(getColumnIndexOrThrow("notification_action_count"))
    )

    private fun csv(value: String): String =
        "\"" + value.replace("\"", "\"\"") + "\""

    private fun Cursor.getStringOrNull(column: String): String? {
        val i = getColumnIndexOrThrow(column)
        return if (isNull(i)) null else getString(i)
    }

    private fun Cursor.getDoubleOrNull(column: String): Double? {
        val i = getColumnIndexOrThrow(column)
        return if (isNull(i)) null else getDouble(i)
    }

    companion object {
        const val DB_NAME = "fomo_controller.db"
        private const val DB_VERSION = 2
    }
}
