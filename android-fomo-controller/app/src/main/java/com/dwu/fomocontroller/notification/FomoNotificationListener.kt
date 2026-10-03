package com.dwu.fomocontroller.notification

import android.app.Notification
import android.app.PendingIntent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.dwu.fomocontroller.automation.AutomationCoordinator
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.EventDatabase
import com.dwu.fomocontroller.model.ControllerMode
import com.dwu.fomocontroller.model.RecordedNotification
import com.dwu.fomocontroller.model.TradeEvent
import com.dwu.fomocontroller.parsing.TradeParser
import com.dwu.fomocontroller.strategy.AdaptiveHybridEngine
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class FomoNotificationListener : NotificationListenerService() {
    private val io = Executors.newSingleThreadExecutor()
    private val cleanup = Executors.newSingleThreadScheduledExecutor()
    private val processingTradeKeys = ConcurrentHashMap.newKeySet<String>()
    private lateinit var db: EventDatabase
    private lateinit var prefs: AppPreferences

    override fun onCreate() {
        super.onCreate()
        instance = this
        db = EventDatabase(applicationContext)
        prefs = AppPreferences(applicationContext)
        AutomationCoordinator.initialize(applicationContext)
        AdaptiveHybridEngine.initialize(applicationContext)
        cleanup.scheduleWithFixedDelay(
            ::clearHandledNotifications,
            CLEANUP_INTERVAL_MINUTES,
            CLEANUP_INTERVAL_MINUTES,
            TimeUnit.MINUTES
        )
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        cleanup.execute(::clearHandledNotifications)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        cleanup.shutdownNow()
        io.shutdown()
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != FOMO_PACKAGE) return

        val notification = sbn.notification
        val fields = readFields(notification)
        val title = fields.title
        val selectedText = fields.selectedText

        if (selectedText.isBlank()) {
            cancelSafely(sbn.key, "blank/non-trade Fomo notification")
            return
        }

        val action = TradeParser.findAction(selectedText)
        if (action == null) {
            cancelSafely(sbn.key, "non-trade Fomo notification")
            if (TradeParser.isThesisOnly(selectedText)) {
                io.execute {
                    runCatching {
                        db.upsert(baseEvent(sbn, title, selectedText, "THESIS_IGNORED"))
                    }.onFailure { error ->
                        Log.w(TAG, "Could not record thesis-only notification ${sbn.key}", error)
                    }
                }
            }
            return
        }

        val capturedTime = System.currentTimeMillis()
        processingTradeKeys += sbn.key

        io.execute {
            try {
                val parsed = TradeParser.parse(title, selectedText)
                val raw = RecordedNotification(
                    notificationKey = sbn.key,
                    notificationId = sbn.id,
                    notificationTag = sbn.tag,
                    packageName = sbn.packageName,
                    postTime = sbn.postTime,
                    capturedTime = capturedTime,
                    title = title,
                    normalText = fields.normalText,
                    bigText = fields.bigText,
                    selectedText = selectedText,
                    action = action,
                    trader = parsed.trader,
                    coin = parsed.coin,
                    marketCap = parsed.marketCap,
                    sourceAmount = parsed.sourceAmount,
                    channelId = notification.channelId,
                    category = notification.category,
                    groupKey = sbn.groupKey,
                    hasContentIntent = notification.contentIntent != null,
                    notificationActionCount = notification.actions?.size ?: 0
                )

                runCatching { db.recordNotification(raw) }
                    .onFailure { error ->
                        Log.e(
                            TAG,
                            "Raw buy/sell capture failed; notification remains visible: ${sbn.key}",
                            error
                        )
                    }
                    .getOrElse { return@execute }

                val complete = parsed.action != null &&
                    parsed.trader != null &&
                    parsed.coin != null &&
                    parsed.marketCap != null &&
                    parsed.sourceAmount != null

                if (!complete) {
                    runCatching {
                        db.upsert(
                            baseEvent(
                                sbn, title, selectedText, "PARSE_FAILED",
                                parsed.action, parsed.trader, parsed.coin,
                                parsed.marketCap, parsed.sourceAmount
                            )
                        )
                    }.onFailure { error ->
                        Log.w(TAG, "Could not record parse failure for ${sbn.key}", error)
                    }
                    cancelSafely(sbn.key, "durably captured buy/sell with parse failure")
                    return@execute
                }

                val marketCap = parsed.marketCap!!
                val sourceAmount = parsed.sourceAmount!!
                val decision = runCatching {
                    AdaptiveHybridEngine.evaluate(
                        notificationKey = sbn.key,
                        action = parsed.action!!,
                        trader = parsed.trader!!,
                        token = parsed.coin!!,
                        marketCap = marketCap,
                        sourceAmount = sourceAmount,
                        eventTimeMs = sbn.postTime,
                        reserveForExecution = prefs.mode != ControllerMode.OBSERVE
                    )
                }.getOrElse { error ->
                    runCatching {
                        db.upsert(
                            baseEvent(
                                sbn, title, selectedText, "MODEL_FAILED",
                                parsed.action, parsed.trader, parsed.coin, marketCap, sourceAmount
                            ).copy(failureReason = "Adaptive Hybrid v2 failed closed: ${error.message}")
                        )
                    }.onFailure { dbError ->
                        Log.w(TAG, "Could not record model failure for ${sbn.key}", dbError)
                    }
                    cancelSafely(sbn.key, "durably captured buy/sell with model failure")
                    return@execute
                }

                val event = TradeEvent(
                    notificationKey = sbn.key,
                    notificationId = sbn.id,
                    notificationTag = sbn.tag,
                    packageName = sbn.packageName,
                    postTime = sbn.postTime,
                    capturedTime = capturedTime,
                    title = title,
                    rawText = selectedText,
                    action = parsed.action,
                    trader = parsed.trader,
                    coin = parsed.coin,
                    marketCap = marketCap,
                    sourceAmount = sourceAmount,
                    copyAmount = decision.copyAmount,
                    state = when {
                        !decision.execute -> "FILTERED"
                        prefs.mode == ControllerMode.OBSERVE -> "ADAPTIVE_OBSERVED"
                        else -> "ADAPTIVE_SELECTED"
                    },
                    failureReason = decision.auditSummary()
                )
                db.upsert(event)

                if (decision.execute && prefs.mode != ControllerMode.OBSERVE) {
                    AutomationCoordinator.enqueue(event)
                } else {
                    cancelSafely(sbn.key, "durably captured buy/sell no longer needed for opening")
                }
            } catch (error: Throwable) {
                Log.e(TAG, "Unexpected buy/sell processing failure for ${sbn.key}", error)
                if (runCatching { db.hasRecordedNotification(sbn.key) }.getOrDefault(false)) {
                    cancelSafely(sbn.key, "durably captured buy/sell after unexpected failure")
                }
            } finally {
                processingTradeKeys -= sbn.key
            }
        }
    }

    private fun clearHandledNotifications() {
        val active = runCatching { activeNotifications.toList() }
            .onFailure { error -> Log.w(TAG, "Could not read active notifications", error) }
            .getOrElse { return }

        active.asSequence()
            .filter { it.packageName == FOMO_PACKAGE }
            .forEach { activeNotification ->
                runCatching {
                    if (activeNotification.key in processingTradeKeys) return@runCatching
                    val action = TradeParser.findAction(
                        readFields(activeNotification.notification).selectedText
                    )
                    if (action == null || db.isSafeToClearNotification(activeNotification.key)) {
                        cancelNotification(activeNotification.key)
                    }
                }.onFailure { error ->
                    Log.w(
                        TAG,
                        "Cleanup failed for ${activeNotification.key}; later sweeps will retry",
                        error
                    )
                }
            }
    }

    private fun cancelSafely(notificationKey: String, reason: String) {
        runCatching { cancelNotification(notificationKey) }
            .onFailure { error ->
                Log.w(TAG, "Could not clear $notificationKey ($reason)", error)
            }
    }

    private fun readFields(notification: Notification): NotificationFields {
        val extras = notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val normalText = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?.toString()
            ?.takeIf { it.isNotBlank() }
        val textLines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.joinToString("\n") { it.toString() }
            ?.takeIf { it.isNotBlank() }
        val selectedText = when {
            !bigText.isNullOrBlank() -> bigText
            normalText.isNotBlank() -> normalText
            !textLines.isNullOrBlank() -> textLines
            else -> ""
        }
        return NotificationFields(title, normalText, bigText, selectedText)
    }

    private fun baseEvent(
        sbn: StatusBarNotification,
        title: String,
        text: String,
        state: String,
        action: String? = null,
        trader: String? = null,
        coin: String? = null,
        marketCap: Double? = null,
        sourceAmount: Double? = null
    ) = TradeEvent(
        notificationKey = sbn.key,
        notificationId = sbn.id,
        notificationTag = sbn.tag,
        packageName = sbn.packageName,
        postTime = sbn.postTime,
        capturedTime = System.currentTimeMillis(),
        title = title,
        rawText = text,
        action = action,
        trader = trader,
        coin = coin,
        marketCap = marketCap,
        sourceAmount = sourceAmount,
        copyAmount = sourceAmount?.times(prefs.copyRatio),
        state = state
    )

    private data class NotificationFields(
        val title: String,
        val normalText: String,
        val bigText: String?,
        val selectedText: String
    )

    companion object {
        private const val TAG = "FomoNotificationClear"
        private const val FOMO_PACKAGE = "family.fomo.app"
        private const val CLEANUP_INTERVAL_MINUTES = 3L

        @Volatile private var instance: FomoNotificationListener? = null

        fun openExactNotification(notificationKey: String): Boolean {
            val service = instance ?: return false
            val active = runCatching { service.activeNotifications }.getOrNull() ?: return false
            val sbn = active.firstOrNull { it.key == notificationKey } ?: return false
            val intent = sbn.notification.contentIntent ?: return false

            return try {
                intent.send()
                service.cancelSafely(notificationKey, "exact content intent invoked")
                true
            } catch (_: PendingIntent.CanceledException) {
                false
            }
        }

        fun clearExactNotification(notificationKey: String): Boolean {
            val service = instance ?: return false
            return runCatching {
                service.cancelNotification(notificationKey)
                true
            }.onFailure { error ->
                Log.w(TAG, "Could not clear exact notification $notificationKey", error)
            }.getOrDefault(false)
        }
    }
}
