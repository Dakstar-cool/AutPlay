package app.autplay.ui

import android.annotation.SuppressLint
import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.ui.player.PlaybackMiniPlayer
import app.autplay.ui.player.NowPlayingScreen
import java.util.Locale

/** Test-APK-only deterministic surface used to capture the M3 visual evidence matrix. */
class M3VisualEvidenceActivity : ComponentActivity() {
    @SuppressLint("AppBundleLocaleChanges") // Debug-only evidence APK always packages both locales.
    override fun onCreate(savedInstanceState: Bundle?) {
        val language = intent.getStringExtra("language")
        if (language != null) {
            @Suppress("DEPRECATION")
            val configuration = Configuration(resources.configuration).apply {
                setLocale(Locale.forLanguageTag(language))
            }
            @Suppress("DEPRECATION")
            resources.updateConfiguration(configuration, resources.displayMetrics)
        }
        super.onCreate(savedInstanceState)
        val dark = intent.getBooleanExtra("dark", false)
        val screen = intent.getStringExtra("screen") ?: "home"
        setContent {
            var activeScreen by remember { mutableStateOf(screen) }
            var fixturePlaying by remember { mutableStateOf(screen == "mini-playing") }
            var fixturePositionMs by remember { mutableStateOf(64_000L) }
            var fixtureSeekPreviewMs by remember {
                mutableStateOf(if (screen == "player-dragging") 140_000L else null)
            }
            var fixtureShuffle by remember { mutableStateOf(false) }
            var fixtureRepeat by remember { mutableStateOf(RepeatModePresentation.Off) }
            AutPlayTheme(
                AutPlayAppearance(if (dark) AutPlayThemeMode.Dark else AutPlayThemeMode.Light),
            ) {
                Surface(Modifier.fillMaxSize()) {
                    when (activeScreen) {
                        "player", "player-local", "player-dragging", "player-buffering", "no-player" -> NowPlayingScreen(
                            state = when (activeScreen) {
                                "no-player" -> PlaybackPresentationState()
                                "player-buffering" -> playerState(false, PlaybackSourcePresentation.Vault).copy(
                                    playbackStatus = PlaybackStatus.Buffering,
                                )
                                else -> playerState(
                                    wave = false,
                                    source = if (activeScreen == "player-local") PlaybackSourcePresentation.Local else PlaybackSourcePresentation.Vault,
                                )
                            }.copy(
                                isPlaying = fixturePlaying,
                                positionMs = fixturePositionMs,
                                seekPreviewPositionMs = fixtureSeekPreviewMs,
                                shuffleModeEnabled = fixtureShuffle,
                                repeatMode = fixtureRepeat,
                            ),
                            onTogglePlayPause = { fixturePlaying = !fixturePlaying },
                            onToggleShuffle = { fixtureShuffle = !fixtureShuffle },
                            onCycleRepeat = {
                                fixtureRepeat = when (fixtureRepeat) {
                                    RepeatModePresentation.Off -> RepeatModePresentation.All
                                    RepeatModePresentation.All -> RepeatModePresentation.One
                                    RepeatModePresentation.One -> RepeatModePresentation.Off
                                }
                            },
                            onSeekBegin = { fixtureSeekPreviewMs = it },
                            onSeekUpdate = { fixtureSeekPreviewMs = it },
                            onSeekCommit = {
                                fixturePositionMs = fixtureSeekPreviewMs ?: fixturePositionMs
                                fixtureSeekPreviewMs = null
                            },
                            onLike = {},
                            onDislike = {},
                            feedbackEnabled = true,
                            onObservingChanged = {},
                        )
                        "wave" -> NowPlayingScreen(
                            state = playerState(wave = true, source = PlaybackSourcePresentation.Vault),
                            onTogglePlayPause = {},
                            onToggleShuffle = {},
                            onCycleRepeat = {},
                            onSeekBegin = {},
                            onSeekUpdate = {},
                            onSeekCommit = {},
                            onLike = {},
                            onDislike = {},
                            feedbackEnabled = false,
                            onObservingChanged = {},
                        )
                        "search" -> AutPlayAdaptiveShell(
                            selectedDestination = UiDestination.Search,
                            onDestinationSelected = {},
                        ) { _, padding, _ ->
                            SearchProductScreen(
                                state = SearchScreenUiState(
                                    query = "quiet",
                                    results = listOf(CoreTrackUiItem("search-track", "Quiet Signals", "Mara Lin")),
                                    searched = true,
                                ),
                                contentPadding = padding,
                                onQueryChange = {},
                                onSearch = {},
                                onPlay = {},
                            )
                        }
                        "library" -> AutPlayAdaptiveShell(
                            selectedDestination = UiDestination.Library,
                            onDestinationSelected = {},
                        ) { _, padding, _ ->
                            LibraryProductScreen(
                                state = LibraryScreenUiState(
                                    localMode = true,
                                    tracks = listOf(
                                        CoreTrackUiItem("library-track", "Quiet Signals", "Mara Lin", selected = true),
                                        CoreTrackUiItem("library-track-2", "Northern Lights", "Sofia Reed"),
                                    ),
                                ),
                                contentPadding = padding,
                                onAddLocal = {},
                                onSelect = {},
                                onRemoveOrRestore = {},
                                onLike = {},
                            )
                        }
                        "permission" -> androidx.compose.foundation.layout.Box(
                            Modifier.fillMaxSize().systemBarsPadding(),
                        ) {
                            LibraryProductScreen(
                                state = LibraryScreenUiState(
                                    localMode = true,
                                    tracks = listOf(
                                        CoreTrackUiItem(
                                            id = "permission-track",
                                            title = "Archive recording",
                                            artist = "Sofia Reed",
                                            selected = true,
                                            permissionRevoked = true,
                                        ),
                                    ),
                                ),
                                contentPadding = androidx.compose.foundation.layout.PaddingValues(),
                                onAddLocal = {},
                                onSelect = {},
                                onRemoveOrRestore = {},
                                onLike = {},
                            )
                        }
                        "error" -> androidx.compose.foundation.layout.Box(
                            Modifier.fillMaxSize().padding(24.dp),
                            contentAlignment = androidx.compose.ui.Alignment.Center,
                        ) {
                            AutPlayStateSurface(
                                AutPlayStateKind.Error,
                                stringResource(R.string.player_controls_unavailable),
                            )
                        }
                        else -> {
                            val miniState = when (activeScreen) {
                                "mini-playing" -> playerState(false, PlaybackSourcePresentation.Local).copy(isPlaying = fixturePlaying)
                                "mini-paused" -> playerState(false, PlaybackSourcePresentation.Local)
                                "mini-unavailable" -> playerState(false, PlaybackSourcePresentation.Local).copy(
                                    controls = PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE),
                                )
                                else -> null
                            }
                            AutPlayAdaptiveShell(
                                selectedDestination = UiDestination.Home,
                                onDestinationSelected = { destination ->
                                    activeScreen = when (destination) {
                                        UiDestination.Home -> "home"
                                        UiDestination.Search -> "search"
                                        UiDestination.Library -> "library"
                                        else -> activeScreen
                                    }
                                },
                                nowPlayingAvailable = miniState != null,
                                nowPlayingBar = {
                                    miniState?.let {
                                        PlaybackMiniPlayer(
                                            state = it,
                                            onOpen = { activeScreen = "player-local" },
                                            onTogglePlayPause = { fixturePlaying = !fixturePlaying },
                                            onObservingChanged = {},
                                        )
                                    }
                                },
                                detailPane = {
                                    androidx.compose.foundation.layout.Column {
                                        androidx.compose.material3.Text(
                                            stringResource(R.string.expanded_queue_title),
                                            style = androidx.compose.material3.MaterialTheme.typography.titleLarge,
                                        )
                                        androidx.compose.material3.Text("Quiet Signals · Mara Lin")
                                    }
                                },
                            ) { _, padding, _ ->
                                HomeProductScreen(
                                    state = HomeScreenUiState(
                                        localMode = false,
                                        loading = activeScreen == "loading",
                                        offlineFallback = activeScreen == "offline",
                                        releases = if (activeScreen == "empty") emptyList() else listOf(
                                            HomeReleaseUiItem("release", "Northern Lights", "Sofia Reed", "2026"),
                                        ),
                                        recommendations = if (activeScreen == "empty") emptyList() else listOf(
                                            HomeRecommendationUiItem(
                                                "track",
                                                "Quiet Signals",
                                                "Mara Lin",
                                                getString(R.string.home_recommendations),
                                                true,
                                            ),
                                        ),
                                    ),
                                    contentPadding = padding,
                                    onOpenListenTogether = { activeScreen = "wave" },
                                    onRecommendationVisible = {},
                                    onLike = {},
                                    onDislike = {},
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    private fun playerState(
        wave: Boolean,
        source: PlaybackSourcePresentation,
    ): PlaybackPresentationState = PlaybackPresentationState(
        mediaId = "fixture-entry",
        title = "Quiet Signals",
        artist = "Mara Lin",
        positionMs = 64_000,
        bufferedPositionMs = 119_000,
        durationMs = 214_000,
        isSeekable = true,
        source = source,
        context = ActiveQueueContext.Loaded("fixture-queue", "fixture-entry", if (wave) "WAVE" else "USER"),
        controls = if (wave) PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE) else PlaybackControlGate.Allowed,
        seekEnabled = !wave,
        shuffleEnabled = !wave,
        repeatEnabled = !wave,
    )
}
