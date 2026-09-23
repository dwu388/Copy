package com.dwu.fomocontroller.notification

import android.app.Notification
import android.app.PendingIntent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.dwu.fomocontroller.automation.AutomationCoordinator
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.EventDatabase
import com.dwu.fomocontroller.model.ControllerMode
import com.dwu.fomocontroller.model.TradeEvent
import com.dwu.fomocontroller.parsing.TradeParser
import java.util.concurrent.Executors

class FomoNotificationListener : NotificationListenerService() {
    private val io = Executors.newSingleThreadExecutor()
    private lateinit var db: EventDatabase
    private lateinit var prefs: AppPreferences

    override fun onCreate() {
        super.onCreate()
        instance = this
        db = EventDatabase(applicationContext)
        prefs = AppPreferences(applicationContext)
        AutomationCoordinator.initialize(applicationContext)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        io.shutdown()
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != FOMO_PACKAGE) return

        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val normalText = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()
        val selectedText = if (!bigText.isNullOrBlank()) bigText else normalText

        if (selectedText.isBlank()) return

        if (TradeParser.isThesisOnly(selectedText)) {
            io.execute {
                db.upsert(baseEvent(sbn, title, selectedText, "THESIS_IGNORED"))
                cancelNotification(sbn.key)
            }
            return
        }

        if (!TradeParser.isTradeNotification(selectedText)) return

        io.execute {
            val parsed = TradeParser.parse(title, selectedText)
            val complete = parsed.action != null &&
                parsed.trader != null &&
                parsed.coin != null &&
                parsed.marketCap != null &&
                parsed.sourceAmount != null

            if (!complete) {
                db.upsert(
                    baseEvent(
                        sbn, title, selectedText, "PARSE_FAILED",
                        parsed.action, parsed.trader, parsed.coin,
                        parsed.marketCap, parsed.sourceAmount
                    )
                )
                return@execute
            }

            val marketCap = parsed.marketCap!!
            val sourceAmount = parsed.sourceAmount!!
            val copyAmount = sourceAmount * prefs.copyRatio
            val qualified =
                marketCap < prefs.maxMarketCap &&
                    sourceAmount < prefs.maxSourceAmount

            val event = TradeEvent(
                notificationKey = sbn.key,
                notificationId = sbn.id,
                notificationTag = sbn.tag,
                packageName = sbn.packageName,
                postTime = sbn.postTime,
                capturedTime = System.currentTimeMillis(),
                title = title,
                rawText = selectedText,
                action = parsed.action,
                trader = parsed.trader,
                coin = parsed.coin,
                marketCap = marketCap,
                sourceAmount = sourceAmount,
                copyAmount = copyAmount,
                state = if (qualified) "PARSED" else "FILTERED",
                failureReason = if (qualified) null else "Outside configured market-cap/source-amount limits"
            )
            db.upsert(event)

            if (qualified && prefs.mode != ControllerMode.OBSERVE) {
                AutomationCoordinator.enqueue(event)
            }
        }
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

    companion object {
        private const val FOMO_PACKAGE = "family.fomo.app"

        @Volatile private var instance: FomoNotificationListener? = null

        fun openExactNotification(notificationKey: String): Boolean {
            val service = instance ?: return false
            val active = runCatching { service.activeNotifications }.getOrNull() ?: return false
            val sbn = active.firstOrNull { it.key == notificationKey } ?: return false
            val intent = sbn.notification.contentIntent ?: return false

            return try {
                intent.send()
                true
            } catch (_: PendingIntent.CanceledException) {
                false
            }
        }
    }
}
