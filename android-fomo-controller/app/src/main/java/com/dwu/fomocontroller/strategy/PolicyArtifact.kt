package com.dwu.fomocontroller.strategy

import org.json.JSONObject

data class PolicyArtifact(
    val schemaVersion: String,
    val policyModelId: String,
    val parentPolicyModelId: String?,
    val trainedThrough: String,
    val config: StrategyConfig
) {
    companion object {
        fun bootstrap(config: StrategyConfig, trainedThrough: String) = PolicyArtifact(
            schemaVersion = "copy_policy_v1",
            policyModelId = "adaptive_hybrid_v2:${config.name}",
            parentPolicyModelId = null,
            trainedThrough = trainedThrough,
            config = config
        )

        fun fromJson(text: String): PolicyArtifact {
            val root = JSONObject(text)
            require(root.getString("schemaVersion") == "copy_policy_v1") {
                "Unsupported policy schema ${root.optString("schemaVersion")}"
            }
            return PolicyArtifact(
                schemaVersion = root.getString("schemaVersion"),
                policyModelId = root.getString("policyModelId").also {
                    require(it.isNotBlank()) { "Policy modelId is blank" }
                },
                parentPolicyModelId = root.optString("parentPolicyModelId").takeIf { it.isNotBlank() },
                trainedThrough = root.getString("trainedThrough"),
                config = StrategyConfig.fromJson(root.getJSONObject("config"))
            )
        }
    }
}
