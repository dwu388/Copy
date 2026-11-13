package com.dwu.fomocontroller.strategy

import com.dwu.fomocontroller.model.RecordedNotification
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class Net2xModelTest {
    private fun load(): AdaptiveHybridModel {
        val privateCandidate = javaClass.classLoader?.getResourceAsStream("notification_net_2x.json.gz")
        if (privateCandidate != null) return privateCandidate.use(AdaptiveHybridModel::fromGzip)
        return checkNotNull(javaClass.classLoader?.getResourceAsStream("notification_net_2x.json"))
            .bufferedReader().use { AdaptiveHybridModel.fromJsonText(it.readText()) }
    }

    @Test fun bundledModelMatchesPythonFixtures() {
        val model = load()
        assertEquals("copy_notification_hgb_2x_v1", model.schemaVersion)
        assertNotNull(model.target)
        model.validateParity()
    }

    @Test fun syntheticBootstrapCannotAuthorizeABuy() {
        val model = load()
        if (model.modelId == "net2x_untrained_bootstrap") {
            for (fixture in model.parityFixtures) {
                val score = model.score(fixture.trader, fixture.marketCap, fixture.sourceBuyUsd,
                    fixture.token, fixture.features)
                assertFalse(model.target!!.qualifies(score, 100.0, 0.0))
            }
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun incompleteEntryFeaturesFailClosed() {
        load().score("alpha", 100000.0, 1000.0)
    }

    @Test fun netDoubleRequiresCostsAndAbsoluteProbability() {
        val target = Net2xTarget(20.0, 0.0, 0.5)
        assertFalse(target.qualifies(HybridScore(false, .9, 1.0, .99), 50.0, 0.0))
        assertFalse(target.qualifies(HybridScore(false, .4, 2.0, .99), 50.0, 0.0))
        assertFalse(target.qualifies(HybridScore(false, .9, 1.2, .99), 1.0, 0.0))
        assertTrue(target.qualifies(HybridScore(false, .9, 1.2, .99), 50.0, 0.0))
        assertTrue(target.qualifies(HybridScore(false, .9, 1.04, .99), 100.0, 0.0))
        assertFalse(target.qualifies(HybridScore(false, .9, 1.04, .99), 50.0, 0.0))
    }

    @Test fun replayFeaturesMatchPythonForEverySyntheticEntry() {
        val text = checkNotNull(javaClass.classLoader?.getResourceAsStream("notification_feature_fixtures.json"))
            .bufferedReader().use { it.readText() }
        val fixtures = JSONArray(text)
        val replay = NotificationFeatures()
        for (i in 0 until fixtures.length()) {
            val fixture = fixtures.getJSONObject(i)
            val r = fixture.getJSONObject("row")
            val actual = replay.consume(recorded(r))
            if (fixture.isNull("features")) assertNull(actual)
            else {
                val expected = fixture.getJSONObject("features")
                assertNotNull(actual)
                assertEquals(NotificationFeatures.NAMES.toSet(), actual!!.keys)
                for (name in NotificationFeatures.NAMES) {
                    assertEquals("event=$i feature=$name", expected.getDouble(name), actual.getValue(name), 1e-12)
                }
            }
        }
    }

    private fun recorded(r: JSONObject) = RecordedNotification(
        eventId = r.getLong("event_id"), notificationKey = r.getString("notification_key"),
        notificationId = 0, notificationTag = null, packageName = r.getString("package_name"),
        postTime = r.getLong("post_time"), capturedTime = r.getLong("captured_time"),
        title = "", normalText = "", bigText = null, selectedText = "",
        action = r.getString("action"), trader = r.getString("trader"), coin = r.getString("coin"),
        marketCap = r.getDouble("market_cap"), sourceAmount = r.getDouble("source_amount"),
        channelId = null, category = null, groupKey = null, hasContentIntent = true, notificationActionCount = 0
    )
}
