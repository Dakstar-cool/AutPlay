package app.autplay.ui.profilepairing

import app.autplay.R
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySourceState
import app.autplay.application.accountrecovery.AccountRestorationPurpose
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

internal data class AccountRecoveryUiState(
    val source: AccountRecoverySourceState = AccountRecoverySourceState.Idle,
    val recipient: AccountRecoveryRecipientState = AccountRecoveryRecipientState.Idle,
    val canManage: Boolean = false, val canRecover: Boolean = false,
    val fileNotice: String? = null,
    val setupRequired: Boolean = false,
    val purpose: AccountRestorationPurpose = AccountRestorationPurpose.RECOVERY,
)
internal data class AccountRecoveryActions(
    val configure: (Boolean) -> Unit = {}, val export: () -> Unit = {}, val pickFile: () -> Unit = {},
    val manual: (String, String, String) -> Unit = { _, _, _ -> }, val confirmServer: () -> Unit = {},
    val confirmAccount: (String) -> Unit = {}, val retrySource: () -> Unit = {},
    val retryRecipient: () -> Unit = {}, val cancel: () -> Unit = {},
)
internal fun accountRecoveryBlocksFirstBind(state: AccountRecoveryUiState?): Boolean = when (val recipient = state?.recipient) {
    null, AccountRecoveryRecipientState.Idle, AccountRecoveryRecipientState.Connected -> false
    is AccountRecoveryRecipientState.Blocked -> true
    else -> true
}

@Composable
internal fun AccountRecoveryCard(state: AccountRecoveryUiState, actions: AccountRecoveryActions) {
    // Secrets and private origins are intentionally absent from saved-instance state.
    var origin by remember { mutableStateOf("") }
    var account by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var manualOpen by remember { mutableStateOf(false) }
    var confirmRotation by remember { mutableStateOf(false) }
    val localExport = (state.source as? AccountRecoverySourceState.Ready)?.canExport == true
    val deletion = state.purpose == AccountRestorationPurpose.DELETE_CANCEL
    if (!state.canManage && !state.canRecover && !localExport && !state.setupRequired && state.recipient == AccountRecoveryRecipientState.Idle) return
    Surface(shape = MaterialTheme.shapes.large, tonalElevation = 1.dp) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(if (deletion) R.string.deletion_cancel_title else R.string.recovery_title), style = MaterialTheme.typography.titleMedium)
            if (state.setupRequired) Text(stringResource(R.string.recovery_new_account_incomplete))
            if (state.setupRequired && !state.canManage && !localExport) {
                Text(stringResource(R.string.recovery_unavailable))
                OutlinedButton(onClick = actions.retrySource) { Text(stringResource(R.string.recovery_retry)) }
            }
            if (state.canManage || localExport) when (val source = state.source) {
                AccountRecoverySourceState.Working -> Text(stringResource(R.string.recovery_working))
                is AccountRecoverySourceState.Ready -> {
                    Text(stringResource(if (source.exportConfirmed) R.string.recovery_saved else if (source.canExport) R.string.recovery_save_required else if (source.configured) R.string.recovery_existing_code else R.string.recovery_setup_required))
                    if (source.canExport) Button(onClick = actions.export) { Text(stringResource(R.string.recovery_export)) }
                    if (state.canManage) OutlinedButton(onClick = { if (source.configured) confirmRotation = true else actions.configure(false) }) {
                        Text(stringResource(if (source.configured) R.string.recovery_rotate else R.string.recovery_create))
                    }
                }
                is AccountRecoverySourceState.Blocked -> {
                    Text(stringResource(R.string.recovery_unavailable))
                    Button(onClick = actions.retrySource) { Text(stringResource(R.string.recovery_retry)) }
                    OutlinedButton(onClick = { confirmRotation = true }) { Text(stringResource(R.string.recovery_rotate)) }
                }
                AccountRecoverySourceState.Idle -> Button(onClick = actions.retrySource) { Text(stringResource(R.string.recovery_setup)) }
            }
            when (val recipient = state.recipient) {
                AccountRecoveryRecipientState.Idle -> if (state.canRecover) {
                    Text(stringResource(if (deletion) R.string.deletion_cancel_description else R.string.recovery_description))
                    Button(onClick = actions.pickFile) { Text(stringResource(R.string.recovery_import)) }
                    OutlinedButton(onClick = { manualOpen = !manualOpen }) { Text(stringResource(R.string.recovery_manual)) }
                    if (manualOpen) {
                        OutlinedTextField(origin, { if (it.length <= 2048) origin = it }, label = { Text(stringResource(R.string.recovery_server)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(account, { if (it.length <= 36) account = it }, label = { Text(stringResource(R.string.recovery_account_id)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(code, { if (it.length <= 96) code = it }, label = { Text(stringResource(R.string.recovery_code)) }, singleLine = true,
                            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                        Button(onClick = { actions.manual(origin, account, code); code = ""; manualOpen = false }, enabled = origin.isNotBlank() && account.length == 36 && code.isNotBlank()) {
                            Text(stringResource(R.string.recovery_inspect_server))
                        }
                    }
                }
                AccountRecoveryRecipientState.Working -> Text(stringResource(R.string.recovery_working))
                is AccountRecoveryRecipientState.ConfirmServer -> {
                    Text(stringResource(R.string.recovery_confirm_server))
                    Text(recipient.label)
                    Text(recipient.identity.apiOrigin)
                    Text(recipient.identity.streamOrigin)
                    Text(recipient.identity.thumbprint)
                    Text(recipient.accountId)
                    Button(onClick = actions.confirmServer) { Text(stringResource(R.string.recovery_trust)) }
                    TextButton(onClick = actions.cancel) { Text(stringResource(R.string.recovery_cancel)) }
                }
                is AccountRecoveryRecipientState.ConfirmAccount -> {
                    Text(stringResource(if (deletion) R.string.deletion_confirm_cancel else R.string.recovery_confirm_account, recipient.preview.label))
                    Text(recipient.preview.accountId)
                    Text(stringResource(if (deletion) R.string.deletion_cancel_warning else R.string.recovery_revoke_warning))
                    recipient.preview.deletion?.let { Text(stringResource(R.string.deletion_deadline, it.cancelBefore.toString())) }
                    Button(onClick = { actions.confirmAccount(recipient.preview.accountId) }) { Text(stringResource(if (deletion) R.string.deletion_cancel_commit else R.string.recovery_commit)) }
                    TextButton(onClick = actions.cancel) { Text(stringResource(R.string.recovery_cancel)) }
                }
                is AccountRecoveryRecipientState.Blocked -> {
                    Text(stringResource(if (recipient.commitPending) R.string.recovery_pending else R.string.recovery_unavailable))
                    if (recipient.pending) Button(onClick = actions.retryRecipient) { Text(stringResource(R.string.recovery_retry)) }
                    if (!recipient.commitPending) TextButton(onClick = actions.cancel) { Text(stringResource(R.string.recovery_cancel)) }
                }
                AccountRecoveryRecipientState.Connected -> Text(stringResource(if (deletion) R.string.deletion_cancelled else R.string.recovery_connected))
            }
            state.fileNotice?.let { Text(stringResource(if (it == "saved") R.string.recovery_saved else R.string.recovery_file_failed)) }
        }
    }
    if (confirmRotation) AlertDialog(onDismissRequest = { confirmRotation = false },
        title = { Text(stringResource(R.string.recovery_rotate)) }, text = { Text(stringResource(R.string.recovery_rotation_warning)) },
        confirmButton = { Button(onClick = { confirmRotation = false; actions.configure(true) }) { Text(stringResource(R.string.recovery_rotate)) } },
        dismissButton = { TextButton(onClick = { confirmRotation = false }) { Text(stringResource(R.string.recovery_cancel)) } })
}
