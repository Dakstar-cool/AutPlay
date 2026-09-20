package app.autplay.ui.settings

import android.content.res.Configuration
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.TrainingConsentUi
import app.autplay.application.trainingconsent.TrainingConsentPolicy
import app.autplay.application.trainingconsent.TrainingConsentState
import java.io.File
import java.util.Locale
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class TrainingConsentCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun unknownPolicyOffersEqualRussianChoicesWithoutOptimisticConfirmation() {
        val choices = mutableListOf<String>()
        render(TrainingConsentUi(TrainingConsentState(TrainingConsentPolicy(ID, "UNKNOWN", 0)), true,
            choose = { choices += it }), "ru")
        compose.onNodeWithText("Участие выключено. Решение ещё не сохранено.").assertIsDisplayed()
        val allow = compose.onNodeWithText("Разрешить")
        val refuse = compose.onNodeWithText("Отказаться")
        assertEquals(allow.fetchSemanticsNode().boundsInRoot.width, refuse.fetchSemanticsNode().boundsInRoot.width, 1f)
        capture("training-consent-unknown-ru-dark.png")
        allow.performClick()
        refuse.performClick()
        compose.runOnIdle { assertEquals(listOf("GRANTED", "DENIED"), choices) }
        compose.onNodeWithText("Участие разрешено для этого аккаунта.").assertDoesNotExist()
    }

    @Test fun confirmedGrantShowsExplicitWithdrawalAndModelWeightsLimit() {
        var decision: String? = null
        render(TrainingConsentUi(TrainingConsentState(TrainingConsentPolicy(ID, "GRANTED", 1)), true,
            choose = { decision = it }), "en")
        compose.onNodeWithText("Participation is allowed for this account.").assertIsDisplayed()
        compose.onNodeWithText("Allow").assertIsNotEnabled()
        compose.onNodeWithText("Withdrawing stops new use and removes related training examples and unfinished work. Already trained shared models continue to work until their normal replacement; withdrawal does not remove past contributions from their weights.").assertIsDisplayed()
        capture("training-consent-granted-en-dark.png")
        compose.onNodeWithText("Withdraw consent").performClick()
        compose.runOnIdle { assertEquals("WITHDRAWN", decision) }
    }

    @Test fun uncertainOfflineMutationAllowsOnlyRetryAndExplicitWithdrawal() {
        val choices = mutableListOf<String>()
        var retries = 0
        render(TrainingConsentUi(TrainingConsentState(TrainingConsentPolicy(ID, "UNKNOWN", 0),
            pending = true, error = true), true, choose = { choices += it }, retry = { retries++ }), "ru")
        compose.onNodeWithText("Разрешить").assertDoesNotExist()
        compose.onNodeWithText("Отказаться").assertDoesNotExist()
        compose.onNodeWithText("Участие разрешено для этого аккаунта.").assertDoesNotExist()
        capture("training-consent-pending-ru-dark.png")
        compose.onNodeWithText("Обновить / повторить").performScrollTo().performClick()
        compose.onNodeWithText("Отозвать согласие").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(1, retries); assertEquals(listOf("WITHDRAWN"), choices) }
    }

    private fun render(ui: TrainingConsentUi, locale: String) {
        val config = Configuration(compose.activity.resources.configuration).apply { setLocale(Locale.forLanguageTag(locale)) }
        val localized = compose.activity.createConfigurationContext(config)
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides config,
                LocalResources provides localized.resources) {
                MaterialTheme(colorScheme = darkColorScheme()) {
                    Surface(Modifier.fillMaxSize()) {
                        Column(Modifier.verticalScroll(rememberScrollState())) { TrainingConsentCard(ui) }
                    }
                }
            }
        }
        compose.mainClock.advanceTimeByFrame()
        compose.waitForIdle()
    }
    private fun capture(name: String) {
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        File(compose.activity.getExternalFilesDir(null), name).outputStream().use {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }
    private companion object { const val ID = "11111111-1111-4111-8111-111111111111" }
}
