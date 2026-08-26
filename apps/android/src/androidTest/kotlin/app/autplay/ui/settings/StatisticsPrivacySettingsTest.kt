package app.autplay.ui.settings

import androidx.activity.ComponentActivity
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.social.ProfileStatisticsSettingsState
import app.autplay.data.settings.NonSecretSettings
import app.autplay.domain.ServerProfileId
import app.autplay.ui.AppLanguage
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class StatisticsPrivacySettingsTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun failedOffRemainsVisuallyOnWithAnExplicitError() {
        val changed = AtomicReference<Boolean?>()
        render(
            settings = NonSecretSettings(activeServerProfileId = PROFILE),
            state = ProfileStatisticsSettingsState.Confirmed(enabled = true, revision = 4),
            errorCode = "server_unavailable",
            onChange = changed::set,
        )

        compose.onNodeWithContentDescription(context.getString(R.string.settings_statistics_share))
            .performScrollTo()
            .assertIsOn()
            .performClick()
        compose.onNodeWithText(context.getString(R.string.settings_statistics_update_failed))
            .performScrollTo()
            .assertIsDisplayed()
        compose.runOnIdle { assertEquals(false, changed.get()) }
    }

    @Test
    fun standaloneSharingIsUnavailableButExplainsThatOwnerStatisticsStayOffline() {
        render(
            settings = NonSecretSettings(),
            state = ProfileStatisticsSettingsState.Unavailable,
        )

        compose.onNodeWithContentDescription(context.getString(R.string.settings_statistics_share))
            .performScrollTo()
            .assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.settings_statistics_unavailable_local))
            .performScrollTo()
            .assertIsDisplayed()
    }

    private fun render(
        settings: NonSecretSettings,
        state: ProfileStatisticsSettingsState,
        errorCode: String? = null,
        onChange: (Boolean) -> Unit = {},
    ) {
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    SettingsProductScreen(
                        settings = settings,
                        onUpdate = {},
                        onAppLanguageChange = { _: AppLanguage -> },
                        onChooseLibraryRoot = {},
                        onRescanLibraryRoot = {},
                        onExportSettings = {},
                        onImportSettings = {},
                        statisticsSettings = state,
                        statisticsSettingsErrorCode = errorCode,
                        onStatisticsVisibilityChange = onChange,
                        onNavigate = {},
                    )
                }
            }
        }
    }

    private companion object {
        val PROFILE = ServerProfileId("11111111-1111-4111-8111-111111111111")
    }
}
