package app.autplay.ui.profilepairing

import app.autplay.R
import app.autplay.application.profilepairing.AdmissionState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay

/** Presentation-only S1B copy. It never renders the poll bearer and does not make it saveable. */
internal data class AdmissionUiState(val admission: AdmissionState = AdmissionState.RequestReady)
internal data class AdmissionActions(
    val request: () -> Unit = {}, val confirmComparison: () -> Unit = {}, val poll: () -> Unit = {},
    val confirmAccount: () -> Unit = {}, val cancel: () -> Unit = {}, val retry: () -> Unit = {},
)

@Composable
internal fun AdmissionPanel(state: AdmissionUiState, actions: AdmissionActions) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val poll = rememberUpdatedState(actions.poll)
    LaunchedEffect(state.admission, lifecycleOwner) {
        if (state.admission is AdmissionState.Pending) {
            lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
                repeat(300) {
                    poll.value()
                    delay(3_000L)
                }
            }
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        when (val value = state.admission) {
            AdmissionState.RequestReady -> { Text(stringResource(R.string.admission_request_intro)); Button(onClick = actions.request) { Text(stringResource(R.string.admission_request_approval)) } }
            is AdmissionState.AwaitingComparison -> {
                LaunchedEffect(value.checkpoint) { actions.confirmComparison() }
                Text(stringResource(R.string.admission_waiting_for_admin))
            }
            is AdmissionState.Pending -> { Text(stringResource(R.string.admission_waiting_for_admin)); Button(onClick = actions.poll) { Text(stringResource(R.string.admission_check_status)) }; OutlinedButton(onClick = actions.cancel) { Text(stringResource(R.string.admission_cancel)) } }
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
