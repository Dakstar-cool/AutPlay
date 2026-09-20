package app.autplay.ui.profilepairing

import android.content.res.Configuration
import android.graphics.Bitmap
import android.view.WindowManager
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
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.application.accountrecovery.AccountRecoveryPreview
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySourceState
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.selfpairing.SelfPairingIdentity
import java.io.File
import java.util.Locale
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Screens use synthetic account/origin fixtures; production Profile retains FLAG_SECURE. */
@RunWith(AndroidJUnit4::class)
class AccountRecoveryCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun newAccountRemainsIncompleteOfflineUntilDocumentIsSaved() {
        render(AccountRecoveryUiState(setupRequired = true), AccountRecoveryActions(), "ru")
        compose.onNodeWithText("Настройка аккаунта не завершена. Создайте и сохраните TXT восстановления, чтобы закончить. Локальная музыка остаётся доступной.").assertIsDisplayed()
        compose.onNodeWithText("Recovery TXT saved.").assertDoesNotExist()
        capture("account-recovery-setup-required-ru-dark.png")
    }

    @Test fun verifiedSaveShowsSavedStateAndKeepsOfflineExportAvailable() {
        render(AccountRecoveryUiState(source = AccountRecoverySourceState.Ready(true, true, 2, exportConfirmed = true)),
            AccountRecoveryActions(), "en")
        compose.onNodeWithText("Recovery TXT saved.").assertIsDisplayed()
        compose.onNodeWithText("Save recovery TXT").assertIsDisplayed()
        capture("account-recovery-saved-en-dark.png")
    }

    @Test fun offlineRussianExportIsVisibleAndRunsOnlyOnTap() {
        var exports = 0
        render(AccountRecoveryUiState(source = AccountRecoverySourceState.Ready(true, true, 2)),
            AccountRecoveryActions(export = { exports++ }), "ru")
        compose.onNodeWithText("Сохранить TXT восстановления").assertIsDisplayed()
        compose.onNodeWithText("Заменить код восстановления").assertDoesNotExist()
        compose.runOnIdle { assertEquals(0, exports) }
        capture("account-recovery-export-ru-dark.png")
        compose.onNodeWithText("Сохранить TXT восстановления").performClick()
        compose.runOnIdle { assertEquals(1, exports) }
    }

    @Test fun importedFileNamesServerBeforeCodeCanBeSent() {
        var confirmed = false
        val identity = SelfPairingIdentity(ID, 1, "a".repeat(64), "https://api.test.invalid", "https://stream.test.invalid")
        render(AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.ConfirmServer(identity, ID, "Test account")),
            AccountRecoveryActions(confirmServer = { confirmed = true }), "en")
        compose.onNodeWithText(identity.apiOrigin).assertIsDisplayed()
        compose.onNodeWithText(identity.streamOrigin).assertIsDisplayed()
        compose.onNodeWithText(identity.thumbprint).assertIsDisplayed()
        compose.runOnIdle { assertFalse(confirmed) }
        capture("account-recovery-server-en-dark.png")
        compose.onNodeWithText("Confirm server").performScrollTo().performClick()
        compose.runOnIdle { assertTrue(confirmed) }
    }

    @Test fun russianAccountConfirmationExplainsRevocationBeforeCommit() {
        var account: String? = null
        render(AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.ConfirmAccount(AccountRecoveryPreview(ID, "Моя музыка", "USER", 1))),
            AccountRecoveryActions(confirmAccount = { account = it }), "ru")
        compose.onNodeWithText("Восстановить аккаунт «Моя музыка»?").assertIsDisplayed()
        compose.onNodeWithText("Все прежние устройства, сессии браузера, passkeys и приглашения потеряют доступ. Данные аккаунта сохранятся.").assertIsDisplayed()
        compose.runOnIdle { assertNull(account) }
        capture("account-recovery-confirm-ru-dark.png")
        compose.onNodeWithText("Восстановить этот аккаунт").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(ID, account) }
    }

    @Test fun uncertainCommitCannotBeCancelledAndKeepsProfileSecure() {
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ProfilePairingScreen(ProfilePairingUiState(pairing = PairingState.NotConnected,
                        accountRecovery = AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.Blocked("unavailable", true, true))),
                        ProfilePairingActions())
                }
            }
        }
        settle()
        compose.runOnIdle { assertTrue(compose.activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0) }
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.profile_check_server)).assertDoesNotExist()
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.recovery_cancel)).assertDoesNotExist()
    }

    private fun render(state: AccountRecoveryUiState, actions: AccountRecoveryActions, locale: String) {
        val config = Configuration(compose.activity.resources.configuration).apply { setLocale(Locale.forLanguageTag(locale)) }
        val localized = compose.activity.createConfigurationContext(config)
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides config) {
                MaterialTheme(colorScheme = darkColorScheme()) {
                    Surface(Modifier.fillMaxSize()) {
                        Column(Modifier.verticalScroll(rememberScrollState())) { AccountRecoveryCard(state, actions) }
                    }
                }
            }
        }
        settle()
    }
    private fun settle() { compose.mainClock.advanceTimeByFrame(); compose.waitForIdle() }
    private fun capture(name: String) {
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        File(compose.activity.getExternalFilesDir(null), name).outputStream().use {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }
    private companion object { const val ID = "11111111-1111-4111-8111-111111111111" }
}
