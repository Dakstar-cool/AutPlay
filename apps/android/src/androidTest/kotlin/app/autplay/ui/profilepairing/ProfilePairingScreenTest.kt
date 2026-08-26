package app.autplay.ui.profilepairing

import androidx.compose.material3.MaterialTheme
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.semantics.SemanticsProperties
import app.autplay.R
import app.autplay.application.profilepairing.CapabilityState
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.profilepairing.TrustedServerIdentity
import app.autplay.application.profilepairing.AdmissionAccount
import app.autplay.application.profilepairing.AdmissionState
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.Assert.assertEquals
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicInteger

@RunWith(AndroidJUnit4::class)
class ProfilePairingScreenTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun localModeKeepsMusicAvailableAndOffersTrustedCheck() {
        render(ProfilePairingUiState(PairingState.NotConnected))

        compose.onNodeWithText(context.getString(R.string.profile_connection_local)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_check_server)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_local_body)).assertIsDisplayed()
    }

    @Test
    fun approvedAdmissionRequiresExplicitAccountConfirmation() {
        val confirmations = AtomicInteger(0)
        render(
            ProfilePairingUiState(
                pairing = connectedState(),
                admission = AdmissionUiState(
                    AdmissionState.Approved(
                        approvedCheckpoint(),
                        AdmissionAccount(UserId("66666666-6666-4666-8666-666666666666"), "Owner"),
                    ),
                ),
            ),
            ProfilePairingActions(admission = AdmissionActions(confirmAccount = confirmations::incrementAndGet)),
        )
        compose.onNodeWithText("Approved for Owner (66666666-6666-4666-8666-666666666666). Confirm this account before connecting.").assertIsDisplayed()
        compose.onNodeWithText("Confirm account").performClick()
        compose.runOnIdle { assertEquals(1, confirmations.get()) }
    }

    @Test
    fun trustStepShowsNormalizedOriginAndFullIdentityFingerprint() {
        val snapshot = connectedState().snapshot
        val formattedFingerprint = snapshot.expectedIdentityThumbprintSha256
            .chunked(4)
            .joinToString(" ")
        render(
            ProfilePairingUiState(
                pairing = PairingState.AwaitingTrust(snapshot),
                serverLabel = "Personal server",
            ),
        )

        compose.onNodeWithText(
            context.getString(R.string.profile_confirmed_origin, snapshot.apiOrigin),
        ).assertIsDisplayed()
        compose.onNodeWithText(
            context.getString(R.string.profile_identity_fingerprint, formattedFingerprint),
        ).assertIsDisplayed()
    }

    @Test
    fun enrollmentConfirmationShowsAccountDeviceAndLocalChoiceBeforeExchange() {
        val chosen = AtomicReference<ExistingLocalDataChoice?>(null)
        val snapshot = connectedState().snapshot.copy(expectedDeviceId = null)
        render(
            ProfilePairingUiState(
                pairing = PairingState.AwaitingConfirmation(snapshot),
                serverLabel = "Personal server",
                accountLabel = "Owner",
                deviceLabel = "This phone",
                localDataChoiceRequired = true,
            ),
            ProfilePairingActions(chooseLocalData = chosen::set),
        )

        compose.onNodeWithText(context.getString(R.string.profile_account_named, "Owner")).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_device_named, "This phone")).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_local_data_choice_title)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_keep_on_phone)).performClick()
        compose.runOnIdle { assertEquals(ExistingLocalDataChoice.KEEP_ON_PHONE, chosen.get()) }
    }

    @Test
    fun connectedLogoutAllRequiresConfirmationAndSuppressesDuplicateAction() {
        val performed = AtomicReference<ProfileRemoteAction?>(null)
        render(
            state = ProfilePairingUiState(
                pairing = connectedState(),
                accountLabel = "Music account",
                deviceLabel = "Phone",
                pendingSyncCount = 2,
            ),
            actions = ProfilePairingActions(performRemoteAction = performed::set),
        )

        compose.onNodeWithText(context.getString(R.string.profile_connection_sync_pending)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_logout_all)).performClick()
        compose.onNodeWithText(context.getString(R.string.profile_confirm_logout_all_title)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_confirm)).performClick()
        compose.runOnIdle { assert(performed.get() == ProfileRemoteAction.LOGOUT_ALL) }
    }

    @Test
    fun pendingLifecycleActionDisablesAllDestructiveControls() {
        render(ProfilePairingUiState(connectedState(), pendingRemoteAction = ProfileRemoteAction.LOGOUT_ALL))

        compose.onNodeWithText(context.getString(R.string.profile_logout)).assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.profile_logout_all)).assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.profile_revoke_device)).assertIsNotEnabled()
    }

    @Test
    fun cancellingLocalDataReviewDoesNotApplyAnyEvent() {
        val applied = AtomicReference<List<String>?>(null)
        val cancelled = AtomicInteger(0)
        render(
            ProfilePairingUiState(connectedState(), localDataReview = reviewState()),
            ProfilePairingActions(
                cancelLocalDataReview = { cancelled.incrementAndGet() },
                applyLocalDataSelection = applied::set,
            ),
        )

        compose.onNodeWithText(context.getString(R.string.profile_cancel)).performClick()
        compose.runOnIdle {
            assert(cancelled.get() == 1)
            assert(applied.get() == null)
        }
    }

    @Test
    fun localDataNeedsExplicitSelectionThenSecondApplyConfirmation() {
        val applied = AtomicReference<List<String>?>(null)
        render(
            ProfilePairingUiState(connectedState(), localDataReview = reviewState()),
            ProfilePairingActions(applyLocalDataSelection = applied::set),
        )

        compose.onNodeWithText("PLAY · 10 · ${context.getString(R.string.profile_local_data_not_selected)}").performClick()
        compose.onNodeWithText(context.getString(R.string.profile_apply_selected_changes)).performClick()
        compose.runOnIdle { assert(applied.get() == null) }
        compose.onNodeWithText(context.getString(R.string.profile_apply)).performClick()
        compose.runOnIdle { assert(applied.get() == listOf("change-1")) }
    }

    @Test
    fun localDataApplyIsSuppressedAfterFirstConfirmationWhilePending() {
        val calls = AtomicInteger(0)
        render(
            ProfilePairingUiState(connectedState(), localDataReview = reviewState()),
            ProfilePairingActions(applyLocalDataSelection = { calls.incrementAndGet() }),
        )

        compose.onNodeWithText("PLAY · 10 · ${context.getString(R.string.profile_local_data_not_selected)}").performClick()
        compose.onNodeWithText(context.getString(R.string.profile_apply_selected_changes)).performClick()
        compose.onNodeWithText(context.getString(R.string.profile_apply)).performClick()
        compose.runOnIdle { assert(calls.get() == 1) }
        compose.onNodeWithText(context.getString(R.string.profile_apply_selected_changes)).assertIsNotEnabled()
    }

    @Test
    fun authenticatedInvitationManagementUsesBoundedExpiryAndClearsOnCancel() {
        val createdExpiry = AtomicReference<Int?>(null)
        val cancelled = AtomicInteger(0)
        render(
            ProfilePairingUiState(
                pairing = connectedState(),
                invitationManagement = InvitationManagementUiState(
                    canCreate = true,
                    minExpiryMinutes = 10,
                    maxExpiryMinutes = 60,
                    createdSecret = "shown-once-secret",
                ),
            ),
            ProfilePairingActions(
                createInvitation = createdExpiry::set,
                cancelCreatedInvitation = { cancelled.incrementAndGet() },
            ),
        )

        compose.onNodeWithText(context.getString(R.string.profile_create_invitation)).performClick()
        compose.runOnIdle { assert(createdExpiry.get() == 10) }
        compose.onNodeWithContentDescription("One-time invitation secret").assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_cancel_invitation)).performClick()
        compose.runOnIdle { assert(cancelled.get() == 1) }
    }

    @Test
    fun invitationQrHasOnlyGenericAccessibilitySemantics() {
        val secret = "shown-once-secret"
        render(
            ProfilePairingUiState(
                pairing = connectedState(),
                invitationManagement = InvitationManagementUiState(
                    canCreate = true,
                    minExpiryMinutes = 10,
                    maxExpiryMinutes = 60,
                    createdSecret = secret,
                ),
            ),
        )

        val qr = compose.onNodeWithContentDescription(QR_CONTENT_DESCRIPTION).fetchSemanticsNode()
        assertEquals(listOf(QR_CONTENT_DESCRIPTION), qr.config[SemanticsProperties.ContentDescription])
    }

    private fun render(state: ProfilePairingUiState, actions: ProfilePairingActions = ProfilePairingActions()) {
        compose.setContent { MaterialTheme { ProfilePairingScreen(state, actions) } }
        // v2 uses StandardTestDispatcher; advance the initial composition before querying nodes.
        compose.mainClock.advanceTimeByFrame()
        compose.waitForIdle()
    }

    private fun connectedState(): PairingState.Connected {
        val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
        val user = UserId("22222222-2222-4222-8222-222222222222")
        val device = DeviceId("33333333-3333-4333-8333-333333333333")
        val identity = TrustedServerIdentity("44444444-4444-4444-8444-444444444444", 1, "a".repeat(64))
        return PairingState.Connected(
            PairingFlowSnapshot(
                generationId = "55555555-5555-4555-8555-555555555555",
                apiOrigin = "https://example.test",
                streamOrigin = "https://stream.example.test",
                serverProfileId = profile,
                expectedServerInstanceId = identity.serverInstanceId,
                expectedIdentityEpoch = identity.identityEpoch,
                expectedIdentityThumbprintSha256 = identity.identityThumbprintSha256,
                expectedUserId = user,
                expectedDeviceId = device,
                deviceKeyThumbprintSha256 = null,
                operationId = null,
                bindingCommitId = null,
            ),
            CapabilityState(identity, user, device, 1, 1, Long.MAX_VALUE, setOf("logout_all")),
        )
    }

    private fun reviewState() = LocalDataReviewUiState(
        items = listOf(PendingLocalDataUiSummary("change-1", "PLAY", 10L)),
    )

    private fun approvedCheckpoint() = app.autplay.application.profilepairing.AdmissionCheckpoint(
        requestId = "77777777-7777-4777-8777-777777777777",
        requestSha256 = "b".repeat(64),
        serverProfileId = ServerProfileId("11111111-1111-4111-8111-111111111111"),
        serverInstanceId = "44444444-4444-4444-8444-444444444444",
        identityEpoch = 1,
        identityThumbprintSha256 = "a".repeat(64),
        deviceKeyThumbprintSha256 = "c".repeat(64),
        generationId = "88888888-8888-4888-8888-888888888888",
        apiOrigin = "https://example.test",
        streamOrigin = "https://stream.example.test",
    )

}
