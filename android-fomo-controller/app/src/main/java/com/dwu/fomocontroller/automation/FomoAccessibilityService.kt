package com.dwu.fomocontroller.automation

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

class FomoAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        super.onServiceConnected()
        AutomationCoordinator.initialize(applicationContext)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.packageName?.toString() != "family.fomo.app") return
        AutomationCoordinator.onFomoUiChanged(rootInActiveWindow)
    }

    override fun onInterrupt() = Unit
}
