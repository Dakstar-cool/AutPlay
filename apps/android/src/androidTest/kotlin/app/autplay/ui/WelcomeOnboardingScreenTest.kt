package app.autplay.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import kotlinx.coroutines.CompletableDeferred
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class WelcomeOnboardingScreenTest {
    @get:Rule val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val phrase = "first-users-test-phrase"

    @Test
    fun createsAccountOnceAndOpensHomeWithoutIntermediateSteps() {
        val finish = CompletableDeferred<Unit>()
        var creates = 0
        var completed: Pair<UiDestination, String>? = null
        var material: CharArray? = null
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(
                    createAccount = { id, secret ->
                        creates++
                        assertEquals("local_listener", id)
                        assertEquals(phrase, secret.concatToString())
                        material = secret
                        finish.await()
                        true
                    },
                    onComplete = { destination, id -> completed = destination to id; true },
                )
            }
        }
        fillValidForm()
        submit().performScrollTo().performClick()
        submit().assertIsNotEnabled().performClick()
        composeRule.runOnIdle { assertEquals(1, creates); finish.complete(Unit) }
        composeRule.waitUntil(5_000) { completed != null }
        composeRule.runOnIdle {
            assertEquals(UiDestination.Home to "local_listener", completed)
            assertTrue(requireNotNull(material).all { it == '\u0000' })
        }
    }

    @Test
    fun invalidPasswordAndMismatchExplainWhySubmissionIsBlocked() {
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(createAccount = { _, _ -> true }, onComplete = { _, _ -> true })
            }
        }
        field(R.string.ui_friends_refresh_public_id_label).performTextInput("local_listener")
        field(R.string.portable_account_password).performScrollTo().performTextInput("short")
        field(R.string.portable_account_password_repeat).performScrollTo().performTextInput("different")
        composeRule.onNodeWithText(context.getString(R.string.onboarding_password_invalid))
            .performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.onboarding_password_mismatch))
            .performScrollTo().assertIsDisplayed()
        submit().assertIsNotEnabled()
        field(R.string.portable_account_password).performScrollTo().performTextReplacement(phrase)
        field(R.string.portable_account_password_repeat).performScrollTo().performTextReplacement(phrase)
        submit().assertIsEnabled()
    }

    @Test
    fun failedCreationKeepsTheFormAndAllowsRetry() {
        var creates = 0
        var completions = 0
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(
                    createAccount = { _, _ -> ++creates > 1 },
                    onComplete = { _, _ -> completions++; true },
                )
            }
        }
        fillValidForm()
        submit().performScrollTo().performClick()
        composeRule.onNodeWithText(context.getString(R.string.onboarding_save_failed))
            .performScrollTo().assertIsDisplayed()
        field(R.string.ui_friends_refresh_public_id_label).assertTextContains("@Local_Listener")
        submit().performScrollTo().assertIsEnabled().performClick()
        composeRule.waitUntil(5_000) { completions == 1 }
        composeRule.runOnIdle { assertEquals(2, creates) }
    }

    @Test
    fun existingAccountUsesItsIdWithoutCreatingAnotherAccount() {
        var chosenId: String? = null
        composeRule.setContent {
            AutPlayTheme {
                WelcomeOnboardingScreen(
                    createAccount = { _, _ -> error("Must not create an existing account") },
                    existingAccount = { chosenId = it; true },
                    onComplete = { _, _ -> error("Existing-account route owns navigation") },
                )
            }
        }
        field(R.string.ui_friends_refresh_public_id_label).performTextInput("@Local_Listener")
        submit().assertIsNotEnabled()
        composeRule.onNodeWithText(context.getString(R.string.portable_account_existing))
            .performScrollTo().performClick()
        composeRule.runOnIdle { assertEquals("local_listener", chosenId) }
    }

    private fun fillValidForm() {
        field(R.string.ui_friends_refresh_public_id_label).performTextInput("@Local_Listener")
        field(R.string.portable_account_password).performScrollTo().performTextInput(phrase)
        field(R.string.portable_account_password_repeat).performScrollTo().performTextInput(phrase)
    }

    private fun field(label: Int) = composeRule.onNodeWithText(context.getString(label))
    private fun submit() = field(R.string.onboarding_continue_local)
}
