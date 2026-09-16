package app.autplay.ui.player

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiState

internal data class NowPlayingRouteActions(
    val togglePlayPause: () -> Unit,
    val toggleShuffle: () -> Unit,
    val cycleRepeat: () -> Unit,
    val previous: () -> Unit = {},
    val next: () -> Unit = {},
    val seekBegin: (Long) -> Unit,
    val seekUpdate: (Long) -> Unit,
    val seekCommit: () -> Unit,
    val like: () -> Unit,
    val dislike: () -> Unit,
    val clearPreference: () -> Unit,
    val setCurrentListenTasteExcluded: (Boolean) -> Unit = {},
    val setSessionTasteExcluded: (Boolean) -> Unit = {},
    val scheduleSleepTimer: (Long) -> Unit,
    val stopAfterCurrentTrack: () -> Unit,
    val cancelSleepTimer: () -> Unit,
    val observingChanged: (Boolean) -> Unit,
    val queue: QueueEditorUiActions = QueueEditorUiActions(),
    val download: () -> Unit = {},
)

internal data class NowPlayingTasteUiState(
    val listenExcluded: Boolean = false,
    val sessionExcluded: Boolean = false,
    val listenActionAvailable: Boolean = false,
    val sessionActionAvailable: Boolean = false,
    val errorCode: String? = null,
)

@Composable
internal fun NowPlayingRouteRenderer(
    state: PlaybackPresentationState,
    feedbackEnabled: Boolean,
    preference: PlaybackPreferenceUiState,
    taste: NowPlayingTasteUiState,
    sleepTimerRemainingMinutes: Int?,
    stopAfterCurrentTrackActive: Boolean,
    queueState: QueueEditorUiState,
    actions: NowPlayingRouteActions,
    modifier: Modifier = Modifier,
    downloadState: String? = null,
) {
    NowPlayingScreen(
        state = state,
        onTogglePlayPause = actions.togglePlayPause,
        onToggleShuffle = actions.toggleShuffle,
        onCycleRepeat = actions.cycleRepeat,
        onPrevious = actions.previous,
        onNext = actions.next,
        onSeekBegin = actions.seekBegin,
        onSeekUpdate = actions.seekUpdate,
        onSeekCommit = actions.seekCommit,
        onLike = actions.like,
        onDislike = actions.dislike,
        preference = preference,
        onClearPreference = actions.clearPreference,
        feedbackEnabled = feedbackEnabled,
        listenExcludedFromTaste = taste.listenExcluded,
        sessionExcludedFromTaste = taste.sessionExcluded,
        listenTasteActionAvailable = taste.listenActionAvailable,
        sessionTasteActionAvailable = taste.sessionActionAvailable,
        tasteExclusionError = taste.errorCode,
        onSetCurrentListenTasteExcluded = actions.setCurrentListenTasteExcluded,
        onSetSessionTasteExcluded = actions.setSessionTasteExcluded,
        sleepTimerRemainingMinutes = sleepTimerRemainingMinutes,
        stopAfterCurrentTrackActive = stopAfterCurrentTrackActive,
        onSetSleepTimer = actions.scheduleSleepTimer,
        onStopAfterCurrentTrack = actions.stopAfterCurrentTrack,
        onCancelSleepTimer = actions.cancelSleepTimer,
        onObservingChanged = actions.observingChanged,
        queueState = queueState,
        queueActions = actions.queue,
        onDownload = actions.download,
        downloadState = downloadState,
        modifier = modifier,
    )
}
