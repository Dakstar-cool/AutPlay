package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.hasTestTag
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class HomeMyWaveTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun randomActionCanBeRepeatedAndDoesNotOpenListenTogether() {
        var starts = 0
        var together = 0
        render(true, { starts++ }, { together++ })
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-my-wave"))
        compose.onNodeWithTag("home-my-wave").assertIsEnabled().performClick().performClick()
        compose.onNodeWithTag("home-product-list").performScrollToNode(
            androidx.compose.ui.test.hasText(context.getString(R.string.home_my_wave_random)),
        )
        compose.onNodeWithText(context.getString(R.string.home_my_wave_random)).assertIsDisplayed()
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
