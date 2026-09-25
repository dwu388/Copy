package com.dwu.fomocontroller.model

data class RecordedNotification(
    val eventId: Long? = null,
    val notificationKey: String,
    val notificationId: Int,
    val notificationTag: String?,
    val packageName: String,
    val postTime: Long,
    val capturedTime: Long,
    val title: String,
    val normalText: String,
    val bigText: String?,
    val selectedText: String,
    val action: String,
    val trader: String?,
    val coin: String?,
    val marketCap: Double?,
    val sourceAmount: Double?,
    val channelId: String?,
    val category: String?,
    val groupKey: String?,
    val hasContentIntent: Boolean,
    val notificationActionCount: Int
)

data class RecorderStats(
    val total: Long,
    val buys: Long,
    val sells: Long,
    val latestPostTime: Long?
)
