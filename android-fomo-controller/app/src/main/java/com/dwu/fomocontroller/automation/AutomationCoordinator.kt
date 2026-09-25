package com.dwu.fomocontroller.automation

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityNodeInfo
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.EventDatabase
import com.dwu.fomocontroller.model.ControllerMode
import com.dwu.fomocontroller.model.TradeEvent
import com.dwu.fomocontroller.notification.FomoNotificationListener
import com.dwu.fomocontroller.strategy.AdaptiveHybridEngine
import java.util.ArrayDeque

object AutomationCoordinator {
    private const val OPEN_TIMEOUT_MS = 8_000L
    private const val STEP_TIMEOUT_MS = 8_000L

    private val handler = Handler(Looper.getMainLooper())
    private val queue = ArrayDeque<String>()

    @Volatile private var activeKey: String? = null
    @Volatile private var activeStage: String = "IDLE"

    private var deadline: Long = 0L
    private var initialized = false
    private lateinit var db: EventDatabase
    private lateinit var prefs: AppPreferences

    @Synchronized
    fun initialize(context: Context) {
        if (initialized) return
        db = EventDatabase(context.applicationContext)
        prefs = AppPreferences(context.applicationContext)
        AdaptiveHybridEngine.initialize(context.applicationContext)
        initialized = true
    }

    @Synchronized
    fun enqueue(event: TradeEvent) {
        ensureInitialized()
        if (prefs.mode == ControllerMode.OBSERVE) return
        if (queue.contains(event.notificationKey) || activeKey == event.notificationKey) return
        queue.addLast(event.notificationKey)
        db.updateState(event.notificationKey, "QUEUED")
        startNextLocked()
    }

    @Synchronized
    fun onFomoUiChanged(root: AccessibilityNodeInfo?) {
        ensureInitialized()
        val key = activeKey ?: return
        val event = db.get(key) ?: return
        val now = System.currentTimeMillis()

        if (now > deadline) {
            failLocked(key, "UI_TIMEOUT", "Timed out during $activeStage")
            return
        }

        when (activeStage) {
            "VERIFYING" -> {
                val expectedCoin = event.coin ?: run {
                    failLocked(key, "PAGE_MISMATCH", "Parsed event has no coin")
                    return
                }

                if (!AccessibilityTools.containsText(root, expectedCoin)) return

                if (prefs.mode == ControllerMode.DRY_RUN) {
                    val ledgerResult = AdaptiveHybridEngine.recordPaperExecution(key)
                    if (!ledgerResult.startsWith("Recorded ")) {
                        failLocked(key, "PAPER_LEDGER_FAILED", ledgerResult)
                        return
                    }
                    finishLocked(key, "DRY_RUN_VERIFIED")
                    return
                }

                if (prefs.mode != ControllerMode.PREPARE) {
                    finishLocked(key, "OBSERVED")
                    return
                }

                if (!FomoSelectors.calibrated) {
                    failLocked(
                        key,
                        "SELECTORS_NOT_CALIBRATED",
                        "Configure stable Fomo resource IDs before PREPARE mode"
                    )
                    return
                }

                if (event.action == "bought") {
                    activeStage = "BUY_FIND_ENTRY"
                    db.updateState(key, activeStage)
                } else if (event.action == "sold") {
                    activeStage = "SELL_FIND_ENTRY"
                    db.updateState(key, activeStage)
                } else {
                    failLocked(key, "UI_ACTION_FAILED", "Unknown action ${event.action}")
                }
                deadline = now + STEP_TIMEOUT_MS
            }

            "BUY_FIND_ENTRY" -> {
                val button = AccessibilityTools.findById(root, FomoSelectors.BUY_ENTRY_RESOURCE_ID)
                    ?: return
                if (!AccessibilityTools.click(button)) {
                    failLocked(key, "UI_ACTION_FAILED", "Could not click calibrated Buy entry control")
                    return
                }
                activeStage = "BUY_FILL_AMOUNT"
                db.updateState(key, activeStage)
                deadline = now + STEP_TIMEOUT_MS
            }

            "SELL_FIND_ENTRY" -> {
                val button = AccessibilityTools.findById(root, FomoSelectors.SELL_ENTRY_RESOURCE_ID)
                    ?: return
                if (!AccessibilityTools.click(button)) {
                    failLocked(key, "UI_ACTION_FAILED", "Could not click calibrated Sell entry control")
                    return
                }
                activeStage = "SELL_FILL_AMOUNT"
                db.updateState(key, activeStage)
                deadline = now + STEP_TIMEOUT_MS
            }

            "BUY_FILL_AMOUNT", "SELL_FILL_AMOUNT" -> {
                val input = AccessibilityTools.findById(root, FomoSelectors.AMOUNT_INPUT_RESOURCE_ID)
                    ?: return
                val amount = event.copyAmount ?: run {
                    failLocked(key, "UI_ACTION_FAILED", "No calculated copy amount")
                    return
                }

                if (!AccessibilityTools.setText(input, formatAmount(amount))) {
                    failLocked(key, "UI_ACTION_FAILED", "Could not populate calibrated amount input")
                    return
                }

                AdaptiveHybridEngine.markPrepared(key)
                finishLocked(key, if (activeStage == "BUY_FILL_AMOUNT") "PREPARED_BUY" else "PREPARED_SELL")
            }
        }
    }

    @Synchronized
    fun status(): String =
        "stage=$activeStage active=${activeKey ?: "none"} queued=${queue.size}"

    @Synchronized
    fun pauseAndClearQueue() {
        activeKey?.let(AdaptiveHybridEngine::cancelUnexecuted)
        queue.forEach(AdaptiveHybridEngine::cancelUnexecuted)
        queue.clear()
        activeKey = null
        activeStage = "IDLE"
        deadline = 0L
    }

    @Synchronized
    private fun startNextLocked() {
        if (activeKey != null) return
        val key = if (queue.isEmpty()) null else queue.removeFirst()
        if (key == null) return
        val event = db.get(key)
        if (event == null) {
            handler.post { synchronized(this) { startNextLocked() } }
            return
        }

        val ageMs = System.currentTimeMillis() - event.postTime
        if (ageMs > prefs.maxEventAgeSeconds * 1000L) {
            db.updateState(key, "EXPIRED", "Event exceeded configured max age")
            AdaptiveHybridEngine.cancelUnexecuted(key)
            handler.post { synchronized(this) { startNextLocked() } }
            return
        }

        activeKey = key
        activeStage = "OPENING"
        deadline = System.currentTimeMillis() + OPEN_TIMEOUT_MS
        db.updateState(key, "OPENING")

        if (!FomoNotificationListener.openExactNotification(key)) {
            failLocked(key, "INTENT_UNAVAILABLE", "Live notification/contentIntent was not available")
            return
        }

        activeStage = "VERIFYING"
        db.updateState(key, "VERIFYING")
        deadline = System.currentTimeMillis() + OPEN_TIMEOUT_MS
    }

    @Synchronized
    private fun finishLocked(key: String, state: String) {
        db.updateState(key, state)
        activeKey = null
        activeStage = "IDLE"
        deadline = 0L
        handler.post { synchronized(this) { startNextLocked() } }
    }

    @Synchronized
    private fun failLocked(key: String, state: String, reason: String) {
        db.updateState(key, state, reason)
        AdaptiveHybridEngine.cancelUnexecuted(key)
        activeKey = null
        activeStage = "IDLE"
        deadline = 0L
        handler.post { synchronized(this) { startNextLocked() } }
    }

    private fun formatAmount(value: Double): String =
        if (value % 1.0 == 0.0) value.toLong().toString()
        else "%.2f".format(java.util.Locale.US, value)

    private fun ensureInitialized() {
        check(initialized) { "AutomationCoordinator.initialize(context) must be called first" }
    }
}
