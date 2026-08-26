package app.autplay.ui.profilepairing

import app.autplay.application.profilepairing.AdmissionState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp

/** Presentation-only S1B copy. It never renders the poll bearer and does not make it saveable. */
internal data class AdmissionUiState(val admission: AdmissionState = AdmissionState.RequestReady)
internal data class AdmissionActions(
    val request: () -> Unit = {}, val confirmComparison: () -> Unit = {}, val poll: () -> Unit = {},
    val confirmAccount: () -> Unit = {}, val cancel: () -> Unit = {}, val retry: () -> Unit = {},
)

@Composable
internal fun AdmissionPanel(state: AdmissionUiState, actions: AdmissionActions) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        when (val value = state.admission) {
            AdmissionState.RequestReady -> { Text("Request approval from your personal server"); Button(onClick = actions.request) { Text("Request approval") } }
            is AdmissionState.AwaitingComparison -> { Text("Open review locator: ${value.reviewLocator}"); Text("Compare this code in the browser: ${value.sas.chunked(4).joinToString("-")}"); Button(onClick = actions.confirmComparison) { Text("I compared the code") }; OutlinedButton(onClick = actions.cancel) { Text("Cancel") } }
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
