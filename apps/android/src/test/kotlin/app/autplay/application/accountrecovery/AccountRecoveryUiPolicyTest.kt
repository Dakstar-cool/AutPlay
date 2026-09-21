package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.PairingState
import app.autplay.ui.profilepairing.AccountRecoveryUiState
import app.autplay.ui.profilepairing.ProfilePairingUiState
import app.autplay.ui.profilepairing.accountRecoveryBlocksFirstBind
import app.autplay.ui.profilepairing.requiresSecureProfileWindow
import org.junit.Assert.*
import org.junit.Test

class AccountRecoveryUiPolicyTest {
    @Test fun offlineExportAndManualEntryKeepWindowSecure() {
        for (state in listOf(
            AccountRecoveryUiState(canRecover = true),
            AccountRecoveryUiState(source = AccountRecoverySourceState.Ready(true, true, 2)),
            AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.Blocked("unavailable", true, true)),
        )) assertTrue(requiresSecureProfileWindow(ProfilePairingUiState(pairing = PairingState.NotConnected, accountRecovery = state)))
    }

    @Test fun pendingAndFailedCeremoniesBlockCompetingFirstBindUntilDismissed() {
        assertFalse(accountRecoveryBlocksFirstBind(AccountRecoveryUiState()))
        assertTrue(accountRecoveryBlocksFirstBind(AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.Working)))
        assertTrue(accountRecoveryBlocksFirstBind(AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.Blocked("unavailable", false, false))))
        assertFalse(accountRecoveryBlocksFirstBind(AccountRecoveryUiState(recipient = AccountRecoveryRecipientState.Connected)))
    }
}
