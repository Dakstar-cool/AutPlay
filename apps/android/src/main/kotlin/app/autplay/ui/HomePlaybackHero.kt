package app.autplay.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
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
        liked = false,
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
) {
    val title = state.title ?: stringResource(R.string.player_nothing_playing)
    val artist = state.artist ?: stringResource(R.string.player_unknown_artist)
    val density = LocalDensity.current
    val heroHeight = with(density) {
        LocalWindowInfo.current.containerSize.height.toDp() * 0.74f
    }.coerceIn(600.dp, 760.dp)
    val palette = remember(title) { playbackVisualPalette(title) }
    val openPlayerLabel = stringResource(R.string.home_hero_open_player)
    val hasPlaybackTarget = state.hasActivePlayback || state.trackId != null
    val canTogglePlayback = hasPlaybackTarget && (!state.hasActivePlayback || state.playPauseEnabled)
    val lightHero = MaterialTheme.colorScheme.background.luminance() > 0.5f
    val heroContentColor = if (lightHero) MaterialTheme.colorScheme.onSurface else Color.White
    val heroMutedContentColor = if (lightHero) {
        MaterialTheme.colorScheme.onSurfaceVariant
    } else {
        Color.White.copy(alpha = 0.72f)
    }
    val heroGlassSurface = if (lightHero) {
        MaterialTheme.colorScheme.surface.copy(alpha = 0.82f)
    } else {
        AutPlayTokens.colors.glassSurface
    }
    val heroGlassBorder = if (lightHero) {
        MaterialTheme.colorScheme.outline.copy(alpha = 0.22f)
    } else {
        AutPlayTokens.colors.glassBorder
    }
    val heroGradient = if (lightHero) {
        listOf(
            palette.first().copy(alpha = 0.46f),
            palette[1].copy(alpha = 0.24f),
            MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.96f),
            MaterialTheme.colorScheme.background,
        )
    } else {
        listOf(
            palette.first().copy(alpha = 0.86f),
            palette[1].copy(alpha = 0.62f),
            Color(0xFF111216),
            Color(0xFF08090B),
        )
    }

    Surface(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = heroHeight)
            .testTag("home-playback-hero"),
        shape = MaterialTheme.shapes.extraLarge,
        color = Color.Transparent,
    ) {
        Box(
            modifier = Modifier.background(
                Brush.verticalGradient(
                    heroGradient,
                ),
            ),
        ) {
            Canvas(Modifier.fillMaxSize()) {
                drawCircle(
                    brush = Brush.radialGradient(
                        listOf(
                            palette.last().copy(alpha = if (lightHero) 0.22f else 0.62f),
                            Color.Transparent,
                        ),
                    ),
                    radius = size.minDimension * 0.72f,
                    center = androidx.compose.ui.geometry.Offset(size.width * 0.88f, size.height * 0.22f),
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        listOf(
                            palette[2].copy(alpha = if (lightHero) 0.14f else 0.34f),
                            Color.Transparent,
                        ),
                    ),
                    radius = size.minDimension * 0.78f,
                    center = androidx.compose.ui.geometry.Offset(size.width * 0.08f, size.height * 0.66f),
                )
            }
            Column(
                modifier = Modifier.fillMaxSize().padding(
                    start = 20.dp,
                    top = 20.dp + topChromePadding,
                    end = 20.dp,
                    bottom = 20.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
                    val heading: @Composable () -> Unit = {
                        Column(Modifier.testTag("home-hero-heading")) {
                            Text(
                                stringResource(
                                    if (localMode) R.string.library_local_mode else R.string.home_hero_personal_flow,
                                ),
                                style = MaterialTheme.typography.labelLarge,
                                color = heroContentColor,
                            )
                        }
                    }
                    val listenTogether: @Composable () -> Unit = {
                        Surface(
                            onClick = onOpenListenTogether,
                            modifier = Modifier
                                .testTag("home-listen-together")
                                .heightIn(min = 48.dp)
                                .widthIn(max = 190.dp),
                            shape = CircleShape,
                            color = heroGlassSurface,
                            contentColor = heroContentColor,
                            border = BorderStroke(1.dp, heroGlassBorder),
                        ) {
                            Text(
                                text = stringResource(R.string.nav_wave_rooms),
                                modifier = Modifier.padding(horizontal = 16.dp, vertical = 13.dp),
                                style = MaterialTheme.typography.labelLarge,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    if (density.fontScale >= 1.5f || maxWidth < 380.dp) {
                        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            heading()
                            listenTogether()
                        }
                    } else {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            heading()
                            listenTogether()
                        }
                    }
                }

                Text(
                    text = artist,
                    style = MaterialTheme.typography.displaySmall,
                    color = heroContentColor,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )

                BoxWithConstraints(
                    modifier = Modifier.fillMaxWidth().heightIn(min = 210.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    val visualSize = (maxWidth * 0.84f).coerceIn(210.dp, 360.dp)
                    val artworkSize = visualSize * 0.62f
                    AutPlayPlaybackHalo(
                        seed = title,
                        isPlaying = state.isPlaying,
                        surfaceId = "home-playback-halo",
                        modifier = Modifier.size(visualSize),
                    )
                    Box(
                        modifier = Modifier
                            .size(artworkSize)
                            .clip(MaterialTheme.shapes.extraLarge)
                            .clickable(
                                enabled = hasPlaybackTarget,
                                role = Role.Button,
                                onClick = {
                                    if (state.hasActivePlayback) {
                                        onOpenPlayer()
                                    } else {
                                        state.trackId?.let(onPlayTrack)
                                    }
                                },
                            )
                            .semantics {
                                role = Role.Button
                                contentDescription = openPlayerLabel
                                if (!hasPlaybackTarget) disabled()
                            },
                    ) {
                        AutPlayArtwork(title = title, size = artworkSize)
                    }
                }

                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = CircleShape,
                    color = heroGlassSurface,
                    contentColor = heroContentColor,
                    border = BorderStroke(1.dp, heroGlassBorder),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        HeroIconButton(
                            icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                            label = stringResource(
                                if (state.isPlaying) R.string.action_pause else R.string.action_play,
                            ),
                            enabled = canTogglePlayback,
                            onClick = {
                                if (state.hasActivePlayback) {
                                    onTogglePlayPause()
                                } else {
                                    state.trackId?.let(onPlayTrack)
                                }
                            },
                        )
                        Column(
                            modifier = Modifier.weight(1f).clickable(
                                enabled = hasPlaybackTarget,
                                role = Role.Button,
                                onClick = {
                                    if (state.hasActivePlayback) onOpenPlayer() else state.trackId?.let(onPlayTrack)
                                },
                            ),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                title,
                                style = MaterialTheme.typography.titleMedium,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                artist,
                                style = MaterialTheme.typography.labelMedium,
                                color = heroMutedContentColor,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        HeroIconButton(
                            icon = AutPlayIcon.Favorite,
                            label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                            enabled = state.trackId != null && !state.liked,
                            selected = state.liked,
                            onClick = { state.trackId?.let(onLike) },
                        )
                    }
                }
                Text(
                    stringResource(
                        when {
                            state.hasActivePlayback && !state.playPauseEnabled -> R.string.home_hero_control_locked
                            state.isPlaying -> R.string.home_hero_playing
                            hasPlaybackTarget -> R.string.home_hero_ready
                            else -> R.string.home_hero_empty
                        },
                    ),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (state.isPlaying) MaterialTheme.colorScheme.primary else heroMutedContentColor,
                )
            }
        }
    }
}

/** Keeps the active transport reachable after the large Home hero has scrolled away. */
@Composable
internal fun HomePlaybackStickyControl(
    state: HomePlaybackHeroUiState,
    onOpenPlayer: () -> Unit,
    onTogglePlayPause: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (!state.hasActivePlayback) return
    val title = state.title ?: stringResource(R.string.player_nothing_playing)
    val artist = state.artist ?: stringResource(R.string.player_unknown_artist)
    val openPlayerLabel = stringResource(R.string.home_hero_open_player)
    val lightSurface = MaterialTheme.colorScheme.background.luminance() > 0.5f
    val stickyContentColor = if (lightSurface) MaterialTheme.colorScheme.onSurface else Color.White
    Surface(
        modifier = modifier.fillMaxWidth().testTag("home-sticky-playback"),
        shape = CircleShape,
        color = if (lightSurface) AutPlayTokens.colors.raisedSurface else Color(0xEB16171B),
        contentColor = stickyContentColor,
        border = BorderStroke(
            1.dp,
            if (lightSurface) AutPlayTokens.colors.border else Color.White.copy(alpha = 0.14f),
        ),
        shadowElevation = 10.dp,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            HeroIconButton(
                icon = if (state.isPlaying) AutPlayIcon.Pause else AutPlayIcon.Play,
                label = stringResource(if (state.isPlaying) R.string.action_pause else R.string.action_play),
                enabled = state.playPauseEnabled,
                onClick = onTogglePlayPause,
            )
            Column(
                modifier = Modifier
                    .weight(1f)
                    .clickable(role = Role.Button, onClick = onOpenPlayer)
                    .semantics { contentDescription = openPlayerLabel },
            ) {
                Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    artist,
                    style = MaterialTheme.typography.labelMedium,
                    color = if (lightSurface) AutPlayTokens.colors.mutedText else Color.White.copy(alpha = 0.70f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
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
        contentColor = if (lightSurface && !selected) MaterialTheme.colorScheme.onSurface else Color.White,
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
                tint = if (lightSurface && !selected) MaterialTheme.colorScheme.onSurface else Color.White,
            )
        }
    }
}
