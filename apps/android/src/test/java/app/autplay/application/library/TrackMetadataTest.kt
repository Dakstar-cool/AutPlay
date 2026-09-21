package app.autplay.application.library

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class TrackMetadataTest {
    @Test fun impossibleDatesAreRejectedWithoutInventingPrecision() {
        listOf("1998", "2001-04", "2024-02-29").forEach { assertTrue(validMetadataDate(it)) }
        listOf("0000", "2001-13", "2025-02-29", "2025-02-31", "2025-1").forEach { assertFalse(validMetadataDate(it)) }
    }
    @Test fun explicitClearIsDifferentFromMissingFieldAndDateKeepsPrecision() {
        val value = TrackMetadata.decode(Json.parseToJsonElement("""{"revision":4,"state":"READY","fields":{"album":null,"release_date":"1998"},"candidates":[]}""").jsonObject)
        assertNull(value.text("album", "Old album"))
        assertEquals("Original", value.text("title", "Original"))
        assertEquals("1998", value.text("release_date"))
    }
    @Test fun oversizedCandidatesAndRemoteLocatorsAreRejected() {
        val six = JsonArray(List(6) { JsonObject(emptyMap()) })
        assertThrows(IllegalArgumentException::class.java) { TrackMetadata.decode(buildJsonObject {
            put("revision", 1); put("state", "REVIEW"); put("candidates", six)
        }) }
        assertThrows(IllegalArgumentException::class.java) { TrackMetadata.decode(buildJsonObject {
            put("revision", 1); put("state", "READY"); put("artwork_sha256", "https://external.test/private.jpg")
        }) }
    }
}
