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
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.application.accountrecovery.*
import app.autplay.application.profilepairing.PairingState
import java.io.File
import java.time.Instant
import java.util.Locale
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AccountDeletionCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun russianDeletionRequiresTypedAccountAndCodeBeforeRequest() {
        var requests = 0
        render(AccountDeletionUiState(AccountDeletionSourceState.Ready(AccountDeletionStatus(ID, 1, 1, true, null)), true),
            AccountDeletionActions(request = { account, code -> assertEquals(ID, account); assertEquals(AccountRecoveryProof.ALPHABET, code); requests++ }), "ru")
        capture("account-deletion-request-ru-dark.png")
        compose.onNodeWithText("Запросить удаление аккаунта").performClick()
        compose.runOnIdle { assertEquals(0, requests) }
        compose.onNodeWithText("Введите ID аккаунта для подтверждения").performTextInput(ID)
        compose.onNodeWithText("Код восстановления").performTextInput(AccountRecoveryProof.ALPHABET)
        // The dialog button becomes the last matching button; its title is not clickable.
        compose.onAllNodesWithText("Запросить удаление аккаунта").filter(hasClickAction()).onLast().performClick()
        compose.runOnIdle { assertEquals(1, requests) }
    }

    @Test fun lastOwnerShowsReasonWithoutDeletionAction() {
        render(AccountDeletionUiState(AccountDeletionSourceState.Ready(AccountDeletionStatus(ID, 1, 1, false, "last_owner_required")), true),
            AccountDeletionActions(), "en")
        compose.onNodeWithText("The last active owner cannot be deleted. Add another active owner first.").assertIsDisplayed()
        compose.onNodeWithText("Request account deletion").assertDoesNotExist()
        capture("account-deletion-last-owner-en-dark.png")
    }

    @Test fun cancellationNamesAccountAndDeadlineBeforeExplicitCommit() {
        var restored: String? = null
        val requested = Instant.parse("2026-09-18T12:00:00Z")
        val deadline = requested.plusSeconds(30 * 86400)
        val preview = AccountRecoveryPreview(ID, "Моя музыка", "", 1,
            AccountDeletionReceipt(ID, ID, "PENDING", 1, requested, deadline))
        render(AccountDeletionUiState(cancellation = AccountRecoveryUiState(
            recipient = AccountRecoveryRecipientState.ConfirmAccount(preview), purpose = AccountRestorationPurpose.DELETE_CANCEL)),
            AccountDeletionActions(cancellation = AccountRecoveryActions(confirmAccount = { restored = it })), "ru")
        compose.onNodeWithText("Отменить удаление аккаунта «Моя музыка»?").assertIsDisplayed()
        compose.onNodeWithText("Крайний срок отмены (UTC): $deadline").assertIsDisplayed()
        compose.runOnIdle { assertNull(restored) }
        capture("account-deletion-cancel-ru-dark.png")
        compose.onNodeWithText("Отменить удаление и подключиться").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(ID, restored) }
    }

    @Test fun uncertainDeletionHidesNewBindingAndHasNoDiscardAction() {
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ProfilePairingScreen(ProfilePairingUiState(pairing = PairingState.NotConnected,
                        accountDeletion = AccountDeletionUiState(AccountDeletionSourceState.Blocked("unknown", true))),
                        ProfilePairingActions())
                }
            }
        }
        settle()
        compose.runOnIdle { assertTrue(compose.activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0) }
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.profile_check_server)).assertDoesNotExist()
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.recovery_cancel)).assertDoesNotExist()
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.recovery_retry)).assertExists()
    }

    private fun render(state: AccountDeletionUiState, actions: AccountDeletionActions, locale: String) {
        val config = Configuration(compose.activity.resources.configuration).apply { setLocale(Locale.forLanguageTag(locale)) }
        val localized = compose.activity.createConfigurationContext(config)
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides config,
                LocalResources provides localized.resources) {
                MaterialTheme(colorScheme = darkColorScheme()) {
                    Surface(Modifier.fillMaxSize()) {
                        Column(Modifier.verticalScroll(rememberScrollState())) { AccountDeletionCard(state, actions) }
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
