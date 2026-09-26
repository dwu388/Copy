package com.dwu.fomocontroller.notification

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationClearPolicyTest {
    @Test
    fun thesisOnlyIsSafeWithoutRawTradeRow() {
        assertTrue(NotificationClearPolicy.isSafeToClear("THESIS_IGNORED", false))
    }

    @Test
    fun successfulTradeStatesRequireDurableRawRow() {
        val states = listOf(
            "FILTERED",
            "ADAPTIVE_OBSERVED",
            "OBSERVED",
            "DRY_RUN_VERIFIED",
            "PREPARED_BUY",
            "PREPARED_SELL"
        )
        states.forEach { state ->
            assertFalse(NotificationClearPolicy.isSafeToClear(state, false))
            assertTrue(NotificationClearPolicy.isSafeToClear(state, true))
        }
    }

    @Test
    fun failuresAndInFlightStatesStayVisible() {
        val states = listOf(
            null,
            "PARSE_FAILED",
            "MODEL_FAILED",
            "ADAPTIVE_SELECTED",
            "QUEUED",
            "OPENING",
            "VERIFYING",
            "EXPIRED",
            "INTENT_UNAVAILABLE",
            "UI_TIMEOUT",
            "PAGE_MISMATCH",
            "PAPER_LEDGER_FAILED",
            "SELECTORS_NOT_CALIBRATED",
            "UI_ACTION_FAILED"
        )
        states.forEach { state ->
            assertFalse(NotificationClearPolicy.isSafeToClear(state, true))
        }
    }
}
