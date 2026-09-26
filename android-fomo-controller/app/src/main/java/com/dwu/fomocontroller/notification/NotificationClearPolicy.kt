package com.dwu.fomocontroller.notification

/**
 * Fail-closed policy for removing Fomo notifications from Android's active tray.
 *
 * Bought/sold notifications require both a durable raw-recorder row and a
 * terminal successful controller state. Thesis-only notifications are recorded
 * directly in the controller event table and can be cleared without a raw
 * buy/sell row.
 */
object NotificationClearPolicy {
    private val handledTradeStates = setOf(
        "FILTERED",
        "ADAPTIVE_OBSERVED",
        "OBSERVED",
        "DRY_RUN_VERIFIED",
        "PREPARED_BUY",
        "PREPARED_SELL"
    )

    fun isSafeToClear(state: String?, rawTradeRecorded: Boolean): Boolean =
        state == "THESIS_IGNORED" ||
            (rawTradeRecorded && state in handledTradeStates)
}
