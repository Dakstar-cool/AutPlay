package app.autplay.ui.player

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.hideFromAccessibility
import androidx.compose.ui.semantics.semantics
import app.autplay.ui.HomeSwipeTargets
import app.autplay.ui.face.AutPlayResonanceLens
import app.autplay.ui.face.FacePlaybackMode
import app.autplay.ui.face.FacePreferenceMode
import app.autplay.ui.homeCarouselGestures
import app.autplay.ui.rememberHomeCarouselMotion
import app.autplay.ui.rememberSystemAnimationsEnabled

/** The full player follows the same queue and library neighbours as the home carousel. */
@Composable
internal fun NowPlayingFaceCarousel(
    mediaId: String,
    playbackMode: FacePlaybackMode,
    preference: FacePreferenceMode,
    accessibilitySummary: String,
    targets: HomeSwipeTargets,
    enabled: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    modifier: Modifier = Modifier,
    animations: Boolean = rememberSystemAnimationsEnabled(),
) {
    val motion = rememberHomeCarouselMotion(mediaId)
    val visibleTargets = motion.frozenTargets ?: targets
    LaunchedEffect(enabled, targets.previous?.key, targets.next?.key) {
        if (!enabled || motion.frozenTargets?.let { it != targets } == true) motion.cancel(animations)
    }
    BoxWithConstraints(modifier) {
        val widthPixels = with(LocalDensity.current) { maxWidth.toPx() }
        SideEffect { motion.width = widthPixels }
        Box(
            Modifier.fillMaxWidth().clipToBounds()
                .homeCarouselGestures(motion, enabled, targets, animations, onPrevious, onNext)
                .testTag("player-track-carousel"),
        ) {
            for (side in -1..1) {
                val neighbor = if (side < 0) visibleTargets.previous else visibleTargets.next
                if (side != 0 && (neighbor == null || motion.frozenTargets == null)) continue
                Box(
                    Modifier.fillMaxWidth().graphicsLayer {
                        translationX = if (animations) motion.offset + side * motion.width else side * motion.width
                    }.testTag("player-face-card-$side")
                        .then(if (side == 0) Modifier else Modifier.semantics { hideFromAccessibility() }),
                    contentAlignment = Alignment.Center,
                ) {
                    AutPlayResonanceLens(
                        trackSeed = if (side == 0) mediaId else neighbor!!.trackRefId,
                        playbackMode = if (side == 0) playbackMode else FacePlaybackMode.Idle,
                        preference = if (side == 0) preference else FacePreferenceMode.Neutral,
                        accessibilitySummary = accessibilitySummary,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}
