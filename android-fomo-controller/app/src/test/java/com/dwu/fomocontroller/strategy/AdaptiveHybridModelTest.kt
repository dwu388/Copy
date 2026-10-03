package com.dwu.fomocontroller.strategy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONObject
import java.util.zip.GZIPInputStream

class AdaptiveHybridModelTest {
    private fun load(): AdaptiveHybridModel {
        val input = checkNotNull(javaClass.classLoader?.getResourceAsStream("adaptive_hybrid_v2.json.gz"))
        return input.use(AdaptiveHybridModel::fromGzip)
    }

    @Test
    fun kotlinInferenceMatchesSklearnFixtures() {
        val model = load()
        assertEquals("adaptive_hybrid_v2", model.version)
        assertEquals("adaptive_hybrid_hgb_v1", model.schemaVersion)
        assertEquals("adaptive_hybrid_v2", model.modelId)
        assertEquals("mild", model.config?.name)
        assertEquals(0.35303748963112425, model.roiUncertaintyScale, 0.0)

        for (fixture in model.parityFixtures) {
            val actual = model.score(fixture.trader, fixture.marketCap, fixture.sourceBuyUsd)
            if (fixture.trader.equals("DuckSoldier", ignoreCase = true)) {
                assertTrue(actual.excluded)
            } else {
                assertFalse(actual.excluded)
                assertEquals(fixture.probability, actual.probabilityProfitableExit, 1e-12)
                assertEquals(fixture.predictedRoi, actual.predictedRoi, 1e-12)
                assertEquals(fixture.confidenceRank, actual.confidenceRank, 1e-12)
            }
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun invalidMarketCapFailsClosed() {
        load().score("pointfarmcap", 0.0, 500.0)
    }

    @Test(expected = IllegalArgumentException::class)
    fun recentAdapterInfluenceAboveSafetyCapFailsClosed() {
        val input = checkNotNull(javaClass.classLoader?.getResourceAsStream("adaptive_hybrid_v2.json.gz"))
        val root = GZIPInputStream(input).bufferedReader().use { JSONObject(it.readText()) }
        root.put("recentAdapter", JSONObject().apply {
            put("probabilityWeight", 0.36)
            put("roiWeight", 0.30)
            put("classifier", root.getJSONObject("classifier"))
            put("regressor", root.getJSONObject("regressor"))
        })
        AdaptiveHybridModel.fromJsonText(root.toString())
    }
}
