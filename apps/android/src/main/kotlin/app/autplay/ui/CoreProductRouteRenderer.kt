package app.autplay.ui

import app.autplay.application.recommendation.HomeWheelMix

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.Composable
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.core.DetailTarget
import app.autplay.application.search.LibrarySearchKind
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.LibrarySort
import app.autplay.ui.core.ListAnchor
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.ui.playlist.ManualPlaylistUi

internal data class CoreProductRouteActions(
    val openListenTogether: () -> Unit,
    val recommendationVisible: (String) -> Unit,
    val playRecommendation: (String) -> Unit = {},
    val likeRecommendation: (String) -> Unit,
    val dislikeRecommendation: (String) -> Unit,
    val retryHome: () -> Unit,
    val resumeHomeQueue: (String) -> Unit,
    val continueHomeQueue: () -> Unit = {},
    val openHomePlaylist: (String) -> Unit,
    val openOffline: () -> Unit,
    val openProblems: () -> Unit,
    val changeQuery: (String) -> Unit,
    val submitSearch: () -> Unit,
    val moreVaultResults: () -> Unit = {},
    val playSearchResult: (String) -> Unit,
    val playlistVaultTarget: suspend (String) -> String? = { null },
    val playVaultSearchResult: (String) -> Unit = {},
    val changeVaultScope: (Boolean) -> Unit,
    val changeSearchAnchor: (ListAnchor) -> Unit,
    val addLocal: () -> Unit,
    val selectTrack: (String) -> Unit,
    val removeOrRestore: (String) -> Unit,
    val likeTrack: (String) -> Unit,
    val changeLibrarySection: (LibrarySection) -> Unit,
    val changeLibrarySort: (LibrarySort) -> Unit,
    val changeLibraryFilter: (LibraryFilter) -> Unit,
    val openCollection: (LibrarySection, String) -> Unit,
    val openDetail: (DetailTarget) -> Unit,
    val openReview: () -> Unit,
    val changeLibraryAnchor: (ListAnchor) -> Unit,
    val playTrack: (String) -> Unit,
    val playPlaylistEntry: (String) -> Unit,
    val downloadTrack: (String) -> Unit,
    val repairAccess: () -> Unit,
    val playNext: (String) -> Unit = {},
    val addToQueue: (String) -> Unit = {},
    val manualPlaylists: List<ManualPlaylistUi> = emptyList(),
    val manualPlaylistActions: ManualPlaylistActions = ManualPlaylistActions(),
    val changeSearchKind: (LibrarySearchKind) -> Unit = {},
    val playWheelMix: (HomeWheelMix) -> Unit = {},
    val startMyWave: () -> Unit = {},
    val myWaveAllowed: Boolean = true,
    val searchPersonal: ((LibrarySection, LibraryFilter, String) -> kotlinx.coroutines.flow.Flow<LibraryScreenUiState>)? = null,
    val openHeroArtist: (() -> Unit)? = null,
    val playReleaseEntry: ((String) -> Unit)? = null,
    val beginHomeSeek: (Long) -> Unit = {},
    val updateHomeSeek: (Long) -> Unit = {},
    val commitHomeSeek: () -> Unit = {},
    val observeHomeTimeline: (Boolean) -> Unit = {},
)

@Composable
internal fun CoreProductRouteRenderer(
    destination: UiDestination,
    widthClass: UiWidthClass,
    contentPadding: PaddingValues,
    homeState: HomeScreenUiState,
    searchState: SearchScreenUiState,
    libraryState: LibraryScreenUiState,
    detailState: CoreProductDetailUiState,
    selectedDetail: DetailTarget?,
    searchListAnchor: ListAnchor?,
    libraryListAnchor: ListAnchor?,
    actions: CoreProductRouteActions,
    playerState: PlaybackPresentationState = PlaybackPresentationState(),
    currentTrackRefId: String? = null,
    currentTrackLiked: Boolean = false,
    onOpenNowPlaying: () -> Unit = {},
    onTogglePlayPause: () -> Unit = {},
    onPreviousTrack: () -> Unit = {},
    onNextTrack: () -> Unit = {},
    homeSwipeTargets: HomeSwipeTargets = HomeSwipeTargets(),
) {
    when (destination) {
        UiDestination.Home -> HomeProductScreen(
            state = homeState,
            contentPadding = contentPadding,
            onOpenListenTogether = actions.openListenTogether,
            onRecommendationVisible = actions.recommendationVisible,
            onPlayRecommendation = actions.playRecommendation,
            recommendationPlaybackAllowed = actions.myWaveAllowed,
            onLike = actions.likeRecommendation,
            onDislike = actions.dislikeRecommendation,
            onRetry = actions.retryHome,
            onPlayTrack = actions.resumeHomeQueue,
            onContinueListening = actions.continueHomeQueue,
            onStartMyWave = actions.startMyWave,
            onPlayWheelMix = actions.playWheelMix,
            onSeekBegin = actions.beginHomeSeek,
            onSeekUpdate = actions.updateHomeSeek,
            onSeekCommit = actions.commitHomeSeek,
            onTimelineObserving = actions.observeHomeTimeline,
            myWaveAllowed = actions.myWaveAllowed,
            onAddLocal = actions.addLocal,
            importingLocalTrack = libraryState.importingLocalTrack,
            onOpenPlaylist = actions.openHomePlaylist,
            onOpenOffline = actions.openOffline,
            onOpenProblems = actions.openProblems,
            playerState = playerState,
            currentTrackRefId = currentTrackRefId,
            currentTrackLiked = currentTrackLiked,
            onOpenPlayer = onOpenNowPlaying,
            onOpenHeroArtist = actions.openHeroArtist,
            onTogglePlayPause = onTogglePlayPause,
            onLikeHeroTrack = actions.likeTrack,
            onPreviousTrack = onPreviousTrack,
            onNextTrack = onNextTrack,
            swipeTargets = homeSwipeTargets,
            onOpenRelease = { actions.openDetail(DetailTarget(DetailKind.Release, it)) },
        )
        UiDestination.Search -> SearchProductScreen(
            state = searchState,
            contentPadding = contentPadding,
            onQueryChange = actions.changeQuery,
            onSearchKindChange = actions.changeSearchKind,
            onOpenCatalog = actions.openDetail,
            onOpenArtist = { actions.openDetail(DetailTarget(DetailKind.Artist, it)) },
            onSearch = actions.submitSearch,
            onPlay = actions.playSearchResult,
            onPlayVault = actions.playVaultSearchResult,
            playlistVaultTarget = actions.playlistVaultTarget,
            onMoreVault = actions.moreVaultResults,
            manualPlaylists = actions.manualPlaylists,
            manualPlaylistActions = actions.manualPlaylistActions,
            onPlayAcquired = actions.playTrack,
            onRetry = actions.submitSearch,
            onVaultScopeChange = actions.changeVaultScope,
            listAnchor = searchListAnchor,
            onListAnchorChange = actions.changeSearchAnchor,
        )
        UiDestination.Library -> if (widthClass != UiWidthClass.Expanded &&
            selectedDetail != null && selectedDetail.kind != DetailKind.Track
        ) {
            CoreProductDetailScreen(
                state = detailState,
                contentPadding = contentPadding,
                onPlayTrack = actions.playTrack,
                onPlayPlaylistEntry = actions.playPlaylistEntry,
                onPlayReleaseEntry = actions.playReleaseEntry,
                onPlayNext = actions.playNext,
                onAddToQueue = actions.addToQueue,
                manualPlaylists = actions.manualPlaylists,
                manualPlaylistActions = actions.manualPlaylistActions,
                onRemoveOrRestore = actions.removeOrRestore,
                onLike = actions.likeTrack,
                onDownload = actions.downloadTrack,
                onRepairAccess = actions.repairAccess,
                onOpenReview = actions.openReview,
                onOpenDetail = { target ->
                    if (target.kind == DetailKind.Track) actions.playTrack(target.stableId)
                    else actions.openDetail(target)
                },
            )
        } else {
            LibraryProductScreen(
                searchPersonal = actions.searchPersonal,
                state = libraryState.copy(tracks = libraryState.tracks.map { track ->
                    track.copy(selected = track.id == currentTrackRefId)
                }),
                contentPadding = contentPadding,
                onAddLocal = actions.addLocal,
                onSelect = actions.playTrack,
                onRemoveOrRestore = actions.removeOrRestore,
                onLike = actions.likeTrack,
                onSectionChange = actions.changeLibrarySection,
                onSortChange = actions.changeLibrarySort,
                onFilterChange = actions.changeLibraryFilter,
                onOpenCollection = actions.openCollection,
                onOpenReview = actions.openReview,
                listAnchor = libraryListAnchor,
                onListAnchorChange = actions.changeLibraryAnchor,
                onOpenDownloads = actions.openOffline,
                onPlayNext = actions.playNext,
                onAddToQueue = actions.addToQueue,
                onDownload = actions.downloadTrack,
                onRepairAccess = actions.repairAccess,
                manualPlaylists = actions.manualPlaylists,
                manualPlaylistActions = actions.manualPlaylistActions,
            )
        }
        else -> error("CORE_PRODUCT_DESTINATION_REQUIRED")
    }
}
