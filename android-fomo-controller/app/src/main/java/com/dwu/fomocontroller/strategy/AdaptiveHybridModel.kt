package com.dwu.fomocontroller.strategy

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.util.Locale
import java.util.zip.GZIPInputStream
import kotlin.math.exp
import kotlin.math.ln1p

data class HybridScore(
    val excluded: Boolean,
    val probabilityProfitableExit: Double,
    val predictedRoi: Double,
    val confidenceRank: Double
)

data class ModelParityFixture(
    val trader: String,
    val marketCap: Double,
    val sourceBuyUsd: Double,
    val probability: Double,
    val predictedRoi: Double,
    val confidenceRank: Double
)

/** Exact JVM evaluator for the fitted sklearn HistGradientBoosting bundle. */
class AdaptiveHybridModel private constructor(
    val version: String,
    val trainedThrough: String,
    val matchingRule: String,
    val roiUncertaintyScale: Double,
    val config: StrategyConfig,
    private val excludedTraders: Set<String>,
    private val confidenceReference: DoubleArray,
    private val classifier: Ensemble,
    private val regressor: Ensemble,
    val parityFixtures: List<ModelParityFixture>
) {
    fun score(trader: String, marketCap: Double, sourceBuyUsd: Double): HybridScore {
        require(marketCap.isFinite() && marketCap > 0.0) { "marketCap must be positive and finite" }
        require(sourceBuyUsd.isFinite() && sourceBuyUsd > 0.0) { "sourceBuyUsd must be positive and finite" }
        if (trader.lowercase(Locale.ROOT) in excludedTraders) {
            return HybridScore(true, Double.NaN, Double.NaN, 0.0)
        }

        val probability = sigmoid(classifier.predict(trader, marketCap, sourceBuyUsd))
        val roi = regressor.predict(trader, marketCap, sourceBuyUsd)
        val confidence = upperBound(confidenceReference, probability).toDouble() /
            confidenceReference.size.coerceAtLeast(1)
        return HybridScore(false, probability, roi, confidence)
    }

    private fun sigmoid(raw: Double): Double =
        if (raw >= 0.0) 1.0 / (1.0 + exp(-raw))
        else {
            val e = exp(raw)
            e / (1.0 + e)
        }

    private fun upperBound(values: DoubleArray, target: Double): Int {
        var low = 0
        var high = values.size
        while (low < high) {
            val mid = (low + high) ushr 1
            if (target < values[mid]) high = mid else low = mid + 1
        }
        return low
    }

    private data class Node(
        val value: Double,
        val featureIndex: Int,
        val threshold: Double,
        val missingGoesLeft: Boolean,
        val left: Int,
        val right: Int,
        val leaf: Boolean
    )

    private class Ensemble(
        traderCategories: List<String>,
        private val numericMean: DoubleArray,
        private val numericScale: DoubleArray,
        private val baseline: Double,
        private val trees: List<Array<Node>>
    ) {
        private val traderIndex = traderCategories.withIndex().associate { it.value to it.index }
        private val categoryCount = traderCategories.size

        fun predict(trader: String, marketCap: Double, sourceBuyUsd: Double): Double {
            val activeTrader = traderIndex[trader]
            val logMarketCap = (ln1p(marketCap) - numericMean[0]) / numericScale[0]
            val logSourceUsd = (ln1p(sourceBuyUsd) - numericMean[1]) / numericScale[1]
            var raw = baseline
            for (tree in trees) {
                var index = 0
                while (!tree[index].leaf) {
                    val node = tree[index]
                    val value = when {
                        node.featureIndex < categoryCount ->
                            if (node.featureIndex == activeTrader) 1.0 else 0.0
                        node.featureIndex == categoryCount -> logMarketCap
                        node.featureIndex == categoryCount + 1 -> logSourceUsd
                        else -> Double.NaN
                    }
                    index = when {
                        value.isNaN() -> if (node.missingGoesLeft) node.left else node.right
                        value <= node.threshold -> node.left
                        else -> node.right
                    }
                }
                raw += tree[index].value
            }
            return raw
        }
    }

    companion object {
        // AAPT removes the .gz suffix from gzip-compressed assets in the APK.
        // The repository/source fixture remains .json.gz and is loaded with
        // fromGzip() by JVM tests; Android's AssetManager exposes the packaged
        // file as adaptive_hybrid_v2.json and transparently decompresses it.
        private const val ASSET = "adaptive_hybrid_v2.json"

        fun fromAssets(context: Context): AdaptiveHybridModel {
            val root = context.assets.open(ASSET).bufferedReader(Charsets.UTF_8).use { reader ->
                JSONObject(reader.readText())
            }
            return fromJson(root)
        }

        fun fromGzip(input: InputStream): AdaptiveHybridModel {
            val root = GZIPInputStream(input).bufferedReader(Charsets.UTF_8).use { reader ->
                JSONObject(reader.readText())
            }
            return fromJson(root)
        }

        private fun fromJson(root: JSONObject): AdaptiveHybridModel {
            require(root.getString("version") == "adaptive_hybrid_v2") {
                "Unsupported strategy model ${root.optString("version")}"
            }
            return AdaptiveHybridModel(
                version = root.getString("version"),
                trainedThrough = root.getString("trainedThrough"),
                matchingRule = root.getString("matchingRule"),
                roiUncertaintyScale = root.getDouble("roiUncertaintyScale"),
                config = StrategyConfig.fromJson(root.getJSONObject("config")),
                excludedTraders = root.getJSONArray("excludedTraders").strings()
                    .map { it.lowercase(Locale.ROOT) }.toSet(),
                confidenceReference = root.getJSONArray("confidenceReference").doubles(),
                classifier = parseEnsemble(root.getJSONObject("classifier")),
                regressor = parseEnsemble(root.getJSONObject("regressor")),
                parityFixtures = root.getJSONArray("fixtures").objects().map { fixture ->
                    ModelParityFixture(
                        trader = fixture.getString("trader"),
                        marketCap = fixture.getDouble("marketCap"),
                        sourceBuyUsd = fixture.getDouble("sourceBuyUsd"),
                        probability = fixture.getDouble("probability"),
                        predictedRoi = fixture.getDouble("predictedRoi"),
                        confidenceRank = fixture.getDouble("confidenceRank")
                    )
                }
            )
        }

        private fun parseEnsemble(json: JSONObject): Ensemble {
            val treesJson = json.getJSONArray("trees")
            val trees = ArrayList<Array<Node>>(treesJson.length())
            for (i in 0 until treesJson.length()) {
                val nodesJson = treesJson.getJSONArray(i)
                val nodes = Array(nodesJson.length()) { j ->
                    val n = nodesJson.getJSONArray(j)
                    Node(
                        value = n.getDouble(0),
                        featureIndex = n.getInt(1),
                        threshold = n.getDouble(2),
                        missingGoesLeft = n.getInt(3) != 0,
                        left = n.getInt(4),
                        right = n.getInt(5),
                        leaf = n.getInt(6) != 0
                    )
                }
                trees += nodes
            }
            return Ensemble(
                traderCategories = json.getJSONArray("traderCategories").strings(),
                numericMean = json.getJSONArray("numericMean").doubles(),
                numericScale = json.getJSONArray("numericScale").doubles(),
                baseline = json.getDouble("baseline"),
                trees = trees
            )
        }

        private fun JSONArray.strings() = List(length()) { getString(it) }
        private fun JSONArray.doubles() = DoubleArray(length()) { getDouble(it) }
        private fun JSONArray.objects() = List(length()) { getJSONObject(it) }
    }
}
