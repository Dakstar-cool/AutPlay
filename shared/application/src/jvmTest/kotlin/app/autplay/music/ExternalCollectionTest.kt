package app.autplay.application.library

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class ExternalCollectionTest {
    private val operation = "e2985428-de82-4936-8033-88522eb50e39"

    @Test fun accountConfirmationUsesSourceCountsAndTheSelectedAction() {
        val doc = Json.parseToJsonElement("""
            {"operation_id":"$operation","revision":3,"state":"PREVIEW","name":"Edition",
             "action":"ALBUM","expected":100,"read":90,"identified":50,"playable":4,"complete":false,
             "offset":0,"next_offset":50,"entries":[{"title":"Track","disc":2,"number":1}],
             "missing_occurrences":2}
        """).jsonObject
        val preview = ExternalCollection.decode(doc)
        assertEquals(listOf("Track · 2.1"), preview.tracks)
        assertEquals(50, preview.nextOffset)
        assertEquals(2, preview.missingOccurrences)
        assertFalse(preview.complete)
        val request = ExternalCollection.startBody("https://music.yandex.ru/album/1", false, operation)
        assertEquals("false", request.getValue("favorites").jsonPrimitive.content)
        assertFalse("owner" in request)
    }

    @Test fun sharedLinksRequireAnExactProviderOrigin() {
        assertTrue(ExternalCollection.isProviderLink("https://music.yandex.ru/album/1"))
        assertFalse(ExternalCollection.isProviderLink("https://user@music.yandex.ru/album/1"))
        assertFalse(ExternalCollection.isProviderLink("https://open.spotify.com.example/collection/tracks"))
    }
}
