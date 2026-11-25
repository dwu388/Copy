package com.dwu.fomocontroller.strategy

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject
import java.util.zip.GZIPInputStream

class EntrySafetyTest {
    @Test fun hardEntryBoundsAreInclusiveForSourceAndExclusiveForCap() {
        assertNull(EntrySafety.entryReason(99999.0,1000.0,100000.0,1000.0))
        assertEquals("ENTRY_MARKET_CAP",EntrySafety.entryReason(100000.0,1000.0,100000.0,1000.0))
        assertEquals("ENTRY_SOURCE_SIZE",EntrySafety.entryReason(10000.0,999.99,100000.0,1000.0))
        assertEquals("INVALID_ENTRY",EntrySafety.entryReason(Double.NaN,1000.0,100000.0,1000.0))
    }
    @Test fun circuitIsLatchedAndDoesNotAutomaticallyResume() {
        val stopped=EntrySafety.circuit(1000.0,800.0,false,.20)
        assertTrue(stopped.paused)
        assertTrue(EntrySafety.circuit(stopped.peak,1100.0,stopped.paused,.20).paused)
        assertFalse(EntrySafety.circuit(800.0,800.0,false,.20).paused)
        assertFalse(EntrySafety.circuit(1000.0,801.0,false,.20).paused)
    }
    @Test(expected=IllegalArgumentException::class) fun invalidLossLimitFails() {
        EntrySafety.circuit(1000.0,1000.0,false,1.0)
    }
    // Use the already-public legacy artifact with a fixed synthetic calibration map.
    // Private, locally generated model weights are deliberately not required by CI.
    private fun paperJson(): JSONObject {
        val root = JSONObject(GZIPInputStream(javaClass.classLoader!!
            .getResourceAsStream("adaptive_hybrid_v2.json.gz")!!).bufferedReader().use { it.readText() })
        root.put("paperOnly", true).put("labelContract", "notification_24h_net_reference_v1")
        root.put("probabilityCalibration", JSONObject().put("schemaVersion", "platt_oos_v1")
            .put("slope", .8).put("intercept", -.2))
        val reference = root.getJSONArray("confidenceReference")
        val fixtures = root.getJSONArray("fixtures")
        for (i in 0 until fixtures.length()) {
            val f = fixtures.getJSONObject(i)
            val p = f.getDouble("probability").coerceIn(1e-12, 1-1e-12)
            val calibrated = 1 / (1 + kotlin.math.exp(-(.8 * kotlin.math.ln(p/(1-p)) - .2)))
            f.put("probability", calibrated)
            f.put("confidenceRank", (0 until reference.length()).count {
                reference.getDouble(it) <= calibrated
            }.toDouble() / reference.length())
        }
        return root
    }
    @Test fun syntheticCalibratedModelMatchesIndependentFixtures() {
        AdaptiveHybridModel.fromJsonText(paperJson().toString()).validateParity()
    }
    @Test(expected=IllegalArgumentException::class) fun invalidCalibratorIsRejected() {
        val root=paperJson()
        root.getJSONObject("probabilityCalibration").put("slope",-1)
        AdaptiveHybridModel.fromJsonText(root.toString())
    }
    @Test fun strictMarginBlocksNegativeForecastEvenForLowFeeLargeOrders() {
        val model=AdaptiveHybridModel.fromJsonText(paperJson().toString())
        fun choose(roi:Double, forecast:Boolean=true, source:Double=2000.0)=AdaptiveHybridPolicy.chooseBuy(
            1000,"test","X",source,roi,1.0,RuntimeSnapshot(),requireNotNull(model.config),0.0,0.0,
            emptyMap(),emptyMap(),emptyList(),forecast)
        assertFalse(choose(-.01).execute)
        assertFalse(choose(.005).execute)
        assertTrue(choose(.10).execute)
        assertFalse(choose(100.0,false,500.0).execute) // Rule-only cannot use forecast to waive a high fee burden.
        assertTrue(choose(0.0,false,1000.0).execute)
    }
}
