package app.autplay.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.hideFromAccessibility
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R

@Composable
internal fun rememberHomeCarouselMotion(key: String?): HomeCarouselMotion {
    val scope = rememberCoroutineScope()
    val minimum = with(LocalDensity.current) { 112.dp.toPx() }
    val motion = remember(key, minimum) { HomeCarouselMotion(scope, minimum) }
    DisposableEffect(motion) { onDispose { motion.dispose() } }
    return motion
}

@Composable
internal fun HomeArtworkCarousel(
    title: String,
    targets: HomeSwipeTargets,
    motion: HomeCarouselMotion,
    enabled: Boolean,
    canOpen: Boolean,
    onOpen: () -> Unit,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    animations: Boolean = rememberSystemAnimationsEnabled(),
    trackId: String? = null,
) {
    val visibleTargets = motion.frozenTargets ?: targets
    LaunchedEffect(enabled, targets.previous?.key, targets.next?.key) {
        if (!enabled || motion.frozenTargets?.let { it != targets } == true) motion.cancel(animations)
    }
    val openLabel = stringResource(R.string.home_hero_open_player)
    val unknownTitle = stringResource(R.string.player_nothing_playing)
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val artworkSize = (maxWidth * 0.84f).coerceIn(160.dp, 320.dp)
        val widthPixels = with(LocalDensity.current) { maxWidth.toPx() }
        SideEffect { motion.width = widthPixels }
        Box(
            Modifier.fillMaxWidth().height(artworkSize).clipToBounds()
                .homeCarouselGestures(motion, enabled, targets, animations, onPrevious, onNext)
                .testTag("home-track-carousel"),
        ) {
            for (side in -1..1) {
                val neighbor = if (side < 0) visibleTargets.previous else visibleTargets.next
                if (side != 0 && neighbor == null) continue
                Box(
                    Modifier.fillMaxWidth().height(artworkSize).graphicsLayer {
                        translationX = if (animations) motion.offset + side * motion.width else side * motion.width
                    }.testTag("home-cover-$side")
                        .then(if (side == 0) Modifier else Modifier.semantics { hideFromAccessibility() }),
                    contentAlignment = Alignment.Center,
                ) {
                    AutPlayArtwork(
                        title = if (side == 0) title else neighbor?.title ?: unknownTitle,
                        size = artworkSize,
                        trackId = if (side == 0) trackId else neighbor?.trackRefId,
                        modifier = if (side == 0) Modifier.clickable(
                            enabled = canOpen && !motion.busy,
                            role = Role.Button,
                            onClick = onOpen,
                        ).semantics { contentDescription = openLabel } else Modifier,
                    )
                }
            }
        }
    }
}
