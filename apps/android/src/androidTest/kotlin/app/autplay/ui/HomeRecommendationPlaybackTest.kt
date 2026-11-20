package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.click
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class HomeRecommendationPlaybackTest {
    @get:Rule val compose = createComposeRule()

    @Test fun playUsesTheVisibleRecommendationKeyAndWaitsForItsImpression() {
        val presented = mutableStateOf(false)
        val personalPlaybackAllowed = mutableStateOf(true)
        val visible = mutableSetOf<String>()
        val played = mutableListOf<String>()
        val feedback = mutableListOf<String>()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        var organicPlays = 0
        compose.setContent {
            AutPlayTheme {
                HomeProductScreen(
                    state = HomeScreenUiState(
                        false, false, false, emptyList(),
                        listOf(HomeRecommendationUiItem("presentation:item", "Recommended track", "Artist", "For you", presented.value)),
                    ),
                    contentPadding = PaddingValues(),
                    onOpenListenTogether = {},
                    onRecommendationVisible = { visible.add(it) },
                    onLike = { feedback.add("like:$it") }, onDislike = { feedback.add("dislike:$it") },
                    onPlayTrack = { organicPlays++ },
                    onPlayRecommendation = { played.add(it) },
                    recommendationPlaybackAllowed = personalPlaybackAllowed.value,
                )
            }
        }
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-recommendation"))
        compose.onNodeWithTag("home-recommendation").assertIsNotEnabled()
        compose.runOnIdle {
            assertTrue(visible.contains("presentation:item"))
            assertTrue(played.isEmpty())
            presented.value = true
        }
        compose.onNodeWithTag("home-recommendation").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertEquals(listOf("presentation:item"), played)
            assertEquals(0, organicPlays)
            personalPlaybackAllowed.value = false
        }
        compose.onNodeWithTag("home-recommendation").assertIsNotEnabled()
        compose.onNodeWithContentDescription(context.getString(R.string.action_like)).assertIsEnabled().performTouchInput { click() }
        compose.onNodeWithContentDescription(context.getString(R.string.action_dislike)).assertIsEnabled().performTouchInput { click() }
        compose.runOnIdle {
            assertEquals(listOf("like:presentation:item", "dislike:presentation:item"), feedback)
            assertEquals(listOf("presentation:item"), played)
        }
    }
}
