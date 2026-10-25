package com.dwu.fomocontroller.automation

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.os.Environment
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executor

/** Captures the verified Fomo page for a selected paper trade. No media permission is needed. */
object GoScreenshot {
    fun capture(
        service: AccessibilityService,
        root: AccessibilityNodeInfo?,
        key: String,
        action: String,
        trader: String?,
        coin: String?,
        callback: (String) -> Unit
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            callback("Screenshot unavailable: Android 11 or newer is required")
            return
        }
        val hierarchy = StringBuilder().also { dump(root, it, 0) }.toString()
        val executor = Executor { command -> service.mainExecutor.execute(command) }
        try {
            service.takeScreenshot(Display.DEFAULT_DISPLAY, executor,
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                        val buffer = result.hardwareBuffer
                        try {
                            val hardwareBitmap = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                                ?: error("Could not decode screenshot")
                            val bitmap = try {
                                hardwareBitmap.copy(Bitmap.Config.ARGB_8888, false)
                                    ?: error("Could not copy screenshot pixels")
                            } finally {
                                hardwareBitmap.recycle()
                            }
                            Thread {
                                try {
                                    val directory = File(
                                        service.getExternalFilesDir(Environment.DIRECTORY_PICTURES)
                                            ?: File(service.filesDir, "pictures"), "go_screenshots"
                                    )
                                    check(directory.isDirectory || directory.mkdirs()) {
                                        "Could not create screenshot directory"
                                    }
                                    val stamp = SimpleDateFormat("yyyyMMdd'T'HHmmssSSS'Z'", Locale.US).apply {
                                        timeZone = TimeZone.getTimeZone("UTC")
                                    }.format(Date())
                                    val digest = MessageDigest.getInstance("SHA-256")
                                        .digest(key.toByteArray()).take(8).joinToString("") { "%02x".format(it) }
                                    val basename = "${stamp}_${action}_${digest}"
                                    val image = File(directory, "$basename.png")
                                    image.outputStream().use { stream ->
                                        check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)) {
                                            "Could not encode screenshot"
                                        }
                                    }
                                    File(directory, "$basename.txt").writeText(
                                        "action=$action\ntrader=$trader\ncoin=$coin\nnotification_key=$key\n" +
                                            "captured_utc=$stamp\n\nAccessibility hierarchy (resource IDs):\n$hierarchy"
                                    )
                                    callback("Screenshot: ${image.absolutePath}")
                                } catch (error: Exception) {
                                    callback("Screenshot failed: ${error.message}")
                                } finally {
                                    bitmap.recycle()
                                }
                            }.start()
                        } catch (error: Exception) {
                            callback("Screenshot failed: ${error.message}")
                        } finally {
                            buffer.close()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        callback("Screenshot failed: Android error $errorCode")
                    }
                })
        } catch (error: Exception) {
            callback("Screenshot failed: ${error.message}")
        }
    }

    private fun dump(node: AccessibilityNodeInfo?, out: StringBuilder, depth: Int) {
        if (node == null || depth > 30 || out.length > 100_000) return
        out.append("  ".repeat(depth)).append(node.className)
            .append(" id=").append(node.viewIdResourceName.orEmpty())
            .append(" text=").append(node.text?.toString()?.take(120).orEmpty())
            .append(" description=").append(node.contentDescription?.toString()?.take(120).orEmpty())
            .append('\n')
        for (i in 0 until node.childCount) dump(node.getChild(i), out, depth + 1)
    }
}
