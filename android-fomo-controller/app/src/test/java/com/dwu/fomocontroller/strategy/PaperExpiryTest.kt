package com.dwu.fomocontroller.strategy

import org.junit.Assert.*
import org.junit.Test

class PaperExpiryTest {
    @Test fun strictlyMoreThan24Hours() {
        val opened = 1_000_000L
        assertFalse(PaperExpiry.overdue(opened, opened - 1))
        assertFalse(PaperExpiry.overdue(opened, opened + PaperExpiry.MAX_HOLD_MS))
        assertTrue(PaperExpiry.overdue(opened, opened + PaperExpiry.MAX_HOLD_MS + 1))
    }
    @Test fun priceReturnAndSlippageDetermineProceeds() {
        assertEquals(200.0, PaperExpiry.gross(100.0, 1000.0, 2000.0, 0.0), 1e-12)
        assertEquals(45.0, PaperExpiry.gross(100.0, 1000.0, 500.0, .1), 1e-12)
    }
    @Test fun missingOrInvalidQuoteIsExplicitZeroNotEntryPrice() {
        for (quote in listOf(null, 0.0, -1.0, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertEquals(0.0, PaperExpiry.gross(100.0, 1000.0, quote, 0.0), 0.0)
        }
        assertEquals(0.0, AdaptiveHybridPolicy.orderFee(0.0, 0.0), 0.0)
    }
}
