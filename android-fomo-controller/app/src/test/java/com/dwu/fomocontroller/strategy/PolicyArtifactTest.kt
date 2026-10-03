package com.dwu.fomocontroller.strategy

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.zip.GZIPInputStream

class PolicyArtifactTest {
    private fun root(): JSONObject {
        val input = checkNotNull(javaClass.classLoader?.getResourceAsStream("adaptive_hybrid_v2.json.gz"))
        val forecast = GZIPInputStream(input).bufferedReader().use { JSONObject(it.readText()) }
        return JSONObject().apply {
            put("schemaVersion", "copy_policy_v1")
            put("policyModelId", "policy-test")
            put("parentPolicyModelId", JSONObject.NULL)
            put("trainedThrough", forecast.getString("trainedThrough"))
            put("config", forecast.getJSONObject("config"))
        }
    }

    @Test
    fun independentPolicyParses() {
        val policy = PolicyArtifact.fromJson(root().toString())
        assertEquals("policy-test", policy.policyModelId)
        assertEquals("mild", policy.config.name)
    }

    @Test(expected = IllegalArgumentException::class)
    fun unsafeConfidenceFailsClosed() {
        val root = root()
        root.getJSONObject("config").getJSONObject("modes")
            .getJSONObject("NORMAL").put("confidence_threshold", 1.1)
        PolicyArtifact.fromJson(root.toString())
    }
}
