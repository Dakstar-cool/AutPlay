package app.autplay.ui.profilepairing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
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
import app.autplay.R
import app.autplay.application.accountrecovery.AccountDeletionSourceState

internal data class AccountDeletionUiState(val source: AccountDeletionSourceState = AccountDeletionSourceState.Idle,
    val canManage: Boolean = false, val cancellation: AccountRecoveryUiState = AccountRecoveryUiState())
internal data class AccountDeletionActions(val request: (String, String) -> Unit = { _, _ -> },
    val retry: () -> Unit = {}, val cancellation: AccountRecoveryActions = AccountRecoveryActions())

internal fun accountDeletionBlocksFirstBind(state: AccountDeletionUiState?): Boolean =
    state?.source is AccountDeletionSourceState.Working ||
        (state?.source as? AccountDeletionSourceState.Blocked)?.pending == true ||
        accountRecoveryBlocksFirstBind(state?.cancellation)

@Composable
internal fun AccountDeletionCard(state: AccountDeletionUiState, actions: AccountDeletionActions) {
    var confirm by remember { mutableStateOf(false) }
    var account by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    if (state.canManage || state.source != AccountDeletionSourceState.Idle) Surface(
        shape = MaterialTheme.shapes.large, tonalElevation = 1.dp) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.deletion_title), style = MaterialTheme.typography.titleMedium)
            when (val source = state.source) {
                AccountDeletionSourceState.Idle -> Button(onClick = actions.retry) { Text(stringResource(R.string.deletion_check)) }
                AccountDeletionSourceState.Working -> Text(stringResource(R.string.recovery_working))
                is AccountDeletionSourceState.Ready -> {
                    if (source.status.canRequest) {
                        Text(stringResource(R.string.deletion_warning))
                        Text(source.status.accountId)
                        Button(onClick = { confirm = true }) { Text(stringResource(R.string.deletion_request)) }
                    } else Text(stringResource(when (source.status.reason) {
                        "last_owner_required" -> R.string.deletion_last_owner
                        "deletion_initializing" -> R.string.deletion_initializing
                        else -> R.string.recovery_setup_required
                    }))
                }
                is AccountDeletionSourceState.Recorded -> {
                    Text(stringResource(if (source.receipt.state == "PENDING") R.string.deletion_recorded else R.string.deletion_historical))
                    Text(source.receipt.accountId)
                    Text(stringResource(R.string.deletion_deadline, source.receipt.cancelBefore.toString()))
                    TextButton(onClick = actions.retry) { Text(stringResource(R.string.deletion_check)) }
                }
                is AccountDeletionSourceState.NotAccepted -> {
                    Text(stringResource(R.string.deletion_not_accepted))
                    Text(source.resolution.accountId)
                }
                is AccountDeletionSourceState.Blocked -> {
                    Text(stringResource(if (source.pending) R.string.deletion_unknown else R.string.deletion_unavailable))
                    Button(onClick = actions.retry) { Text(stringResource(R.string.recovery_retry)) }
                }
            }
        }
    }
    AccountRecoveryCard(state.cancellation, actions.cancellation)
    if (confirm) AlertDialog(onDismissRequest = { confirm = false; account = ""; code = "" },
        title = { Text(stringResource(R.string.deletion_request)) }, text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.deletion_warning))
                OutlinedTextField(account, { account = it.take(36) }, label = { Text(stringResource(R.string.deletion_confirm_id)) }, singleLine = true)
                OutlinedTextField(code, { code = it.take(96) }, label = { Text(stringResource(R.string.recovery_code)) },
                    visualTransformation = PasswordVisualTransformation(), singleLine = true)
            }
        }, confirmButton = {
            Button(enabled = account == (state.source as? AccountDeletionSourceState.Ready)?.status?.accountId && code.isNotBlank(),
                onClick = { actions.request(account, code); confirm = false; account = ""; code = "" }) { Text(stringResource(R.string.deletion_request)) }
        }, dismissButton = { TextButton(onClick = { confirm = false; account = ""; code = "" }) { Text(stringResource(R.string.recovery_cancel)) } })
}
