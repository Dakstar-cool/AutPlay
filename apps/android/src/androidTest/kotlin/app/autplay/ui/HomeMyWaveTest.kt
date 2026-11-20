package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.hasTestTag
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class HomeMyWaveTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun emptyLocalLibraryOffersImportBeforePlayback() {
        var imports = 0
        var starts = 0
        compose.setContent {
            AutPlayTheme {
                HomeProductScreen(
                    state = HomeScreenUiState(true, false, false, emptyList(), emptyList(), localLibraryEmpty = true),
                    contentPadding = PaddingValues(), onOpenListenTogether = {},
                    onRecommendationVisible = {}, onLike = {}, onDislike = {},
                    onAddLocal = { imports++ }, onStartMyWave = { starts++ },
                )
            }
        }
        compose.onNodeWithTag("home-add-first-track").assertIsDisplayed().performClick()
        compose.onNodeWithTag("home-my-wave").assertDoesNotExist()
        compose.runOnIdle { assertEquals(1, imports); assertEquals(0, starts) }
    }

    @Test fun randomActionCanBeRepeatedAndDoesNotOpenListenTogether() {
        var starts = 0
        var together = 0
        render(true, { starts++ }, { together++ })
        compose.onNodeWithTag("home-my-wave").assertIsDisplayed().assertIsEnabled().performClick().performClick()
        compose.runOnIdle { assertEquals(2, starts); assertEquals(0, together) }
    }

    @Test fun groupAuthorityDisablesPersonalRandomPlayback() {
        var starts = 0
        render(false, { starts++ }, {})
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-my-wave"))
        compose.onNodeWithTag("home-my-wave").assertIsNotEnabled().performClick()
        compose.onNodeWithTag("home-product-list").performScrollToNode(
            androidx.compose.ui.test.hasText(context.getString(R.string.home_my_wave_locked)),
        )
        compose.onNodeWithText(context.getString(R.string.home_my_wave_locked)).assertIsDisplayed()
        compose.runOnIdle { assertEquals(0, starts) }
    }

    @Test fun waveAndSavedQueueHaveDistinctActionsBeforeAndAfterPlaybackStarts() {
        var waveStarts = 0
        var resumes = 0
        var toggles = 0
        val player = mutableStateOf(PlaybackPresentationState())
        val saved = HomeContinueUiItem("saved", "Saved track", "Artist", "0:42")
        compose.setContent {
            AutPlayTheme {
                HomeProductScreen(
                    state = HomeScreenUiState(true, false, false, emptyList(), emptyList(), continueListening = saved),
                    contentPadding = PaddingValues(), playerState = player.value,
                    onOpenListenTogether = {}, onRecommendationVisible = {}, onLike = {}, onDislike = {},
                    onStartMyWave = { waveStarts++ }, onContinueListening = { resumes++ },
                    onTogglePlayPause = { toggles++ },
                )
            }
        }
        compose.onNodeWithTag("home-my-wave").assertIsDisplayed().performClick()
        compose.onNodeWithTag("home-track-carousel").assertDoesNotExist()
        compose.onNodeWithText(saved.title).assertIsDisplayed().performClick()
        compose.runOnIdle {
            assertEquals(1, waveStarts)
            assertEquals(1, resumes)
            player.value = PlaybackPresentationState(
                mediaId = "wave-entry", title = "Wave track", artist = "Wave artist",
                controls = PlaybackControlGate.Allowed, isPlaying = true,
            )
        }
        val hero = hasAnyAncestor(hasTestTag("home-playback-hero"))
        compose.onNode(hasText("Wave track") and hero).assertIsDisplayed()
        compose.onNodeWithContentDescription(context.getString(R.string.action_pause)).performScrollTo().performClick()
        compose.runOnIdle { player.value = player.value.copy(isPlaying = false) }
        compose.onNodeWithContentDescription(context.getString(R.string.action_play)).performScrollTo().assertIsDisplayed()
        compose.onNode(hasTestTag("home-my-wave") and hero).performScrollTo().performClick()
        compose.runOnIdle {
            assertEquals(2, waveStarts)
            assertEquals(1, resumes)
            assertEquals(1, toggles)
        }
    }

    private fun render(allowed: Boolean, start: () -> Unit, together: () -> Unit) {
        compose.setContent {
            AutPlayTheme {
                HomeProductScreen(
                    state = HomeScreenUiState(true, false, false, emptyList(), emptyList()),
                    contentPadding = PaddingValues(),
                    onOpenListenTogether = together,
                    onRecommendationVisible = {}, onLike = {}, onDislike = {},
                    onStartMyWave = start, myWaveAllowed = allowed,
                )
            }
        }
    }
}
