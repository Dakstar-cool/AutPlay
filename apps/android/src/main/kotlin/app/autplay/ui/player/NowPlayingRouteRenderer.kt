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
    val scheduleSleepTimer: (Long) -> Unit,
    val stopAfterCurrentTrack: () -> Unit,
    val cancelSleepTimer: () -> Unit,
    val observingChanged: (Boolean) -> Unit,
    val queue: QueueEditorUiActions = QueueEditorUiActions(),
)

@Composable
internal fun NowPlayingRouteRenderer(
    state: PlaybackPresentationState,
    feedbackEnabled: Boolean,
    preference: PlaybackPreferenceUiState,
    sleepTimerRemainingMinutes: Int?,
    stopAfterCurrentTrackActive: Boolean,
    queueState: QueueEditorUiState,
    actions: NowPlayingRouteActions,
    modifier: Modifier = Modifier,
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
        sleepTimerRemainingMinutes = sleepTimerRemainingMinutes,
        stopAfterCurrentTrackActive = stopAfterCurrentTrackActive,
        onSetSleepTimer = actions.scheduleSleepTimer,
        onStopAfterCurrentTrack = actions.stopAfterCurrentTrack,
        onCancelSleepTimer = actions.cancelSleepTimer,
        onObservingChanged = actions.observingChanged,
        queueState = queueState,
        queueActions = actions.queue,
        modifier = modifier,
    )
}
