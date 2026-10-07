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
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PlaybackSettingsTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun playbackSectionSwitchChangesPreferenceWithoutResettingOtherSettings() {
        var latest = NonSecretSettings(downloadOnMeteredNetwork = true)
        compose.setContent {
            var settings by remember { mutableStateOf(latest) }
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    SettingsProductScreen(settings, { transform ->
                        settings = transform(settings)
                        latest = settings
                    }, {}, {}, {}, {}, {}, onNavigate = {})
                }
            }
        }
        compose.onNodeWithText(context.getString(R.string.settings_playback)).performScrollTo().performClick()
        val toggle = compose.onNodeWithContentDescription(context.getString(R.string.settings_smooth_track_transitions))
        toggle.performScrollTo().assertIsOff().performClick().assertIsOn()
        compose.runOnIdle {
            assertTrue(latest.smoothTrackTransitions)
            assertTrue(latest.downloadOnMeteredNetwork)
        }
        toggle.performClick().assertIsOff()
    }
}
