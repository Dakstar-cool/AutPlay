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
    @Test fun cachedSourceTitlesRefreshWhileManualEditsKeepTheirSpelling() {
        val title = "По разные стороны (Mood Video)"
        fun cached(source: String, locked: Boolean, value: String = title, version: String = "display-v6") = TrackMetadata.decode(Json.parseToJsonElement(
            """{"revision":2,"state":"READY","fields":{"title":"$value"},"provenance":{"title":{"source":"$source","normalization_version":"$version","locked":$locked}}}"""
        ).jsonObject)
        assertEquals("По разные стороны", cached("SOURCE_NATIVE", false).names(title, "Максим Свобода").title)
        assertEquals(title, cached("USER", true).names(title, "Максим Свобода").title)
        val speed = "молчи speed up"
        assertEquals("молчи (Sped Up)", cached("SOURCE_LABEL", false, speed, "display-v7").names(speed, "кис-кис").title)
        assertEquals(speed, cached("USER", true, speed, "display-v7").names(speed, "кис-кис").title)
    }

    @Test fun cachedSiteCreditsRefreshOnlyForProvisionalMetadata() {
        fun credit(source: String) = TrackMetadata.decode(Json.parseToJsonElement(
            """{"revision":2,"state":"READY","fields":{"artist":"Artist [muzmo.ru]"},"provenance":{"artist":{"source":"$source","normalization_version":"display-v7"}}}"""
        ).jsonObject).names("Track", "Artist [muzmo.ru]").artist
        assertEquals("Artist", credit("EMBEDDED"))
        assertEquals("Artist [muzmo.ru]", credit("USER"))
        assertEquals("Artist [muzmo.ru]", credit("MUSICBRAINZ"))
    }

    @Test fun cachedFeatureMovesBothFieldsAndRespectsManualArtist() {
        val title = "Рядом быть feat. Хелависа"
        fun cached(artistSource: String) = TrackMetadata.decode(Json.parseToJsonElement(
            """{"revision":1,"state":"READY","fields":{"title":"$title","artist":"После 11"},"provenance":{"title":{"source":"EMBEDDED","normalization_version":"display-v8"},"artist":{"source":"$artistSource","normalization_version":"display-v8"}}}"""
        ).jsonObject).names("01.mp3", "После 11")
        assertEquals("Рядом быть", cached("EMBEDDED").title)
        assertEquals("После 11 feat. Хелависа", cached("EMBEDDED").artist)
        assertEquals(title, cached("USER").title)
        assertEquals("После 11", cached("USER").artist)
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
