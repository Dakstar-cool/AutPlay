package app.autplay

import app.autplay.application.artist.artistSearchKey
import app.autplay.application.artist.musicSearchKey
import app.autplay.application.artist.RoomArtistCatalogPort
import app.autplay.application.artist.TrackTitleQuery
import app.autplay.application.library.CoreProductRepository
import app.autplay.data.local.AutPlayDatabase
import app.autplay.application.artist.artistScope
import app.autplay.ui.CoreArtistUiItem
import app.autplay.ui.CoreCollectionUiItem
import app.autplay.ui.CoreTrackUiItem
import app.autplay.ui.LibraryScreenUiState
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map

internal class PersonalLibrarySearch(
    private val database: AutPlayDatabase,
    private val library: CoreProductRepository,
    private val artists: RoomArtistCatalogPort,
    private val profile: String?,
    private val preferenceOwner: String = profile ?: "legacy-unscoped",
) {
    fun find(section: LibrarySection, filter: LibraryFilter, query: String): Flow<LibraryScreenUiState> {
        val state = LibraryScreenUiState(localMode = profile == null, tracks = emptyList(), section = section, filter = filter)
        val owner = profile ?: "legacy-unscoped"
        val titleQuery = musicSearchKey(query)
        val trackQuery = TrackTitleQuery(query)
        val artistQuery = artistSearchKey(app.autplay.application.artist.StageCredits.display(query.trim()))
        fun matches(value: String?) = value != null && titleQuery in musicSearchKey(value)
        fun matchesCredit(credit: String?) = credit != null && artistQuery in artistSearchKey(credit)
        val results = when (section) {
            LibrarySection.Tracks -> library.libraryEntries(profile).map { rows ->
                state.copy(tracks = rows.filter { (filter != LibraryFilter.Loved || it.loved) &&
                    (filter != LibraryFilter.OnDevice || it.onDevice) &&
                    (trackQuery.matches(it.title) || matchesCredit(it.artistName)) }.map {
                    CoreTrackUiItem(it.localUserTrackRefId, it.title.orEmpty(), it.artistName, loved = it.loved)
                })
            }
            LibrarySection.Artists -> artists.search(artistScope(profile), query, personal = true).map { rows ->
                state.copy(artists = rows.map { artist -> CoreArtistUiItem(artist.key.artistId.value, artist.name, artist.disambiguation, artist.portraitTrackIds, artist.portraitArtistIds) })
            }
            LibrarySection.Albums -> library.releaseSummaries(profile, Int.MAX_VALUE).map { rows ->
                state.copy(albums = rows.filter { matches(it.title) || matchesCredit(it.artistName) }.map {
                    CoreCollectionUiItem(it.stableId, it.title, it.artistName, it.ownedTrackCount, it.artworkTrackId, it.mbid, it.releaseDateText)
                })
            }
            LibrarySection.Playlists -> database.playlistDao().libraryPlaylists(owner, Int.MAX_VALUE).map { rows ->
                state.copy(playlists = rows.filter { matches(it.playlist.name) }.map {
                    CoreCollectionUiItem(it.playlist.localPlaylistId, it.playlist.name, it.playlist.description, artworkTrackId = it.artworkTrackId)
                })
            }
            else -> flowOf(state)
        }
        return kotlinx.coroutines.flow.combine(results, database.collectionPreferences().observeFavorites(preferenceOwner)) { rows, saved ->
            rows.withSavedCollections(saved.filter {
                if (it.kind == "artist") app.autplay.application.artist.artistNameMatches(it.label.orEmpty(), query) else matches(it.label)
            })
        }.flowOn(Dispatchers.Default)
    }
}

internal fun LibraryScreenUiState.withSavedCollections(saved: List<app.autplay.data.local.CollectionPreferenceEvent>): LibraryScreenUiState {
    val artistsSaved = saved.filter { it.kind == "artist" }.mapNotNull { item ->
        val name = item.label ?: return@mapNotNull null
        item.route?.let { CoreArtistUiItem(it, name, null) }
    }
    val albumsSaved = saved.filter { it.kind == "album" }.mapNotNull { item ->
        val title = item.label ?: return@mapNotNull null
        item.route?.let { CoreCollectionUiItem(it, title, null) }
    }
    return copy(artists = (artists + artistsSaved).distinctBy { it.id },
        albums = (albums + albumsSaved).distinctBy { it.id })
}
