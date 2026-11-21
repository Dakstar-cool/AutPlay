package app.autplay.ui

import androidx.compose.foundation.BorderStroke
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
import androidx.compose.ui.platform.LocalDensity
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
    val coverTint = app.autplay.ui.player.rememberArtworkTint(bitmap) ?: colors.secondary
    Column(
        modifier = modifier.fillMaxWidth().testTag("home-playback-hero")
            .waveGlow(coverTint, androidx.compose.ui.graphics.lerp(coverTint, colors.tertiary, .35f), colors.background)
            .padding(start = 24.dp, end = 24.dp, top = topChromePadding + 6.dp, bottom = 18.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        HomeHeroHeader(stringResource(when {
            state.hasActivePlayback && !state.playPauseEnabled -> R.string.home_hero_control_locked
            state.isPlaying -> R.string.home_hero_playing
            state.hasActivePlayback -> R.string.home_hero_ready
            localMode -> R.string.home_wave_on_device
            else -> R.string.home_hero_personal_flow
        }), togetherLabel, onOpenListenTogether)
        Spacer(Modifier.height(6.dp))
        WaveHeadline(artist)
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
            Surface(
                onClick = openTarget, enabled = hasPlaybackTarget,
                modifier = Modifier.weight(1f), shape = CircleShape, color = colors.surfaceContainerHigh,
            ) {
                Box(Modifier.heightIn(min = 52.dp).padding(horizontal = 10.dp, vertical = 8.dp),
                    contentAlignment = Alignment.Center) {
                    Text(title, textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
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
    onLike: (String) -> Unit,
    onOpenListenTogether: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val play = stringResource(R.string.action_play)
    Column(
        modifier.fillMaxWidth().waveGlow(Color(0xFFD26539), Color(0xFFB85083), colors.background)
            .padding(horizontal = 24.dp, vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        HomeHeroHeader(stringResource(if (localMode) R.string.home_wave_on_device else R.string.home_hero_personal_flow),
            stringResource(R.string.nav_wave_rooms), onOpenListenTogether)
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
        if (state.title != null) {
            Surface(shape = CircleShape, color = colors.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.heightIn(min = 52.dp).padding(start = 16.dp, end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(listOfNotNull(state.artist?.takeIf { it.isNotBlank() }, state.title).joinToString(" — "),
                        Modifier.weight(1f).padding(vertical = 8.dp), textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (state.trackId != null) HeroIconButton(
                        icon = if (state.liked) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
                        label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                        enabled = true, selected = state.liked, onClick = { onLike(state.trackId) },
                    )
                }
            }
        }
        if (!enabled) Text(stringResource(if (emptyLibrary) R.string.home_first_track_body else R.string.home_my_wave_locked),
            textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun WaveHeadline(text: String) {
    Text(text, Modifier.widthIn(max = 540.dp), textAlign = TextAlign.Center,
        style = MaterialTheme.typography.displaySmall.copy(fontSize = 46.sp, lineHeight = 48.sp,
            fontWeight = FontWeight.Bold, letterSpacing = (-1).sp),
        maxLines = 3, overflow = TextOverflow.Ellipsis)
}

private fun Modifier.waveGlow(accent: Color, secondary: Color, base: Color): Modifier = drawWithCache {
    val intensity = if (base.luminance() < .5f) .74f else .36f
    val warm = Brush.radialGradient(
        0f to accent.copy(alpha = intensity), .34f to accent.copy(alpha = intensity * .76f), 1f to accent.copy(alpha = 0f),
        center = Offset(size.width * .38f, size.height * .53f), radius = size.minDimension * .52f,
    )
    val halo = Brush.radialGradient(
        listOf(secondary.copy(alpha = intensity * .72f), secondary.copy(alpha = 0f)),
        center = Offset(size.width * .67f, size.height * .40f), radius = size.minDimension * .54f,
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
private fun HomeHeroHeader(label: String, togetherLabel: String, onOpen: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val fontScale = LocalDensity.current.fontScale
    Box(Modifier.fillMaxWidth()) {
        val heading: @Composable (Modifier) -> Unit = { modifier ->
            Text(
                label,
                modifier = modifier.testTag("home-hero-heading"),
                style = MaterialTheme.typography.labelMedium,
                color = colors.onSurfaceVariant,
            )
        }
        val action: @Composable () -> Unit = {
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
        if (fontScale >= 1.5f) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                heading(Modifier)
                action()
            }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                heading(Modifier.weight(1f))
                action()
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
