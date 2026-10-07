package app.autplay.ui.core

import app.autplay.ui.ArtistBrowseUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CoreProductRefreshPolicyTest {
    @Test
    fun sourcesAlwaysIncludeLocalAndFollowConnectionInsteadOfSavedChoice() {
        assertEquals(setOf(SearchScope.Local), automaticSearchScopes(null))
        assertEquals(setOf(SearchScope.Local, SearchScope.Vault), automaticSearchScopes("profile-a"))
    }

    @Test
    fun oldSavedUnavailableLibraryAndTrackDetailRestoreToOrdinaryBrowse() {
        val saved = CoreProductSavedState(
            query = "saved query",
            librarySection = LibrarySection.Unavailable,
            libraryFilter = LibraryFilter.Unavailable,
            selectedDetail = DetailTarget(DetailKind.Track, "track-a"),
        )
        val state = CoreProductUiState(checkNotNull(CoreProductSavedState.decode(saved.encode())))
        assertEquals("saved query", state.query)
        assertEquals(LibrarySection.Tracks, state.librarySection)
        assertEquals(LibraryFilter.All, state.libraryFilter)
        assertNull(state.selectedDetail)
    }

    @Test
    fun oldTrackSelectionEffectCannotReplaceAnOpenCollection() {
        val state = CoreProductUiState(CoreProductSavedState())
        val album = DetailTarget(DetailKind.Release, "release-a")
        state.selectDetail(album)
        state.selectDetail(DetailTarget(DetailKind.Track, "track-a"))
        assertEquals(album, state.selectedDetail)
        state.clearDetail()
        state.selectedDetail = DetailTarget(DetailKind.Track, "track-a")
        assertNull(state.selectedDetail)
    }

    @Test
    fun restoredOfflineNavigationRedirectsToDownloadsOnceWithoutFilteringLibrary() {
        val state = CoreProductUiState(CoreProductSavedState(
            librarySection = LibrarySection.Offline,
            libraryFilter = LibraryFilter.Downloaded,
        ))
        assertEquals(LibrarySection.Tracks, state.librarySection)
        assertEquals(LibraryFilter.All, state.libraryFilter)
        assertEquals(LibrarySection.Offline, state.snapshot().librarySection)
        assertTrue(state.consumeLegacyDownloadsRedirect())
        assertEquals(false, state.consumeLegacyDownloadsRedirect())
        assertEquals(LibrarySection.Tracks, state.snapshot().librarySection)
    }

    @Test
    fun retiredOfflineSectionDoesNotSilentlyKeepADownloadOnlyFilter() {
        val tracks = listOf(
            summary("downloaded", downloaded = true, availability = TrackAvailability.Available),
            summary("revoked", downloaded = false, availability = TrackAvailability.PermissionRevoked),
        )
        val state = buildLibraryScreenUiState(
            localMode = false,
            tracks = tracks,
            section = LibrarySection.Offline,
            sort = LibrarySort.Title,
            filter = LibraryFilter.Downloaded,
            selectedTrackRefId = null,
            artists = emptyList(),
            artistBrowseState = ArtistBrowseUiState.Unavailable,
            playlists = emptyList(),
            releases = emptyList(),
            reviewCount = 0,
            error = false,
        )
        assertEquals(LibrarySection.Tracks, state.section)
        assertEquals(LibraryFilter.All, state.filter)
        assertEquals(setOf("downloaded", "revoked"), state.tracks.map { it.id }.toSet())
        assertTrue(state.tracks.single { it.id == "revoked" }.permissionRevoked)
        assertTrue(state.tracks.single { it.id == "downloaded" }.downloaded)
    }

    private fun summary(id: String, downloaded: Boolean, availability: TrackAvailability) = CoreTrackSummary(
        stableId = id,
        title = id,
        artist = null,
        addedAtMs = 0,
        sourceOrder = 0,
        loved = false,
        downloaded = downloaded,
        availability = availability,
    )
}
