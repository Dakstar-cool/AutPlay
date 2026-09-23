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
import app.autplay.ui.player.NowPlayingTasteUiState
import app.autplay.ui.player.PlaybackMiniPlayer
import app.autplay.ui.player.PlaybackPreferenceUiState
import app.autplay.ui.queue.QueueEditorPanel
import app.autplay.ui.queue.QueueEditorUiState
import app.autplay.ui.history.HistoryScreen
import app.autplay.ui.history.HistoryUiActions
import app.autplay.ui.history.HistoryUiState
import app.autplay.ui.downloads.DownloadsScreen
import app.autplay.ui.downloads.DownloadsUiActions

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
    val homeSwipeTargets: app.autplay.ui.HomeSwipeTargets,
    val searchState: SearchScreenUiState,
    val libraryState: LibraryScreenUiState,
    val searchListAnchor: ListAnchor?,
    val libraryListAnchor: ListAnchor?,
    val coreRouteActions: CoreProductRouteActions,
    val queueState: QueueEditorUiState,
    val nowPlayingFeedbackEnabled: Boolean,
    val nowPlayingPreference: PlaybackPreferenceUiState,
    val nowPlayingTaste: NowPlayingTasteUiState,
    val sleepTimerRemainingMinutes: Int?,
    val stopAfterCurrentTrackActive: Boolean,
    val nowPlayingActions: NowPlayingRouteActions,
    val historyState: HistoryUiState,
    val historyActions: HistoryUiActions,
    val downloadsPendingIntentId: String?,
    val canDownloadSelected: Boolean,
    val downloadsActions: DownloadsUiActions,
    val legacyState: LegacySecondaryRouteState,
    val legacyActions: LegacySecondaryRouteActions,
    val nowPlayingDownloadState: String? = null,
)

internal data class MainAdaptiveShellActions(
    val dismissCommandError: () -> Unit,
    val navigate: (UiDestination) -> Unit,
    val navigateBack: () -> Unit,
    val closeCoreDetail: () -> Unit,
    val togglePlayPause: () -> Unit,
    val skipHomeTrack: (Boolean) -> Unit,
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
    androidx.compose.runtime.CompositionLocalProvider(app.autplay.ui.LocalDeveloperMode provides state.legacyState.settings.developerMode) {
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
        snackbarHost = {
            app.autplay.ui.CommandErrorBanner(state.legacyState.stableError, actions.dismissCommandError)
        },
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
                    liked = state.currentTrackLiked,
                    likeEnabled = state.currentTrackRefId != null,
                    onLike = { state.currentTrackRefId?.let(actions.likeTrack) },
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
                onPreviousTrack = { actions.skipHomeTrack(false) },
                onNextTrack = { actions.skipHomeTrack(true) },
                homeSwipeTargets = state.homeSwipeTargets,
            )
            UiDestination.NowPlaying -> NowPlayingRouteRenderer(
                downloadState = state.nowPlayingDownloadState,
                state = state.playerState,
                swipeTargets = state.homeSwipeTargets,
                onPrevious = {
                    if (state.homeSwipeTargets.previous != null) actions.skipHomeTrack(false)
                    else state.nowPlayingActions.previous()
                },
                onNext = {
                    if (state.homeSwipeTargets.next != null) actions.skipHomeTrack(true)
                    else state.nowPlayingActions.next()
                },
                feedbackEnabled = state.nowPlayingFeedbackEnabled,
                preference = state.nowPlayingPreference,
                taste = state.nowPlayingTaste,
                sleepTimerRemainingMinutes = state.sleepTimerRemainingMinutes,
                stopAfterCurrentTrackActive = state.stopAfterCurrentTrackActive,
                queueState = state.queueState,
                actions = state.nowPlayingActions,
                modifier = Modifier.padding(contentPadding),
            )
            UiDestination.History -> HistoryScreen(
                state = state.historyState,
                actions = state.historyActions,
                contentPadding = contentPadding,
            )
            UiDestination.Downloads -> DownloadsScreen(
                downloads = state.legacyState.downloads,
                pendingIntentId = state.downloadsPendingIntentId,
                canDownloadSelected = state.canDownloadSelected,
                actions = state.downloadsActions,
                contentPadding = contentPadding,
            )
            else -> LegacySecondaryRouteRenderer(
                state = state.legacyState,
                actions = state.legacyActions,
                contentPadding = contentPadding,
            )
        }
    }
    }
}

internal fun shouldShowPersistentPlayerChrome(destination: UiDestination, hasMedia: Boolean): Boolean =
    hasMedia && destination != UiDestination.NowPlaying
