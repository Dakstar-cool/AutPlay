package app.autplay

import app.autplay.application.profilepairing.AdmissionState
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.ui.profilepairing.AdmissionUiState
import app.autplay.ui.profilepairing.ProfilePairingUiState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UserProfilePresentationTest {
    @Test fun failedInvitationKeepsItsRecoveryVisibleAfterImporting() {
        val initial = ProfilePairingUiState(PairingState.NotConnected, publicAccountRegistration = PublicAccountRegistrationState.Importing)
        assertTrue(profileRequiresConnectionAttention(initial))
        assertTrue(profileRequiresConnectionAttention(initial.copy(publicAccountRegistration = PublicAccountRegistrationState.Blocked("INVALID_INVITATION", firstBindReserved = false))))
    }

    @Test fun idleConnectionControlsStayCollapsedForOrdinaryProfileUse() {
        val initial = ProfilePairingUiState(PairingState.NotConnected, admission = AdmissionUiState(AdmissionState.RequestReady))
        assertFalse(profileRequiresConnectionAttention(initial))
        assertFalse(profileRequiresConnectionAttention(initial.copy(admission = AdmissionUiState(AdmissionState.Connected))))
    }
}
