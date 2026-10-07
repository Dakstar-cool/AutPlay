package app.autplay.ui.social

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.social.PublicIdRegistrationState
import app.autplay.application.social.SocialRuntimeState
import app.autplay.application.social.normalizeSocialPublicId
import app.autplay.application.social.publicIdOrNull
import app.autplay.ui.AutPlayCard
import app.autplay.ui.AutPlayTokens

/** Also usable without a server, with a locally persisted Pending state supplied by the profile. */
@Composable
fun SocialPublicIdPanel(
    state: SocialRuntimeState,
    actions: SocialActions,
    modifier: Modifier = Modifier,
    serverAvailable: Boolean = true,
) {
    val registration = state.publicIdRegistration
    val initial = registration.publicIdOrNull().orEmpty()
    var input by rememberSaveable(initial) { mutableStateOf(initial) }
    val normalized = normalizeSocialPublicId(input)
    AutPlayCard(modifier) {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.ui_friends_refresh_my_id), style = MaterialTheme.typography.titleMedium)
            if (registration is PublicIdRegistrationState.Confirmed) {
                Text("@${registration.publicId}", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.primary)
                Text(stringResource(R.string.ui_friends_refresh_id_confirmed), color = AutPlayTokens.colors.mutedText)
            } else {
                OutlinedTextField(
                    value = input,
                    onValueChange = { input = it.take(64) },
                    label = { Text(stringResource(R.string.ui_friends_refresh_public_id_label)) },
                    supportingText = { Text(stringResource(R.string.ui_friends_refresh_public_id_rules)) },
                    isError = input.isNotBlank() && normalized == null,
                    enabled = !state.publicIdLoading,
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    stringResource(
                        if (registration is PublicIdRegistrationState.Conflict) R.string.ui_friends_refresh_id_conflict
                        else R.string.ui_friends_refresh_id_pending,
                    ),
                    color = if (registration is PublicIdRegistrationState.Conflict) MaterialTheme.colorScheme.error else AutPlayTokens.colors.mutedText,
                    style = MaterialTheme.typography.bodyMedium,
                )
                Button(
                    onClick = { normalized?.let(actions.submitPublicId) },
                    enabled = normalized != null && !state.publicIdLoading,
                    modifier = Modifier.heightIn(min = AutPlayTokens.dimensions.minimumTouchTarget),
                ) { Text(stringResource(if (serverAvailable) R.string.ui_friends_refresh_register_id else R.string.ui_friends_refresh_save_id)) }
            }
            if (state.publicIdLoading) Text(stringResource(R.string.ui_friends_refresh_id_checking), style = MaterialTheme.typography.bodySmall)
            state.publicIdErrorCode?.let {
                Text(
                    stringResource(
                        when (it) {
                            "rate_limited" -> R.string.ui_friends_refresh_rate_limited
                            "auth_attention_required" -> R.string.social_error_reconnect
                            "public_id_invalid", "request_validation_failed" -> R.string.ui_friends_refresh_public_id_rules
                            else -> R.string.ui_friends_refresh_id_connection_required
                        },
                    ),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}
