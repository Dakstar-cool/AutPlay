package app.autplay.ui.player

import androidx.compose.foundation.clickable

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Button
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionOnScreen
import androidx.compose.ui.unit.toSize
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
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.playback.presentation.canSeek
import app.autplay.ui.AutPlayArtwork
import app.autplay.ui.AutPlayArtworkPlaceholder
import app.autplay.ui.rememberArtworkPainter
import app.autplay.ui.rememberTrackArtworkBitmap
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
    onDismiss: (() -> Unit)? = null,
    onPrevious: (() -> Unit)? = null,
    onNext: (() -> Unit)? = null,
    onChooseArtists: (() -> Unit)? = null,
) {
    DisposableEffect(Unit) {
        onObservingChanged(true)
        onDispose { onObservingChanged(false) }
    }
    val dismissMotion = rememberPlayerCollapseMotion()
    val animations = rememberSystemAnimationsEnabled()
    val dismiss = onDismiss.takeIf { state.dismissEnabled }
    val miniColors = AutPlayTokens.colors.miniPlayerScheme
    Surface(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth()
            .onSizeChanged { dismissMotion.travel = it.height.toFloat() }
            .miniPlayerTrackSwipes(state, onPrevious, onNext)
            .playerCollapseGesture(dismiss, dismissMotion, animations)
            .graphicsLayer { translationY = dismissMotion.offset }
            .testTag("persistent-mini-player"),
        color = miniColors.secondaryContainer,
        contentColor = miniColors.onSecondaryContainer,
    ) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TrackChangeAnimation(
                    track = TrackDisplayInfo(
                        state.mediaId,
                        state.title ?: stringResource(R.string.player_nothing_playing),
                        state.artist ?: stringResource(R.string.player_unknown_artist),
                    ),
                    modifier = Modifier.weight(1f).padding(end = 4.dp).testTag("mini-player-track"),
                ) { displayed ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AutPlayArtwork(displayed.title, size = 40.dp, trackId = state.localTrackRefId)
                        Column(Modifier.weight(1f)) {
                            Text(
                                displayed.title,
                                style = MaterialTheme.typography.titleMedium,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                displayed.artist,
                                modifier = if (onChooseArtists == null || displayed.id != state.mediaId) Modifier else
                                    Modifier.testTag("mini-player-open-artists").clickable(
                                        onClickLabel = stringResource(R.string.library_section_artists),
                                        onClick = onChooseArtists,
                                    ),
                                style = MaterialTheme.typography.bodySmall,
                                color = miniColors.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
                IconButton(
                    onClick = onLike,
                    enabled = likeEnabled,
                    modifier = Modifier
                        .size(AutPlayTokens.dimensions.minimumTouchTarget)
                        .testTag("mini-player-like")
                        .semantics { selected = liked },
                ) {
                    AutPlayPlatformIcon(
                        if (liked) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
                        stringResource(if (liked) R.string.action_liked else R.string.action_like),
                        tint = if (liked) miniColors.primary else miniColors.onSecondaryContainer,
                    )
                }
                AutPlayIconButton(
                    icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                    labelRes = if (state.isPlaying) R.string.action_pause else R.string.action_play,
                    onClick = onTogglePlayPause,
                    enabled = state.controls is PlaybackControlGate.Allowed,
                )
            }
            PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(2.dp), miniColors)
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
    artists: List<app.autplay.application.artist.ArtistSummary> = emptyList(),
    onOpenArtist: (String) -> Unit = {},
    onAddToPlaylist: (() -> Unit)? = null,
    serverConnection: PlayerServerConnection = PlayerServerConnection.Offline,
    onChooseArtists: (() -> Unit)? = null,
) {
    DisposableEffect(Unit) {
        onObservingChanged(true)
        onDispose { onObservingChanged(false) }
    }
    val collapse = LocalPlayerCollapse.current
    val mediaId = state.mediaId
    if (mediaId == null) {
        Box(modifier.fillMaxSize()) {
            Column(
                Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                val queueContext = state.context
                val kind = when {
                    queueContext == ActiveQueueContext.Loading || state.connection == PlaybackConnectionState.Connecting ->
                        AutPlayStateKind.Loading
                    queueContext == ActiveQueueContext.Absent -> AutPlayStateKind.Empty
                    queueContext is ActiveQueueContext.Loaded && queueContext.queueType != "GUEST_WAVE" &&
                        queueContext.currentEntryId != null && state.connection == PlaybackConnectionState.Connected ->
                        AutPlayStateKind.Loading
                    else -> AutPlayStateKind.PlaybackUnavailable
                }
                AutPlayStateSurface(kind, stringResource(if (kind == AutPlayStateKind.Loading)
                    R.string.player_preparing else R.string.player_nothing_playing))
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
    val artwork = rememberTrackArtworkBitmap(state.localTrackRefId)
    val artworkPainter = rememberArtworkPainter(artwork)
    val backdrop = rememberPlayerArtworkBackdrop(artwork)
    val collapseMotion = rememberPlayerCollapseMotion()
    val animations = rememberSystemAnimationsEnabled()
    val contentScroll = rememberScrollState()
    var collapseInputCoordinates by remember { mutableStateOf<LayoutCoordinates?>(null) }
    var collapseHeaderBounds by remember { mutableStateOf(Rect.Zero) }
    var collapseCoverBounds by remember { mutableStateOf(Rect.Zero) }
    var coverOnScreen by remember { mutableStateOf(Rect.Zero) }
    var viewingArtwork by rememberSaveable { mutableStateOf(false) }
    var localArtistPicker by rememberSaveable(mediaId) { mutableStateOf(false) }
    val openArtists: () -> Unit = {
        when (artists.size) {
            0 -> Unit
            1 -> onOpenArtist(artists.single().key.artistId.value)
            else -> if (onChooseArtists == null) localArtistPicker = true else onChooseArtists()
        }
    }
    val collapseTargetTop = LocalPlayerCollapseTargetTop.current
    var sheet by rememberSaveable { mutableStateOf<PlayerSheet?>(null) }
    sheet?.let { selectedSheet ->
        ModalBottomSheet(
            onDismissRequest = { sheet = null },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = AutPlayTokens.colors.raisedSurface,
            contentColor = MaterialTheme.colorScheme.onSurface,
            tonalElevation = 0.dp,
        ) {
            when (selectedSheet) {
                PlayerSheet.Queue -> Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(20.dp)) {
                    if (artists.size > 1) androidx.compose.material3.TextButton(
                        onClick = openArtists, modifier = Modifier.testTag("queue-open-artists"),
                    ) { Text(stringResource(R.string.catalog_current_track_artists)) }
                    QueueEditorPanel(queueState, queueActions)
                }
                PlayerSheet.Timer -> SleepTimerSheet(
                    remainingMinutes = sleepTimerRemainingMinutes,
                    stopAfterCurrentTrackActive = stopAfterCurrentTrackActive,
                    onSelectMinutes = { minutes ->
                        onSetSleepTimer(minutes * 60_000L)
                        sheet = null
                    },
                    onStopAfterCurrentTrack = {
                        onStopAfterCurrentTrack()
                        sheet = null
                    },
                    onCancel = {
                        onCancelSleepTimer()
                        sheet = null
                    },
                )
                PlayerSheet.Recommendations -> TasteExclusionControls(
                    listenExcluded = listenExcludedFromTaste,
                    sessionExcluded = sessionExcludedFromTaste,
                    listenActionAvailable = listenTasteActionAvailable,
                    sessionActionAvailable = sessionTasteActionAvailable,
                    errorCode = tasteExclusionError,
                    onSetListenExcluded = onSetCurrentListenTasteExcluded,
                    onSetSessionExcluded = onSetSessionTasteExcluded,
                )
            }
        }
    }
    Box(
        modifier.fillMaxSize()
            .onGloballyPositioned { collapseInputCoordinates = it }
            .playerCollapseGesture(collapse, collapseMotion, animations, PointerEventPass.Initial) { position ->
                val rootPosition = collapseInputCoordinates?.takeIf { it.isAttached }?.localToRoot(position)
                contentScroll.value == 0 && rootPosition != null &&
                    (collapseHeaderBounds.contains(rootPosition) || collapseCoverBounds.contains(rootPosition))
            },
    ) {
        BoxWithConstraints(
            Modifier.fillMaxSize()
                .graphicsLayer { translationY = collapseMotion.offset }
                .background(backdrop.surface)
                .testTag("now-playing-panel"),
        ) {
            val density = LocalDensity.current
            val coverWidth = minOf(maxWidth * 0.77f, 440.dp)
            val collapseTravel = with(density) {
                (collapseTargetTop ?: (maxHeight - 88.dp)).coerceIn(0.dp, maxHeight).toPx()
            }
            SideEffect { collapseMotion.travel = collapseTravel }
            Column(Modifier.fillMaxSize()) {
                Row(
                    Modifier.fillMaxWidth().background(backdrop.header).padding(horizontal = 8.dp)
                        .onGloballyPositioned { collapseHeaderBounds = it.boundsInRoot() }
                        .testTag("player-collapse-header"),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = { collapse?.let { collapseMotion.collapse(animations, it) } }, enabled = collapse != null) {
                        Icon(painterResource(R.drawable.ic_autplay_expand), stringResource(R.string.player_collapse))
                    }
                    Text(
                        stringResource(R.string.nav_now_playing), Modifier.weight(1f),
                        style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    IconButton(onClick = { sheet = PlayerSheet.Queue }, modifier = Modifier.testTag("player-open-queue")) {
                        AutPlayPlatformIcon(AutPlayIcon.Playlist, stringResource(R.string.queue_editor_title))
                    }
                    PlayerMoreMenu(onAddToPlaylist) { sheet = it }
                }
                PlayerBody(
                    modifier = Modifier.weight(1f),
                    scroll = contentScroll,
                    backdrop = backdrop,
                    artwork = {
                        NowPlayingArtworkCarousel(
                            mediaId = mediaId,
                            title = state.title ?: stringResource(R.string.player_nothing_playing),
                            painter = artworkPainter,
                            targets = swipeTargets,
                            enabled = state.controls is PlaybackControlGate.Allowed,
                            onPrevious = onPrevious,
                            onNext = onNext,
                            animations = animations,
                            onOpen = { viewingArtwork = true },
                            modifier = Modifier.widthIn(max = coverWidth).onGloballyPositioned {
                                collapseCoverBounds = it.boundsInRoot()
                                val frame = Rect(it.positionOnScreen(), it.size.toSize())
                                val ratio = artworkPainter.artworkAspectRatio()
                                val width = minOf(frame.width, frame.height * ratio)
                                val half = Offset(width / 2, width / ratio / 2)
                                coverOnScreen = Rect(frame.center - half, frame.center + half)
                            }.graphicsLayer { alpha = if (viewingArtwork) 0f else 1f }.testTag("player-cover"),
                        )
                    },
                ) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(state.title ?: stringResource(R.string.player_nothing_playing),
                            style = MaterialTheme.typography.headlineSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        PlayerArtistLink(state.artist, artists, openArtists)
                        PlaybackTimeline(state, onSeekBegin, onSeekUpdate, onSeekCommit)
                        DirectControlMessage(state)
                    }
                }
                PlayerTransportControls(state, swipeTargets, onPrevious, onNext, onTogglePlayPause,
                    preference, feedbackEnabled, onLike, onDislike, onClearPreference)
                PlayerSecondaryControls(state, serverConnection, downloadState, onDownload, onToggleShuffle, onCycleRepeat,
                    sleepTimerRemainingMinutes, stopAfterCurrentTrackActive, onTimer = { sheet = PlayerSheet.Timer })
            }
        }
    }
    if (viewingArtwork) {
        FullscreenPlayerArtwork(
            title = state.title ?: stringResource(R.string.player_nothing_playing),
            painter = artworkPainter,
            coverOnScreen = coverOnScreen,
            animations = animations,
            onClosed = { viewingArtwork = false },
        )
    }
    if (localArtistPicker && onChooseArtists == null && artists.size > 1) {
        PlayerArtistPicker(artists, { localArtistPicker = false }) {
            localArtistPicker = false
            onOpenArtist(it)
        }
    }
}

private enum class PlayerSheet { Queue, Timer, Recommendations }

@Composable
private fun PlayerMoreMenu(onAddToPlaylist: (() -> Unit)?, onSelect: (PlayerSheet) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { expanded = true }, modifier = Modifier.testTag("player-more")) {
            Icon(painterResource(R.drawable.ic_autplay_more_vertical), stringResource(R.string.player_more_options))
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            if (onAddToPlaylist != null) DropdownMenuItem(
                text = { Text(stringResource(R.string.playlist_add_selected)) },
                onClick = { expanded = false; onAddToPlaylist() },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.player_taste_exclusion_title)) },
                leadingIcon = { Icon(painterResource(R.drawable.ic_autplay_settings), null) },
                onClick = {
                    expanded = false
                    onSelect(PlayerSheet.Recommendations)
                },
                modifier = Modifier.testTag("player-recommendations"),
            )
        }
    }
}

@Composable
private fun PlayerBody(
    modifier: Modifier,
    scroll: ScrollState,
    backdrop: PlayerArtworkBackdrop,
    artwork: @Composable () -> Unit,
    details: @Composable () -> Unit,
) {
    BoxWithConstraints(modifier.fillMaxWidth()) {
        val viewportHeight = constraints.maxHeight
        // Measure the text and timeline first so font size and optional actions determine the space for art.
        Layout(
            modifier = Modifier.fillMaxSize().testTag("now-playing-scroll").verticalScroll(scroll),
            content = {
                val wash = remember(backdrop) { Brush.verticalGradient(listOf(backdrop.header, backdrop.surface)) }
                Box(Modifier.fillMaxWidth().background(wash), contentAlignment = Alignment.Center) { artwork() }
                details()
            },
        ) { (art, info), bounds ->
            val controls = info.measure(bounds.copy(minHeight = 0))
            val coverHeight = (viewportHeight - controls.height).coerceAtLeast(48.dp.roundToPx())
            val cover = art.measure(bounds.copy(minHeight = coverHeight, maxHeight = coverHeight))
            layout(bounds.maxWidth, cover.height + controls.height) {
                cover.placeRelative(0, 0)
                controls.placeRelative(0, cover.height)
            }
        }
    }
}

/** Uses the existing artwork provider and placeholder, with a full-image player presentation. */
@Composable
internal fun PlayerArtwork(
    title: String,
    painter: Painter?,
    modifier: Modifier = Modifier,
    shape: androidx.compose.ui.graphics.Shape = MaterialTheme.shapes.small,
) {
    Box(
        modifier.fillMaxWidth().aspectRatio(painter.artworkAspectRatio()).clip(shape)
            .semantics { contentDescription = title },
        contentAlignment = Alignment.Center,
    ) {
        if (painter != null) {
            Image(painter, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
        } else {
            AutPlayArtworkPlaceholder(title, Modifier.fillMaxSize())
        }
    }
}

@Composable
private fun PlayerTransportControls(
    state: PlaybackPresentationState,
    swipeTargets: HomeSwipeTargets,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    onTogglePlayPause: () -> Unit,
    preference: PlaybackPreferenceUiState,
    feedbackEnabled: Boolean,
    onLike: () -> Unit,
    onDislike: () -> Unit,
    onClearPreference: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).testTag("player-transport-controls"),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val disliked = preference == PlaybackPreferenceUiState.Disliked
        PreferenceIconButton(AutPlayIcon.HeartOff, R.string.action_dislike, disliked, feedbackEnabled,
            if (disliked) onClearPreference else onDislike)
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
        val liked = preference == PlaybackPreferenceUiState.Liked
        PreferenceIconButton(if (liked) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
            R.string.action_like, liked, feedbackEnabled, if (liked) onClearPreference else onLike)
    }
}

@Composable
private fun PlayerSecondaryControls(
    state: PlaybackPresentationState,
    serverConnection: PlayerServerConnection,
    downloadState: String?,
    onDownload: () -> Unit,
    onToggleShuffle: () -> Unit,
    onCycleRepeat: () -> Unit,
    remainingMinutes: Int?,
    stopAfterTrack: Boolean,
    onTimer: () -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
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
        if (serverConnection != PlayerServerConnection.Offline) {
            val saved = downloadState == "COMPLETED" || state.source == PlaybackSourcePresentation.Download
            val pending = downloadState in setOf("REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED")
            val description = stringResource(when {
                saved -> R.string.music_saved_offline
                pending -> R.string.music_downloading
                else -> R.string.music_download_track
            })
            PlayerModeButton(if (saved) AutPlayIcon.Check else AutPlayIcon.Download, description, description,
                active = saved || pending, enabled = serverConnection == PlayerServerConnection.Connected &&
                    !saved && !pending && state.source == PlaybackSourcePresentation.Vault,
                onClick = onDownload, tag = "player-download-track")
        } else {
            PlayerModeButton(AutPlayIcon.Shuffle, stringResource(R.string.player_shuffle),
                stringResource(if (state.shuffleModeEnabled) R.string.player_surface_shuffle_on else R.string.player_surface_shuffle_off),
                state.shuffleModeEnabled, state.shuffleEnabled, onToggleShuffle, "player-shuffle")
        }
        val timerLabel = stringResource(R.string.player_sleep_timer)
        val timerState = when {
            stopAfterTrack -> stringResource(R.string.player_sleep_timer_after_track_active)
            remainingMinutes != null -> pluralStringResource(R.plurals.player_sleep_timer_active, remainingMinutes, remainingMinutes)
            else -> timerLabel
        }
        PlayerModeButton(AutPlayIcon.Timer, timerLabel, timerState, remainingMinutes != null || stopAfterTrack,
            state.controls is PlaybackControlGate.Allowed, onTimer, "player-sleep-timer")
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
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = 24.dp, end = 24.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(stringResource(R.string.player_taste_exclusion_title), style = MaterialTheme.typography.headlineSmall)
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
                this.selected = selected
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
        color = MaterialTheme.colorScheme.primaryFixedDim,
        contentColor = MaterialTheme.colorScheme.onPrimaryFixed,
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
            // Slider uses this width to map gestures to the usable track length.
            thumb = { Spacer(Modifier.size(12.dp)) },
            track = { slider ->
                PlaybackProgressTrack(state, Modifier.fillMaxWidth().height(12.dp),
                    thumbFraction = slider.value / duration.toFloat())
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
private fun PlaybackProgressTrack(
    state: PlaybackPresentationState,
    modifier: Modifier,
    colors: ColorScheme = MaterialTheme.colorScheme,
    thumbFraction: Float? = null,
) {
    val thumbColor = if (state.canSeek) colors.primary else AutPlayTokens.colors.mutedText
    val duration = state.durationMs?.takeIf { it > 0 } ?: 1L
    val played = (state.seekPreviewPositionMs ?: state.positionMs).coerceIn(0, duration).toFloat() / duration
    val buffered = state.bufferedPositionMs.coerceIn(0, duration).toFloat() / duration
    Canvas(modifier) {
        val height = if (thumbFraction == null) size.height else 4.dp.toPx()
        val top = (size.height - height) / 2f
        val radius = CornerRadius(height / 2f)
        val rtl = layoutDirection == androidx.compose.ui.unit.LayoutDirection.Rtl
        fun segment(color: Color, fraction: Float) {
            val width = size.width * fraction
            drawRoundRect(color, Offset(if (rtl) size.width - width else 0f, top),
                androidx.compose.ui.geometry.Size(width, height), radius)
        }
        segment(colors.surfaceVariant, 1f)
        drawLine(colors.outline, Offset(0f, center.y), Offset(size.width, center.y), strokeWidth = 1.dp.toPx())
        segment(colors.outlineVariant, buffered)
        val fraction = thumbFraction ?: played
        segment(colors.primary, fraction)
        if (thumbFraction != null) {
            drawCircle(thumbColor, 6.dp.toPx(), Offset(size.width * (if (rtl) 1f - fraction else fraction), center.y))
        }
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

@Composable
private fun PlayerArtistLink(
    name: String?, artists: List<app.autplay.application.artist.ArtistSummary>, onClick: () -> Unit,
) {
    val link = if (artists.isEmpty()) Modifier else Modifier.testTag("player-open-artist")
        .clickable(onClickLabel = stringResource(R.string.detail_open_artist), onClick = onClick)
    Text(name ?: artists.singleOrNull()?.name ?: stringResource(R.string.player_unknown_artist),
        modifier = link, color = if (artists.isEmpty()) AutPlayTokens.colors.mutedText else MaterialTheme.colorScheme.primary,
        maxLines = 1, overflow = TextOverflow.Ellipsis)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun PlayerArtistPicker(
    artists: List<app.autplay.application.artist.ArtistSummary>,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = AutPlayTokens.colors.raisedSurface,
        modifier = Modifier.testTag("player-artist-picker"),
    ) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp)) {
            Text(stringResource(R.string.library_section_artists), style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.semantics { heading() })
            artists.forEach { artist ->
                val id = artist.key.artistId.value
                Row(
                    Modifier.fillMaxWidth().testTag("player-artist-$id")
                        .clickable(role = androidx.compose.ui.semantics.Role.Button,
                            onClickLabel = stringResource(R.string.detail_open_artist)) { onSelect(id) }.padding(8.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    AutPlayArtwork(artist.name, Modifier.clearAndSetSemantics {}, size = 48.dp)
                    Column {
                        Text(artist.name)
                        artist.disambiguation?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
        }
    }
}
