package app.autplay.ui.profilepairing

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class AccountSessionScreenTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun signedOutScreenRequiresPasswordAndClearsSubmittedSecret() {
        var received: CharArray? = null
        var login: String? = null
        compose.setContent { AutPlayTheme {
            AccountSignInScreen("fixture_user") { value, password ->
                login = value; received = password; false
            }
        } }
        compose.onNode(hasClickAction() and hasText(context.getString(R.string.account_sign_in))).assertIsNotEnabled()
        compose.onNode(hasSetTextAction()).performTextInput("test123!")
        compose.onNode(hasClickAction() and hasText(context.getString(R.string.account_sign_in))).performClick()
        compose.waitForIdle()
        compose.runOnIdle {
            assertEquals("fixture_user", login)
            assertTrue(requireNotNull(received).all { it == '\u0000' })
        }
        compose.onNodeWithText(context.getString(R.string.account_session_failed)).assertIsDisplayed()
    }
}
