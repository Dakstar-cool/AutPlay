package app.autplay.ui.profilepairing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.portableaccount.PortableAccountStatus
import app.autplay.application.portableaccount.PortableAccountCrypto
import app.autplay.application.social.normalizeSocialPublicId
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.PreferenceSection
import app.autplay.ui.SecureAccountWindow
import kotlinx.coroutines.launch

internal data class PortableAccountUiState(val status: PortableAccountStatus, val bound: Boolean,
    val trustedServer: Boolean, val suggestedId: String?, val recoveryFileReady: Boolean = false)
internal data class PortableAccountActions(
    val create: suspend (String, CharArray) -> Boolean = { _, password -> password.fill('\u0000'); false },
    val connect: suspend (String, CharArray, Boolean) -> Boolean = { _, password, _ -> password.fill('\u0000'); false },
    val exportRecovery: () -> Unit = {}, val retry: () -> Unit = {},
    val importRecovery: () -> Unit = {}, val clearRecoveryFile: () -> Unit = {},
    val recover: suspend (String, CharArray, CharArray) -> Boolean = { _, _, _ -> false },
    val recoveryCode: suspend () -> CharArray? = { null },
    val acknowledgeCode: suspend (CharArray) -> Unit = {},
)

@Composable
internal fun PortableAccountCard(state: PortableAccountUiState, actions: PortableAccountActions) {
    val status = state.status
    val scope = rememberCoroutineScope()
    var login by remember(status.publicId, state.suggestedId) { mutableStateOf(status.publicId ?: state.suggestedId.orEmpty()) }
    var password by remember { mutableStateOf("") }
    var repeated by remember { mutableStateOf("") }
    var working by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    var recoveryMode by remember { mutableStateOf(false) }
    var recoveryCode by remember { mutableStateOf("") }
    var exportedCode by remember { mutableStateOf("") }
    val hasAccount = status.publicId != null
    val needsPassword = !hasAccount || state.trustedServer || (state.bound && !status.serverCopyReady)
    SecureAccountWindow(needsPassword || recoveryMode || exportedCode.isNotEmpty())
    PreferenceSection(stringResource(R.string.portable_account_title), AutPlayIcon.Profile,
        stringResource(R.string.portable_account_summary), initiallyExpanded = !hasAccount || state.trustedServer || status.recoveryUnsaved) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            status.publicId?.let { Text("@$it", style = MaterialTheme.typography.titleMedium) }
            Text(stringResource(R.string.portable_account_summary))
            if (status.recoveryUnsaved) {
                Text(stringResource(R.string.portable_account_recovery_unsaved))
                OutlinedButton(onClick = actions.exportRecovery, enabled = !working && !status.busy) { Text(stringResource(R.string.portable_account_save_recovery)) }
                OutlinedButton(onClick = {
                    scope.launch {
                        try {
                            val code = actions.recoveryCode()
                            try { exportedCode = code?.concatToString().orEmpty() } finally { code?.fill('\u0000') }
                        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
                        catch (_: Exception) { failed = true }
                    }
                }, enabled = !working && !status.busy) { Text(stringResource(R.string.portable_account_show_code)) }
                if (exportedCode.isNotEmpty()) {
                    Text(exportedCode, style = MaterialTheme.typography.bodyMedium)
                    Text(stringResource(R.string.portable_account_code_notice))
                    OutlinedButton(onClick = {
                        val code = exportedCode.toCharArray()
                        exportedCode = ""; working = true
                        scope.launch {
                            try { actions.acknowledgeCode(code) }
                            catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
                            catch (_: Exception) { failed = true }
                            finally { code.fill('\u0000'); working = false }
                        }
                    }) { Text(stringResource(R.string.portable_account_code_saved)) }
                }
            }
            if (status.pendingEnrollment) {
                Text(stringResource(R.string.portable_account_pending))
                Button(onClick = actions.retry, enabled = !working && !status.busy) { Text(stringResource(R.string.profile_retry)) }
            } else if (needsPassword && !recoveryMode) {
                OutlinedTextField(value = login, onValueChange = { login = it.take(24) }, enabled = !working && !hasAccount,
                    label = { Text(stringResource(R.string.portable_account_login)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = password, onValueChange = { password = it.take(256) }, enabled = !working,
                    label = { Text(stringResource(R.string.portable_account_password)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                if (!hasAccount) {
                    Text(stringResource(R.string.portable_account_password_rules), style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(value = repeated, onValueChange = { repeated = it.take(256) }, enabled = !working,
                        label = { Text(stringResource(R.string.portable_account_password_repeat)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                }
                fun submit(connect: Boolean, register: Boolean) {
                    val material = password.toCharArray()
                    password = ""; repeated = ""; working = true; failed = false
                    scope.launch {
                        try { failed = !(if (connect) actions.connect(login, material, register) else actions.create(login, material)) }
                        finally { material.fill('\u0000'); working = false }
                    }
                }
                if (!hasAccount || state.bound) Button(enabled = !working && !status.busy && normalizeSocialPublicId(login) != null &&
                    (hasAccount || password == repeated && PortableAccountCrypto.validPassword(password.toCharArray())), onClick = { submit(false, false) }) {
                    Text(stringResource(if (hasAccount) R.string.portable_account_copy_to_server else R.string.portable_account_create))
                }
                if (state.trustedServer) Button(enabled = !working && !status.busy && normalizeSocialPublicId(login) != null && password.isNotEmpty(), onClick = { submit(true, hasAccount) }) {
                    Text(stringResource(if (hasAccount) R.string.portable_account_connect else R.string.portable_account_sign_in))
                }
            }
            if (!status.pendingEnrollment) {
                OutlinedButton(onClick = {
                    recoveryMode = !recoveryMode; password = ""; repeated = ""; recoveryCode = ""
                    actions.clearRecoveryFile()
                }, enabled = !working && !status.busy) { Text(stringResource(if (recoveryMode) R.string.portable_account_cancel_recovery else R.string.portable_account_recover)) }
                if (recoveryMode) {
                    Text(stringResource(R.string.portable_account_recovery_scope))
                    OutlinedButton(onClick = actions.importRecovery, enabled = !working && !status.busy) {
                        Text(stringResource(if (state.recoveryFileReady) R.string.portable_account_file_ready else R.string.portable_account_import_file))
                    }
                    if (!state.recoveryFileReady) {
                        OutlinedTextField(value = login, onValueChange = { login = it.take(24) }, enabled = !working,
                            label = { Text(stringResource(R.string.portable_account_login)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(value = recoveryCode, onValueChange = { recoveryCode = it.take(43) }, enabled = !working,
                            label = { Text(stringResource(R.string.portable_account_recovery_code)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                            visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                    }
                    OutlinedTextField(value = password, onValueChange = { password = it.take(256) }, enabled = !working,
                        label = { Text(stringResource(R.string.portable_account_new_password)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                    OutlinedTextField(value = repeated, onValueChange = { repeated = it.take(256) }, enabled = !working,
                        label = { Text(stringResource(R.string.portable_account_password_repeat)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                    val localRecovery = status.offlineRecoveryAllowed || (!hasAccount && state.recoveryFileReady && !state.trustedServer && !state.bound)
                    if (localRecovery) Text(stringResource(R.string.portable_account_recovery_local))
                    else if (!state.trustedServer && !state.bound) Text(stringResource(R.string.portable_account_recovery_trust))
                    Button(enabled = !working && !status.busy && (state.trustedServer || state.bound || localRecovery) && password == repeated &&
                        PortableAccountCrypto.validPassword(password.toCharArray()) &&
                        (state.recoveryFileReady || recoveryCode.length == 43 && normalizeSocialPublicId(login) != null), onClick = {
                        val material = password.toCharArray(); val code = recoveryCode.toCharArray()
                        password = ""; repeated = ""; recoveryCode = ""; working = true; failed = false
                        scope.launch {
                            try { failed = !actions.recover(login, code, material); if (!failed) recoveryMode = false }
                            finally { material.fill('\u0000'); code.fill('\u0000'); working = false }
                        }
                    }) { Text(stringResource(if (localRecovery) R.string.portable_account_restore_local else R.string.portable_account_reset)) }
                }
            }
            if (failed || status.error != null) Text(stringResource(when (status.error) {
                "ACCOUNT_CHALLENGE_EXPIRED", "ACCOUNT_RECEIPT_EXPIRED" -> R.string.portable_account_expired
                "ACCOUNT_RECOVERY_HISTORY_FULL" -> R.string.portable_account_history_full
                "ACCOUNT_ROOT_TRANSITION_UNAVAILABLE" -> R.string.portable_account_transition_unavailable
                else -> R.string.portable_account_operation_failed
            }), color = MaterialTheme.colorScheme.error)
        }
    }
}
