package app.autplay.ui.player

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.hideFromAccessibility
import androidx.compose.ui.semantics.semantics
import app.autplay.ui.HomeSwipeTargets
import app.autplay.ui.homeCarouselGestures
import app.autplay.ui.rememberHomeCarouselMotion
import app.autplay.ui.rememberTrackArtwork

/** The cover shares the full player's transport actions and preserves each complete image. */
@Composable
internal fun NowPlayingArtworkCarousel(
    mediaId: String,
    title: String,
    trackId: String?,
    targets: HomeSwipeTargets,
    enabled: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    animations: Boolean,
) {
    val painter = rememberTrackArtwork(trackId)
    val intrinsic = painter?.intrinsicSize
    val aspectRatio = intrinsic?.takeIf {
        it.width.isFinite() && it.height.isFinite() && it.width > 0f && it.height > 0f
    }?.let { it.width / it.height } ?: 1f
    val motion = rememberHomeCarouselMotion(mediaId)
    val visibleTargets = motion.frozenTargets ?: targets
    LaunchedEffect(enabled, targets.previous?.key, targets.next?.key) {
        if (!enabled || motion.frozenTargets?.let { it != targets } == true) motion.cancel(animations)
    }
    BoxWithConstraints(Modifier.fillMaxWidth().aspectRatio(aspectRatio)) {
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
                        trackId = if (side == 0) trackId else neighbor!!.trackRefId,
                        painter = if (side == 0) painter else null,
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }
}
