package com.dwu.fomocontroller.parsing

data class ParsedTrade(
    val action: String?,
    val trader: String?,
    val coin: String?,
    val marketCap: Double?,
    val sourceAmount: Double?
)

object TradeParser {
    private val titleRegex = Regex("""^(.*?) at \$([\d,.]+)([kKmM]?) MC""")
    private val tradeRegex = Regex("""@([^\s]+)\s+(bought|sold)\s+\$([\d,]+(?:\.\d+)?)""", RegexOption.IGNORE_CASE)

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
                else -> raw
            }
        }

        val tradeMatch = tradeRegex.find(text)
        val trader = tradeMatch?.groupValues?.getOrNull(1)
        val action = tradeMatch?.groupValues?.getOrNull(2)?.lowercase()
        val amount = tradeMatch?.groupValues?.getOrNull(3)?.replace(",", "")?.toDoubleOrNull()

        return ParsedTrade(
            action = action,
            trader = trader,
            coin = coin,
            marketCap = marketCap,
            sourceAmount = amount
        )
    }

    fun isThesisOnly(text: String): Boolean {
        val lower = text.lowercase()
        return Regex("""\bthesis\b""").containsMatchIn(lower) &&
            !Regex("""\b(?:bought|sold)\b""").containsMatchIn(lower)
    }

    fun isTradeNotification(text: String): Boolean =
        tradeRegex.containsMatchIn(text)
}
