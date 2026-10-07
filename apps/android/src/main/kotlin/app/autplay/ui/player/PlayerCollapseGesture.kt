package app.autplay.ui.player

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.unit.Dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.abs

/** Navigation owns collapse; leaving this screen never changes the playback session. */
internal val LocalPlayerCollapse = staticCompositionLocalOf<(() -> Unit)?> { null }

/** Top of the host's mini player, relative to the now-playing content, when measured by the shell. */
internal val LocalPlayerCollapseTargetTop = staticCompositionLocalOf<Dp?> { null }

internal fun shouldCompletePlayerCollapse(offset: Float, travel: Float, velocity: Float, density: Float): Boolean =
    travel > 0f && velocity > -1000f * density &&
        (offset >= travel * 0.4f || (offset >= 72f * density && velocity >= 1000f * density))

/** Drag writes directly to the graphics layer; only release uses a settling animation. */
@Stable
internal class PlayerCollapseMotion(private val scope: CoroutineScope) {
    var offset by mutableFloatStateOf(0f)
        private set
    var travel: Float = 1f
    private var settleJob: Job? = null
    private var collapsing = false

    fun begin(): Boolean {
        if (collapsing) return false
        settleJob?.cancel()
        return true
    }

    fun drag(delta: Float) { offset = (offset + delta).coerceIn(0f, travel) }

    fun release(velocity: Float, density: Float, animations: Boolean, onCollapse: () -> Unit) {
        finish(shouldCompletePlayerCollapse(offset, travel, velocity, density), velocity, animations, onCollapse)
    }

    fun collapse(animations: Boolean, onCollapse: () -> Unit) {
        if (!begin()) return
        finish(true, 0f, animations, onCollapse)
    }

    private fun finish(complete: Boolean, velocity: Float, animations: Boolean, onCollapse: () -> Unit) {
        collapsing = complete
        settleJob = scope.launch {
            settle(if (collapsing) travel else 0f, velocity, animations)
            if (collapsing) {
                onCollapse()
                // Navigation normally disposes this motion. A host that stays open remains usable.
                offset = 0f
                collapsing = false
            }
        }
    }

    fun cancel(animations: Boolean) {
        if (collapsing) return
        settleJob?.cancel()
        settleJob = scope.launch { settle(0f, 0f, animations) }
    }

    private suspend fun settle(target: Float, velocity: Float, animations: Boolean) {
        if (!animations) { offset = target; return }
        animate(
            initialValue = offset,
            targetValue = target,
            initialVelocity = velocity,
            animationSpec = spring(dampingRatio = Spring.DampingRatioNoBouncy, stiffness = Spring.StiffnessMediumLow),
        ) { value, _ -> offset = value.coerceIn(0f, travel) }
    }

    fun dispose() { settleJob?.cancel() }
}

@Composable
internal fun rememberPlayerCollapseMotion(): PlayerCollapseMotion {
    val scope = rememberCoroutineScope()
    val motion = remember(scope) { PlayerCollapseMotion(scope) }
    DisposableEffect(motion) { onDispose { motion.dispose() } }
    return motion
}

/** Claim downward drags only, leaving upward scrolling and horizontal track gestures available. */
internal fun Modifier.playerCollapseGesture(
    onCollapse: (() -> Unit)?,
    motion: PlayerCollapseMotion,
    animations: Boolean,
): Modifier = if (onCollapse == null) this else pointerInput(onCollapse, motion, animations) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        val velocity = VelocityTracker()
        velocity.addPosition(down.uptimeMillis, down.position)
        var distance = Offset.Zero
        var claimed = false
        var released = false
        try {
            while (true) {
                val event = awaitPointerEvent()
                val change = event.changes.firstOrNull { it.id == down.id } ?: break
                if (change.isConsumed || event.changes.count { it.pressed } > 1) break
                val delta = change.positionChange()
                distance += delta
                velocity.addPosition(change.uptimeMillis, change.position)
                if (!claimed) {
                    if (abs(distance.x) > viewConfiguration.touchSlop && abs(distance.x) >= abs(distance.y)) break
                    if (distance.y < -viewConfiguration.touchSlop) break
                    if (distance.y > viewConfiguration.touchSlop && distance.y > abs(distance.x) * 1.25f) {
                        if (!motion.begin()) break
                        claimed = true
                        motion.drag(distance.y - viewConfiguration.touchSlop)
                    }
                } else motion.drag(delta.y)
                if (claimed) change.consume()
                if (!change.pressed) {
                    released = true
                    if (claimed) motion.release(velocity.calculateVelocity().y, density, animations, onCollapse)
                    break
                }
            }
        } finally {
            if (claimed && !released) motion.cancel(animations)
        }
    }
}
