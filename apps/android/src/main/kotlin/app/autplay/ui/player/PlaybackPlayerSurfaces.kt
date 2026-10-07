package app.autplay.ui.player

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.selected
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
import app.autplay.ui.AutPlayArtworkPlaceholder
import app.autplay.ui.rememberTrackArtwork
import app.autplay.ui.rememberSystemAnimationsEnabled
import app.autplay.ui.HomeSwipeTargets
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayIconButton
import app.autplay.ui.queue.QueueEditorPanel
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiState
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.TrackChangeAnimation
import app.autplay.ui.TrackDisplayInfo

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
    liked: Boolean = false,
    likeEnabled: Boolean = false,
    onLike: () -> Unit = {},
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
            modifier = Modifier.fillMaxWidth().testTag("persistent-mini-player").clickable(onClick = onOpen),
        ) {
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    TrackChangeAnimation(
                        track = TrackDisplayInfo(
                            state.mediaId,
                            state.title ?: stringResource(R.string.player_nothing_playing),
                            state.artist ?: stringResource(R.string.player_unknown_artist),
                        ),
                        modifier = Modifier.weight(1f).testTag("mini-player-track"),
                    ) { displayed ->
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            AutPlayArtwork(displayed.title, size = 44.dp, trackId = state.localTrackRefId)
                            Column(Modifier.weight(1f)) {
                                Text(
                                    displayed.title,
                                    style = MaterialTheme.typography.titleMedium,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    displayed.artist,
                                    style = MaterialTheme.typography.bodySmall,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                    IconButton(
                        onClick = onLike,
                        enabled = likeEnabled,
                        modifier = Modifier.testTag("mini-player-like"),
                    ) {
                        AutPlayPlatformIcon(
                            AutPlayIcon.Favorite,
                            stringResource(if (liked) R.string.action_liked else R.string.action_like),
                            tint = if (liked) MaterialTheme.colorScheme.primary else AutPlayTokens.colors.onMiniPlayer,
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
    listenExcludedFromTaste: Boolean = false,
    sessionExcludedFromTaste: Boolean = false,
    listenTasteActionAvailable: Boolean = false,
    sessionTasteActionAvailable: Boolean = false,
    tasteExclusionError: String? = null,
    onSetCurrentListenTasteExcluded: (Boolean) -> Unit = {},
    onSetSessionTasteExcluded: (Boolean) -> Unit = {},
    onDownload: () -> Unit = {},
    downloadState: String? = null,
    swipeTargets: HomeSwipeTargets = HomeSwipeTargets(),
) {
    DisposableEffect(Unit) {
        onObservingChanged(true)
        onDispose { onObservingChanged(false) }
    }
    val collapse = LocalPlayerCollapse.current
    if (state.mediaId == null) {
        Box(modifier.fillMaxSize()) {
            Column(
                Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                AutPlayStateSurface(
                    AutPlayStateKind.PlaybackUnavailable,
                    stringResource(R.string.player_nothing_playing),
                )
            }
            IconButton(
                onClick = { collapse?.invoke() },
                enabled = collapse != null,
                modifier = Modifier.align(Alignment.TopStart).padding(8.dp),
            ) {
                Icon(painterResource(R.drawable.ic_autplay_expand), stringResource(R.string.player_collapse))
            }
        }
        return
    }
    val collapseMotion = rememberPlayerCollapseMotion()
    val animations = rememberSystemAnimationsEnabled()
    val collapseGesture = Modifier.playerCollapseGesture(collapse, collapseMotion, animations)
    val collapseTargetTop = LocalPlayerCollapseTargetTop.current
    var showSleepTimer by rememberSaveable { mutableStateOf(false) }
    var showQueue by rememberSaveable { mutableStateOf(false) }
    val queueSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    if (showQueue) {
        ModalBottomSheet(onDismissRequest = { showQueue = false }, sheetState = queueSheetState) {
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(20.dp)) {
                QueueEditorPanel(queueState, queueActions)
            }
        }
    }
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
            .graphicsLayer { translationY = collapseMotion.offset }
            .background(MaterialTheme.colorScheme.background)
            .testTag("now-playing-panel"),
    ) {
        val artworkWidth = maxWidth
        val density = LocalDensity.current
        val collapseTravel = with(density) {
            (collapseTargetTop ?: (maxHeight - 88.dp)).coerceIn(0.dp, maxHeight).toPx()
        }
        SideEffect { collapseMotion.travel = collapseTravel }
        Column(Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(vertical = 14.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 20.dp).heightIn(min = 48.dp)
                        .then(collapseGesture).testTag("player-collapse-header"),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    IconButton(onClick = { collapse?.let { collapseMotion.collapse(animations, it) } }, enabled = collapse != null) {
                        Icon(painterResource(R.drawable.ic_autplay_expand), stringResource(R.string.player_collapse))
                    }
                    Text(stringResource(R.string.nav_now_playing), style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.size(48.dp))
                }
                Box(Modifier.width(artworkWidth).then(collapseGesture).testTag("player-cover")) {
                    NowPlayingArtworkCarousel(
                        mediaId = state.mediaId,
                        title = state.title ?: stringResource(R.string.player_nothing_playing),
                        trackId = state.localTrackRefId,
                        targets = swipeTargets,
                        enabled = state.controls is PlaybackControlGate.Allowed,
                        onPrevious = onPrevious,
                        onNext = onNext,
                        animations = animations,
                    )
                }
                Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
                            Text(
                                state.title ?: stringResource(R.string.player_nothing_playing),
                                style = MaterialTheme.typography.headlineSmall,
                                textAlign = TextAlign.Start,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                state.artist ?: stringResource(R.string.player_unknown_artist),
                                color = AutPlayTokens.colors.mutedText,
                                textAlign = TextAlign.Start,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
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
                    val timerLabel = when {
                            stopAfterCurrentTrackActive -> stringResource(R.string.player_sleep_timer_after_track_active)
                            sleepTimerRemainingMinutes != null -> pluralStringResource(
                                R.plurals.player_sleep_timer_active,
                                sleepTimerRemainingMinutes,
                                sleepTimerRemainingMinutes,
                            )
                            else -> stringResource(R.string.player_sleep_timer)
                    }
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        if (state.source == PlaybackSourcePresentation.Vault || downloadState != null) {
                            Surface(
                                onClick = onDownload,
                                enabled = state.source == PlaybackSourcePresentation.Vault &&
                                    downloadState !in setOf("COMPLETED", "REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED"),
                                color = Color.Transparent,
                                modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("player-download-track"),
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    AutPlayPlatformIcon(AutPlayIcon.Download, null, tint = MaterialTheme.colorScheme.primary)
                                    Text(stringResource(when (downloadState) {
                                        "COMPLETED" -> R.string.music_saved_offline
                                        "REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED" -> R.string.music_downloading
                                        else -> R.string.music_download_track
                                    }), style = MaterialTheme.typography.bodyMedium)
                                }
                            }
                        } else Spacer(Modifier.weight(1f))
                        IconButton(
                            onClick = { showSleepTimer = true }, enabled = state.controls is PlaybackControlGate.Allowed,
                            modifier = Modifier.testTag("player-sleep-timer").semantics { stateDescription = timerLabel },
                        ) {
                            AutPlayPlatformIcon(AutPlayIcon.Timer, stringResource(R.string.player_sleep_timer),
                                tint = if (sleepTimerRemainingMinutes != null || stopAfterCurrentTrackActive)
                                    MaterialTheme.colorScheme.primary else AutPlayTokens.colors.mutedText)
                        }
                        IconButton(onClick = { showQueue = true }, modifier = Modifier.testTag("player-open-queue")) {
                            AutPlayPlatformIcon(AutPlayIcon.Playlist, stringResource(R.string.queue_editor_title),
                                tint = AutPlayTokens.colors.mutedText)
                        }
                    }
                    TasteExclusionControls(
                        listenExcluded = listenExcludedFromTaste,
                        sessionExcluded = sessionExcludedFromTaste,
                        listenActionAvailable = listenTasteActionAvailable,
                        sessionActionAvailable = sessionTasteActionAvailable,
                        errorCode = tasteExclusionError,
                        onSetListenExcluded = onSetCurrentListenTasteExcluded,
                        onSetSessionExcluded = onSetSessionTasteExcluded,
                    )
                    Spacer(Modifier.height(12.dp))
                }
            }
            PlayerTransportControls(state, swipeTargets, onToggleShuffle, onCycleRepeat, onPrevious, onNext, onTogglePlayPause)
        }
    }
}

/** Uses the existing artwork provider and placeholder, with a full-image player presentation. */
@Composable
internal fun PlayerArtwork(title: String, trackId: String?, modifier: Modifier = Modifier, painter: Painter? = null) {
    val artworkPainter = painter ?: rememberTrackArtwork(trackId)
    val intrinsic = artworkPainter?.intrinsicSize
    val aspectRatio = intrinsic?.takeIf {
        it.width.isFinite() && it.height.isFinite() && it.width > 0f && it.height > 0f
    }?.let { it.width / it.height } ?: 1f
    Box(
        modifier.fillMaxWidth().aspectRatio(aspectRatio).clip(MaterialTheme.shapes.small)
            .semantics { contentDescription = title },
        contentAlignment = Alignment.Center,
    ) {
        if (artworkPainter != null) {
            Image(artworkPainter, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
        } else {
            AutPlayArtworkPlaceholder(title, Modifier.fillMaxSize())
        }
    }
}

@Composable
private fun PlayerTransportControls(
    state: PlaybackPresentationState,
    swipeTargets: HomeSwipeTargets,
    onToggleShuffle: () -> Unit,
    onCycleRepeat: () -> Unit,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    onTogglePlayPause: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 12.dp).testTag("player-transport-controls"),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PlayerModeButton(
            icon = AutPlayIcon.Shuffle,
            label = stringResource(R.string.player_shuffle),
            stateLabel = stringResource(if (state.shuffleModeEnabled) R.string.player_surface_shuffle_on else R.string.player_surface_shuffle_off),
            active = state.shuffleModeEnabled,
            enabled = state.shuffleEnabled,
            onClick = onToggleShuffle,
            tag = "player-shuffle",
        )
        AutPlayIconButton(AutPlayIcon.Previous, R.string.action_previous, onPrevious,
            enabled = (swipeTargets.previous != null || state.previousMediaId != null) && state.controls is PlaybackControlGate.Allowed)
        PrimaryTransportButton(
            icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
            labelRes = if (state.isPlaying) R.string.action_pause else R.string.action_play,
            onClick = onTogglePlayPause,
            enabled = state.controls is PlaybackControlGate.Allowed,
        )
        AutPlayIconButton(AutPlayIcon.Next, R.string.action_next, onNext,
            enabled = (swipeTargets.next != null || state.nextMediaId != null) && state.controls is PlaybackControlGate.Allowed)
        PlayerModeButton(
            icon = AutPlayIcon.Repeat,
            label = stringResource(repeatLabel(state.repeatMode)),
            stateLabel = stringResource(repeatLabel(state.repeatMode)),
            active = state.repeatMode != RepeatModePresentation.Off,
            enabled = state.repeatEnabled,
            onClick = onCycleRepeat,
            tag = "player-repeat",
            one = state.repeatMode == RepeatModePresentation.One,
        )
    }
}

@Composable
private fun PlayerModeButton(
    icon: AutPlayIcon,
    label: String,
    stateLabel: String,
    active: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    tag: String,
    one: Boolean = false,
) {
    val tint = when {
        !enabled -> AutPlayTokens.colors.mutedText.copy(alpha = 0.38f)
        active -> MaterialTheme.colorScheme.onPrimaryContainer
        else -> AutPlayTokens.colors.mutedText
    }
    Surface(
        shape = MaterialTheme.shapes.medium,
        color = if (active) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
        border = if (active) BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = if (enabled) 0.75f else 0.25f)) else null,
    ) {
        IconButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier.size(AutPlayTokens.dimensions.minimumTouchTarget).testTag(tag).semantics {
                contentDescription = label
                stateDescription = stateLabel
                selected = active
            },
        ) {
            Box(contentAlignment = Alignment.Center) {
                AutPlayPlatformIcon(icon, null, Modifier.size(24.dp), tint = tint)
                if (one) Text("1", Modifier.background(MaterialTheme.colorScheme.primaryContainer),
                    style = MaterialTheme.typography.labelSmall, color = tint)
            }
        }
    }
}

@Composable
private fun TasteExclusionControls(
    listenExcluded: Boolean,
    sessionExcluded: Boolean,
    listenActionAvailable: Boolean,
    sessionActionAvailable: Boolean,
    errorCode: String?,
    onSetListenExcluded: (Boolean) -> Unit,
    onSetSessionExcluded: (Boolean) -> Unit,
) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    val expansionLabel = stringResource(if (expanded) R.string.player_section_expanded else R.string.player_section_collapsed)
    Column(Modifier.fillMaxWidth()) {
        HorizontalDivider(color = AutPlayTokens.colors.border)
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("player-taste-toggle")
                .clickable { expanded = !expanded }
                .semantics { stateDescription = expansionLabel },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(stringResource(R.string.player_taste_exclusion_title), Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium, color = AutPlayTokens.colors.mutedText)
            Icon(painterResource(R.drawable.ic_autplay_expand), null,
                Modifier.size(18.dp).graphicsLayer { rotationZ = if (expanded) 180f else 0f },
                tint = AutPlayTokens.colors.mutedText)
        }
        if (expanded) {
            TasteExclusionRow(
                label = stringResource(R.string.player_exclude_listen_short),
                checked = listenExcluded,
                enabled = listenActionAvailable,
                testTag = "taste-exclude-listen",
                onCheckedChange = onSetListenExcluded,
            )
            TasteExclusionRow(
                label = stringResource(R.string.player_exclude_session_short),
                checked = sessionExcluded,
                enabled = sessionActionAvailable,
                testTag = "taste-exclude-session",
                onCheckedChange = onSetSessionExcluded,
            )
            if (!listenActionAvailable) {
                Text(
                    stringResource(R.string.player_taste_listen_unavailable),
                    style = MaterialTheme.typography.bodySmall,
                    color = AutPlayTokens.colors.mutedText,
                )
            }
        }
        if (errorCode != null) {
            Text(
                stringResource(R.string.player_taste_exclusion_error),
                modifier = Modifier.testTag("taste-exclusion-error"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun TasteExclusionRow(
    label: String,
    checked: Boolean,
    enabled: Boolean,
    testTag: String,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
            modifier = Modifier.testTag(testTag),
        )
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
    ) {
        IconButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier.size(48.dp).semantics {
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
@OptIn(ExperimentalMaterial3Api::class)
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
            thumb = {
                Box(
                    Modifier.size(12.dp).background(
                        if (state.canSeek) MaterialTheme.colorScheme.primary else AutPlayTokens.colors.mutedText,
                        CircleShape,
                    ),
                )
            },
            track = {
                PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(4.dp))
            },
            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics {
                contentDescription = accessibilityLabel
                stateDescription = accessibilityState
            },
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(formatDuration(displayed), style = MaterialTheme.typography.labelMedium)
            Text(
                formatDuration(duration),
                style = MaterialTheme.typography.labelMedium,
                color = AutPlayTokens.colors.mutedText,
            )
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

private fun formatDuration(valueMs: Long): String {
    val totalSeconds = valueMs.coerceAtLeast(0) / 1_000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%d:%02d".format(minutes, seconds)
}
