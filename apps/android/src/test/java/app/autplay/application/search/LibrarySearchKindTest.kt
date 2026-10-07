package app.autplay.application.search

import org.junit.Assert.*
import org.junit.Test

class LibrarySearchKindTest {
    private val builder = SafeFtsQueryBuilder()

    @Test fun fieldFilterAppliesToEveryTermAndUsesStableWireNames() {
        val expected = listOf(
            LibrarySearchKind.All to "{title artist album}", LibrarySearchKind.Track to "title",
            LibrarySearchKind.Artist to "artist", LibrarySearchKind.Album to "album",
        )
        expected.forEach { (kind, columns) ->
            assertEquals("$columns:(\"first\"* AND \"second\"*)", builder.build("First Second", kind))
        }
        assertEquals(listOf("all", "track", "artist", "album"), LibrarySearchKind.entries.map { it.wireValue })
    }

    @Test fun queryCannotChangeSelectedColumnsOrInjectMatchOperators() {
        val match = builder.build("title:(secret OR *) - {album} \"quoted\" NEAR(a,b)", LibrarySearchKind.Artist)!!
        assertTrue(match.startsWith("artist:("))
        assertEquals(1, match.count { it == ':' })
        assertFalse(match.contains(" OR "))
        assertFalse(match.contains("NEAR("))
        assertFalse(match.contains('{'))
        assertEquals(1, match.count { it == '(' })
        assertEquals(1, match.count { it == ')' })
    }

    @Test fun everyKindRejectsEmptyOrUnsearchableInputAndPreservesBoundedUnicodeTokens() {
        LibrarySearchKind.entries.forEach { kind ->
            assertNull(builder.build(" \"***[]:+", kind))
            val match = builder.build("Музыка 東京 " + "x".repeat(1000), kind)!!
            assertTrue(match.contains("\"музыка\"*"))
            assertTrue(match.contains("\"東京\"*"))
            assertFalse(match.contains("x".repeat(49)))
            assertTrue(match.length < 256)
        }
    }
}
