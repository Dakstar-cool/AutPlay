package app.autplay.ui.player

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.autplay.playback.presentation.PlaybackPresentationState

internal data class NowPlayingRouteActions(
    val togglePlayPause: () -> Unit,
    val toggleShuffle: () -> Unit,
    val cycleRepeat: () -> Unit,
    val seekBegin: (Long) -> Unit,
    val seekUpdate: (Long) -> Unit,
    val seekCommit: () -> Unit,
    val like: () -> Unit,
    val dislike: () -> Unit,
    val observingChanged: (Boolean) -> Unit,
)

@Composable
internal fun NowPlayingRouteRenderer(
    state: PlaybackPresentationState,
    feedbackEnabled: Boolean,
    actions: NowPlayingRouteActions,
    modifier: Modifier = Modifier,
) {
    NowPlayingScreen(
        state = state,
        onTogglePlayPause = actions.togglePlayPause,
        onToggleShuffle = actions.toggleShuffle,
        onCycleRepeat = actions.cycleRepeat,
        onSeekBegin = actions.seekBegin,
        onSeekUpdate = actions.seekUpdate,
        onSeekCommit = actions.seekCommit,
        onLike = actions.like,
        onDislike = actions.dislike,
        feedbackEnabled = feedbackEnabled,
        onObservingChanged = actions.observingChanged,
        modifier = modifier,
    )
}
