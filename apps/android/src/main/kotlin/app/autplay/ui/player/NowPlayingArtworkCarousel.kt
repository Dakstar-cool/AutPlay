package app.autplay.ui.player

import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.hideFromAccessibility
import androidx.compose.ui.semantics.semantics
import app.autplay.R
import app.autplay.ui.HomeSwipeTargets
import app.autplay.ui.homeCarouselGestures
import app.autplay.ui.rememberHomeCarouselMotion
import app.autplay.ui.rememberTrackArtwork

/** The cover shares the full player's transport actions and preserves each complete image. */
@Composable
internal fun NowPlayingArtworkCarousel(
    mediaId: String,
    title: String,
    painter: Painter?,
    targets: HomeSwipeTargets,
    enabled: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    animations: Boolean,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val motion = rememberHomeCarouselMotion(mediaId)
    val visibleTargets = motion.frozenTargets ?: targets
    LaunchedEffect(enabled, targets.previous?.key, targets.next?.key) {
        if (!enabled || motion.frozenTargets?.let { it != targets } == true) motion.cancel(animations)
    }
    val openLabel = stringResource(R.string.player_expand_artwork)
    BoxWithConstraints(modifier.aspectRatio(painter.artworkAspectRatio())
        .clickable(enabled = !motion.busy, onClickLabel = openLabel, role = Role.Button, onClick = onOpen)
        .cancelArtworkClickAfterDrag()) {
        val widthPixels = with(LocalDensity.current) { maxWidth.toPx() }
        SideEffect { motion.width = widthPixels }
        Box(
            Modifier.fillMaxSize().clipToBounds()
                .homeCarouselGestures(motion, enabled, targets, animations, onPrevious, onNext),
        ) {
            for (side in -1..1) {
                val neighbor = if (side < 0) visibleTargets.previous else visibleTargets.next
                if (side != 0 && (neighbor == null || motion.frozenTargets == null)) continue
                Box(
                    Modifier.fillMaxSize().graphicsLayer {
                        translationX = if (animations) motion.offset + side * motion.width else side * motion.width
                    }.then(if (side == 0) Modifier else Modifier.semantics { hideFromAccessibility() }),
                ) {
                    PlayerArtwork(
                        title = if (side == 0) title else neighbor!!.title ?: title,
                        painter = if (side == 0) painter else rememberTrackArtwork(neighbor!!.trackRefId),
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }
}

/** A drag inside a large cover can stay within its click bounds even when no neighbour is available. */
private fun Modifier.cancelArtworkClickAfterDrag(): Modifier = pointerInput(Unit) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        var dragged = false
        do {
            val event = awaitPointerEvent()
            val change = event.changes.firstOrNull { it.id == down.id } ?: break
            dragged = dragged || event.changes.size != 1 || (change.position - down.position).getDistance() > viewConfiguration.touchSlop
            if (dragged && !change.pressed) change.consume()
        } while (change.pressed)
    }
}

internal fun Painter?.artworkAspectRatio(): Float {
    val size = this?.intrinsicSize ?: return 1f
    return if (size.width.isFinite() && size.height.isFinite() && size.width > 0f && size.height > 0f) {
        size.width / size.height
    } else 1f
}
