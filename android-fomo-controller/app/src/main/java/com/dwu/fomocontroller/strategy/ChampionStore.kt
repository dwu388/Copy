package com.dwu.fomocontroller.strategy

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.GZIPInputStream

data class ActiveChampions(
    val forecast: AdaptiveHybridModel,
    val policy: PolicyArtifact,
    val source: String
)

/**
 * Loads independently versioned forecast and policy champions.
 *
 * A maintenance process stages a complete directory under files/models/staging.
 * At startup the directory is verified in full, then swapped with active. An
 * invalid or partial directory is never activated. The prior active directory
 * is retained as previous for immediate rollback.
 */
object ChampionStore {
    private const val TAG = "CopyChampionStore"
    private const val MANIFEST = "champion_manifest.json"
    private const val MANIFEST_SCHEMA = "copy_champion_manifest_v1"

    fun load(context: Context): ActiveChampions {
        val bundledForecast = AdaptiveHybridModel.fromAssets(context).also { it.validateParity() }
        val bundledPolicy = PolicyArtifact.bootstrap(
            requireNotNull(bundledForecast.config) { "Bundled generation-0 policy is missing" },
            bundledForecast.trainedThrough
        )
        val root = File(context.filesDir, "models").apply { mkdirs() }
        promoteStaged(root)

        for ((name, directory) in listOf("active" to File(root, "active"), "previous" to File(root, "previous"))) {
            runCatching { loadDirectory(directory) }
                .onSuccess { return it.copy(source = name) }
                .onFailure { error ->
                    if (directory.exists()) Log.e(TAG, "Rejected $name champion: ${error.message}", error)
                }
        }
        return ActiveChampions(bundledForecast, bundledPolicy, "bundled")
    }

    private fun promoteStaged(root: File) {
        val staging = File(root, "staging")
        if (!File(staging, MANIFEST).isFile) return
        runCatching { loadDirectory(staging) }
            .onFailure { error ->
                Log.e(TAG, "Staged champions failed validation and were not activated", error)
                return
            }

        val active = File(root, "active")
        val previous = File(root, "previous")
        if (previous.exists() && !previous.deleteRecursively()) {
            Log.e(TAG, "Could not clear previous champion directory; activation aborted")
            return
        }
        if (active.exists() && !active.renameTo(previous)) {
            Log.e(TAG, "Could not retain active champion as previous; activation aborted")
            return
        }
        if (!staging.renameTo(active)) {
            Log.e(TAG, "Could not atomically activate staged champions; restoring prior active")
            if (previous.exists()) previous.renameTo(active)
        }
    }

    private fun loadDirectory(directory: File): ActiveChampions {
        require(directory.isDirectory) { "Champion directory is missing" }
        val manifest = JSONObject(File(directory, MANIFEST).readText())
        require(manifest.getString("schemaVersion") == MANIFEST_SCHEMA) {
            "Unsupported champion manifest schema"
        }
        val forecastEntry = manifest.getJSONObject("forecast")
        val policyEntry = manifest.getJSONObject("policy")
        val forecastBytes = verifiedBytes(directory, forecastEntry)
        val policyBytes = verifiedBytes(directory, policyEntry)
        val forecast = if (isGzip(forecastBytes)) {
            AdaptiveHybridModel.fromGzipBytes(forecastBytes)
        } else {
            AdaptiveHybridModel.fromJsonText(forecastBytes.toString(Charsets.UTF_8))
        }
        forecast.validateParity()
        require(forecast.modelId == forecastEntry.getString("modelId")) {
            "Forecast ID does not match manifest"
        }
        val policyText = if (isGzip(policyBytes)) {
            GZIPInputStream(policyBytes.inputStream()).bufferedReader().use { it.readText() }
        } else policyBytes.toString(Charsets.UTF_8)
        val policy = PolicyArtifact.fromJson(policyText)
        require(policy.policyModelId == policyEntry.getString("policyModelId")) {
            "Policy ID does not match manifest"
        }
        return ActiveChampions(forecast, policy, directory.name)
    }

    private fun verifiedBytes(directory: File, entry: JSONObject): ByteArray {
        val relative = entry.getString("file")
        require(!relative.contains("..") && !File(relative).isAbsolute) { "Unsafe champion path" }
        val file = File(directory, relative)
        require(file.isFile) { "Missing champion artifact $relative" }
        val bytes = file.readBytes()
        val actual = MessageDigest.getInstance("SHA-256")
            .digest(bytes).joinToString("") { "%02x".format(it) }
        require(actual.equals(entry.getString("sha256"), ignoreCase = true)) {
            "SHA-256 mismatch for $relative"
        }
        return bytes
    }

    private fun isGzip(bytes: ByteArray): Boolean =
        bytes.size >= 2 && bytes[0] == 0x1f.toByte() && bytes[1] == 0x8b.toByte()
}
