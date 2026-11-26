package app.autplay.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.setProgress
import app.autplay.playback.presentation.canSeek
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.sp
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState

public data class HomePlaybackHeroUiState(
    public val trackId: String?,
    public val title: String?,
    public val artist: String?,
    public val isPlaying: Boolean,
    public val hasActivePlayback: Boolean,
    public val liked: Boolean,
    public val playPauseEnabled: Boolean = true,
    public val playbackKey: String? = trackId,
)

public fun buildHomePlaybackHeroUiState(
    homeState: HomeScreenUiState,
    playerState: PlaybackPresentationState,
    currentTrackRefId: String?,
    currentTrackLiked: Boolean,
): HomePlaybackHeroUiState {
    if (playerState.mediaId != null) {
        return HomePlaybackHeroUiState(
            trackId = currentTrackRefId,
            title = playerState.title,
            artist = playerState.artist,
            isPlaying = playerState.isPlaying,
            hasActivePlayback = true,
            liked = currentTrackLiked,
            playPauseEnabled = playerState.controls is PlaybackControlGate.Allowed,
            playbackKey = playerState.mediaId,
        )
    }
    val fallback = homeState.continueListening?.let {
        HomeTrackUiItem(it.trackId, it.title, it.artist)
    } ?: homeState.recentlyPlayed.firstOrNull()
    return HomePlaybackHeroUiState(
        trackId = fallback?.id,
        title = fallback?.title,
        artist = fallback?.artist,
        isPlaying = false,
        hasActivePlayback = false,
        liked = fallback?.id in homeState.likedTrackIds,
        playPauseEnabled = fallback != null,
    )
}

@Composable
public fun HomePlaybackHero(
    state: HomePlaybackHeroUiState,
    localMode: Boolean,
    onOpenPlayer: () -> Unit,
    onPlayTrack: (String) -> Unit,
    onTogglePlayPause: () -> Unit,
    onLike: (String) -> Unit,
    onOpenListenTogether: () -> Unit,
    modifier: Modifier = Modifier,
    topChromePadding: Dp = 0.dp,
    swipeTargets: HomeSwipeTargets = HomeSwipeTargets(),
    onPrevious: () -> Unit = {},
    onNext: () -> Unit = {},
    onOpenArtist: (() -> Unit)? = null,
    playerState: PlaybackPresentationState = PlaybackPresentationState(),
    onSeek: (Long) -> Unit = {},
) {
    val title = state.title ?: stringResource(R.string.player_nothing_playing)
    val artist = state.artist ?: stringResource(R.string.player_unknown_artist)
    val togetherLabel = stringResource(R.string.nav_wave_rooms)
    val hasPlaybackTarget = state.hasActivePlayback || state.trackId != null
    val motion = rememberHomeCarouselMotion(state.playbackKey)
    val playbackAllowed = hasPlaybackTarget && (!state.hasActivePlayback || state.playPauseEnabled)
    val canTogglePlayback = playbackAllowed && !motion.busy
    val playLabel = stringResource(if (state.isPlaying) R.string.action_pause else R.string.action_play)
    val openTarget = {
        if (state.hasActivePlayback) onOpenPlayer() else state.trackId?.let(onPlayTrack)
        Unit
    }
    val colors = MaterialTheme.colorScheme
    val bitmap = rememberTrackArtworkBitmap(state.trackId)
    val palette = app.autplay.ui.player.rememberArtworkPalette(bitmap)
    val coverTint = palette?.dominant ?: colors.secondary
    val secondaryTint = palette?.accent ?: androidx.compose.ui.graphics.lerp(coverTint, colors.tertiary, .35f)
    Column(
        modifier = modifier.fillMaxWidth().testTag("home-playback-hero")
            .waveGlow(coverTint, secondaryTint, colors.background)
            .padding(start = 24.dp, end = 24.dp, top = topChromePadding + 6.dp, bottom = 18.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        HomeHeroHeader(togetherLabel, onOpenListenTogether)
        Spacer(Modifier.height(6.dp))
        WaveHeadline(artist, if (onOpenArtist == null) Modifier else Modifier
            .testTag("hero-open-artist").clickable(onClickLabel = stringResource(R.string.detail_open_artist),
                onClick = onOpenArtist))
        Spacer(Modifier.height(8.dp))
        HomeArtworkCarousel(
            title = title, targets = swipeTargets, motion = motion,
            enabled = playbackAllowed && state.trackId != null, canOpen = hasPlaybackTarget,
            onOpen = openTarget, onPrevious = onPrevious, onNext = onNext,
            compactCover = true, artworkPainter = rememberArtworkPainter(bitmap),
        )
        Row(
            Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HeroIconButton(
                icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                label = playLabel, enabled = canTogglePlayback,
                onClick = { if (state.hasActivePlayback) onTogglePlayPause() else state.trackId?.let(onPlayTrack) },
            )
            HomeTrackTitle(title, playerState, onSeek, Modifier.weight(1f), openTarget.takeIf { hasPlaybackTarget })
            HeroIconButton(
                icon = if (state.liked) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
                label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                enabled = state.trackId != null && !motion.busy, selected = state.liked,
                onClick = { state.trackId?.let(onLike) },
            )
        }
    }
}

@Composable
internal fun HomeWaveStartHero(
    state: HomePlaybackHeroUiState,
    localMode: Boolean,
    emptyLibrary: Boolean,
    enabled: Boolean,
    onStart: () -> Unit,
    onResume: () -> Unit,
    onLike: (String) -> Unit,
    onOpenListenTogether: () -> Unit,
    modifier: Modifier = Modifier,
    onOpenArtist: (() -> Unit)? = null,
    playerState: PlaybackPresentationState = PlaybackPresentationState(),
    onSeek: (Long) -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val play = stringResource(R.string.home_my_wave)
    Column(
        modifier.fillMaxWidth().waveGlow(Color(0xFFD26539), Color(0xFFB85083), colors.background)
            .padding(horizontal = 24.dp, vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        HomeHeroHeader(stringResource(R.string.nav_wave_rooms), onOpenListenTogether)
        Spacer(Modifier.height(32.dp))
        WaveHeadline(stringResource(R.string.home_my_wave))
        Surface(
            onClick = onStart, enabled = enabled, shape = CircleShape,
            color = if (enabled) colors.primary else colors.surfaceContainerHigh,
            contentColor = if (enabled) colors.onPrimary else colors.onSurfaceVariant,
            modifier = Modifier.size(84.dp).testTag("home-my-wave")
                .semantics { contentDescription = play; role = Role.Button },
        ) {
            Box(contentAlignment = Alignment.Center) { AutPlayPlatformIcon(AutPlayIcon.Play, null, Modifier.size(36.dp)) }
        }
        if (state.title != null || state.hasActivePlayback) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                HeroIconButton(
                    icon = AutPlayIcon.Play, label = stringResource(R.string.action_continue),
                    enabled = if (state.hasActivePlayback) state.playPauseEnabled else enabled,
                    onClick = onResume,
                )
                HomeTrackTitle(listOfNotNull(state.artist?.takeIf { it.isNotBlank() }, state.title
                    ?: stringResource(R.string.player_nothing_playing)).joinToString(" — "), playerState, onSeek,
                    Modifier.weight(1f).testTag("hero-resume-artist"), onOpenArtist,
                    stringResource(R.string.detail_open_artist))
                if (state.trackId != null) HeroIconButton(
                    icon = if (state.liked) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
                    label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                    enabled = true, selected = state.liked, onClick = { onLike(state.trackId) },
                )
            }
        }
        if (!enabled && !emptyLibrary) Text(stringResource(R.string.home_my_wave_locked),
            textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun HomeTrackTitle(
    title: String, player: PlaybackPresentationState, onSeek: (Long) -> Unit, modifier: Modifier,
    onClick: (() -> Unit)?, clickLabel: String? = null,
) {
    val duration = player.durationMs?.takeIf { it > 0 && !player.isLive && player.mediaId != null }
    val canSeek = duration != null && player.canSeek
    val rtl = androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl
    var preview by remember(player.mediaId, duration, canSeek) { mutableStateOf<Long?>(null) }
    val seek by rememberUpdatedState(onSeek)
    val position = (preview ?: player.seekPreviewPositionMs ?: player.positionMs).coerceIn(0L, duration ?: 0L)
    val progress = if (duration == null) Modifier else {
        val description = stringResource(R.string.player_progress_description, formatDuration(position), formatDuration(duration))
        Modifier.semantics {
            progressBarRangeInfo = ProgressBarRangeInfo(position.toFloat(), 0f..duration.toFloat())
            stateDescription = description
            if (canSeek) setProgress { value -> seek(value.toLong().coerceIn(0, duration)); true }
        }.then(if (!canSeek) Modifier else Modifier.pointerInput(player.mediaId, duration, rtl) {
            detectHorizontalDragGestures(
                onDragEnd = { preview?.let(seek); preview = null },
                onDragCancel = { preview = null },
            ) { change, _ ->
                val fraction = (change.position.x / size.width).coerceIn(0f, 1f)
                preview = (duration * (if (rtl) 1f - fraction else fraction)).toLong()
                change.consume()
            }
        })
    }
    TrackProgressTitle(title, duration?.let { position.toFloat() / it } ?: 0f,
        modifier.testTag("home-track-title").then(progress)
            .then(if (onClick == null) Modifier else Modifier.clickable(onClickLabel = clickLabel, onClick = onClick)))
}

@Composable
internal fun TrackProgressTitle(title: String, fraction: Float, modifier: Modifier = Modifier) {
    val base = MaterialTheme.colorScheme.surfaceContainerHigh
    val fill = Color.White.copy(alpha = if (base.luminance() > .5f) .55f else .16f)
    Box(modifier.heightIn(min = 52.dp).clip(CircleShape).background(base)
        .drawBehind {
            val width = size.width * fraction.coerceIn(0f, 1f)
            val left = if (layoutDirection == androidx.compose.ui.unit.LayoutDirection.Rtl) size.width - width else 0f
            drawRect(fill, Offset(left, 0f), Size(width, size.height))
        }.padding(horizontal = 12.dp, vertical = 8.dp), contentAlignment = Alignment.Center) {
        Text(title, textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun WaveHeadline(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier.widthIn(max = 540.dp), textAlign = TextAlign.Center,
        style = MaterialTheme.typography.displaySmall.copy(fontSize = 46.sp, lineHeight = 48.sp,
            fontWeight = FontWeight.Bold, letterSpacing = (-1).sp),
        maxLines = 3, overflow = TextOverflow.Ellipsis)
}

private fun Modifier.waveGlow(accent: Color, secondary: Color, base: Color): Modifier = drawWithCache {
    val intensity = if (base.luminance() < .5f) .94f else .46f
    val warm = Brush.radialGradient(
        0f to accent.copy(alpha = intensity), .40f to accent.copy(alpha = intensity * .86f), .72f to accent.copy(alpha = intensity * .38f), 1f to accent.copy(alpha = 0f),
        center = Offset(size.width * .38f, size.height * .53f), radius = size.minDimension * .68f,
    )
    val halo = Brush.radialGradient(
        0f to secondary.copy(alpha = intensity * .88f), .46f to secondary.copy(alpha = intensity * .7f), 1f to secondary.copy(alpha = 0f),
        center = Offset(size.width * .72f, size.height * .54f), radius = size.minDimension * .65f,
    )
    val edge = Brush.verticalGradient(0f to base, .16f to base.copy(alpha = 0f),
        .82f to base.copy(alpha = 0f), 1f to base)
    onDrawBehind {
        drawRect(base)
        drawRect(halo)
        drawRect(warm)
        drawRect(edge)
    }
}

@Composable
private fun HomeHeroHeader(togetherLabel: String, onOpen: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Surface(
            onClick = onOpen,
            modifier = Modifier.testTag("home-listen-together").heightIn(min = 48.dp)
                .semantics { contentDescription = togetherLabel; role = Role.Button },
            shape = CircleShape,
            color = colors.surfaceContainerHigh,
            contentColor = colors.onSurface,
        ) {
            Row(
                Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                AutPlayPlatformIcon(AutPlayIcon.Wave, null, Modifier.size(18.dp), colors.primary)
                Text(stringResource(R.string.home_listen_together_short), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun HeroIconButton(
    icon: AutPlayIcon,
    label: String,
    enabled: Boolean,
    onClick: () -> Unit,
    selected: Boolean = false,
) {
    val lightSurface = MaterialTheme.colorScheme.background.luminance() > 0.5f
    Surface(
        shape = CircleShape,
        color = when {
            selected -> MaterialTheme.colorScheme.primary
            lightSurface -> MaterialTheme.colorScheme.surface.copy(alpha = 0.78f)
            else -> Color.Black.copy(alpha = 0.34f)
        },
        contentColor = if (selected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
        border = BorderStroke(
            1.dp,
            if (lightSurface) {
                MaterialTheme.colorScheme.outline.copy(alpha = 0.24f)
            } else {
                Color.White.copy(alpha = 0.16f)
            },
        ),
    ) {
        IconButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier
                .size(AutPlayTokens.dimensions.minimumTouchTarget)
                .semantics {
                    contentDescription = label
                    if (selected) stateDescription = label
                },
        ) {
            AutPlayPlatformIcon(
                icon = icon,
                contentDescription = null,
                modifier = Modifier.size(24.dp),
                tint = if (selected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
