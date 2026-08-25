package app.autplay.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import org.junit.Rule
import org.junit.Test

class WelcomeOnboardingScreenTest {
    @get:Rule
    val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun educationEndsWithOptionalServerChoice() {
        var completedDestination: UiDestination? = null
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(
                    onComplete = { destination ->
                        completedDestination = destination
                        true
                    },
                )
            }
        }

        repeat(2) {
            composeRule.onNodeWithText(context.getString(R.string.onboarding_next)).performClick()
        }
        composeRule.onNodeWithText(context.getString(R.string.onboarding_server_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.onboarding_connect_server)).performClick()
        composeRule.waitForIdle()
        check(completedDestination == UiDestination.Profile)
    }
}
