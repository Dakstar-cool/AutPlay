package app.autplay.ui

import android.content.res.Configuration
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class HomePlaybackHeroTest {
    @get:Rule
    val composeRule = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun activeHeroKeepsPlayerTransportArtworkAndLikeInteractiveAtCompactWidth() {
        var playerOpens = 0
        var toggles = 0
        var likedTrack: String? = null
        composeRule.setContent {
            Box(Modifier.width(320.dp)) {
                AutPlayTheme {
                    HomePlaybackHero(
                        state = HomePlaybackHeroUiState(
                            trackId = "track",
                            title = "A deliberately long active track title",
                            artist = "Current artist",
                            isPlaying = true,
                            hasActivePlayback = true,
                            liked = false,
                        ),
                        localMode = true,
                        onOpenPlayer = { playerOpens += 1 },
                        onPlayTrack = {},
                        onTogglePlayPause = { toggles += 1 },
                        onLike = { likedTrack = it },
                        onOpenListenTogether = {},
                    )
                }
            }
        }

        composeRule.onNodeWithTag("home-playback-hero").assertIsDisplayed()
        composeRule.onNodeWithTag("playback-halo").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_pause))
            .assertIsDisplayed().performClick()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like))
            .assertIsDisplayed().performClick()
        composeRule.onNodeWithContentDescription(context.getString(R.string.home_hero_open_player))
            .assertIsDisplayed().performClick()

        composeRule.runOnIdle {
            assertEquals(1, toggles)
            assertEquals("track", likedTrack)
            assertEquals(1, playerOpens)
        }
    }

    @Test
    fun resumableFallbackStartsItsTrackBeforeAPlayerExists() {
        var requestedTrack: String? = null
        var playerOpens = 0
        composeRule.setContent {
            AutPlayTheme {
                HomePlaybackHero(
                    state = HomePlaybackHeroUiState(
                        trackId = "resume-track",
                        title = "Resume me",
                        artist = "Local artist",
                        isPlaying = false,
                        hasActivePlayback = false,
                        liked = false,
                    ),
                    localMode = true,
                    onOpenPlayer = { playerOpens += 1 },
                    onPlayTrack = { requestedTrack = it },
                    onTogglePlayPause = {},
                    onLike = {},
                    onOpenListenTogether = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.home_hero_open_player))
            .performClick()

        composeRule.runOnIdle {
            assertEquals("resume-track", requestedTrack)
            assertEquals(0, playerOpens)
        }
    }

    @Test
    fun restoredPlaybackWithoutLocalTrackMappingStillOpensAndToggles() {
        var playerOpens = 0
        var toggles = 0
        composeRule.setContent {
            AutPlayTheme {
                HomePlaybackHero(
                    state = HomePlaybackHeroUiState(
                        trackId = null,
                        title = "Restored track",
                        artist = "Current artist",
                        isPlaying = true,
                        hasActivePlayback = true,
                        liked = false,
                    ),
                    localMode = true,
                    onOpenPlayer = { playerOpens += 1 },
                    onPlayTrack = {},
                    onTogglePlayPause = { toggles += 1 },
                    onLike = {},
                    onOpenListenTogether = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_pause)).performClick()
        composeRule.onNodeWithContentDescription(context.getString(R.string.home_hero_open_player)).performClick()
        composeRule.runOnIdle {
            assertEquals(1, toggles)
            assertEquals(1, playerOpens)
        }
    }

    @Test
    fun lockedPlaybackKeepsDirectPauseDisabled() {
        composeRule.setContent {
            AutPlayTheme {
                HomePlaybackHero(
                    state = HomePlaybackHeroUiState(
                        trackId = "wave-track",
                        title = "Wave track",
                        artist = "Room artist",
                        isPlaying = true,
                        hasActivePlayback = true,
                        liked = false,
                        playPauseEnabled = false,
                    ),
                    localMode = false,
                    onOpenPlayer = {},
                    onPlayTrack = {},
                    onTogglePlayPause = {},
                    onLike = {},
                    onOpenListenTogether = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_pause))
            .assertIsDisplayed().assertIsNotEnabled()
    }

    @Test
    fun fallbackHeroKeepsItsHaloDecorativeInsteadOfClaimingPausedPlayback() {
        composeRule.setContent {
            AutPlayTheme {
                HomePlaybackHero(
                    state = HomePlaybackHeroUiState(
                        trackId = "paused-track",
                        title = "Paused track",
                        artist = "Local artist",
                        isPlaying = false,
                        hasActivePlayback = false,
                        liked = false,
                    ),
                    localMode = true,
                    onOpenPlayer = {},
                    onPlayTrack = {},
                    onTogglePlayPause = {},
                    onLike = {},
                    onOpenListenTogether = {},
                )
            }
        }

        val halo = composeRule.onNodeWithTag("playback-halo").assertIsDisplayed().fetchSemanticsNode()
        assertTrue(!halo.config.contains(SemanticsProperties.ContentDescription))
    }

    @Test
    fun russianHeroStacksHeaderAndActionAtTwoHundredPercentFontScale() {
        val configuration = Configuration(context.resources.configuration).apply {
            setLocale(java.util.Locale.forLanguageTag("ru"))
        }
        val localizedContext = context.createConfigurationContext(configuration)
        val density = Density(context.resources.displayMetrics.density, fontScale = 2f)
        composeRule.setContent {
            CompositionLocalProvider(
                LocalContext provides localizedContext,
                LocalConfiguration provides configuration,
                LocalResources provides localizedContext.resources,
                LocalDensity provides density,
            ) {
                Box(Modifier.width(320.dp)) {
                    AutPlayTheme {
                        HomePlaybackHero(
                            state = HomePlaybackHeroUiState(
                                trackId = "track",
                                title = "Quiet Signals",
                                artist = "Mara Lin",
                                isPlaying = true,
                                hasActivePlayback = true,
                                liked = false,
                            ),
                            localMode = false,
                            onOpenPlayer = {},
                            onPlayTrack = {},
                            onTogglePlayPause = {},
                            onLike = {},
                            onOpenListenTogether = {},
                        )
                    }
                }
            }
        }

        val heading = composeRule.onNodeWithTag("home-hero-heading").fetchSemanticsNode().boundsInRoot
        val action = composeRule.onNodeWithTag("home-listen-together").fetchSemanticsNode().boundsInRoot
        assertTrue("Hero action must be stacked below the heading at 200% font scale", heading.bottom <= action.top)
    }
}
