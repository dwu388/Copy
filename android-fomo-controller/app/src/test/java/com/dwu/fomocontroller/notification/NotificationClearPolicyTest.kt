package com.dwu.fomocontroller.notification

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationClearPolicyTest {
    @Test
    fun tradeCannotClearUntilRawCaptureIsDurable() {
        val states = listOf(
            null,
            "FILTERED",
            "PARSE_FAILED",
            "MODEL_FAILED",
            "EXPIRED",
            "DRY_RUN_VERIFIED"
        )
        states.forEach { state ->
            assertFalse(NotificationClearPolicy.isSafeToClear(state, false))
        }
    }

    @Test
    fun tradeStaysLiveOnlyWhileExactNotificationMayStillBeNeeded() {
        val states = listOf(
            "ADAPTIVE_SELECTED",
            "QUEUED",
            "OPENING"
        )
        states.forEach { state ->
            assertFalse(NotificationClearPolicy.isSafeToClear(state, true))
        }
    }

    @Test
    fun durableTradeClearsAfterOpeningOrAnyTerminalOutcome() {
        val states = listOf(
            null,
            "FILTERED",
            "ADAPTIVE_OBSERVED",
            "VERIFYING",
            "OBSERVED",
            "DRY_RUN_VERIFIED",
            "PREPARED_BUY",
            "PREPARED_SELL",
            "PARSE_FAILED",
            "MODEL_FAILED",
            "EXPIRED",
            "INTENT_UNAVAILABLE",
            "UI_TIMEOUT",
            "PAGE_MISMATCH",
            "PAPER_LEDGER_FAILED",
            "SELECTORS_NOT_CALIBRATED",
            "UI_ACTION_FAILED",
            "AUTOMATION_CANCELED"
        )
        states.forEach { state ->
            assertTrue(NotificationClearPolicy.isSafeToClear(state, true))
        }
    }
}
