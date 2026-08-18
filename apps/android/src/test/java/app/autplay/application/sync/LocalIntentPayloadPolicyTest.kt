package app.autplay.application.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class LocalIntentPayloadPolicyTest {
    @Test
    fun canonicalizesTheExactV1StringOnlyPayload() {
        assertEquals(
            "{\"artist\":\"B\\nA\",\"library_entry_local_id\":\"entry\",\"title\":\"A\\\"B\"}",
            policy("""{"title":"A\"B","artist":"B\nA","library_entry_local_id":"entry"}"""),
        )
    }

    @Test
    fun rejectsUnknownTypeVersionAndAggregate() {
        assertCode(LocalIntentPayloadErrorCode.UNSUPPORTED_EVENT_TYPE) { policy(eventType = "OTHER") }
        assertCode(LocalIntentPayloadErrorCode.UNSUPPORTED_SCHEMA_VERSION) { policy(schemaVersion = 2) }
        assertCode(LocalIntentPayloadErrorCode.UNSUPPORTED_AGGREGATE_TYPE) { policy(aggregateType = "OTHER") }
    }

    @Test
    fun rejectsMalformedNonObjectDuplicateAndNonStringValues() {
        assertCode(LocalIntentPayloadErrorCode.MALFORMED_JSON) { policy("{") }
        assertCode(LocalIntentPayloadErrorCode.MALFORMED_JSON) {
            policy("{\"artist\":NaN,\"library_entry_local_id\":\"id\",\"title\":\"t\"}")
        }
        assertCode(LocalIntentPayloadErrorCode.TOP_LEVEL_NOT_OBJECT) { policy("[]") }
        assertCode(LocalIntentPayloadErrorCode.DUPLICATE_PROPERTY) {
            policy("{\"artist\":\"a\",\"artist\":\"b\",\"library_entry_local_id\":\"id\",\"title\":\"t\"}")
        }
        assertCode(LocalIntentPayloadErrorCode.NON_STRING_VALUE) {
            policy("{\"artist\":1,\"library_entry_local_id\":\"id\",\"title\":\"t\"}")
        }
    }

    @Test
    fun rejectsUnknownAndForbiddenFieldsBeforeTheyCanBecomeAnEvent() {
        assertCode(LocalIntentPayloadErrorCode.UNSUPPORTED_PAYLOAD_SHAPE) {
            policy("{\"artist\":\"a\",\"library_entry_local_id\":\"id\",\"title\":\"t\",\"extra\":\"x\"}")
        }
        assertCode(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME) {
            policy("{\"artist\":\"a\",\"library_entry_local_id\":\"id\",\"title\":\"t\",\"raw_audio\":\"x\"}")
        }
        assertCode(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME) {
            policy("{\"artist\":{\"audio_bytes\":\"x\"},\"library_entry_local_id\":\"id\",\"title\":\"t\"}")
        }
    }

    @Test
    fun rejectsExcessiveDepthAndLoneSurrogates() {
        val nested = (1..33).joinToString(prefix = "", separator = "") { "{\"a\":" } + "\"x\"" + "}".repeat(33)
        assertCode(LocalIntentPayloadErrorCode.MAX_NESTING_EXCEEDED) { policy(nested) }
        assertCode(LocalIntentPayloadErrorCode.LONE_SURROGATE) {
            policy("""{"artist":"\uD800","library_entry_local_id":"id","title":"t"}""")
        }
    }

    @Test
    fun storedPayloadMustAlreadyBeCanonical() {
        assertCode(LocalIntentPayloadErrorCode.NON_CANONICAL_PAYLOAD) {
            LocalIntentPayloadPolicy.validateStoredCanonical(
                LocalIntentPayloadPolicy.USER_TRACK_REF_CREATED,
                1,
                LocalIntentPayloadPolicy.USER_TRACK_REF,
                "{\"title\":\"t\",\"artist\":\"a\",\"library_entry_local_id\":\"id\"}",
            )
        }
    }

    @Test
    fun acceptsAtLimitAndRejectsOverLimit() {
        val emptyPayload = "{\"artist\":\"\",\"library_entry_local_id\":\"id\",\"title\":\"t\"}"
        val atLimitArtist = "a".repeat(
            LocalIntentPayloadPolicy.MAX_CANONICAL_BYTES - policy(emptyPayload).toByteArray().size,
        )
        val atLimit = "{\"artist\":\"$atLimitArtist\",\"library_entry_local_id\":\"id\",\"title\":\"t\"}"
        assertEquals(LocalIntentPayloadPolicy.MAX_CANONICAL_BYTES, policy(atLimit).toByteArray().size)
        assertCode(LocalIntentPayloadErrorCode.PAYLOAD_TOO_LARGE) {
            policy("{\"artist\":\"${atLimitArtist}a\",\"library_entry_local_id\":\"id\",\"title\":\"t\"}")
        }
    }

    private fun policy(
        payload: String = "{\"artist\":\"a\",\"library_entry_local_id\":\"id\",\"title\":\"t\"}",
        eventType: String = LocalIntentPayloadPolicy.USER_TRACK_REF_CREATED,
        schemaVersion: Int = 1,
        aggregateType: String = LocalIntentPayloadPolicy.USER_TRACK_REF,
    ): String = LocalIntentPayloadPolicy.canonicalize(eventType, schemaVersion, aggregateType, payload)

    private fun assertCode(code: LocalIntentPayloadErrorCode, action: () -> Unit) {
        try {
            action()
            fail("Expected ${code.name}")
        } catch (error: LocalIntentPayloadException) {
            assertEquals(code, error.code)
            assertEquals(code.name, error.message)
        }
    }
}
