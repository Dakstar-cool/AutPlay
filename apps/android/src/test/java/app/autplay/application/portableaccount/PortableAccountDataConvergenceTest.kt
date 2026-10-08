package app.autplay.application.portableaccount

import app.autplay.data.local.PortableLocalListeningRow
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class PortableAccountDataConvergenceTest {
    @Test fun twoServerCopiesAbsorbRepeatedRoundtripsWithStableHashAndAllAliases() {
        val a = listening(id(10), id(20), id(50), "Zulu", "\uE000", 60000, "SEARCH", "SLEEP", false, 300, listOf("\uE000", "Jazz"))
        val b = listening(id(20), id(30), id(40), "Alpha", "😀", 90000, "ORGANIC", "GENERAL", true, 200, listOf("😀", "Ambient"))
        val merged = PortableAccountDataCodec.merge(a, b)
        val expectedHash = PortableAccountDataCodec.hash(merged.document)
        assertEquals(expectedHash, PortableAccountDataCodec.hash(PortableAccountDataCodec.merge(b, a).document))
        var current = merged
        listOf(a, b, a, b, merged).forEach { remote ->
            current = PortableAccountDataCodec.merge(current, remote)
            assertEquals(expectedHash, PortableAccountDataCodec.hash(current.document))
        }
        assertEquals(listOf(id(10), id(20), id(30)), current.document["event_ids"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals("Alpha", current.document["title"]!!.jsonPrimitive.content)
        assertEquals("\uE000", current.document["artist"]!!.jsonPrimitive.content)
        assertEquals(60000L, current.document["duration_ms"]!!.jsonPrimitive.long)
        assertEquals("GENERAL", current.document["context"]!!.jsonPrimitive.content)
        assertEquals("ORGANIC", current.document["event_origin"]!!.jsonPrimitive.content)
        assertEquals(id(40), current.document["track_key"]!!.jsonPrimitive.content)
        assertTrue(current.document["excluded_from_taste"]!!.jsonPrimitive.boolean)
        // Native legacy playlist limit and Unicode code-point length match Python validation.
        val playlist = PortableAccountDataCodec.checked(id(60), "PLAYLIST", buildJsonObject {
            put("version", 1); put("updated_at_ms", 0); put("name", "😀".repeat(500)); put("description", JsonNull); put("deleted", false)
        })
        assertEquals(500, playlist.document["name"]!!.jsonPrimitive.content.codePointCount(0, 1000))
    }

    private fun listening(event: String, alias: String, track: String, title: String, artist: String, duration: Long,
        origin: String, context: String, excluded: Boolean, updated: Long, genres: List<String>): PortableDataRecord {
        val base = PortableAccountDataCodec.listening(PortableLocalListeningRow(event, alias, track, title, artist, null, 1, 30000, duration, excluded, origin, context, updated))
        return PortableAccountDataCodec.checked(base.recordId, base.kind, JsonObject(base.document + ("genres" to JsonArray(genres.map(::JsonPrimitive)))))
    }
    private fun id(number: Int): String = "00000000-0000-0000-0000-%012d".format(number)
}
