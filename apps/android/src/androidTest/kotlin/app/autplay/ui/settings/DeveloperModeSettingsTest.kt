package app.autplay.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.AutPlayTheme
import org.junit.Rule
import org.junit.Test

class DeveloperModeSettingsTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun developerControlsAppearOnlyAfterExplicitToggleAndDisappearAgain() {
        compose.setContent {
            var settings by remember { mutableStateOf(NonSecretSettings()) }
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    SettingsProductScreen(settings, { settings = it(settings) }, {}, {}, {}, {}, {}, onNavigate = {})
                }
            }
        }
        compose.onNodeWithText(context.getString(R.string.settings_diagnostics)).assertDoesNotExist()
        compose.onNodeWithText(context.getString(R.string.settings_about)).performScrollTo().performClick()
        val toggle = compose.onNodeWithContentDescription(context.getString(R.string.settings_developer_mode))
        toggle.performScrollTo().assertIsOff().performClick().assertIsOn()
        compose.onNodeWithText(context.getString(R.string.settings_diagnostics)).performScrollTo().assertExists()
        toggle.performScrollTo().performClick().assertIsOff()
        compose.onNodeWithText(context.getString(R.string.settings_diagnostics)).assertDoesNotExist()
    }
}
