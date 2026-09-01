package app.autplay.ui.profilepairing

import app.autplay.application.profilepairing.AdmissionCheckpoint
import app.autplay.application.profilepairing.AdmissionState
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.AccountInvitationParser
import app.autplay.application.publicaccess.OwnerProvisioningUiState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.domain.ServerProfileId
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Test

class ProfilePairingSecureWindowTest {
    @Test
    fun recoveredAdmissionComparisonRequiresSecureWindow() {
        val checkpoint = AdmissionCheckpoint(
            requestId = "77777777-7777-4777-8777-777777777777",
            requestSha256 = "a".repeat(64),
            serverProfileId = ServerProfileId("22222222-2222-4222-8222-222222222222"),
            serverInstanceId = "33333333-3333-4333-8333-333333333333",
            identityEpoch = 1,
            identityThumbprintSha256 = "a".repeat(64),
            deviceKeyThumbprintSha256 = "b".repeat(64),
            generationId = "88888888-8888-4888-8888-888888888888",
            apiOrigin = "https://example.test",
            streamOrigin = "https://stream.example.test",
        )
        val state = ProfilePairingUiState(
            pairing = PairingState.NotConnected,
            admission = AdmissionUiState(
                AdmissionState.AwaitingComparison(checkpoint, "review-locator", "000000000001"),
            ),
        )

        assertTrue(requiresSecureProfileWindow(state))
    }

    @Test
    fun publicAccountSecretEntryAndOwnerShownOnceDocumentRequireSecureWindow() {
        assertTrue(
            requiresSecureProfileWindow(
                ProfilePairingUiState(
                    pairing = PairingState.NotConnected,
                    publicAccountRegistration = PublicAccountRegistrationState.Importing,
                ),
            ),
        )
        assertTrue(
            requiresSecureProfileWindow(
                ProfilePairingUiState(pairing = PairingState.NotConnected),
                hasTypedPublicInvitation = true,
            ),
        )
        val invitation = AccountInvitationParser.parseQr(document())
        try {
            assertTrue(
                requiresSecureProfileWindow(
                    ProfilePairingUiState(
                        pairing = PairingState.NotConnected,
                        ownerProvisioning = OwnerProvisioningUiState(
                            shownInvitation = invitation,
                            available = true,
                        ),
                    ),
                ),
            )
        } finally {
            invitation.close()
        }
    }

    @Test
    fun publicAccountTrustPresentationContainsEverySecurityBinding() {
        val invitation = AccountInvitationParser.parseQr(document())
        try {
            val presentation = publicAccountTrustPresentation(
                PublicAccountRegistrationState.AwaitingTrust(
                    invitation,
                    "AutPlay home",
                    invitation.identityThumbprintSha256,
                ),
            )
            assertEquals("AutPlay home", presentation.serverLabel)
            assertEquals(invitation.identityThumbprintSha256, presentation.fingerprint)
            assertEquals("https://api.example", presentation.apiOrigin)
            assertEquals("https://stream.example", presentation.streamOrigin)
            assertEquals("Friend", presentation.accountDisplayName)
            assertEquals("USER", presentation.accountRole)
            assertEquals("2999-01-01T00:00:00Z", presentation.expiresAt)
        } finally {
            invitation.close()
        }
    }

    @Test
    fun publicCeremonyHidesOrdinaryFirstBindUntilItIsSafelyReleased() {
        val invitation = AccountInvitationParser.parseQr(document())
        try {
            assertTrue(
                publicRegistrationBlocksOrdinaryFirstBind(
                    PublicAccountRegistrationState.AwaitingConfirmation(invitation),
                ),
            )
            assertTrue(
                publicRegistrationBlocksOrdinaryFirstBind(
                    PublicAccountRegistrationState.Blocked(
                        "ACCOUNT_REGISTRATION_EXACT_REPLAY_REQUIRED",
                        firstBindReserved = true,
                    ),
                ),
            )
            assertTrue(
                !publicRegistrationBlocksOrdinaryFirstBind(
                    PublicAccountRegistrationState.Blocked("ACCOUNT_INVITATION_INVALID"),
                ),
            )
        } finally {
            invitation.close()
        }
    }

    private fun document() =
        """{"contract_version":"v1","schema_version":1,"invitation_id":"10000000-0000-4000-8000-000000000001","server_instance_id":"10000000-0000-4000-8000-000000000002","identity_epoch":1,"identity_thumbprint_sha256":"${"a".repeat(64)}","api_origin":"https://api.example","stream_origin":"https://stream.example","account_display_name":"Friend","account_role":"USER","issued_at":"2026-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","invitation_secret":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY","secret_handling":"DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"}"""
}
