package app.autplay.ui.profilepairing

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AppLanguageProvider
import app.autplay.R
import app.autplay.application.profilepairing.AdmissionState
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.localizedAppContext
import app.autplay.ui.AppLanguage
import app.autplay.ui.AutPlayTheme
import org.junit.Rule
import org.junit.Test

class ProfileLanguageTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun registrationAndAdmissionSwitchTogetherWithoutStaleCopy() {
        val language = mutableStateOf(AppLanguage.English)
        compose.setContent {
            AppLanguageProvider(language.value.storedValue) {
                AutPlayTheme {
                    Column(Modifier.verticalScroll(rememberScrollState())) {
                        ProfilePairingScreen(
                            state = ProfilePairingUiState(
                                PairingState.NotConnected,
                                publicAccountRegistration = PublicAccountRegistrationState.Idle,
                                admission = AdmissionUiState(AdmissionState.Cancelled),
                            ),
                            actions = ProfilePairingActions(),
                        )
                    }
                }
            }
        }
        val resources = listOf(R.string.profile_join_server, R.string.profile_account_invite_hint,
            R.string.profile_account_check_invite, R.string.admission_cancelled, R.string.admission_retry)
        for (selected in listOf(AppLanguage.Russian, AppLanguage.English)) {
            compose.runOnIdle { language.value = selected }
            val opposite = if (selected == AppLanguage.Russian) AppLanguage.English else AppLanguage.Russian
            for (id in resources) {
                compose.onNodeWithText(localizedAppContext(context, selected).getString(id)).performScrollTo().assertIsDisplayed()
                compose.onNodeWithText(localizedAppContext(context, opposite).getString(id)).assertDoesNotExist()
            }
        }
    }
}
