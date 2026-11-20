package app.autplay.ui.profilepairing

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.ui.SecureAccountWindow
import kotlinx.coroutines.launch

@Composable
internal fun AccountSignInScreen(publicId: String, signIn: suspend (String, CharArray) -> Boolean) {
    SecureAccountWindow(true)
    Column(Modifier.fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(stringResource(R.string.account_sign_in), style = MaterialTheme.typography.headlineMedium)
        Text("@$publicId")
        AccountPasswordForm(publicId, R.string.account_sign_in, signIn)
    }
}

@Composable
internal fun AccountSignOutDialog(publicId: String, recoveryUnsaved: Boolean, onDismiss: () -> Unit,
    onConfirm: suspend (String, CharArray) -> Boolean, remove: Boolean = false) {
    SecureAccountWindow(true)
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(stringResource(if (remove) R.string.account_remove_empty_local else R.string.account_sign_out)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(if (remove) R.string.account_remove_empty_explanation else R.string.account_sign_out_explanation))
                if (recoveryUnsaved) Text(stringResource(R.string.portable_account_recovery_unsaved))
                Text("@$publicId")
                AccountPasswordForm(publicId, if (remove) R.string.account_remove_empty_local else R.string.account_sign_out) { login, password ->
                    onConfirm(login, password).also { if (it) onDismiss() }
                }
            }
        }, confirmButton = {}, dismissButton = {
            TextButton(onClick = onDismiss) { Text(stringResource(R.string.profile_cancel)) }
        })
}

@Composable
private fun AccountPasswordForm(publicId: String, action: Int, submit: suspend (String, CharArray) -> Boolean) {
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    OutlinedTextField(password, { password = it.take(256) }, enabled = !busy, singleLine = true,
        modifier = Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.portable_account_password)) },
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password))
    if (failed) Text(stringResource(R.string.account_session_failed), color = MaterialTheme.colorScheme.error)
    Button(enabled = !busy && password.isNotEmpty(), onClick = {
        val material = password.toCharArray(); password = ""; busy = true; failed = false
        scope.launch {
            try { failed = !submit(publicId, material) }
            finally { material.fill('\u0000'); busy = false }
        }
    }) { Text(stringResource(action)) }
}
