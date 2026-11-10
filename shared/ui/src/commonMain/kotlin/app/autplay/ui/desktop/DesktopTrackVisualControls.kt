package app.autplay.ui.desktop

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopMetadataState

private const val DESKTOP_TRACK_VISUAL_DURATION_MS: Int = 180

/** Decoded pixels and labels only; no callbacks, playback state or asynchronous work in a scene. */
private data class DesktopTrackVisualPresentation(val title: String, val artist: String, val bitmap: ImageBitmap?)

/** Shared full/mini-player surface. Navigation and the setting are reset inputs, never commands. */
@Composable
internal fun DesktopTrackVisuals(
    input: DesktopTransportInput, metadata: DesktopMetadataState, language: DesktopLanguage,
    modifier: Modifier = Modifier, compact: Boolean = false, artworkSize: Dp = if (compact) 64.dp else 192.dp,
    navigationCurrent: Boolean = true, smoothTrackTransitions: Boolean = false,
) {
    val playback = input.playback
    val trackId = playback.localTrackRefId.takeIf { playback.mediaId != null }
    val bitmap = rememberDesktopArtworkBitmap(trackId, trackId?.let { metadata.artworks[it] })
    val latest = DesktopTrackVisualPresentation(
        if (playback.mediaId != null) playback.title ?: DesktopText.NothingPlaying.inLanguage(language)
        else DesktopText.NothingPlaying.inLanguage(language),
        playback.artist.orEmpty().takeIf { playback.mediaId != null }.orEmpty(), bitmap,
    )
    val retained = remember { mutableStateOf<DesktopTrackVisualFrames<DesktopTrackVisualPresentation>?>(null) }
    val frames = advanceDesktopTrackVisualFrames(retained.value,
        DesktopTrackVisualFrame(desktopTrackVisualIdentity(playback), latest), input.section,
        input.navigationRevision, navigationCurrent, smoothTrackTransitions)
    // Updating this composable's own state makes reset/replacement synchronous with its inputs.
    if (retained.value != frames) retained.value = frames
    val cover = (if (bitmap == null) DesktopMetadataText.NoCover else DesktopMetadataText.Cover).label(language)
    val description = listOf(latest.title, latest.artist, cover).filter { it.isNotBlank() }.joinToString(". ")
    Box(modifier.clipToBounds().clearAndSetSemantics { contentDescription = description }) {
        val transition = frames.transition
        key(transition) {
            val progress = remember { Animatable(if (transition == null) 1f else 0f) }
            if (transition != null) {
                LaunchedEffect(transition) {
                    try {
                        progress.animateTo(1f, tween(DESKTOP_TRACK_VISUAL_DURATION_MS))
                    } finally {
                        // Cancellation/disposal releases the old pixels as well as normal completion.
                        retained.value?.let { retained.value = finishDesktopTrackVisualTransition(it, transition) }
                    }
                }
            }
            frames.outgoing?.let { outgoing ->
                Box(Modifier.matchParentSize().graphicsLayer { alpha = 1f - progress.value }
                    .clearAndSetSemantics {}) {
                    DesktopTrackVisualScene(outgoing.value, language, compact, artworkSize)
                }
            }
            Box(Modifier.graphicsLayer { alpha = progress.value }) {
                DesktopTrackVisualScene(frames.current.value, language, compact, artworkSize)
            }
        }
    }
}

/** Drawing only: even the outgoing cover is an already decoded, immutable bitmap. */
@Composable
private fun DesktopTrackVisualScene(
    scene: DesktopTrackVisualPresentation, language: DesktopLanguage, compact: Boolean, artworkSize: Dp,
) {
    if (compact) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            DesktopArtworkImage(scene.bitmap, language, artworkSize)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(scene.title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.titleMedium)
                Text(scene.artist, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    } else {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            DesktopArtworkImage(scene.bitmap, language, artworkSize)
            Text(scene.title, maxLines = 2, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.headlineMedium)
            Text(scene.artist, maxLines = 2, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.titleMedium)
        }
    }
}

/** Passive desktop carousel. Only fresh prepared actor-neighbor entries can supply side covers. */
@Composable
internal fun DesktopPlayerArtworkCarousel(
    input: DesktopTransportInput, view: DesktopPlayerView, fresh: Boolean, metadata: DesktopMetadataState,
    language: DesktopLanguage, navigationCurrent: Boolean, smoothTrackTransitions: Boolean,
) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val neighborSize = minOf(80.dp, maxWidth * 0.18f)
        val currentSize = minOf(192.dp, maxWidth * 0.48f)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            DesktopTrackVisualNeighbor(view.previous.takeIf { fresh }, DesktopText.PlayerPrevious,
                metadata, language, neighborSize, Modifier.weight(1f))
            DesktopTrackVisuals(input, metadata, language, Modifier.weight(3f), artworkSize = currentSize,
                navigationCurrent = navigationCurrent, smoothTrackTransitions = smoothTrackTransitions)
            DesktopTrackVisualNeighbor(view.next.takeIf { fresh }, DesktopText.PlayerNext,
                metadata, language, neighborSize, Modifier.weight(1f))
        }
    }
}

@Composable
private fun DesktopTrackVisualNeighbor(
    entry: DesktopPlayerEntry?, label: DesktopText, metadata: DesktopMetadataState,
    language: DesktopLanguage, size: Dp, modifier: Modifier,
) {
    val track = entry?.track
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(label.inLanguage(language), style = MaterialTheme.typography.labelLarge)
        DesktopArtworkView(track?.id, track?.id?.let { metadata.artworks[it] }, language, size)
        Text(track?.title ?: (if (entry == null) DesktopText.PlayerNoNeighbor else DesktopText.PlayerEntryMissing)
            .inLanguage(language), maxLines = 2, overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.bodySmall)
        track?.artist?.takeIf { it.isNotBlank() }?.let {
            Text(it, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
        }
    }
}
