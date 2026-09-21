package app.autplay.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sign

/** Pixel movement is read only by graphics layers, never by composition or layout. */
@Stable
internal class HomeCarouselMotion(private val scope: CoroutineScope, private val minimumTravel: Float) {
    var offset by mutableFloatStateOf(0f)
        private set
    var width by mutableFloatStateOf(1f)
    var frozenTargets by mutableStateOf<HomeSwipeTargets?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    private var job: Job? = null
    private var alive = true
    private var commandSent = false
    private var gestureTravel = 0f

    fun begin(targets: HomeSwipeTargets): Boolean {
        if (!alive || busy) return false
        job?.cancel()
        gestureTravel = 0f
        frozenTargets = targets
        return true
    }

    fun drag(delta: Float) {
        val targets = frozenTargets ?: return
        gestureTravel += delta
        val proposed = offset + delta
        val available = if (proposed < 0f) targets.next != null else targets.previous != null
        offset = if (available) proposed.coerceIn(-width, width) else (offset + delta * 0.15f).coerceIn(-width * 0.06f, width * 0.06f)
    }

    fun release(animations: Boolean, onPrevious: () -> Unit, onNext: () -> Unit) {
        val targets = frozenTargets ?: return
        val threshold = max(minimumTravel, width * 0.35f)
        val forward = offset < 0f
        val target = if (forward) targets.next else targets.previous
        if (abs(offset) < threshold || abs(gestureTravel) < threshold || sign(offset) != sign(gestureTravel) || target == null) {
            cancel(animations)
            return
        }
        busy = true
        job = scope.launch {
            moveTo(if (forward) -width else width, animations)
            if (!alive) return@launch
            commandSent = true
            if (forward) onNext() else onPrevious()
            // Playback owns the current item. Re-keying the carousel acknowledges success.
            // A failed command must not leave an optimistic cover stuck on screen.
            delay(1800)
            moveTo(0f, animations)
            commandSent = false
            busy = false
            frozenTargets = null
        }
    }

    fun cancel(animations: Boolean) {
        if (commandSent || !alive) return
        job?.cancel()
        busy = false
        job = scope.launch {
            moveTo(0f, animations)
            frozenTargets = null
        }
    }

    private suspend fun moveTo(target: Float, animations: Boolean) {
        if (!animations) { offset = target; return }
        animate(offset, target, animationSpec = tween(180, easing = FastOutSlowInEasing)) { value, _ -> offset = value }
    }

    fun dispose() { alive = false; job?.cancel() }
}

/** Claim deliberate horizontal motion; leave vertical/diagonal scrolling to LazyColumn. */
internal fun Modifier.homeCarouselGestures(
    motion: HomeCarouselMotion,
    enabled: Boolean,
    targets: HomeSwipeTargets,
    animations: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
): Modifier = pointerInput(motion, enabled, targets.previous?.key, targets.next?.key, animations) {
    if (!enabled) return@pointerInput
    try {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            var distance = Offset.Zero
            var dragging = false
            var released = false
            val slop = viewConfiguration.touchSlop * 1.5f
            try {
                while (true) {
                    val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id }
                    if (change == null || change.isConsumed) {
                        if (dragging) motion.cancel(animations)
                        break
                    }
                    val delta = change.positionChange()
                    distance += delta
                    if (!dragging) {
                        if (abs(distance.y) > slop && abs(distance.y) * 1.5f >= abs(distance.x)) break
                        if (abs(distance.x) > slop && abs(distance.x) > abs(distance.y) * 1.5f) {
                            if (!motion.begin(targets)) break
                            dragging = true
                            motion.drag(distance.x - sign(distance.x) * slop)
                            change.consume()
                        }
                    } else {
                        motion.drag(delta.x)
                        change.consume()
                    }
                    if (!change.pressed) {
                        released = true
                        if (dragging) motion.release(animations, onPrevious, onNext)
                        break
                    }
                }
            } finally {
                if (dragging && !released) motion.cancel(animations)
            }
        }
    } finally {
        motion.cancel(animations)
    }
}
