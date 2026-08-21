package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.Composable
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.LibrarySort
import app.autplay.ui.core.ListAnchor

internal data class CoreProductRouteActions(
    val openListenTogether: () -> Unit,
    val recommendationVisible: (String) -> Unit,
    val likeRecommendation: (String) -> Unit,
    val dislikeRecommendation: (String) -> Unit,
    val retryHome: () -> Unit,
    val resumeHomeQueue: (String) -> Unit,
    val openHomePlaylist: (String) -> Unit,
    val openOffline: () -> Unit,
    val openProblems: () -> Unit,
    val changeQuery: (String) -> Unit,
    val submitSearch: () -> Unit,
    val playSearchResult: (String) -> Unit,
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
) {
    when (destination) {
        UiDestination.Home -> HomeProductScreen(
            state = homeState,
            contentPadding = contentPadding,
            onOpenListenTogether = actions.openListenTogether,
            onRecommendationVisible = actions.recommendationVisible,
            onLike = actions.likeRecommendation,
            onDislike = actions.dislikeRecommendation,
            onRetry = actions.retryHome,
            onPlayTrack = actions.resumeHomeQueue,
            onOpenPlaylist = actions.openHomePlaylist,
            onOpenOffline = actions.openOffline,
            onOpenProblems = actions.openProblems,
            playerState = playerState,
            currentTrackRefId = currentTrackRefId,
            currentTrackLiked = currentTrackLiked,
            onOpenPlayer = onOpenNowPlaying,
            onTogglePlayPause = onTogglePlayPause,
            onLikeHeroTrack = actions.likeTrack,
        )
        UiDestination.Search -> SearchProductScreen(
            state = searchState,
            contentPadding = contentPadding,
            onQueryChange = actions.changeQuery,
            onSearch = actions.submitSearch,
            onPlay = actions.playSearchResult,
            onRetry = actions.submitSearch,
            onVaultScopeChange = actions.changeVaultScope,
            listAnchor = searchListAnchor,
            onListAnchorChange = actions.changeSearchAnchor,
        )
        UiDestination.Library -> if (widthClass != UiWidthClass.Expanded && selectedDetail != null) {
            CoreProductDetailScreen(
                state = detailState,
                contentPadding = contentPadding,
                onPlayTrack = actions.playTrack,
                onPlayPlaylistEntry = actions.playPlaylistEntry,
                onRemoveOrRestore = actions.removeOrRestore,
                onLike = actions.likeTrack,
                onDownload = actions.downloadTrack,
                onRepairAccess = actions.repairAccess,
                onOpenReview = actions.openReview,
                onOpenDetail = actions.openDetail,
            )
        } else {
            LibraryProductScreen(
                state = libraryState,
                contentPadding = contentPadding,
                onAddLocal = actions.addLocal,
                onSelect = actions.selectTrack,
                onRemoveOrRestore = actions.removeOrRestore,
                onLike = actions.likeTrack,
                onSectionChange = actions.changeLibrarySection,
                onSortChange = actions.changeLibrarySort,
                onFilterChange = actions.changeLibraryFilter,
                onOpenCollection = actions.openCollection,
                onOpenReview = actions.openReview,
                listAnchor = libraryListAnchor,
                onListAnchorChange = actions.changeLibraryAnchor,
            )
        }
        else -> error("CORE_PRODUCT_DESTINATION_REQUIRED")
    }
}
