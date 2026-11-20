package app.autplay.ui.player

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import kotlin.math.abs

@Composable
internal fun Modifier.miniPlayerTrackSwipes(
    state: PlaybackPresentationState,
    onPrevious: (() -> Unit)?,
    onNext: (() -> Unit)?,
): Modifier {
    val enabled = state.controls is PlaybackControlGate.Allowed && state.mediaId != null
    val previousId = state.previousMediaId?.takeIf { enabled && it != state.mediaId }
    val nextId = state.nextMediaId?.takeIf { enabled && it != state.mediaId }
    val previous by rememberUpdatedState(onPrevious.takeIf { previousId != null })
    val next by rememberUpdatedState(onNext.takeIf { nextId != null })
    val previousLabel = stringResource(R.string.player_previous_track)
    val nextLabel = stringResource(R.string.player_next_track)
    return semantics {
        customActions = buildList {
            previous?.let { add(CustomAccessibilityAction(previousLabel) { it(); true }) }
            next?.let { add(CustomAccessibilityAction(nextLabel) { it(); true }) }
        }
    }.pointerInput(state.mediaId, previousId, nextId) {
        awaitEachGesture {
            // Observe before child buttons so a swipe that starts over a button cancels its click.
            val pass = PointerEventPass.Initial
            val down = awaitFirstDown(requireUnconsumed = false, pass = pass)
            val threshold = (size.width * 0.2f).coerceIn(48.dp.toPx(), 80.dp.toPx())
            var horizontal = false
            while (true) {
                val event = awaitPointerEvent(pass)
                val change = event.changes.singleOrNull() ?: break
                if (change.id != down.id || change.isConsumed) break
                val distance = change.position - down.position
                if (!horizontal) {
                    if (abs(distance.y) > viewConfiguration.touchSlop && abs(distance.y) >= abs(distance.x)) break
                    horizontal = abs(distance.x) > viewConfiguration.touchSlop &&
                        abs(distance.x) > abs(distance.y) * 1.25f
                }
                if (horizontal) change.consume()
                if (!change.pressed) {
                    if (horizontal) {
                        if (distance.x <= -threshold) next?.invoke()
                        if (distance.x >= threshold) previous?.invoke()
                    }
                    break
                }
            }
        }
    }
}
