package app.autplay.ui.profilepairing

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.PersistableBundle
import app.autplay.R
import app.autplay.application.profilepairing.AdmissionState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp

/** Presentation-only S1B copy. It never renders the poll bearer and does not make it saveable. */
internal data class AdmissionUiState(val admission: AdmissionState = AdmissionState.RequestReady)
internal data class AdmissionActions(
    val request: () -> Unit = {}, val confirmComparison: () -> Unit = {}, val poll: () -> Unit = {},
    val confirmAccount: () -> Unit = {}, val cancel: () -> Unit = {}, val retry: () -> Unit = {},
)

@Composable
internal fun AdmissionPanel(state: AdmissionUiState, actions: AdmissionActions) {
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        when (val value = state.admission) {
            AdmissionState.RequestReady -> { Text("Request approval from your personal server"); Button(onClick = actions.request) { Text("Request approval") } }
            is AdmissionState.AwaitingComparison -> {
                val formattedCode = value.sas.chunked(4).joinToString("-")
                val locatorLabel = stringResource(R.string.admission_review_locator_clipboard_label)
                val codeLabel = stringResource(R.string.admission_comparison_code_clipboard_label)
                Text("Open review locator: ${value.reviewLocator}")
                OutlinedButton(
                    onClick = { copySensitiveText(context, locatorLabel, value.reviewLocator) },
                ) { Text(stringResource(R.string.admission_copy_review_locator)) }
                Text("Compare this code in the browser: $formattedCode")
                OutlinedButton(
                    onClick = { copySensitiveText(context, codeLabel, formattedCode) },
                ) { Text(stringResource(R.string.admission_copy_comparison_code)) }
                Button(onClick = actions.confirmComparison) { Text("I compared the code") }
                OutlinedButton(onClick = actions.cancel) { Text("Cancel") }
            }
            is AdmissionState.Pending -> { Text("Waiting for approval. Local library and playback remain available."); Button(onClick = actions.poll) { Text("Check status") }; OutlinedButton(onClick = actions.cancel) { Text("Cancel") } }
            is AdmissionState.Approved -> { Text("Approved for ${value.account.label} (${value.account.userId.value}). Confirm this account before connecting."); Button(onClick = actions.confirmAccount) { Text("Confirm account") }; OutlinedButton(onClick = actions.cancel) { Text("Cancel") } }
            is AdmissionState.Exchanging -> Text("Connecting approved device…")
            AdmissionState.Connected -> Text("Connected")
            is AdmissionState.Rejected -> Terminal("Approval was rejected.", actions)
            is AdmissionState.Blocked -> Terminal("This device key is blocked.", actions)
            is AdmissionState.Expired -> Terminal("Approval request expired.", actions)
            AdmissionState.Cancelled -> Terminal("Approval request cancelled.", actions)
            AdmissionState.Unavailable -> Terminal("Approval service is unavailable. Local music remains available.", actions)
            AdmissionState.IdentityChanged -> Terminal("Server identity changed. Recheck trust before trying again.", actions)
        }
    }
}

@Composable private fun Terminal(copy: String, actions: AdmissionActions) { Text(copy); Button(onClick = actions.retry) { Text("Try again") } }

private const val SENSITIVE_CLIPBOARD_EXTRA = "android.content.extra.IS_SENSITIVE"

private fun copySensitiveText(context: Context, label: String, value: String) {
    val clip = ClipData.newPlainText(label, value)
    clip.description.extras = PersistableBundle().apply {
        putBoolean(SENSITIVE_CLIPBOARD_EXTRA, true)
    }
    context.getSystemService(ClipboardManager::class.java).setPrimaryClip(clip)
}
