package app.autplay.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
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
        var selectedPublicId: String? = null
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(
                    onComplete = { destination, publicId ->
                        completedDestination = destination
                        selectedPublicId = publicId
                        true
                    },
                )
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.onboarding_next)).assertIsNotEnabled()
        composeRule.onNodeWithText(context.getString(R.string.ui_friends_refresh_public_id_label))
            .performScrollTo().performTextInput("@PTICA_1")
        repeat(2) {
            composeRule.onNodeWithText(context.getString(R.string.onboarding_next)).performScrollTo().performClick()
        }
        composeRule.onNodeWithText(context.getString(R.string.onboarding_server_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.onboarding_connect_server)).performScrollTo().performClick()
        composeRule.waitForIdle()
        check(completedDestination == UiDestination.Profile)
        check(selectedPublicId == "ptica_1")
    }

    @Test
    fun mandatoryNameStillCompletesToLocalPlaybackWithoutServerReservation() {
        var completed: Pair<UiDestination, String>? = null
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(onComplete = { destination, publicId ->
                    completed = destination to publicId
                    true
                })
            }
        }
        composeRule.onNodeWithText(context.getString(R.string.ui_friends_refresh_public_id_label))
            .performScrollTo().performTextInput("local_user")
        repeat(2) {
            composeRule.onNodeWithText(context.getString(R.string.onboarding_next)).performScrollTo().performClick()
        }
        composeRule.onNodeWithText(context.getString(R.string.onboarding_continue_local)).performScrollTo().performClick()
        composeRule.waitForIdle()
        check(completed == (UiDestination.Home to "local_user"))
    }
}
