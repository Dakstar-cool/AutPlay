package app.autplay

import app.autplay.application.library.CoreLibraryEntrySummary
import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackContinuationTest {
    @Test fun libraryRetainsSuccessorsAndFiltersRemovedEntries() {
        val library = listOf(entry("first"), entry("selected"), entry("removed", true), entry("next"), entry("next"))
        assertEquals(listOf("first", "selected", "next"), continuationTrackIds("selected", "LIBRARY", library))
    }

    @Test fun homeOrDownloadedTrackOutsideLibraryStartsBeforeLibraryContinuation() {
        assertEquals(listOf("selected", "next"), continuationTrackIds("selected", "USER", listOf(entry("next"))))
        assertEquals(listOf("selected"), continuationTrackIds("selected", "LIBRARY", emptyList()))
    }

    @Test fun specialQueuesKeepTheirOwnContext() {
        for (type in listOf("PLAYLIST", "SEARCH", "WAVE", "GUEST_WAVE")) {
            assertEquals(listOf("selected"), continuationTrackIds("selected", type, listOf(entry("next"))))
        }
    }

    @Test fun oversizedFavoritesKeepTheSelectedTrackAndItsSuccessorsPlayable() {
        val library = (0..10_001).map { entry(it.toString()) }
        val queue = continuationTrackIds("10000", "LIBRARY", library)
        assertEquals(10_000, queue.size)
        assertEquals(listOf("10000", "10001"), queue.takeLast(2))
        val outside = continuationTrackIds("other", "USER", library)
        assertEquals(10_000, outside.size)
        assertEquals("other", outside.first())
    }

    private fun entry(id: String, removed: Boolean = false) =
        CoreLibraryEntrySummary(id, id, id, null, 0, "AVAILABLE", removed, false)
}
