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
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CorePlaylistDetailEntry
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreReleaseDetailTrack
import app.autplay.application.library.CoreTechnicalDetails
import app.autplay.application.library.CoreTrackAvailability
import app.autplay.application.library.CoreTrackDetail
import app.autplay.application.library.CoreTrackDetailCapability
import app.autplay.application.library.CoreTrackPreferenceState
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.ui.player.PlaybackMiniPlayer
import app.autplay.ui.player.NowPlayingScreen
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistCredit
import app.autplay.application.artist.ArtistCreditId
import app.autplay.application.artist.ArtistCreditMember
import app.autplay.application.artist.ArtistDetail
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.artist.ArtistSummary
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import java.util.Locale

/** Test-APK-only deterministic surface used to capture post-RC frontend evidence matrices. */
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
            var fixturePlaying by remember { mutableStateOf(screen == "mini-playing" || screen == "home") }
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
                        "search", "search-vault-offline" -> AutPlayAdaptiveShell(
                            selectedDestination = UiDestination.Search,
                            onDestinationSelected = {},
                        ) { _, padding, _ ->
                            SearchProductScreen(
                                state = SearchScreenUiState(
                                    query = "quiet",
                                    results = listOf(CoreTrackUiItem("search-track", "Quiet Signals", "Mara Lin")),
                                    searched = true,
                                    vaultAvailable = activeScreen == "search-vault-offline",
                                    vaultSelected = activeScreen == "search-vault-offline",
                                    vaultError = activeScreen == "search-vault-offline",
                                ),
                                contentPadding = padding,
                                onQueryChange = {},
                                onSearch = {},
                                onPlay = {},
                            )
                        }
                        "library", "library-empty", "library-albums" -> AutPlayAdaptiveShell(
                            selectedDestination = UiDestination.Library,
                            onDestinationSelected = {},
                        ) { _, padding, _ ->
                            LibraryProductScreen(
                                state = LibraryScreenUiState(
                                    localMode = true,
                                    tracks = if (activeScreen == "library-empty") emptyList() else listOf(
                                        CoreTrackUiItem("library-track", "Quiet Signals", "Mara Lin", selected = true),
                                        CoreTrackUiItem("library-track-2", "Northern Lights", "Sofia Reed"),
                                    ),
                                    section = if (activeScreen == "library-albums") {
                                        app.autplay.ui.core.LibrarySection.Albums
                                    } else {
                                        app.autplay.ui.core.LibrarySection.Tracks
                                    },
                                    albums = if (activeScreen == "library-albums") listOf(
                                        CoreCollectionUiItem("release", "Northern Lights", "Sofia Reed", 10),
                                    ) else emptyList(),
                                ),
                                contentPadding = padding,
                                onAddLocal = {},
                                onSelect = {},
                                onRemoveOrRestore = {},
                                onLike = {},
                            )
                        }
                        "detail-track" -> CoreProductDetailScreen(
                            state = CoreProductDetailUiState(
                                target = DetailTarget(DetailKind.Track, "track"),
                                track = CoreTrackDetail(
                                    localUserTrackRefId = "track",
                                    localRecordingId = "recording",
                                    serverRecordingId = null,
                                    title = "Quiet Signals",
                                    artistName = "Mara Lin",
                                    albumName = "Night Archive",
                                    durationMs = 244_000,
                                    artworkRef = null,
                                    preference = CoreTrackPreferenceState("NEUTRAL", false),
                                    availability = CoreTrackAvailability.PERMISSION_REVOKED,
                                    capabilities = setOf(
                                        CoreTrackDetailCapability.LIKE,
                                        CoreTrackDetailCapability.REAUTHORIZE_LIBRARY_ROOT,
                                        CoreTrackDetailCapability.OPEN_IDENTITY_REVIEW,
                                    ),
                                    technicalDetails = CoreTechnicalDetails(
                                        resolutionStatus = "REVIEW_REQUIRED",
                                        resolutionConfidence = 0.72,
                                        recordingKind = "STUDIO",
                                        versionText = "Original mix",
                                    ),
                                ),
                            ),
                        )
                        "detail-playlist" -> CoreProductDetailScreen(
                            state = CoreProductDetailUiState(
                                target = DetailTarget(DetailKind.Playlist, "playlist"),
                                playlist = CorePlaylistDetail(
                                    localPlaylistId = "playlist",
                                    name = "Evening duplicates",
                                    description = "A local playlist",
                                    playlistType = "MANUAL",
                                    entries = listOf(
                                        CorePlaylistDetailEntry("entry-1", "track", "Quiet Signals", "Mara Lin", 244_000, false),
                                        CorePlaylistDetailEntry("entry-2", "track", "Quiet Signals", "Mara Lin", 244_000, false),
                                    ),
                                ),
                            ),
                        )
                        "detail-release" -> CoreProductDetailScreen(
                            state = CoreProductDetailUiState(
                                target = DetailTarget(DetailKind.Release, "release"),
                                release = CoreReleaseDetail(
                                    localReleaseId = "release",
                                    serverReleaseId = null,
                                    title = "Northern Lights",
                                    artistName = "Sofia Reed",
                                    releaseDateText = "2026-08-20",
                                    releaseType = "ALBUM",
                                    artworkRef = null,
                                    tracks = listOf(
                                        CoreReleaseDetailTrack(
                                            "release-track-1",
                                            "recording-1",
                                            1,
                                            1,
                                            "1",
                                            "Quiet Signals",
                                            "Sofia Reed",
                                            244_000,
                                            "track-1",
                                        ),
                                        CoreReleaseDetailTrack(
                                            "release-track-2",
                                            "recording-2",
                                            1,
                                            2,
                                            "2",
                                            "After the Rain",
                                            "Sofia Reed",
                                            221_000,
                                            null,
                                        ),
                                    ),
                                ),
                                subjectArtistCredits = listOf(artistCreditFixture()),
                            ),
                        )
                        "detail-artist" -> CoreProductDetailScreen(
                            state = CoreProductDetailUiState(
                                target = DetailTarget(DetailKind.Artist, ARTIST_ID),
                                artist = ArtistDetail(
                                    summary = artistSummaryFixture(),
                                    credits = listOf(artistCreditFixture()),
                                ),
                                artistAppearances = listOf(
                                    ArtistAppearance(
                                        ArtistCreditId(CREDIT_ID),
                                        "RECORDING",
                                        ServerId(RECORDING_ID),
                                        "Quiet Signals",
                                        ArtistLocalTarget.Track("track-1"),
                                    ),
                                    ArtistAppearance(
                                        ArtistCreditId(CREDIT_ID),
                                        "RELEASE",
                                        ServerId(RELEASE_ID),
                                        "Northern Lights",
                                        ArtistLocalTarget.Release("release"),
                                    ),
                                ),
                            ),
                        )
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
                                        recommendationLoading = activeScreen == "loading",
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
                                        continueListening = if (activeScreen == "empty") null else HomeContinueUiItem(
                                            "track", "Quiet Signals", "Mara Lin", "1:04",
                                        ),
                                        recentlyPlayed = if (activeScreen == "empty") emptyList() else listOf(
                                            HomeTrackUiItem("recent", "Northern Lights", "Sofia Reed"),
                                        ),
                                        playlists = if (activeScreen == "empty") emptyList() else listOf(
                                            CoreCollectionUiItem("playlist", "Evening duplicates", "A local playlist"),
                                        ),
                                        offlineReady = if (activeScreen == "empty") emptyList() else listOf(
                                            HomeTrackUiItem("offline", "Quiet Signals", "Mara Lin"),
                                        ),
                                        problems = if (activeScreen == "permission") listOf(
                                            HomeProblemUiItem("attention", "1 item needs attention"),
                                        ) else emptyList(),
                                    ),
                                    contentPadding = padding,
                                    onOpenListenTogether = { activeScreen = "wave" },
                                    onRecommendationVisible = {},
                                    onLike = {},
                                    onDislike = {},
                                    playerState = playerState(false, PlaybackSourcePresentation.Local).copy(
                                        isPlaying = fixturePlaying,
                                    ),
                                    currentTrackRefId = "track",
                                    onOpenPlayer = { activeScreen = "player-local" },
                                    onTogglePlayPause = { fixturePlaying = !fixturePlaying },
                                    onLikeHeroTrack = {},
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

    private fun artistSummaryFixture() = ArtistSummary(
        key = ArtistKey(ServerProfileId(PROFILE_ID), ArtistId(ARTIST_ID)),
        name = "Sofia Reed",
        sortName = "Reed, Sofia",
        artistType = "PERSON",
        disambiguation = "singer and songwriter",
        countryCode = "GB",
        identityStatus = "VERIFIED",
    )

    private fun artistCreditFixture() = ArtistCredit(
        id = ArtistCreditId(CREDIT_ID),
        displayName = "Sofia Reed",
        members = listOf(
            ArtistCreditMember(ArtistId(ARTIST_ID), 0, "Sofia Reed", "", "PRIMARY"),
        ),
    )

    private companion object {
        const val PROFILE_ID = "11111111-1111-4111-8111-111111111111"
        const val ARTIST_ID = "22222222-2222-4222-8222-222222222222"
        const val CREDIT_ID = "33333333-3333-4333-8333-333333333333"
        const val RECORDING_ID = "44444444-4444-4444-8444-444444444444"
        const val RELEASE_ID = "55555555-5555-4555-8555-555555555555"
    }
}
