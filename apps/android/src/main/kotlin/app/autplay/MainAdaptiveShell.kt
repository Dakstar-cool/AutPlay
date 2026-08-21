package app.autplay

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.AutPlayAdaptiveShell
import app.autplay.ui.CoreProductDetailScreen
import app.autplay.ui.CoreProductDetailUiState
import app.autplay.ui.CoreProductRouteActions
import app.autplay.ui.CoreProductRouteRenderer
import app.autplay.ui.HomeScreenUiState
import app.autplay.ui.LibraryScreenUiState
import app.autplay.ui.SearchScreenUiState
import app.autplay.ui.UiDestination
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.ListAnchor
import app.autplay.ui.player.NowPlayingRouteActions
import app.autplay.ui.player.NowPlayingRouteRenderer
import app.autplay.ui.player.PlaybackMiniPlayer

internal data class MainAdaptiveShellState(
    val destination: UiDestination,
    val unreadSyncConflicts: Int,
    val navigationCanGoBack: Boolean,
    val hasVisibleCoreDetail: Boolean,
    val playerState: PlaybackPresentationState,
    val currentTrackRefId: String?,
    val currentTrackLiked: Boolean,
    val coreDetailState: CoreProductDetailUiState,
    val selectedDetail: DetailTarget?,
    val homeState: HomeScreenUiState,
    val searchState: SearchScreenUiState,
    val libraryState: LibraryScreenUiState,
    val searchListAnchor: ListAnchor?,
    val libraryListAnchor: ListAnchor?,
    val coreRouteActions: CoreProductRouteActions,
    val nowPlayingFeedbackEnabled: Boolean,
    val nowPlayingActions: NowPlayingRouteActions,
    val legacyState: LegacySecondaryRouteState,
    val legacyActions: LegacySecondaryRouteActions,
)

internal data class MainAdaptiveShellActions(
    val navigate: (UiDestination) -> Unit,
    val navigateBack: () -> Unit,
    val closeCoreDetail: () -> Unit,
    val togglePlayPause: () -> Unit,
    val setMiniPlayerObserving: (Boolean) -> Unit,
    val playTrack: (String) -> Unit,
    val playPlaylistEntry: (String) -> Unit,
    val removeOrRestore: (String) -> Unit,
    val likeTrack: (String) -> Unit,
    val downloadTrack: (String) -> Unit,
    val repairAccess: () -> Unit,
    val openReview: () -> Unit,
    val openDetail: (DetailTarget) -> Unit,
)

@Composable
internal fun MainAdaptiveShell(
    state: MainAdaptiveShellState,
    actions: MainAdaptiveShellActions,
) {
    AutPlayAdaptiveShell(
        selectedDestination = state.destination,
        onDestinationSelected = actions.navigate,
        unreadSyncConflicts = state.unreadSyncConflicts,
        canNavigateBack = state.hasVisibleCoreDetail || state.navigationCanGoBack,
        onNavigateBack = {
            if (state.hasVisibleCoreDetail) actions.closeCoreDetail() else actions.navigateBack()
        },
        onProfileClick = { actions.navigate(UiDestination.Profile) },
        onSettingsClick = { actions.navigate(UiDestination.Settings) },
        onNowPlayingClick = { actions.navigate(UiDestination.NowPlaying) },
        nowPlayingAvailable = state.playerState.mediaId != null,
        nowPlayingBar = {
            if (state.playerState.mediaId != null) {
                PlaybackMiniPlayer(
                    state = state.playerState,
                    onOpen = { actions.navigate(UiDestination.NowPlaying) },
                    onTogglePlayPause = actions.togglePlayPause,
                    onObservingChanged = actions.setMiniPlayerObserving,
                )
            }
        },
        detailPane = {
            if (state.destination == UiDestination.Library && state.selectedDetail != null) {
                CoreProductDetailScreen(
                    state = state.coreDetailState,
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
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(stringResource(R.string.expanded_queue_title), style = MaterialTheme.typography.titleLarge)
                    Text(
                        stringResource(R.string.expanded_queue_body),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    state.playerState.title?.let { title ->
                        Text(title, style = MaterialTheme.typography.titleMedium)
                    }
                }
            }
        },
    ) { _, contentPadding, widthClass ->
        when (state.destination) {
            UiDestination.Home, UiDestination.Search, UiDestination.Library -> CoreProductRouteRenderer(
                destination = state.destination,
                widthClass = widthClass,
                contentPadding = contentPadding,
                homeState = state.homeState,
                searchState = state.searchState,
                libraryState = state.libraryState,
                detailState = state.coreDetailState,
                selectedDetail = state.selectedDetail,
                searchListAnchor = state.searchListAnchor,
                libraryListAnchor = state.libraryListAnchor,
                actions = state.coreRouteActions,
                playerState = state.playerState,
                currentTrackRefId = state.currentTrackRefId,
                currentTrackLiked = state.currentTrackLiked,
                onOpenNowPlaying = { actions.navigate(UiDestination.NowPlaying) },
                onTogglePlayPause = actions.togglePlayPause,
            )
            UiDestination.NowPlaying -> NowPlayingRouteRenderer(
                state = state.playerState,
                feedbackEnabled = state.nowPlayingFeedbackEnabled,
                actions = state.nowPlayingActions,
                modifier = Modifier.padding(contentPadding),
            )
            else -> LegacySecondaryRouteRenderer(
                state = state.legacyState,
                actions = state.legacyActions,
                contentPadding = contentPadding,
            )
        }
    }
}
