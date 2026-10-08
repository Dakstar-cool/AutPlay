package app.autplay.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AppLanguageProvider
import app.autplay.R
import app.autplay.appearanceFrom
import app.autplay.data.settings.NonSecretSettings
import app.autplay.localizedAppContext
import app.autplay.ui.AppLanguage
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class AppearanceSettingsTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun themeAndLanguageSwitchPreservePreferencesAndExpandedSections() {
        var latest = NonSecretSettings(appLanguage = "EN", accentPalette = "BLUE", downloadOnMeteredNetwork = true)
        compose.setContent {
            var settings by remember { mutableStateOf(latest) }
            AppLanguageProvider(settings.appLanguage) {
                AutPlayTheme(appearanceFrom(settings)) {
                    Column(Modifier.verticalScroll(rememberScrollState())) {
                        SettingsProductScreen(
                            settings = settings,
                            onUpdate = { settings = it(settings); latest = settings },
                            onAppLanguageChange = { settings = settings.copy(appLanguage = it.storedValue); latest = settings },
                            onChooseLibraryRoot = {}, onRescanLibraryRoot = {},
                            onExportSettings = {}, onImportSettings = {}, onNavigate = {},
                        )
                    }
                }
            }
        }
        fun label(language: AppLanguage, resource: Int) = localizedAppContext(context, language).getString(resource)
        compose.onNodeWithText(label(AppLanguage.English, R.string.settings_playback)).performScrollTo().performClick()
        compose.onNodeWithTag("settings-theme-toggle").performScrollTo().performClick()
        compose.runOnIdle { assertEquals("LIGHT", latest.appearanceMode) }
        compose.onNodeWithTag("settings-language-RU").performScrollTo().performClick()
        compose.onNodeWithTag("settings-language-RU").assertIsSelected()
        compose.onNodeWithText(label(AppLanguage.Russian, R.string.settings_appearance)).assertIsDisplayed()
        compose.onNodeWithText(label(AppLanguage.English, R.string.settings_appearance)).assertDoesNotExist()
        compose.onNodeWithText(label(AppLanguage.Russian, R.string.settings_smooth_track_transitions)).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(label(AppLanguage.English, R.string.settings_smooth_track_transitions)).assertDoesNotExist()
        compose.onNodeWithTag("settings-theme-toggle").performScrollTo().performClick()
        compose.onNodeWithTag("settings-language-EN").performScrollTo().performClick()
        compose.onNodeWithTag("settings-language-EN").assertIsSelected()
        compose.onNodeWithText(label(AppLanguage.Russian, R.string.settings_appearance)).assertDoesNotExist()
        compose.onNodeWithText("System language").assertDoesNotExist()
        compose.onNodeWithText("Use device theme").assertDoesNotExist()
        compose.onNodeWithText("Blue accent").assertDoesNotExist()
        compose.runOnIdle {
            assertEquals("DARK", latest.appearanceMode)
            assertEquals("EN", latest.appLanguage)
            assertEquals("BLUE", latest.accentPalette)
            assertTrue(latest.downloadOnMeteredNetwork)
        }
    }
}
