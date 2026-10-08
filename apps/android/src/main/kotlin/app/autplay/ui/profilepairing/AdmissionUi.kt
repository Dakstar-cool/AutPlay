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
            is AdmissionState.Approved -> { Text(stringResource(R.string.admission_approved_account, value.account.label, value.account.userId.value)); Button(onClick = actions.confirmAccount) { Text(stringResource(R.string.admission_confirm_account)) }; OutlinedButton(onClick = actions.cancel) { Text(stringResource(R.string.admission_cancel)) } }
            is AdmissionState.Exchanging -> Text(stringResource(R.string.admission_connecting_device))
            AdmissionState.Connected -> Text(stringResource(R.string.admission_connected))
            is AdmissionState.Rejected -> Terminal(stringResource(R.string.admission_rejected), actions)
            is AdmissionState.Blocked -> Terminal(stringResource(R.string.admission_blocked), actions)
            is AdmissionState.Expired -> Terminal(stringResource(R.string.admission_expired), actions)
            AdmissionState.Cancelled -> Terminal(stringResource(R.string.admission_cancelled), actions)
            AdmissionState.Unavailable -> Terminal(stringResource(R.string.admission_unavailable), actions)
            AdmissionState.IdentityChanged -> Terminal(stringResource(R.string.admission_identity_changed), actions)
        }
    }
}

@Composable private fun Terminal(copy: String, actions: AdmissionActions) { Text(copy); Button(onClick = actions.retry) { Text(stringResource(R.string.admission_retry)) } }
