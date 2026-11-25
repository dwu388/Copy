package com.dwu.fomocontroller.strategy

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TraderWinRateGuardTest {
    private fun fixtures(): JSONObject = JSONObject(javaClass.classLoader!!
        .getResourceAsStream("trader_guard_parity.json")!!.bufferedReader().use { it.readText() })

    @Test fun assessmentParityFixtures() {
        val cases = fixtures().getJSONArray("assessments")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val rows = c.getJSONArray("outcomes")
            val outcomes = (0 until rows.length()).map { j ->
                val row = rows.getJSONArray(j)
                TraderGuardOutcome(row.getLong(0), row.getDouble(1))
            }
            val stats = TraderWinRateGuard.statistics(outcomes, c.getLong("now"))
            val actual = TraderWinRateGuard.assess(TraderGuardMode.valueOf(c.getString("mode")),
                stats, c.getDouble("threshold"))
            assertEquals(c.getString("name"), c.getString("status"), actual.status)
            assertEquals(c.getInt("samples"), actual.stats.samples)
            assertEquals(c.getInt("wins"), actual.stats.wins)
            assertEquals(c.getBoolean("allowed"), actual.allowed)
        }
    }

    @Test fun valuationParityFixtures() {
        val rows = fixtures().getJSONArray("valuations")
        for (i in 0 until rows.length()) {
            val c = rows.getJSONObject(i)
            assertEquals(c.getDouble("net"), TraderWinRateGuard.netReferenceUsd(
                c.getDouble("source"), c.getDouble("entry"),
                if (c.isNull("exit")) null else c.getDouble("exit")), 1e-9)
        }
    }

    @Test fun unavailableHistoryCannotBlockWarningButBlocksFilter() {
        val stats = TraderGuardStats(0, 0)
        assertTrue(TraderGuardAssessment(TraderGuardMode.WARN, "UNAVAILABLE", stats, .5).allowed)
        assertFalse(TraderGuardAssessment(TraderGuardMode.FILTER, "UNAVAILABLE", stats, .5).allowed)
    }

    @Test fun identityNormalizationAndUnknownModes() {
        assertEquals("oinkersrus", TraderWinRateGuard.normalize(" OinkersRUs "))
        assertEquals(TraderGuardMode.WARN, TraderGuardMode.from("invalid"))
        assertEquals(TraderGuardMode.WARN, TraderGuardMode.from(null))
    }

    @Test fun invalidConfigurationIsRejected() {
        for (threshold in listOf(Double.NaN, Double.POSITIVE_INFINITY, -.1, 1.1)) {
            try {
                TraderWinRateGuard.assess(TraderGuardMode.WARN, TraderGuardStats(0, 0), threshold)
                fail("accepted invalid threshold")
            } catch (_: IllegalArgumentException) { }
        }
    }
}
