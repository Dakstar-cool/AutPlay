package app.autplay.ui.core

import android.text.format.DateUtils
import app.autplay.application.artist.ArtistSummary
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.application.library.CoreHomePlaylistSummary
import app.autplay.application.library.CoreHomeTrackSummary
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.library.CoreLocalAudioSummary
import app.autplay.application.library.CoreReleaseSummary
import app.autplay.application.library.CoreResumeQueueSummary
import app.autplay.application.library.CoreTrackPreferenceSummary
import app.autplay.ui.CoreCollectionUiItem
import app.autplay.ui.CoreArtistUiItem
import app.autplay.ui.CoreTrackUiItem
import app.autplay.ui.HomeContinueUiItem
import app.autplay.ui.HomeProblemUiItem
import app.autplay.ui.HomeRecommendationUiItem
import app.autplay.ui.HomeReleaseUiItem
import app.autplay.ui.HomeScreenUiState
import app.autplay.ui.HomeTrackUiItem
import app.autplay.ui.LibraryScreenUiState
import app.autplay.ui.ArtistBrowseUiState

internal fun buildLibraryTrackSummaries(
    entries: List<CoreLibraryEntrySummary>,
    audioStates: List<CoreLocalAudioSummary>,
    preferences: List<CoreTrackPreferenceSummary>,
    downloadedTrackIds: Set<String>,
    serverConnected: Boolean,
    untitledTrack: String,
): List<CoreTrackSummary> {
    val audioByTrack = audioStates.groupBy(CoreLocalAudioSummary::stableId)
    val lovedTrackIds = preferences.asSequence().filter(CoreTrackPreferenceSummary::loved)
        .map(CoreTrackPreferenceSummary::stableId).toSet()
    return entries.asSequence().filterNot(CoreLibraryEntrySummary::removed).mapIndexed { index, entry ->
        val audio = audioByTrack[entry.localUserTrackRefId].orEmpty()
        val downloaded = entry.localUserTrackRefId in downloadedTrackIds
        CoreTrackSummary(
            stableId = entry.localUserTrackRefId,
            title = entry.title ?: untitledTrack,
            artist = entry.artistName,
            addedAtMs = entry.addedAtMs,
            sourceOrder = index,
            loved = entry.localUserTrackRefId in lovedTrackIds,
            downloaded = downloaded,
            availability = resolveTrackAvailability(
                entry.availabilityStatus,
                audio,
                downloaded,
                serverConnected && entry.serverPlaybackCandidate,
            ),
        )
    }.toList()
}

private fun resolveTrackAvailability(
    libraryAvailability: String,
    audioStates: List<CoreLocalAudioSummary>,
    downloaded: Boolean,
    serverPlaybackCandidate: Boolean,
): TrackAvailability = when {
    downloaded || serverPlaybackCandidate || libraryAvailability == "LOCAL" ||
        audioStates.any { it.status == "AVAILABLE" && it.persistedUriPermission } -> TrackAvailability.Available
    audioStates.any { it.status == "PERMISSION_REVOKED" || !it.persistedUriPermission } ->
        TrackAvailability.PermissionRevoked
    audioStates.isNotEmpty() -> TrackAvailability.Missing
    else -> TrackAvailability.MetadataOnly
}

internal data class HomeProblemCounts(
    val permissionRevoked: Int,
    val reviewRequired: Int,
    val failedDownloads: Int,
)

internal fun countHomeProblems(
    tracks: List<CoreTrackSummary>,
    reviewCount: Int,
    downloads: List<DownloadIntentPresentation>,
): HomeProblemCounts = HomeProblemCounts(
    permissionRevoked = tracks.count { it.availability == TrackAvailability.PermissionRevoked },
    reviewRequired = reviewCount,
    failedDownloads = downloads.count { it.state == "FAILED" },
)

internal fun buildHomeScreenUiState(
    localMode: Boolean,
    recommendationLoading: Boolean,
    offlineFallback: Boolean,
    releases: List<CoreReleaseSummary>,
    recommendations: List<HomeRecommendationUiItem>,
    continueListening: CoreResumeQueueSummary?,
    recentlyPlayed: List<CoreHomeTrackSummary>,
    recentlyAdded: List<CoreHomeTrackSummary>,
    playlists: List<CoreHomePlaylistSummary>,
    libraryTracks: List<CoreTrackSummary>,
    problems: List<HomeProblemUiItem>,
    recommendationError: Boolean,
    untitledTrack: String,
): HomeScreenUiState {
    val playableTrackIds = libraryTracks.asSequence()
        .filter { it.availability == TrackAvailability.Available }
        .map(CoreTrackSummary::stableId)
        .toSet()
    return HomeScreenUiState(
    localMode = localMode,
    recommendationLoading = recommendationLoading,
    offlineFallback = offlineFallback,
    releases = releases.map { HomeReleaseUiItem(it.stableId, it.title, it.artistName, null) },
    recommendations = recommendations,
    continueListening = continueListening?.takeIf { it.localUserTrackRefId in playableTrackIds }?.let { queue ->
        HomeContinueUiItem(
            trackId = queue.localUserTrackRefId,
            title = queue.title ?: untitledTrack,
            artist = queue.artistName,
            positionText = DateUtils.formatElapsedTime(queue.positionMs.coerceAtLeast(0) / 1_000),
        )
    },
    recentlyPlayed = recentlyPlayed.filter { it.stableId in playableTrackIds }
        .map { HomeTrackUiItem(it.stableId, it.title ?: untitledTrack, it.artistName) },
    recentlyAdded = recentlyAdded.filter { it.stableId in playableTrackIds }
        .map { HomeTrackUiItem(it.stableId, it.title ?: untitledTrack, it.artistName) },
    playlists = playlists.map { CoreCollectionUiItem(it.stableId, it.title, it.description) },
    offlineReady = libraryTracks.filter(CoreTrackSummary::downloaded).take(8)
        .map { HomeTrackUiItem(it.stableId, it.title, it.artist) },
    problems = problems,
    recommendationError = recommendationError,
)
}

internal fun buildLibraryScreenUiState(
    localMode: Boolean,
    tracks: List<CoreTrackSummary>,
    section: LibrarySection,
    sort: LibrarySort,
    filter: LibraryFilter,
    selectedTrackRefId: String?,
    artists: List<ArtistSummary>,
    artistBrowseState: ArtistBrowseUiState,
    playlists: List<CoreHomePlaylistSummary>,
    releases: List<CoreReleaseSummary>,
    reviewCount: Int,
    importingLocalTrack: Boolean = false,
    lastImportedTitle: String? = null,
    error: Boolean,
): LibraryScreenUiState {
    val effectiveFilter = when (section) {
        LibrarySection.Offline -> LibraryFilter.Downloaded
        LibrarySection.Unavailable -> LibraryFilter.Unavailable
        else -> filter
    }
    val visibleTracks = filterAndSortTracks(tracks, effectiveFilter, sort).map { item ->
        CoreTrackUiItem(
            id = item.stableId,
            title = item.title,
            artist = item.artist,
            selected = item.stableId == selectedTrackRefId,
            permissionRevoked = item.availability == TrackAvailability.PermissionRevoked,
            downloaded = item.downloaded,
            loved = item.loved,
        )
    }
    return LibraryScreenUiState(
        localMode = localMode,
        tracks = visibleTracks,
        section = section,
        sort = sort,
        filter = filter,
        artists = artists.map { artist ->
            CoreArtistUiItem(
                id = artist.key.artistId.value,
                name = artist.name,
                subtitle = listOfNotNull(artist.disambiguation, artist.countryCode)
                    .filter(String::isNotBlank).joinToString(" · ").ifBlank { null },
            )
        },
        artistBrowseState = artistBrowseState,
        playlists = playlists.map { CoreCollectionUiItem(it.stableId, it.title, it.description) },
        albums = releases.map { CoreCollectionUiItem(it.stableId, it.title, it.artistName) },
        reviewCount = reviewCount,
        importingLocalTrack = importingLocalTrack,
        lastImportedTitle = lastImportedTitle,
        error = error,
    )
}
