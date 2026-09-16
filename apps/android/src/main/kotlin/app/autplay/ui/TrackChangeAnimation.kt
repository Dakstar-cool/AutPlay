package app.autplay.ui

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** Only metadata is animated: playback progress and transport controls keep one live owner. */
internal data class TrackDisplayInfo(val id: String?, val title: String, val artist: String)

@Composable
internal fun TrackChangeAnimation(
    track: TrackDisplayInfo,
    modifier: Modifier = Modifier,
    animationsEnabled: Boolean = rememberSystemAnimationsEnabled(),
    content: @Composable (TrackDisplayInfo) -> Unit,
) {
    Crossfade(
        targetState = track,
        modifier = modifier,
        animationSpec = tween(if (animationsEnabled) 260 else 0, easing = FastOutSlowInEasing),
        label = "track-metadata-change",
        content = content,
    )
}
