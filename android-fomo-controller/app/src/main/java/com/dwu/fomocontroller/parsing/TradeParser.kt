package com.dwu.fomocontroller.parsing

data class ParsedTrade(
    val action: String?,
    val trader: String?,
    val coin: String?,
    val marketCap: Double?,
    val sourceAmount: Double?
)

object TradeParser {
    private val titleRegex = Regex("""^(.*?) at \$([\d,.]+)([kKmMbB]?) MC""", RegexOption.IGNORE_CASE)
    private val tradeRegex = Regex(
        """@([^\s]+)\s+(bought|sold)\s+\$([\d,]+(?:\.\d+)?)""",
        RegexOption.IGNORE_CASE
    )
    private val actionRegex = Regex("""\b(bought|sold)\b""", RegexOption.IGNORE_CASE)

    fun parse(title: String, text: String): ParsedTrade {
        val titleMatch = titleRegex.find(title)
        val coin = titleMatch?.groupValues?.getOrNull(1)?.trim()?.takeIf { it.isNotBlank() }
        val marketCap = titleMatch?.let {
            val raw = it.groupValues[2].replace(",", "").toDoubleOrNull()
            val suffix = it.groupValues[3].lowercase()
            when {
                raw == null -> null
                suffix == "k" -> raw * 1_000.0
                suffix == "m" -> raw * 1_000_000.0
                suffix == "b" -> raw * 1_000_000_000.0
                else -> raw
            }
        }

        val tradeMatch = tradeRegex.find(text)
        val trader = tradeMatch?.groupValues?.getOrNull(1)
        val action = tradeMatch?.groupValues?.getOrNull(2)?.lowercase() ?: findAction(text)
        val amount = tradeMatch?.groupValues?.getOrNull(3)?.replace(",", "")?.toDoubleOrNull()

        return ParsedTrade(
            action = action,
            trader = trader,
            coin = coin,
            marketCap = marketCap,
            sourceAmount = amount
        )
    }

    fun findAction(text: String): String? =
        actionRegex.find(text)?.groupValues?.getOrNull(1)?.lowercase()

    fun isThesisOnly(text: String): Boolean {
        val lower = text.lowercase()
        return Regex("""\bthesis\b""").containsMatchIn(lower) &&
            findAction(lower) == null
    }

    fun isTradeNotification(text: String): Boolean =
        findAction(text) != null
}
