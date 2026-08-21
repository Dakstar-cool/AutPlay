package app.autplay.ui.core

import java.nio.charset.StandardCharsets
import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CoreProductStateTest {
    @Test
    fun savedStateRoundTripsSelectionScopesAndStableDetail() {
        val state = CoreProductSavedState(
            query = "live remix / тест",
            scopes = setOf(SearchScope.Local, SearchScope.Vault),
            librarySection = LibrarySection.Albums,
            librarySort = LibrarySort.Artist,
            libraryFilter = LibraryFilter.Downloaded,
            selectedDetail = DetailTarget(DetailKind.Release, "release-id:42"),
            searchListAnchor = ListAnchor("search:live remix / тест", "search-result:track/7", 12),
            libraryListAnchor = ListAnchor("library:Albums:Artist:Downloaded", "library-album:release-id:42", 4),
        )

        assertEquals(state, CoreProductSavedState.decode(state.encode()))
        assertNull(CoreProductSavedState.decode(listOf("future")))
    }

    @Test
    fun productionUiStateSnapshotsOnlyInteractionIdentity() {
        val state = CoreProductUiState(CoreProductSavedState())
        state.query = "saved query"
        state.librarySection = LibrarySection.Playlists
        state.librarySort = LibrarySort.Title
        state.libraryFilter = LibraryFilter.Loved
        state.selectDetail(DetailTarget(DetailKind.Playlist, "playlist-id"))
        state.searchListAnchor = ListAnchor("search:saved query", "search-result:entry-id", 7)
        state.libraryListAnchor = ListAnchor("library:Playlists:Title:Loved", "library-playlist:playlist-id", 3)

        assertEquals(state.snapshot(), CoreProductSavedState.decode(state.snapshot().encode()))
        state.clearDetail()
        assertNull(state.selectedDetail)
    }

    @Test
    fun artistDetailRoundTripsCanonicalIdentityWithoutUsingDisplayName() {
        val artistId = "11111111-1111-4111-8111-111111111111"
        val state = CoreProductUiState(CoreProductSavedState())

        state.selectDetail(DetailTarget(DetailKind.Artist, artistId))

        val restored = CoreProductSavedState.decode(state.snapshot().encode())
        assertEquals(DetailTarget(DetailKind.Artist, artistId), restored?.selectedDetail)
    }

    @Test
    fun versionOneSavedStateRetainsSelectionAndMigratesLegacyAnchor() {
        fun encoded(value: String): String = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(value.toByteArray(StandardCharsets.UTF_8))
        val restored = CoreProductSavedState.decode(
            listOf(
                "1",
                encoded("older query"),
                "Local",
                "Tracks",
                "RecentlyAdded",
                "All",
                "Track",
                encoded("track-id"),
                encoded("legacy-row-id"),
            ),
        )

        assertEquals("older query", restored?.query)
        assertEquals(DetailTarget(DetailKind.Track, "track-id"), restored?.selectedDetail)
        assertEquals(ListAnchor("legacy", "legacy-row-id"), restored?.libraryListAnchor)
    }

    @Test
    fun staleSearchGenerationCannotReplaceNewerQueryOrBinding() {
        val guard = SearchGenerationGuard()
        val old = guard.begin("older", setOf(SearchScope.Local), "profile-a")
        val current = guard.begin("newer", setOf(SearchScope.Local, SearchScope.Vault), "profile-a")

        assertFalse(guard.accepts(old))
        assertTrue(guard.accepts(current))
        guard.invalidate()
        assertFalse(guard.accepts(current))
    }

    @Test
    fun visibleSearchRowsClearAndRejectOldRequestAfterQueryChange() {
        val guard = SearchGenerationGuard()
        val store = SearchResultStore<String>()
        val old = guard.begin("older", setOf(SearchScope.Local), "profile-a")
        store.start(old)
        assertTrue(store.accept(old, listOf("old-track")))

        guard.invalidate()
        store.invalidate()
        val current = guard.begin("newer", setOf(SearchScope.Local), "profile-a")
        store.start(current)

        assertTrue(store.results.isEmpty())
        assertFalse(store.accept(old, listOf("late-old-track")))
        assertTrue(store.results.isEmpty())
        assertTrue(store.accept(current, listOf("new-track")))
        assertEquals(listOf("new-track"), store.results)
    }

    @Test
    fun lateOldProfileResultsCannotCrossBindingChange() {
        val guard = SearchGenerationGuard()
        val store = SearchResultStore<String>()
        val profileA = guard.begin("query", setOf(SearchScope.Local), "profile-a")
        store.start(profileA)
        assertTrue(store.accept(profileA, listOf("private-a-track")))

        // Composition may observe the new binding before its LaunchedEffect invalidates old work.
        assertTrue(store.visibleFor("query", setOf(SearchScope.Local), "profile-b").isEmpty())
        assertFalse(store.matchesContext("query", setOf(SearchScope.Local), "profile-b"))

        guard.invalidate()
        store.invalidate()
        val profileB = guard.begin("query", setOf(SearchScope.Local), "profile-b")
        store.start(profileB)

        assertFalse(guard.accepts(profileA))
        assertFalse(store.accept(profileA, listOf("private-a-track")))
        assertTrue(store.results.isEmpty())
        assertTrue(guard.accepts(profileB))
        assertTrue(store.accept(profileB, listOf("profile-b-track")))
        assertEquals(listOf("profile-b-track"), store.results)
    }

    @Test
    fun filteringAndSortingRetainsStableTrackIdentity() {
        val tracks = listOf(
            track("two", "Beta", 2, loved = true, downloaded = false),
            track("one", "alpha", 1, loved = true, downloaded = true),
            track("missing", "Lost", 3, loved = false, downloaded = false, TrackAvailability.Missing),
        )

        assertEquals(
            listOf("one", "two"),
            filterAndSortTracks(tracks, LibraryFilter.Loved, LibrarySort.Title).map { it.stableId },
        )
        assertEquals(
            listOf("missing"),
            filterAndSortTracks(tracks, LibraryFilter.Unavailable, LibrarySort.RecentlyAdded).map { it.stableId },
        )
    }

    @Test
    fun playlistOrderPreservesDuplicateTrackOccurrences() {
        val ordered = orderedPlaylistOccurrences(
            listOf(
                PlaylistOccurrence("entry-b", "same-track", "b"),
                PlaylistOccurrence("entry-a", "same-track", "a"),
            ),
        )

        assertEquals(listOf("entry-a", "entry-b"), ordered.map { it.playlistEntryId })
        assertEquals(listOf("same-track", "same-track"), ordered.map { it.trackRefId })
    }

    @Test
    fun singleFlightGateDispatchesOneActionUntilCompletion() {
        val gate = SingleFlightActionGate()

        assertTrue(gate.begin("like:track"))
        assertFalse(gate.begin("like:track"))
        gate.complete("like:track")
        assertTrue(gate.begin("like:track"))
    }

    private fun track(
        id: String,
        title: String,
        order: Int,
        loved: Boolean,
        downloaded: Boolean,
        availability: TrackAvailability = TrackAvailability.Available,
    ): CoreTrackSummary = CoreTrackSummary(
        stableId = id,
        title = title,
        artist = "Artist",
        addedAtMs = order.toLong(),
        sourceOrder = order,
        loved = loved,
        downloaded = downloaded,
        availability = availability,
    )
}
