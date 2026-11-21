package app.autplay.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.test.hasText
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.player.PlaybackMiniPlayer
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class HomeInteractionTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private var next = 0
    private var previous = 0
    private var toggles = 0

    private fun render() {
        compose.setContent {
            var liked by remember { mutableStateOf(false) }
            var player by remember { mutableStateOf(PlaybackPresentationState(mediaId = "entry", title = "Current song", artist = "Current artist", isPlaying = true, controls = PlaybackControlGate.Allowed)) }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = UiDestination.Home,
                    onDestinationSelected = {},
                    nowPlayingAvailable = true,
                    nowPlayingBar = {
                        PlaybackMiniPlayer(player, {}, { toggles++ }, {}, liked, true, { liked = !liked })
                    },
                ) { _, padding, _ ->
                    HomeProductScreen(
                        state = HomeScreenUiState(true, false, false, emptyList(), emptyList(),
                            recentlyPlayed = (1..30).map { HomeTrackUiItem("$it", "Recent $it", "Artist") }),
                        contentPadding = padding,
                        onOpenListenTogether = {}, onRecommendationVisible = {}, onLike = {}, onDislike = {},
                        playerState = player, currentTrackRefId = "track", currentTrackLiked = liked,
                        onTogglePlayPause = { toggles++ },
                        onLikeHeroTrack = { liked = !liked },
                        onPreviousTrack = { previous++; player = player.copy(mediaId = "entry", title = "Current song") },
                        onNextTrack = { next++; player = player.copy(mediaId = "next", title = "Next song") },
                        swipeTargets = HomeSwipeTargets(
                            HomeSwipeTarget("entry", "track", "Current song", "Current artist"),
                            HomeSwipeTarget("next", "next-track", "Next song", "Current artist"),
                        ),
                    )
                }
            }
        }
    }

    @Test fun horizontalSwipesSwitchDirectionAndVerticalScrollDoesNotSkip() {
        render()
        compose.onNodeWithTag("home-track-carousel").performTouchInput { swipeLeft() }
        compose.onNodeWithTag("home-track-carousel").performTouchInput { swipeRight() }
        compose.onNodeWithTag("home-product-list").performTouchInput { swipeUp() }
        compose.runOnIdle { assertEquals(1, next); assertEquals(1, previous) }
    }

    @Test fun avatarStaysInPlaceAndHomeProvidesTheOnlyTransport() {
        render()
        val profile = compose.onNodeWithContentDescription(context.getString(R.string.action_open_profile_settings))
        val top = profile.fetchSemanticsNode().boundsInRoot.top
        val hero = hasAnyAncestor(hasTestTag("home-playback-hero"))
        compose.onNode(hasContentDescription(context.getString(R.string.action_like)) and hero).performClick()
        compose.onNode(hasContentDescription(context.getString(R.string.action_liked)) and hero).performClick()
        compose.onNode(hasContentDescription(context.getString(R.string.action_pause)) and hero).performClick()
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasText("Recent 20"))
        compose.onNodeWithTag("persistent-mini-player").assertDoesNotExist()
        assertEquals(top, profile.fetchSemanticsNode().boundsInRoot.top)
        compose.runOnIdle { assertEquals(1, toggles) }
    }
}
