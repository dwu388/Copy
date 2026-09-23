package com.dwu.fomocontroller.automation

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo

object AccessibilityTools {
    fun findById(root: AccessibilityNodeInfo?, resourceId: String): AccessibilityNodeInfo? {
        if (root == null || resourceId.isBlank()) return null
        return runCatching {
            root.findAccessibilityNodeInfosByViewId(resourceId).firstOrNull()
        }.getOrNull()
    }

    fun containsText(root: AccessibilityNodeInfo?, expected: String): Boolean {
        if (root == null || expected.isBlank()) return false

        val direct = runCatching {
            root.findAccessibilityNodeInfosByText(expected)
        }.getOrNull().orEmpty()

        if (direct.any { node ->
                node.text?.toString()?.contains(expected, ignoreCase = true) == true ||
                    node.contentDescription?.toString()?.contains(expected, ignoreCase = true) == true
            }) {
            return true
        }

        return walkContains(root, expected)
    }

    private fun walkContains(node: AccessibilityNodeInfo, expected: String): Boolean {
        val text = node.text?.toString().orEmpty()
        val description = node.contentDescription?.toString().orEmpty()
        if (text.contains(expected, ignoreCase = true) ||
            description.contains(expected, ignoreCase = true)
        ) {
            return true
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            if (walkContains(child, expected)) return true
        }
        return false
    }

    fun click(node: AccessibilityNodeInfo?): Boolean {
        if (node == null) return false
        if (node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true

        var parent = node.parent
        var depth = 0
        while (parent != null && depth < 4) {
            if (parent.isClickable &&
                parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            ) {
                return true
            }
            parent = parent.parent
            depth++
        }
        return false
    }

    fun setText(node: AccessibilityNodeInfo?, value: String): Boolean {
        if (node == null) return false
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                value
            )
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }
}
