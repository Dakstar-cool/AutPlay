package app.autplay.application.server

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Test

class AlbumTracklistTest {
    @Test fun preservesPrintedPositionsRepeatedSongsAndUnknownAvailability() {
        val rows = decodeAlbumTracklist(Json.parseToJsonElement("""
            {"source":"discogs","id":"123","tracks":[
                {"position":"A1","title":"Theme","in_vault":true},
                {"position":"B4","title":"Theme","in_vault":null},
                {"position":"B5","title":"Finale","in_vault":false}]}
        """).jsonObject).tracks
        assertEquals(listOf("A1", "B4", "B5"), rows.map { it.position })
        assertEquals(listOf(true, null, false), rows.map { it.inVault })
    }

    @Test fun onlyReleaseLinksAndNumericIdsCanSelectDiscogsEditions() {
        assertEquals("123", discogsReleaseId("123"))
        assertEquals("123", discogsReleaseId("https://www.discogs.com/ru/release/123-Album"))
        assertNull(discogsReleaseId("https://www.discogs.com/master/123"))
        assertNull(discogsReleaseId("https://example.org/release/123"))
    }
}
