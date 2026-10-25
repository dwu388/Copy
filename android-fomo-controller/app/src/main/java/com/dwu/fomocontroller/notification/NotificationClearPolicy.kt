package com.dwu.fomocontroller.notification

/**
 * Selected buy/sell notifications stay live through UI verification and
 * screenshot capture. Every trade requires a durable raw-recorder row before it
 * can be cleared. Missing controller-state rows are safe after durable capture,
 * so clearing the dashboard event log cannot strand notifications.
 *
 * Non-trade Fomo notifications are handled directly by the listener and do not
 * use this policy.
 */
object NotificationClearPolicy {
    private val notificationStillNeededStates = setOf(
        "ADAPTIVE_SELECTED",
        "QUEUED",
        "OPENING",
        "VERIFYING",
        "SCREENSHOT_PENDING",
        "BUY_FIND_ENTRY",
        "SELL_FIND_ENTRY",
        "BUY_FILL_AMOUNT",
        "SELL_FILL_AMOUNT"
    )

    fun isSafeToClear(state: String?, rawTradeRecorded: Boolean): Boolean =
        rawTradeRecorded && state !in notificationStillNeededStates
}
