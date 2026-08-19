package app.autplay.ui

import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.TrackPreferenceActions
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import app.autplay.R

class AdaptiveShellTest {
    @get:Rule
    val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun compactNavigationRoutesEveryPrimaryActionToContent() {
        composeRule.setContent {
            var selected by remember { mutableStateOf<UiDestination>(UiDestination.Home) }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = selected,
                    onDestinationSelected = { selected = it },
                ) { destination, _, _ ->
                    Text("route:${destination.route}")
                }
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.nav_search)).performClick()
        composeRule.onNodeWithText("route:search").assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithText("route:library").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_open_settings)).performClick()
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

    @Test
    fun homeFailureIsTruthfulAndRetryable() {
        composeRule.setContent {
            var retried by remember { mutableStateOf(false) }
            AutPlayTheme {
                if (retried) {
                    Text("retry-requested")
                } else {
                    HomeProductScreen(
                        state = HomeScreenUiState(false, false, false, emptyList(), emptyList(), error = true),
                        contentPadding = PaddingValues(),
                        onOpenListenTogether = {},
                        onRecommendationVisible = {},
                        onLike = {},
                        onDislike = {},
                        onRetry = { retried = true },
                    )
                }
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.action_retry)).performClick()
        composeRule.onNodeWithText("retry-requested").assertIsDisplayed()
    }

    @Test
    fun searchFailureIsTruthfulAndRetryable() {
        composeRule.setContent {
            var retried by remember { mutableStateOf(false) }
            AutPlayTheme {
                if (retried) {
                    Text("search-retry-requested")
                } else {
                    SearchProductScreen(
                        state = SearchScreenUiState("query", emptyList(), searched = true, error = true),
                        contentPadding = PaddingValues(),
                        onQueryChange = {},
                        onSearch = {},
                        onPlay = {},
                        onRetry = { retried = true },
                    )
                }
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.action_retry)).performClick()
        composeRule.onNodeWithText("search-retry-requested").assertIsDisplayed()
    }

    @Test
    fun libraryFailureRemainsVisibleWithLocalContent() {
        composeRule.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = LibraryScreenUiState(
                        localMode = true,
                        tracks = listOf(CoreTrackUiItem("track", "Local fixture", "Artist")),
                        error = true,
                    ),
                    contentPadding = PaddingValues(),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                )
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithText("Local fixture").assertIsDisplayed()
    }
}
