package app.autplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import app.autplay.application.accountrecovery.AccountDeletionSourceRuntime
import app.autplay.application.accountrecovery.AccountDeletionSourceState
import app.autplay.application.accountrecovery.AccountRecoveryRecipientRuntime
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySourceRuntime
import app.autplay.application.accountrecovery.AccountRestorationPurpose
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.profilepairing.AccountDeletionActions
import app.autplay.ui.profilepairing.AccountDeletionUiState
import app.autplay.ui.profilepairing.AccountRecoveryUiState
import app.autplay.ui.profilepairing.accountRecoveryBlocksFirstBind
import kotlinx.coroutines.launch

internal data class MainAccountDeletionUi(val state: AccountDeletionUiState, val actions: AccountDeletionActions)

@Composable
internal fun rememberAccountDeletionUi(source: AccountDeletionSourceRuntime, cancellation: AccountRecoveryRecipientRuntime,
    recoverySource: AccountRecoverySourceRuntime, ordinaryRecipient: AccountRecoveryRecipientState,
    settings: NonSecretSettings, pairing: PairingState, registration: PublicAccountRegistrationState,
    selfRecipient: SelfPairingRecipientState, bound: Boolean): MainAccountDeletionUi {
    val scope = rememberCoroutineScope()
    val sourceState by source.state.collectAsState()
    val cancellationState by cancellation.state.collectAsState()
    val canManage = (pairing as? PairingState.Connected)?.capabilities?.supportedOperations?.contains("account_deletion") == true
    LaunchedEffect(canManage, settings.m5Binding?.bindingCommitId, cancellationState) {
        if (canManage || cancellationState == AccountRecoveryRecipientState.Connected) source.load()
    }
    val unknown = sourceState is AccountDeletionSourceState.Working || (sourceState as? AccountDeletionSourceState.Blocked)?.pending == true
    val recipientUi = rememberAccountRecoveryUi(recoverySource, cancellation, settings, pairing, registration,
        selfRecipient, bound, AccountRestorationPurpose.DELETE_CANCEL,
        unknown || accountRecoveryBlocksFirstBind(AccountRecoveryUiState(recipient = ordinaryRecipient)))
    return MainAccountDeletionUi(AccountDeletionUiState(sourceState, canManage, recipientUi.state),
        AccountDeletionActions({ account, code -> scope.launch { source.request(account, code) } },
            { scope.launch { source.load() } }, recipientUi.actions))
}
