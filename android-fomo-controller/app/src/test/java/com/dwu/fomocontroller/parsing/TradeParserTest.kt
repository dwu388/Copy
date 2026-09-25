package com.dwu.fomocontroller.parsing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TradeParserTest {
    @Test
    fun wholeWordActionDetectionDoesNotRequireFullParse() {
        assertEquals("bought", TradeParser.findAction("someone bought something"))
        assertEquals("sold", TradeParser.findAction("SOLD notification"))
        assertNull(TradeParser.findAction("oversold"))
    }

    @Test
    fun parsesExistingFomoShape() {
        val parsed = TradeParser.parse(
            "BONK at $47.3k MC",
            "@cryptojuggler3 bought $837"
        )

        assertEquals("bought", parsed.action)
        assertEquals("cryptojuggler3", parsed.trader)
        assertEquals("BONK", parsed.coin)
        assertEquals(47_300.0, parsed.marketCap!!, 0.001)
        assertEquals(837.0, parsed.sourceAmount!!, 0.001)
    }

    @Test
    fun parsesLargeMarketCapSuffix() {
        val parsed = TradeParser.parse(
            "WIF at $2.4M MC",
            "@pointfarmcap sold $1,250.50"
        )

        assertEquals("sold", parsed.action)
        assertEquals(2_400_000.0, parsed.marketCap!!, 0.001)
        assertEquals(1_250.50, parsed.sourceAmount!!, 0.001)
    }
}
