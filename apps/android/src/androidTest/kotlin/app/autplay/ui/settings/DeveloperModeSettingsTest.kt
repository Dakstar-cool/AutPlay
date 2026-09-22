package app.autplay.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
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

    @Test fun developerModeCannotBeChangedLocallyAndRefreshesAdminPermission() {
        var refreshes = 0
        compose.setContent {
            var settings by remember { mutableStateOf(NonSecretSettings()) }
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    SettingsProductScreen(
                        settings,
                        { settings = it(settings) },
                        {}, {}, {}, {}, {},
                        onRefreshDeveloperMode = { refreshes++ },
                        onNavigate = {},
                    )
                }
            }
        }
        compose.onNodeWithText(context.getString(R.string.settings_diagnostics)).assertDoesNotExist()
        compose.onNodeWithText(context.getString(R.string.settings_about)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.settings_developer_mode)).performScrollTo().assertExists()
        compose.onNodeWithText(context.getString(R.string.settings_developer_refresh)).performScrollTo().performClick()
        compose.runOnIdle { assert(refreshes == 1) }
        compose.onNodeWithContentDescription(context.getString(R.string.settings_developer_mode)).assertDoesNotExist()
    }
}
