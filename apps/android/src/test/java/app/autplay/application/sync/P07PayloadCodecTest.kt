package app.autplay.application.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class P07PayloadCodecTest {
    @Test
    fun canonicalizesAndPreservesUnknownAttributionData() {
        assertEquals(
            "{\"attribution\":{\"future_key\":\"retained\",\"request_id\":\"request\"},\"preference\":\"LIKED\"}",
            P07PayloadCodec.canonicalize("{\"preference\":\"LIKED\",\"attribution\":{\"request_id\":\"request\",\"future_key\":\"retained\"}}"),
        )
    }

    @Test
    fun refusesUnsafePayloadNames() {
        try {
            P07PayloadCodec.canonicalize("{\"access_token\":\"secret\"}")
            fail("Expected safety failure")
        } catch (error: LocalIntentPayloadException) {
            assertEquals(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME, error.code)
        }
    }

    @Test fun normalizesExponentNumbersAndRejectsDuplicateNames() {
        assertEquals("{\"count\":1}", P07PayloadCodec.canonicalize("{\"count\":1e0}"))
        assertEquals(
            "{\"numbers\":[333333333.3333333,1e+30,4.5,0.002,1e-27]}",
            P07PayloadCodec.canonicalize(
                "{\"numbers\":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001]}",
            ),
        )
        assertEquals(
            "{\"left\":{\"same\":1},\"right\":{\"same\":2}}",
            P07PayloadCodec.canonicalize("{\"left\":{\"same\":1},\"right\":{\"same\":2}}"),
        )
        try { P07PayloadCodec.canonicalize("{\"x\":1,\"x\":2}"); fail("Expected duplicate") } catch (error: LocalIntentPayloadException) { assertEquals(LocalIntentPayloadErrorCode.DUPLICATE_PROPERTY, error.code) }
    }
}
