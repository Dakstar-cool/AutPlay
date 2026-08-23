package app.autplay.ui.player

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.playback.presentation.canSeek
import app.autplay.ui.AutPlayArtwork
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayIconButton
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayPlaybackHalo
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens

@Composable
public fun PlaybackMiniPlayer(
    state: PlaybackPresentationState,
    onOpen: () -> Unit,
    onTogglePlayPause: () -> Unit,
    onObservingChanged: (Boolean) -> Unit,
) {
    DisposableEffect(Unit) {
        onObservingChanged(true)
        onDispose { onObservingChanged(false) }
    }
    Box(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
        Surface(
            color = AutPlayTokens.colors.miniPlayerSurface,
            contentColor = AutPlayTokens.colors.onMiniPlayer,
            tonalElevation = 6.dp,
            shadowElevation = 8.dp,
            shape = MaterialTheme.shapes.large,
            modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen),
        ) {
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 9.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    AutPlayArtwork(state.title ?: stringResource(R.string.player_nothing_playing), size = 48.dp)
                    Column(Modifier.weight(1f)) {
                        Text(
                            state.title ?: stringResource(R.string.player_nothing_playing),
                            style = MaterialTheme.typography.titleMedium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            state.artist ?: stringResource(R.string.player_unknown_artist),
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    AutPlayIconButton(
                        icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                        labelRes = if (state.isPlaying) R.string.action_pause else R.string.action_play,
                        onClick = onTogglePlayPause,
                        enabled = state.controls is PlaybackControlGate.Allowed,
                    )
                }
                PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(3.dp))
            }
        }
    }
}

@Composable
public fun NowPlayingScreen(
    state: PlaybackPresentationState,
    onTogglePlayPause: () -> Unit,
    onToggleShuffle: () -> Unit,
    onCycleRepeat: () -> Unit,
    onSeekBegin: (Long) -> Unit,
    onSeekUpdate: (Long) -> Unit,
    onSeekCommit: () -> Unit,
    onLike: () -> Unit,
    onDislike: () -> Unit,
    feedbackEnabled: Boolean,
    onObservingChanged: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    DisposableEffect(Unit) {
        onObservingChanged(true)
        onDispose { onObservingChanged(false) }
    }
    if (state.mediaId == null) {
        Box(modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
            AutPlayStateSurface(
                AutPlayStateKind.PlaybackUnavailable,
                stringResource(R.string.player_nothing_playing),
            )
        }
        return
    }
    BoxWithConstraints(
        modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.46f),
                        MaterialTheme.colorScheme.background,
                        MaterialTheme.colorScheme.background,
                    ),
                ),
            ),
    ) {
        val haloSize = (maxWidth - 16.dp).coerceAtMost(460.dp)
        val artworkSize = haloSize * 0.64f
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Box(modifier = Modifier.size(haloSize), contentAlignment = Alignment.Center) {
                AutPlayPlaybackHalo(
                    seed = state.title ?: state.mediaId,
                    isPlaying = state.isPlaying,
                    modifier = Modifier.fillMaxSize(),
                )
                AutPlayArtwork(
                    title = state.title ?: stringResource(R.string.player_nothing_playing),
                    size = artworkSize,
                )
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    state.title ?: stringResource(R.string.player_nothing_playing),
                    style = MaterialTheme.typography.headlineMedium,
                    textAlign = TextAlign.Center,
                )
                Text(
                    state.artist ?: stringResource(R.string.player_unknown_artist),
                    color = AutPlayTokens.colors.mutedText,
                    textAlign = TextAlign.Center,
                )
                Text(
                    "${stringResource(R.string.player_queue_context)} · ${stringResource(sourceLabel(state.source))}",
                    style = MaterialTheme.typography.labelMedium,
                    color = AutPlayTokens.colors.mutedText,
                )
            }
            PlaybackTimeline(state, onSeekBegin, onSeekUpdate, onSeekCommit)
            DirectControlMessage(state)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                AutPlayIconButton(AutPlayIcon.Previous, R.string.action_previous, {}, enabled = false)
                PrimaryTransportButton(
                    icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                    labelRes = if (state.isPlaying) R.string.action_pause else R.string.action_play,
                    onClick = onTogglePlayPause,
                    enabled = state.controls is PlaybackControlGate.Allowed,
                )
                AutPlayIconButton(AutPlayIcon.Next, R.string.action_next, {}, enabled = false)
            }
            Text(
                stringResource(R.string.player_queue_transition_unavailable),
                style = MaterialTheme.typography.bodySmall,
                color = AutPlayTokens.colors.mutedText,
                textAlign = TextAlign.Center,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                AutPlayIconButton(
                    AutPlayIcon.Shuffle,
                    R.string.player_shuffle,
                    onToggleShuffle,
                    enabled = state.shuffleEnabled,
                )
                AutPlayIconButton(
                    AutPlayIcon.Repeat,
                    repeatLabel(state.repeatMode),
                    onCycleRepeat,
                    enabled = state.repeatEnabled,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(
                    onClick = onLike,
                    enabled = feedbackEnabled,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(R.string.action_like)) }
                OutlinedButton(
                    onClick = onDislike,
                    enabled = feedbackEnabled,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(R.string.action_dislike)) }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun PrimaryTransportButton(
    icon: AutPlayIcon,
    @androidx.annotation.StringRes labelRes: Int,
    onClick: () -> Unit,
    enabled: Boolean,
) {
    val label = stringResource(labelRes)
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.primary,
        contentColor = MaterialTheme.colorScheme.onPrimary,
        shadowElevation = 8.dp,
    ) {
        IconButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier.size(64.dp).semantics { contentDescription = label },
        ) {
            AutPlayPlatformIcon(icon = icon, contentDescription = null, modifier = Modifier.size(30.dp))
        }
    }
}

@Composable
private fun PlaybackTimeline(
    state: PlaybackPresentationState,
    onSeekBegin: (Long) -> Unit,
    onSeekUpdate: (Long) -> Unit,
    onSeekCommit: () -> Unit,
) {
    val duration = state.durationMs
    if (duration == null || duration <= 0 || state.isLive) {
        PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(6.dp))
        Text(
            stringResource(if (state.isLive) R.string.player_timeline_live else R.string.player_timeline_unknown),
            color = AutPlayTokens.colors.mutedText,
        )
        return
    }
    var dragging by remember(state.mediaId, duration) { mutableStateOf(false) }
    val displayed = (state.seekPreviewPositionMs ?: state.positionMs).coerceIn(0, duration)
    val accessibilityState = stringResource(
        R.string.player_progress_description,
        formatDuration(displayed),
        formatDuration(duration),
    )
    val accessibilityLabel = stringResource(R.string.player_seek_description)
    Column(Modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth()) {
            PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(48.dp).padding(vertical = 22.dp))
            Slider(
                value = displayed.toFloat(),
                onValueChange = { value ->
                    val target = value.toLong().coerceIn(0, duration)
                    if (!dragging) {
                        dragging = true
                        onSeekBegin(target)
                    } else {
                        onSeekUpdate(target)
                    }
                },
                onValueChangeFinished = {
                    dragging = false
                    onSeekCommit()
                },
                valueRange = 0f..duration.toFloat(),
                enabled = state.canSeek,
                modifier = Modifier.fillMaxWidth().semantics {
                    contentDescription = accessibilityLabel
                    stateDescription = accessibilityState
                },
            )
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(formatDuration(displayed), style = MaterialTheme.typography.labelMedium)
            Text(formatDuration(duration), style = MaterialTheme.typography.labelMedium)
        }
    }
}

@Composable
private fun PlaybackProgressTrack(state: PlaybackPresentationState, modifier: Modifier) {
    val duration = state.durationMs?.takeIf { it > 0 } ?: 1L
    val played = (state.seekPreviewPositionMs ?: state.positionMs).coerceIn(0, duration).toFloat() / duration
    val buffered = state.bufferedPositionMs.coerceIn(0, duration).toFloat() / duration
    val base = AutPlayTokens.colors.border
    val bufferColor = MaterialTheme.colorScheme.primaryContainer
    val playedColor = MaterialTheme.colorScheme.primary
    Canvas(modifier) {
        val radius = CornerRadius(size.height / 2f)
        drawRoundRect(base, cornerRadius = radius)
        drawRoundRect(bufferColor, size = size.copy(width = size.width * buffered), cornerRadius = radius)
        drawRoundRect(playedColor, size = size.copy(width = size.width * played), cornerRadius = radius)
    }
}

@Composable
private fun DirectControlMessage(state: PlaybackPresentationState) {
    val reason = (state.controls as? PlaybackControlGate.Locked)?.reason
    val message = when {
        reason == PlaybackControlLockReason.WAVE_QUEUE -> R.string.player_timeline_locked_wave
        reason in setOf(
            PlaybackControlLockReason.CONTEXT_LOADING,
            PlaybackControlLockReason.CONTEXT_UNAVAILABLE,
            PlaybackControlLockReason.QUEUE_MEDIA_MISMATCH,
        ) -> R.string.player_timeline_recovering
        reason != null -> R.string.player_controls_unavailable
        state.playbackStatus == PlaybackStatus.Buffering -> R.string.player_buffering
        else -> null
    }
    if (message != null) {
        Text(stringResource(message), color = AutPlayTokens.colors.mutedText, textAlign = TextAlign.Center)
    }
}

private fun repeatLabel(mode: RepeatModePresentation): Int = when (mode) {
    RepeatModePresentation.Off -> R.string.player_repeat_off
    RepeatModePresentation.One -> R.string.player_repeat_one
    RepeatModePresentation.All -> R.string.player_repeat_all
}

private fun sourceLabel(source: PlaybackSourcePresentation): Int = when (source) {
    PlaybackSourcePresentation.Local -> R.string.player_source_local
    PlaybackSourcePresentation.Download -> R.string.player_source_download
    PlaybackSourcePresentation.Vault -> R.string.player_source_vault
    PlaybackSourcePresentation.Unknown -> R.string.player_source_unknown
}

private fun formatDuration(valueMs: Long): String {
    val totalSeconds = valueMs.coerceAtLeast(0) / 1_000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%d:%02d".format(minutes, seconds)
}
