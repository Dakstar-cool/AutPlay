package app.autplay.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.portableaccount.PortableAccountCrypto
import app.autplay.application.social.normalizeSocialPublicId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** Creates the required account locally before opening the music library. */
@Composable
public fun WelcomeOnboardingScreen(
    onComplete: suspend (UiDestination, String) -> Boolean,
    modifier: Modifier = Modifier,
    initialPublicId: String? = null,
    createAccount: (suspend (String, CharArray) -> Boolean)? = null,
    existingAccount: (suspend (String) -> Boolean)? = null,
) {
    var saving by remember { mutableStateOf(false) }
    var saveFailed by rememberSaveable { mutableStateOf(false) }
    var publicIdInput by rememberSaveable { mutableStateOf(initialPublicId.orEmpty()) }
    // Passwords must not enter saved instance state.
    var password by remember { mutableStateOf("") }
    var passwordConfirmation by remember { mutableStateOf("") }
    val publicId = normalizeSocialPublicId(publicIdInput)
    val passwordValid = createAccount == null || PortableAccountCrypto.validPassword(password.toCharArray())
    val passwordsMatch = createAccount == null || password == passwordConfirmation
    val ready = publicId != null && passwordValid && passwordsMatch
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    SecureAccountWindow(createAccount != null)
    BackHandler(enabled = saving) { /* Keep the in-flight account transaction on this screen. */ }

    fun submit(existing: Boolean) {
        val selectedId = publicId ?: return
        if (saving || (!existing && !ready)) return
        saving = true
        saveFailed = false
        keyboard?.hide()
        focus.clearFocus()
        scope.launch {
            val material = password.toCharArray()
            try {
                val completed = if (existing) {
                    existingAccount?.invoke(selectedId) == true
                } else {
                    val created = createAccount?.invoke(selectedId, material) ?: true
                    created && onComplete(UiDestination.Home, selectedId)
                }
                saveFailed = !completed
                if (completed) {
                    password = ""
                    passwordConfirmation = ""
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                saveFailed = true
            } finally {
                material.fill('\u0000')
                saving = false
            }
        }
    }

    Column(
        modifier = modifier.fillMaxSize().systemBarsPadding().imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = AutPlayTokens.dimensions.screenPadding, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(stringResource(R.string.onboarding_welcome_title),
            style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(stringResource(R.string.onboarding_welcome_body), style = MaterialTheme.typography.bodyLarge)
        OutlinedTextField(
            value = publicIdInput,
            onValueChange = { publicIdInput = it.take(64); saveFailed = false },
            label = { Text(stringResource(R.string.ui_friends_refresh_public_id_label)) },
            supportingText = { Text(stringResource(R.string.ui_friends_refresh_public_id_rules)) },
            isError = publicIdInput.isNotBlank() && publicId == null,
            enabled = !saving, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Next),
            keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Down) }),
            modifier = Modifier.fillMaxWidth(),
        )
        if (createAccount != null) {
            OutlinedTextField(
                value = password,
                onValueChange = { password = it.take(256); saveFailed = false },
                label = { Text(stringResource(R.string.portable_account_password)) },
                supportingText = { Text(stringResource(if (password.isNotEmpty() && !passwordValid)
                    R.string.onboarding_password_invalid else R.string.portable_account_password_rules)) },
                isError = password.isNotEmpty() && !passwordValid,
                enabled = !saving, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Next),
                keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Down) }),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = passwordConfirmation,
                onValueChange = { passwordConfirmation = it.take(256); saveFailed = false },
                label = { Text(stringResource(R.string.portable_account_password_repeat)) },
                supportingText = {
                    if (passwordConfirmation.isNotEmpty() && !passwordsMatch)
                        Text(stringResource(R.string.onboarding_password_mismatch))
                },
                isError = passwordConfirmation.isNotEmpty() && !passwordsMatch,
                enabled = !saving, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { keyboard?.hide(); focus.clearFocus() }),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Text(stringResource(R.string.onboarding_local_note), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (!ready && !saving) {
            Text(stringResource(R.string.onboarding_required_fields), style = MaterialTheme.typography.bodySmall)
        }
        if (saving) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text(stringResource(R.string.onboarding_saving), style = MaterialTheme.typography.bodyMedium)
        }
        if (saveFailed) {
            Text(stringResource(R.string.onboarding_save_failed), color = MaterialTheme.colorScheme.error)
        }
        Button(onClick = { submit(false) }, enabled = !saving && ready, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.onboarding_continue_local))
        }
        if (existingAccount != null) {
            OutlinedButton(onClick = { submit(true) }, enabled = !saving && publicId != null,
                modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.portable_account_existing))
            }
        }
    }
}
