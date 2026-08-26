package app.autplay.ui.profilepairing

import app.autplay.application.profilepairing.AdmissionCheckpoint
import app.autplay.application.profilepairing.AdmissionState
import app.autplay.application.profilepairing.PairingState
import app.autplay.domain.ServerProfileId
import org.junit.Assert.assertTrue
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
}
