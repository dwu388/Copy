package com.dwu.fomocontroller.model

data class TradeEvent(
    val notificationKey: String,
    val notificationId: Int,
    val notificationTag: String?,
    val packageName: String,
    val postTime: Long,
    val capturedTime: Long,
    val title: String,
    val rawText: String,
    val action: String?,
    val trader: String?,
    val coin: String?,
    val marketCap: Double?,
    val sourceAmount: Double?,
    val copyAmount: Double?,
    val state: String,
    val failureReason: String? = null,
    val updatedTime: Long = System.currentTimeMillis()
)
