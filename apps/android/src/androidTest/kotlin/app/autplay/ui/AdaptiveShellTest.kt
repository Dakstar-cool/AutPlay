package app.autplay.ui

import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.TrackPreferenceActions
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Rule
import org.junit.Test

class AdaptiveShellTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun compactNavigationRoutesEveryPrimaryActionToContent() {
        composeRule.setContent {
            var selected by remember { mutableStateOf<UiDestination>(UiDestination.Home) }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = selected,
                    onDestinationSelected = { selected = it },
                ) { destination, _ ->
                    Text("route:${destination.route}")
                }
            }
        }

        composeRule.onNodeWithText("Search").performClick()
        composeRule.onNodeWithText("route:search").assertIsDisplayed()
        composeRule.onNodeWithText("Library").performClick()
        composeRule.onNodeWithText("route:library").assertIsDisplayed()
        composeRule.onNodeWithText("Listen together").performClick()
        composeRule.onNodeWithText("route:wave-rooms").assertIsDisplayed()
        composeRule.onNodeWithText("More").performClick()
        composeRule.onNodeWithText("route:settings").assertIsDisplayed()
    }

    @Test
    fun currentTrackFeedbackRemainsReachableAt320Dp() {
        composeRule.setContent {
            Box(Modifier.width(320.dp)) {
                TrackPreferenceActions(enabled = true, onLike = {}, onDislike = {})
            }
        }

        composeRule.onNodeWithText("Like current track").assertIsDisplayed()
        composeRule.onNodeWithText("Dislike current track").assertIsDisplayed()
    }
}
