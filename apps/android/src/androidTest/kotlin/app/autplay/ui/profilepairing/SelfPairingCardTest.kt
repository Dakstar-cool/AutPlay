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
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingQr
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.application.selfpairing.SelfPairingSourceState
import app.autplay.application.selfpairing.SelfPairingStatus
import java.io.File
import java.time.Instant
import java.util.Locale
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Synthetic fixtures only. Capture the card directly; production Profile stays FLAG_SECURE. */
@RunWith(AndroidJUnit4::class)
class SelfPairingCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun darkRussianQrFitsCompactScreenAndKeepsSecretOutOfSemantics() {
        val secret = "A".repeat(43).toByteArray()
        val identity = SelfPairingIdentity(ID, 1, "a".repeat(64), "https://example.test", "https://example.test")
        val qr = SelfPairingQr(ID, identity, Instant.parse("2099-01-01T12:15:00Z"), secret)
        val payload = qr.encode().toString(Charsets.UTF_8)
        try {
            render(SelfPairingUiState(source = SelfPairingSourceState.Ready(status("OPEN"), payload)))
            val node = compose.onNodeWithContentDescription(QR_CONTENT_DESCRIPTION)
            node.performScrollTo().assertIsDisplayed()
            val semantics = node.fetchSemanticsNode()
            assertEquals(listOf(QR_CONTENT_DESCRIPTION), semantics.config[SemanticsProperties.ContentDescription])
            assertTrue(semantics.boundsInRoot.left >= 0f)
            assertTrue(semantics.boundsInRoot.right <= compose.onRoot().fetchSemanticsNode().boundsInRoot.right)
            compose.onNodeWithText(payload).assertDoesNotExist()
            capture("self-pairing-qr-ru-dark.png")
        } finally { qr.close() }
    }

    @Test
    fun sourceApprovalRequiresButtonAndDisplaysComparisonCode() {
        val decision = AtomicReference<Pair<String, String?>?>(null)
        render(SelfPairingUiState(source = SelfPairingSourceState.Ready(status("CLAIMED"), null)),
            SelfPairingActions(decide = { action, code -> decision.set(action to code) }))
        compose.onNodeWithText("1234 5678 9012").assertIsDisplayed()
        compose.runOnIdle { assertNull(decision.get()) }
        capture("self-pairing-approval-ru-dark.png")
        compose.onNodeWithText("Коды совпадают — разрешить подключение").performScrollTo().performClick()
        compose.runOnIdle { assertEquals("APPROVE" to "123456789012", decision.get()) }
    }

    @Test
    fun recipientConfirmationNamesAccountAndNeverSubmitsOnRender() {
        val account = AtomicReference<String?>(null)
        render(SelfPairingUiState(recipient = SelfPairingRecipientState.AwaitingAccountConfirmation(status("APPROVED"))),
            SelfPairingActions(confirmAccount = account::set))
        compose.onNodeWithText("Подключить этот телефон к аккаунту «Моя музыка»?").assertIsDisplayed()
        compose.runOnIdle { assertNull(account.get()) }
        capture("self-pairing-account-ru-dark.png")
        compose.onNodeWithText("Подключить к этому аккаунту").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(ID, account.get()) }
    }

    @Test
    fun pendingRecipientProtectsWindowAndHidesCompetingFirstBind() {
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ProfilePairingScreen(ProfilePairingUiState(
                        pairing = PairingState.NotConnected,
                        selfPairing = SelfPairingUiState(recipient = SelfPairingRecipientState.WaitingForApproval(status("CLAIMED"))),
                    ), ProfilePairingActions())
                }
            }
        }
        settle()
        compose.runOnIdle {
            assertTrue(compose.activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0)
        }
        compose.onNodeWithText(compose.activity.getString(app.autplay.R.string.profile_check_server)).assertDoesNotExist()
    }

    @Test
    fun completedRecipientCanCloseBeforeScanningAnotherPhone() {
        var dismissed = false
        render(SelfPairingUiState(recipient = SelfPairingRecipientState.Connected, canScan = true),
            SelfPairingActions(dismissRecipient = { dismissed = true }))
        compose.onNodeWithText("Закрыть").performClick()
        compose.runOnIdle { assertTrue(dismissed) }
    }

    private fun render(state: SelfPairingUiState, actions: SelfPairingActions = SelfPairingActions()) {
        val config = Configuration(compose.activity.resources.configuration).apply { setLocale(Locale.forLanguageTag("ru")) }
        val localized = compose.activity.createConfigurationContext(config)
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides config) {
                MaterialTheme(colorScheme = darkColorScheme()) {
                    Surface(Modifier.fillMaxSize()) {
                        Column(Modifier.verticalScroll(rememberScrollState())) { SelfPairingCard(state, actions) }
                    }
                }
            }
        }
        settle()
    }

    private fun settle() {
        compose.mainClock.advanceTimeByFrame()
        compose.waitForIdle()
    }

    private fun capture(name: String) {
        compose.onNodeWithText("Подключение телефонов").performScrollTo()
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        File(compose.activity.getExternalFilesDir(null), name).outputStream().use {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    private fun status(state: String) = SelfPairingStatus(
        ceremonyId = ID, state = state, revision = 2, expiresAt = Instant.parse("2099-01-01T12:15:00Z"),
        claimId = ID, claimHash = "b".repeat(64), deviceName = "Новый телефон", keyThumbprint = "c".repeat(64),
        comparisonCode = "123456789012", accountId = ID, accountLabel = "Моя музыка", approvalOperationId = ID, deviceId = null,
    )

    companion object { private const val ID = "11111111-1111-4111-8111-111111111111" }
}
