package app.autplay

import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.AppLanguage
import app.autplay.ui.AutPlayAccent
import app.autplay.ui.AutPlayThemeMode
import org.junit.Assert.assertEquals
import org.junit.Test

class AppearancePreferencesTest {
    @Test fun legacyAndUnknownPreferencesUseExplicitPresentationWithoutRewritingStoredValues() {
        for (language in listOf("SYSTEM", "FUTURE")) {
            val settings = NonSecretSettings(appLanguage = language, appearanceMode = "SYSTEM", accentPalette = "BLUE")
            assertEquals(AppLanguage.English, AppLanguage.fromStoredValue(settings.appLanguage))
            assertEquals(AutPlayThemeMode.Dark, appearanceFrom(settings).mode)
            assertEquals(AutPlayAccent.Coral, appearanceFrom(settings).accent)
            assertEquals(language, settings.appLanguage)
            assertEquals("SYSTEM", settings.appearanceMode)
            assertEquals("BLUE", settings.accentPalette)
        }
        assertEquals(AutPlayThemeMode.Light, appearanceFrom(NonSecretSettings(appearanceMode = "LIGHT")).mode)
        assertEquals(AppLanguage.Russian, AppLanguage.fromStoredValue("RU"))
        assertEquals(listOf(AppLanguage.Russian, AppLanguage.English), AppLanguage.entries)
    }
}
