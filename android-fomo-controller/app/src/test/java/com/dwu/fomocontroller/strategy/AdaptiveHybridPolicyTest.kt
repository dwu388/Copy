package com.dwu.fomocontroller.strategy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdaptiveHybridPolicyTest {
    private fun model(): AdaptiveHybridModel {
        val input = checkNotNull(javaClass.classLoader?.getResourceAsStream("adaptive_hybrid_v2.json.gz"))
        return input.use(AdaptiveHybridModel::fromGzip)
    }

    @Test
    fun feesMatchValidatedSchedulesAndCrossover() {
        assertEquals(0.95, AdaptiveHybridPolicy.orderFee(100.0, 0.0), 0.0)
        assertEquals(0.95, AdaptiveHybridPolicy.orderFee(190.0, 0.0), 1e-12)
        assertEquals(1.00, AdaptiveHybridPolicy.orderFee(200.0, 0.0), 1e-12)
        assertEquals(0.855, AdaptiveHybridPolicy.orderFee(190.0, 0.10), 1e-12)
        assertEquals(0.90, AdaptiveHybridPolicy.orderFee(200.0, 0.10), 1e-12)
    }

    @Test
    fun deteriorationIsImmediateAndRecoveryIsHysteretic() {
        val cfg = model().config
        val state = RuntimeSnapshot(cash = 800.0, openCost = 0.0, peakEquity = 1_000.0)
        AdaptiveHybridPolicy.updateMode(state, cfg)
        assertEquals("DEFENSIVE", state.mode)

        state.cash = 1_000.0
        state.peakEquity = 1_000.0
        state.shadowReturns += List(10) { 0.10 }
        state.exitsSinceModeChange = cfg.regimes.recoveryExits - 1
        AdaptiveHybridPolicy.updateMode(state, cfg)
        assertEquals("DEFENSIVE", state.mode)

        state.exitsSinceModeChange++
        AdaptiveHybridPolicy.updateMode(state, cfg)
        assertEquals("CAUTION", state.mode)
    }

    @Test
    fun modeConfidenceAndReserveGateFailClosed() {
        val model = model()
        val state = RuntimeSnapshot()
        val below = AdaptiveHybridPolicy.chooseBuy(
            nowMs = 1_000_000L,
            trader = "pointfarmcap",
            token = "TEST",
            sourceBuyUsd = 500.0,
            predictedRoi = 1.0,
            confidence = 0.799999,
            state = state,
            cfg = model.config,
            feeDiscount = 0.0,
            roiUncertaintyScale = model.roiUncertaintyScale,
            openByTrader = emptyMap(),
            openByToken = emptyMap(),
            recentEntries = emptyList()
        )
        assertFalse(below.execute)
        assertEquals("MODE_CONFIDENCE", below.reason)

        val eligible = AdaptiveHybridPolicy.chooseBuy(
            nowMs = 1_000_000L,
            trader = "pointfarmcap",
            token = "TEST",
            sourceBuyUsd = 1_000.0,
            predictedRoi = 1.0,
            confidence = 0.95,
            state = RuntimeSnapshot(),
            cfg = model.config,
            feeDiscount = 0.0,
            roiUncertaintyScale = model.roiUncertaintyScale,
            openByTrader = emptyMap(),
            openByToken = emptyMap(),
            recentEntries = emptyList()
        )
        assertTrue(eligible.execute)
        assertTrue(eligible.copyUsd!! > 0.0)
        assertTrue(eligible.reserveRequired!! >= 150.0)
    }
}
