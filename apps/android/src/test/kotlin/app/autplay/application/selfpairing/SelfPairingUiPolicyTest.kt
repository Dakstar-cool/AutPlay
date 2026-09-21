package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.PairingState
import app.autplay.ui.profilepairing.ProfilePairingUiState
import app.autplay.ui.profilepairing.SelfPairingUiState
import app.autplay.ui.profilepairing.requiresSecureProfileWindow
import app.autplay.ui.profilepairing.selfPairingBlocksFirstBind
import app.autplay.ui.profilepairing.selfPairingNeedsPolling
import java.time.Instant
import org.junit.Assert.*
import org.junit.Test

class SelfPairingUiPolicyTest {
    @Test fun sourceQrUsesSecureWindowAndOnlyActiveCeremoniesPoll() {
        val open = status("OPEN")
        val state = SelfPairingUiState(source = SelfPairingSourceState.Ready(open, "sensitive QR fixture"))
        assertTrue(requiresSecureProfileWindow(ProfilePairingUiState(PairingState.NotConnected, selfPairing = state)))
        assertTrue(selfPairingNeedsPolling(state))
        assertFalse(selfPairingBlocksFirstBind(state))
        for (terminal in listOf("EXCHANGED", "EXPIRED", "CANCELLED", "REJECTED")) {
            assertFalse(selfPairingNeedsPolling(state.copy(source = SelfPairingSourceState.Ready(status(terminal), null))))
        }
    }

    @Test fun uncertainFirstBindBlocksOtherProtocolsAndSuccessfulBindingReleasesThem() {
        val blocked = SelfPairingUiState(recipient = SelfPairingRecipientState.Blocked("self_pairing_unavailable", true))
        assertTrue(selfPairingBlocksFirstBind(blocked))
        assertTrue(requiresSecureProfileWindow(ProfilePairingUiState(PairingState.NotConnected, selfPairing = blocked)))
        assertTrue(selfPairingNeedsPolling(blocked))
        assertFalse(selfPairingBlocksFirstBind(blocked.copy(recipient = SelfPairingRecipientState.Connected)))
        assertFalse(selfPairingNeedsPolling(blocked.copy(recipient = SelfPairingRecipientState.Connected)))
    }

    private fun status(state: String) = SelfPairingStatus("10000000-0000-4000-8000-000000000001", state, 1, Instant.now().plusSeconds(300), null, null, null, null, null, null, null, null, null)
}
