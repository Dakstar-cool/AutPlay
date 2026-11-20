package app.autplay.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performSemanticsAction
import app.autplay.data.settings.PlaybackAudioSettings
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class PlaybackAudioSettingsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun gainModesPreampAndEqualizerControlsApplyAndReset() {
        var latest = PlaybackAudioSettings()
        compose.setContent {
            var settings by remember { mutableStateOf(latest) }
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    PlaybackAudioSettingsPanel(settings) { transform -> settings = transform(settings); latest = settings }
                }
            }
        }
        compose.onNodeWithTag("replay-gain-TRACK").performClick()
        compose.onNodeWithTag("replay-gain-preamp").performScrollTo()
            .performSemanticsAction(SemanticsActions.SetProgress) { it(-3f) }
        compose.runOnIdle { assertEquals("TRACK", latest.replayGainMode); assertEquals(-3, latest.preampDb) }
        compose.onNodeWithTag("replay-gain-ALBUM").performScrollTo().performClick()
        compose.onNodeWithTag("replay-gain-protection").performScrollTo().assertIsOn().performClick().assertIsOff()
        compose.onNodeWithTag("equalizer-enabled").performScrollTo().assertIsOff().performClick().assertIsOn()
        compose.onNodeWithTag("equalizer-band-2").performScrollTo()
            .performSemanticsAction(SemanticsActions.SetProgress) { it(-6f) }
        compose.runOnIdle { assertEquals(-6, latest.equalizerBandsDb[2]); assertEquals("ALBUM", latest.replayGainMode) }
        compose.onNodeWithTag("equalizer-reset").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(List(5) { 0 }, latest.equalizerBandsDb) }
        compose.onNodeWithTag("equalizer-enabled").performScrollTo().performClick().assertIsOff()
        compose.onNodeWithTag("replay-gain-OFF").performScrollTo().performClick()
        compose.runOnIdle { assertEquals("OFF", latest.replayGainMode); assertEquals(-3, latest.preampDb) }
    }
}
