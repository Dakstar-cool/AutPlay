package app.autplay.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.UiDestination
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class SettingsNavigationTest {
    @get:Rule val compose = createComposeRule()

    @Test fun profileAndSixSectionsNavigateAndLocalAudioActionIsDirect() {
        val page = mutableStateOf<UiDestination>(UiDestination.Settings)
        var audioRequests = 0
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    SettingsProductScreen(NonSecretSettings(), {}, {}, {}, {}, {}, {},
                        onNavigate = { page.value = it }, page = page.value, onChooseAudio = { audioRequests++ })
                }
            }
        }
        compose.onNodeWithTag("settings-row-settings-downloads").assertDoesNotExist()
        compose.onNodeWithTag("settings-row-profile").performClick()
        compose.runOnIdle { assertEquals(UiDestination.Profile, page.value) }
        assertEquals(6, UiDestination.settingsNavigation.size)
        UiDestination.settingsNavigation.forEach { destination ->
            compose.runOnIdle { page.value = UiDestination.Settings }
            compose.onNodeWithTag("settings-row-${destination.route}").performScrollTo().performClick()
            compose.runOnIdle { assertEquals(destination, page.value) }
        }
        compose.runOnIdle { page.value = UiDestination.SettingsMusic }
        compose.onNodeWithTag("settings-add-audio").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(1, audioRequests) }
        compose.runOnIdle { page.value = UiDestination.SettingsAbout }
        compose.onNodeWithTag("settings-row-settings-diagnostics").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(UiDestination.SettingsDiagnostics, page.value) }
    }
}
