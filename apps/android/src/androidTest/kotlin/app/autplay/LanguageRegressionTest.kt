package app.autplay

import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.*
import app.autplay.ui.AppLanguage
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Runs only on the disposable test emulator; exercises the complete Activity owner chain. */
class LanguageRegressionTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private var activity: ActivityScenario<MainActivity>? = null
    @Before fun prepare() = runBlocking {
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(NonSecretSettings(onboardingRevision = CURRENT_ONBOARDING_REVISION))
    }
    @After fun finish(): Unit = runBlocking {
        activity?.close()
        InstrumentationRegistry.getInstrumentation().runOnMainSync { synchronizeFrameworkAppLanguage(context, AppLanguage.System) }
        applicationNonSecretSettingsStore(context).update(NonSecretSettings(onboardingRevision = CURRENT_ONBOARDING_REVISION))
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        Unit
    }
    @Test fun languageSwitchRecreationAndColdActivityLaunchRetainAllOwners() {
        activity = ActivityScenario.launch(MainActivity::class.java)
        for (language in listOf(AppLanguage.English, AppLanguage.Russian, AppLanguage.System)) {
            runBlocking {
                applicationNonSecretSettingsStore(context).update(NonSecretSettings(appLanguage = language.storedValue, onboardingRevision = CURRENT_ONBOARDING_REVISION))
            }
            InstrumentationRegistry.getInstrumentation().runOnMainSync { synchronizeFrameworkAppLanguage(context, language) }
            awaitNavigation(language)
            if (android.os.Build.VERSION.SDK_INT >= 33 && language == AppLanguage.System) {
                org.junit.Assert.assertTrue(context.getSystemService(android.app.LocaleManager::class.java).applicationLocales.isEmpty)
            }
            activity!!.recreate()
            awaitNavigation(language)
            activity!!.close()
            activity = ActivityScenario.launch(MainActivity::class.java)
            awaitNavigation(language)
        }
    }
    private fun awaitNavigation(language: AppLanguage) {
        val text = localizedAppContext(context, language).getString(R.string.nav_library)
        compose.waitUntil(15_000) { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
        compose.waitForIdle()
    }
}
