package app.autplay

import android.app.Activity
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalContext
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.application.selfpairing.SelfPairingRecipientRuntime
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.application.selfpairing.SelfPairingSourceRuntime
import app.autplay.application.selfpairing.SelfPairingSourceState
import app.autplay.ui.profilepairing.SelfPairingActions
import app.autplay.ui.profilepairing.SelfPairingUiState
import app.autplay.ui.profilepairing.publicRegistrationBlocksOrdinaryFirstBind
import kotlinx.coroutines.launch

internal data class MainSelfPairingUi(val state: SelfPairingUiState, val actions: SelfPairingActions)

/** Connects the account screen to the durable, existing device-pairing ceremony. */
@Composable
internal fun rememberSelfPairingUi(
    source: SelfPairingSourceRuntime,
    recipient: SelfPairingRecipientRuntime,
    pairing: PairingState,
    registration: PublicAccountRegistrationState,
    bound: Boolean,
    onboardingComplete: Boolean,
): MainSelfPairingUi {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val sourceState by source.state.collectAsState()
    val recipientState by recipient.state.collectAsState()
    LaunchedEffect(source, recipient, onboardingComplete) {
        if (onboardingComplete) {
            source.resume()
            recipient.resume()
        }
    }
    val scanner = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val payload = result.data?.getStringExtra(EnrollmentQrScannerActivity.EXTRA_PAYLOAD)
            ?.takeIf { result.resultCode == Activity.RESULT_OK && it.length in 2..4096 }
        if (payload != null) scope.launch {
            val bytes = payload.toByteArray(Charsets.UTF_8)
            try { recipient.inspectQr(bytes) } finally { bytes.fill(0) }
        }
    }
    val canAdd = (pairing as? PairingState.Connected)?.capabilities?.supportedOperations
        ?.contains("self_device_pairing") == true
    val canScan = !bound && !publicRegistrationBlocksOrdinaryFirstBind(registration) &&
        (pairing is PairingState.NotConnected || pairing is PairingState.Cancelled)
    return MainSelfPairingUi(
        SelfPairingUiState(sourceState, recipientState, canAddPhone = canAdd, canScan = canScan),
        SelfPairingActions(
            start = { scope.launch { source.start() } },
            scan = { scanner.launch(Intent(context, EnrollmentQrScannerActivity::class.java)) },
            trust = { scope.launch { recipient.confirmTrust() } },
            confirmAccount = { account -> scope.launch { recipient.confirmAccount(account) } },
            decide = { action, code -> scope.launch { source.decide(action, code) } },
            retrySource = { scope.launch { source.resume() } },
            retryRecipient = { scope.launch { recipient.resume() } },
            dismissSource = { scope.launch { source.dismiss() } },
            dismissRecipient = { scope.launch { recipient.dismiss() } },
            poll = {
                if (source.state.value is SelfPairingSourceState.Ready) source.poll()
                if (recipient.state.value !in listOf(SelfPairingRecipientState.Idle, SelfPairingRecipientState.Connected)) recipient.poll()
                val sourceFailure = source.state.value as? SelfPairingSourceState.Blocked
                val recipientFailure = recipient.state.value as? SelfPairingRecipientState.Blocked
                if (sourceFailure != null || recipientFailure != null) 15 else 2
            },
        ),
    )
}
