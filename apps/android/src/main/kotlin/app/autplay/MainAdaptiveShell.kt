package app.autplay

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
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
import app.autplay.ui.player.PlaybackPreferenceUiState
import app.autplay.ui.queue.QueueEditorPanel
import app.autplay.ui.queue.QueueEditorUiState

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
    val queueState: QueueEditorUiState,
    val nowPlayingFeedbackEnabled: Boolean,
    val nowPlayingPreference: PlaybackPreferenceUiState,
    val sleepTimerRemainingMinutes: Int?,
    val stopAfterCurrentTrackActive: Boolean,
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
        nowPlayingAvailable = shouldShowPersistentPlayerChrome(
            destination = state.destination,
            hasMedia = state.playerState.mediaId != null,
        ),
        nowPlayingBar = {
            if (shouldShowPersistentPlayerChrome(state.destination, state.playerState.mediaId != null)) {
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
                    onPlayNext = state.coreRouteActions.playNext,
                    onAddToQueue = state.coreRouteActions.addToQueue,
                    manualPlaylists = state.coreRouteActions.manualPlaylists,
                    manualPlaylistActions = state.coreRouteActions.manualPlaylistActions,
                    onRemoveOrRestore = actions.removeOrRestore,
                    onLike = actions.likeTrack,
                    onDownload = actions.downloadTrack,
                    onRepairAccess = actions.repairAccess,
                    onOpenReview = actions.openReview,
                    onOpenDetail = actions.openDetail,
                )
            } else {
                QueueEditorPanel(state.queueState, state.nowPlayingActions.queue)
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
                preference = state.nowPlayingPreference,
                sleepTimerRemainingMinutes = state.sleepTimerRemainingMinutes,
                stopAfterCurrentTrackActive = state.stopAfterCurrentTrackActive,
                queueState = state.queueState,
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

internal fun shouldShowPersistentPlayerChrome(destination: UiDestination, hasMedia: Boolean): Boolean =
    hasMedia && destination != UiDestination.Home && destination != UiDestination.NowPlaying
