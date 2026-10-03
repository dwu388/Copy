package com.dwu.fomocontroller.notification

/**
 * Buy/sell notifications stay live only while their exact content intent may
 * still be needed. Every trade requires a durable raw-recorder row before it
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
        "OPENING"
    )

    fun isSafeToClear(state: String?, rawTradeRecorded: Boolean): Boolean =
        rawTradeRecorded && state !in notificationStillNeededStates
}
