package com.dwu.fomocontroller.automation

object FomoSelectors {
    const val BUY_ENTRY_RESOURCE_ID = ""
    const val SELL_ENTRY_RESOURCE_ID = ""
    const val AMOUNT_INPUT_RESOURCE_ID = ""

    val calibrated: Boolean
        get() = BUY_ENTRY_RESOURCE_ID.isNotBlank() &&
            SELL_ENTRY_RESOURCE_ID.isNotBlank() &&
            AMOUNT_INPUT_RESOURCE_ID.isNotBlank()
}
