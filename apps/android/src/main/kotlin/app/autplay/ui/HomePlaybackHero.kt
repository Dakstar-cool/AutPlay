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
import androidx.compose.runtime.remember
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
        ?: homeState.recentlyAdded.firstOrNull()
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
    val palette = remember(title) { playbackVisualPalette(title) }
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
    Surface(
        modifier = modifier.fillMaxWidth().testTag("home-playback-hero"),
        color = colors.background,
    ) {
        Column(
            modifier = Modifier
                .background(Brush.verticalGradient(listOf(palette[0].copy(alpha = 0.10f), Color.Transparent)))
                .padding(start = 24.dp, top = topChromePadding + 12.dp, end = 24.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            HomeHeroHeader(localMode, togetherLabel, onOpenListenTogether)
            HomeArtworkCarousel(
                trackId = state.trackId,
                title = title,
                targets = swipeTargets,
                motion = motion,
                enabled = playbackAllowed && state.trackId != null,
                canOpen = hasPlaybackTarget,
                onOpen = openTarget,
                onPrevious = onPrevious,
                onNext = onNext,
            )
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(6.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    title, style = MaterialTheme.typography.headlineMedium, color = colors.onSurface,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                )
                Text(
                    artist, style = MaterialTheme.typography.bodyMedium, color = colors.onSurfaceVariant,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    onClick = {
                        if (state.hasActivePlayback) onTogglePlayPause() else state.trackId?.let(onPlayTrack)
                    },
                    enabled = canTogglePlayback,
                    modifier = Modifier.weight(1f).heightIn(min = 56.dp)
                        .semantics { contentDescription = playLabel; role = Role.Button },
                    shape = CircleShape,
                    color = if (canTogglePlayback) colors.primary else colors.surfaceContainerHigh,
                    contentColor = if (canTogglePlayback) colors.onPrimary else colors.onSurfaceVariant,
                ) {
                    Row(
                        Modifier.padding(horizontal = 20.dp, vertical = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterHorizontally),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AutPlayPlatformIcon(if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play, null, Modifier.size(22.dp))
                        Text(playLabel, style = MaterialTheme.typography.titleMedium)
                    }
                }
                HeroIconButton(
                    icon = AutPlayIcon.Favorite,
                    label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                    enabled = state.trackId != null && !motion.busy,
                    selected = state.liked,
                    onClick = { state.trackId?.let(onLike) },
                )
            }
            Text(
                stringResource(when {
                    state.hasActivePlayback && !state.playPauseEnabled -> R.string.home_hero_control_locked
                    state.isPlaying -> R.string.home_hero_playing
                    hasPlaybackTarget -> R.string.home_hero_ready
                    else -> R.string.home_hero_empty
                }),
                style = MaterialTheme.typography.labelMedium,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                color = if (state.isPlaying) colors.primary else colors.onSurfaceVariant,
            )
        }
    }
}


@Composable
private fun HomeHeroHeader(localMode: Boolean, togetherLabel: String, onOpen: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val fontScale = LocalDensity.current.fontScale
    Box(Modifier.fillMaxWidth()) {
        val heading: @Composable (Modifier) -> Unit = { modifier ->
            Text(
                stringResource(if (localMode) R.string.library_local_mode else R.string.home_hero_personal_flow),
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
