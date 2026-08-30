package app.autplay.ui.player

import androidx.compose.foundation.BorderStroke
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
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.testTag
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
import app.autplay.ui.queue.QueueEditorPanel
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiState
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayPlaybackHalo
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.playbackVisualPalette

public enum class PlaybackPreferenceUiState {
    Neutral,
    Liked,
    Disliked,
}

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
    Box(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 6.dp)) {
        Surface(
            color = AutPlayTokens.colors.miniPlayerSurface,
            contentColor = AutPlayTokens.colors.onMiniPlayer,
            tonalElevation = 2.dp,
            shadowElevation = 14.dp,
            shape = MaterialTheme.shapes.large,
            border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
            modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen),
        ) {
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    AutPlayArtwork(state.title ?: stringResource(R.string.player_nothing_playing), size = 44.dp)
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
@OptIn(ExperimentalMaterial3Api::class)
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
    onPrevious: () -> Unit = {},
    onNext: () -> Unit = {},
    preference: PlaybackPreferenceUiState = PlaybackPreferenceUiState.Neutral,
    onClearPreference: () -> Unit = {},
    sleepTimerRemainingMinutes: Int? = null,
    stopAfterCurrentTrackActive: Boolean = false,
    onSetSleepTimer: (Long) -> Unit = {},
    onStopAfterCurrentTrack: () -> Unit = {},
    onCancelSleepTimer: () -> Unit = {},
    queueState: QueueEditorUiState = QueueEditorUiState(),
    queueActions: QueueEditorUiActions = QueueEditorUiActions(),
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
    val visualSeed = state.title ?: state.mediaId
    val palette = remember(visualSeed) { playbackVisualPalette(visualSeed) }
    var showSleepTimer by rememberSaveable { mutableStateOf(false) }
    val sleepTimerSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    if (showSleepTimer) {
        ModalBottomSheet(
            onDismissRequest = { showSleepTimer = false },
            sheetState = sleepTimerSheetState,
        ) {
            SleepTimerSheet(
                remainingMinutes = sleepTimerRemainingMinutes,
                stopAfterCurrentTrackActive = stopAfterCurrentTrackActive,
                onSelectMinutes = { minutes ->
                    onSetSleepTimer(minutes * 60_000L)
                    showSleepTimer = false
                },
                onStopAfterCurrentTrack = {
                    onStopAfterCurrentTrack()
                    showSleepTimer = false
                },
                onCancel = {
                    onCancelSleepTimer()
                    showSleepTimer = false
                },
            )
        }
    }
    BoxWithConstraints(
        modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        palette.first().copy(alpha = 0.46f),
                        palette.last().copy(alpha = 0.20f),
                        MaterialTheme.colorScheme.background.copy(alpha = 0.96f),
                        MaterialTheme.colorScheme.background,
                    ),
                ),
            ),
    ) {
        val haloSize = (maxWidth - 36.dp).coerceAtMost(390.dp)
        val artworkSize = haloSize * 0.76f
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 14.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Surface(
                shape = CircleShape,
                color = AutPlayTokens.colors.glassSurface,
                border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
            ) {
                Text(
                    "${stringResource(R.string.player_queue_context)} · ${stringResource(sourceLabel(state.source))}",
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.82f),
                )
            }
            Box(modifier = Modifier.size(haloSize), contentAlignment = Alignment.Center) {
                    AutPlayPlaybackHalo(
                        seed = state.title ?: state.mediaId,
                        isPlaying = state.isPlaying,
                        surfaceId = "now-playing-halo",
                    modifier = Modifier.fillMaxSize(),
                )
                AutPlayArtwork(
                    title = state.title ?: stringResource(R.string.player_nothing_playing),
                    size = artworkSize,
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
                    Text(
                        state.title ?: stringResource(R.string.player_nothing_playing),
                        style = MaterialTheme.typography.headlineMedium,
                        textAlign = TextAlign.Start,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        state.artist ?: stringResource(R.string.player_unknown_artist),
                        color = AutPlayTokens.colors.mutedText,
                        textAlign = TextAlign.Start,
                    )
                }
                PreferenceIconButton(
                    icon = AutPlayIcon.ThumbDown,
                    labelRes = R.string.action_dislike,
                    selected = preference == PlaybackPreferenceUiState.Disliked,
                    enabled = feedbackEnabled,
                    onClick = {
                        if (preference == PlaybackPreferenceUiState.Disliked) onClearPreference() else onDislike()
                    },
                )
                PreferenceIconButton(
                    icon = AutPlayIcon.ThumbUp,
                    labelRes = R.string.action_like,
                    selected = preference == PlaybackPreferenceUiState.Liked,
                    enabled = feedbackEnabled,
                    onClick = {
                        if (preference == PlaybackPreferenceUiState.Liked) onClearPreference() else onLike()
                    },
                )
            }
            PlaybackTimeline(state, onSeekBegin, onSeekUpdate, onSeekCommit)
            DirectControlMessage(state)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                AutPlayIconButton(
                    AutPlayIcon.Shuffle,
                    R.string.player_shuffle,
                    onToggleShuffle,
                    enabled = state.shuffleEnabled,
                )
                AutPlayIconButton(
                    AutPlayIcon.Previous,
                    R.string.action_previous,
                    onPrevious,
                    enabled = queueState.canPrevious && state.controls is PlaybackControlGate.Allowed,
                )
                PrimaryTransportButton(
                    icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                    labelRes = if (state.isPlaying) R.string.action_pause else R.string.action_play,
                    onClick = onTogglePlayPause,
                    enabled = state.controls is PlaybackControlGate.Allowed,
                )
                AutPlayIconButton(
                    AutPlayIcon.Next,
                    R.string.action_next,
                    onNext,
                    enabled = queueState.canNext && state.controls is PlaybackControlGate.Allowed,
                )
                AutPlayIconButton(
                    AutPlayIcon.Repeat,
                    repeatLabel(state.repeatMode),
                    onCycleRepeat,
                    enabled = state.repeatEnabled,
                )
            }
            PlayerFeatureCard(
                icon = AutPlayIcon.Timer,
                title = stringResource(R.string.player_sleep_timer),
                body = when {
                    stopAfterCurrentTrackActive -> stringResource(R.string.player_sleep_timer_after_track_active)
                    sleepTimerRemainingMinutes != null -> pluralStringResource(
                        R.plurals.player_sleep_timer_active,
                        sleepTimerRemainingMinutes,
                        sleepTimerRemainingMinutes,
                    )
                    else -> stringResource(R.string.player_sleep_timer_body)
                },
                onClick = { showSleepTimer = true },
                enabled = state.controls is PlaybackControlGate.Allowed,
                modifier = Modifier.testTag("player-sleep-timer"),
            )
            QueueEditorPanel(queueState, queueActions)
            FutureWaveByTrackCard()
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun PreferenceIconButton(
    icon: AutPlayIcon,
    @androidx.annotation.StringRes labelRes: Int,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val label = stringResource(labelRes)
    Surface(
        shape = CircleShape,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
        contentColor = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
        border = BorderStroke(
            1.dp,
            if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.38f) else AutPlayTokens.colors.border,
        ),
    ) {
        IconButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier.size(52.dp).semantics {
                contentDescription = label
                if (selected) stateDescription = label
            },
        ) {
            AutPlayPlatformIcon(
                icon = icon,
                contentDescription = null,
                modifier = Modifier.size(24.dp),
                tint = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

@Composable
private fun PlayerFeatureCard(
    icon: AutPlayIcon,
    title: String,
    body: String,
    onClick: () -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().heightIn(min = 76.dp),
        shape = MaterialTheme.shapes.large,
        color = AutPlayTokens.colors.glassSurface,
        border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            AutPlayPlatformIcon(
                icon = icon,
                contentDescription = null,
                modifier = Modifier.size(26.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium)
                Text(body, style = MaterialTheme.typography.bodySmall, color = AutPlayTokens.colors.mutedText)
            }
        }
    }
}

@Composable
private fun FutureWaveByTrackCard() {
    val unavailable = stringResource(R.string.player_wave_by_track_unavailable)
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 92.dp)
            .testTag("player-wave-by-track")
            .semantics {
                contentDescription = unavailable
                disabled()
            },
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.58f),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            AutPlayPlatformIcon(
                icon = AutPlayIcon.Wave,
                contentDescription = null,
                modifier = Modifier.size(30.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.player_wave_by_track), style = MaterialTheme.typography.titleMedium)
                Text(
                    stringResource(R.string.player_wave_by_track_body),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surface.copy(alpha = 0.78f)) {
                Text(
                    stringResource(R.string.player_coming_soon),
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.labelMedium,
                )
            }
        }
    }
}

@Composable
private fun SleepTimerSheet(
    remainingMinutes: Int?,
    stopAfterCurrentTrackActive: Boolean,
    onSelectMinutes: (Long) -> Unit,
    onStopAfterCurrentTrack: () -> Unit,
    onCancel: () -> Unit,
) {
    var selectedMinutes by rememberSaveable { mutableIntStateOf(remainingMinutes?.coerceIn(1, 60) ?: 30) }
    var endAfterCurrentTrack by rememberSaveable { mutableStateOf(stopAfterCurrentTrackActive) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(start = 24.dp, end = 24.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            stringResource(R.string.player_sleep_timer),
            style = MaterialTheme.typography.headlineSmall,
            textAlign = TextAlign.Center,
        )
        Row(
            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                stringResource(R.string.player_sleep_timer_after_track),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.titleMedium,
            )
            Switch(
                checked = endAfterCurrentTrack,
                onCheckedChange = { endAfterCurrentTrack = it },
                modifier = Modifier.testTag("sleep-timer-after-track"),
            )
        }
        SleepTimerDial(
            selectedMinutes = selectedMinutes,
            onMinutesChanged = { selectedMinutes = it },
            accessibilityLabel = stringResource(R.string.player_sleep_timer_dial_description),
            enabled = !endAfterCurrentTrack,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                if (endAfterCurrentTrack) onStopAfterCurrentTrack() else onSelectMinutes(selectedMinutes.toLong())
            },
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag("sleep-timer-confirm"),
        ) {
            Text(stringResource(R.string.player_sleep_timer_set))
        }
        if (remainingMinutes != null || stopAfterCurrentTrackActive) {
            OutlinedButton(
                onClick = onCancel,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) {
                Text(stringResource(R.string.player_sleep_timer_cancel))
            }
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
        reason == PlaybackControlLockReason.SOURCE_UNAVAILABLE -> R.string.player_source_unavailable
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
