package app.autplay.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
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
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
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
    if (playerState.mediaId != null && currentTrackRefId != null) {
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
) {
    val title = state.title ?: stringResource(R.string.player_nothing_playing)
    val artist = state.artist ?: stringResource(R.string.player_unknown_artist)
    val density = LocalDensity.current
    val heroHeight = with(density) {
        LocalWindowInfo.current.containerSize.height.toDp() * 0.5f
    }.coerceIn(380.dp, 560.dp)
    val palette = remember(title) { playbackVisualPalette(title) }
    val openPlayerLabel = stringResource(R.string.home_hero_open_player)
    val hasTrack = state.trackId != null
    val canTogglePlayback = hasTrack && (!state.hasActivePlayback || state.playPauseEnabled)

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
                Brush.linearGradient(
                    listOf(
                        palette.first().copy(alpha = 0.28f),
                        MaterialTheme.colorScheme.surface,
                        palette.last().copy(alpha = 0.20f),
                    ),
                ),
            ),
        ) {
            Column(
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 18.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Box(modifier = Modifier.fillMaxWidth()) {
                    val heading: @Composable () -> Unit = {
                        Column(Modifier.testTag("home-hero-heading")) {
                            Text(stringResource(R.string.home_my_wave), style = MaterialTheme.typography.titleLarge)
                            Text(
                                stringResource(
                                    if (localMode) R.string.state_local_continues else R.string.home_hero_personal_flow,
                                ),
                                style = MaterialTheme.typography.labelMedium,
                                color = AutPlayTokens.colors.mutedText,
                            )
                        }
                    }
                    val listenTogether: @Composable () -> Unit = {
                        AutPlayChip(
                            text = stringResource(R.string.home_listen_together),
                            selected = false,
                            onClick = onOpenListenTogether,
                            modifier = Modifier.testTag("home-listen-together"),
                        )
                    }
                    if (density.fontScale >= 1.5f) {
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

                BoxWithConstraints(
                    modifier = Modifier.fillMaxWidth().heightIn(min = 220.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    val visualSize = (maxWidth * 0.68f).coerceIn(190.dp, 280.dp)
                    val artworkSize = visualSize * 0.56f
                    AutPlayPlaybackHalo(
                        seed = title,
                        isPlaying = state.isPlaying,
                        modifier = Modifier.size(visualSize),
                    )
                    Box(
                        modifier = Modifier
                            .size(artworkSize)
                            .clip(MaterialTheme.shapes.extraLarge)
                            .clickable(
                                enabled = hasTrack,
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
                                if (!hasTrack) disabled()
                            },
                    ) {
                        AutPlayArtwork(title = title, size = artworkSize)
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            title,
                            style = MaterialTheme.typography.titleLarge,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            artist,
                            color = AutPlayTokens.colors.mutedText,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
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
                    HeroIconButton(
                        icon = AutPlayIcon.Favorite,
                        label = stringResource(if (state.liked) R.string.action_liked else R.string.action_like),
                        enabled = hasTrack && !state.liked,
                        selected = state.liked,
                        onClick = { state.trackId?.let(onLike) },
                    )
                }
                Text(
                    stringResource(
                        when {
                            state.hasActivePlayback && !state.playPauseEnabled -> R.string.home_hero_control_locked
                            state.isPlaying -> R.string.home_hero_playing
                            hasTrack -> R.string.home_hero_ready
                            else -> R.string.home_hero_empty
                        },
                    ),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (state.isPlaying) MaterialTheme.colorScheme.primary else AutPlayTokens.colors.mutedText,
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
    Surface(
        shape = CircleShape,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
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
                tint = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
