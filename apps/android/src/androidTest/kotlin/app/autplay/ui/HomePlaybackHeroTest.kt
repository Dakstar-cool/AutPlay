package app.autplay.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import org.junit.Assert.assertEquals
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
}
