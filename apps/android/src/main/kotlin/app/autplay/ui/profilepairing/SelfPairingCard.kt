package app.autplay.ui.profilepairing

import app.autplay.R
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.application.selfpairing.SelfPairingSourceState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlin.random.Random
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

internal data class SelfPairingUiState(
    val source: SelfPairingSourceState = SelfPairingSourceState.Idle,
    val recipient: SelfPairingRecipientState = SelfPairingRecipientState.Idle,
    val canAddPhone: Boolean = false,
    val canScan: Boolean = false,
)

internal data class SelfPairingActions(
    val start: () -> Unit = {}, val scan: () -> Unit = {},
    val trust: () -> Unit = {}, val confirmAccount: (String) -> Unit = {},
    val decide: (String, String?) -> Unit = { _, _ -> },
    val retrySource: () -> Unit = {}, val retryRecipient: () -> Unit = {},
    val dismissSource: () -> Unit = {}, val dismissRecipient: () -> Unit = {},
    /** Returns the minimum next interval: 2 normally, 15 after rate limits or network errors. */
    val poll: suspend () -> Int = { 2 },
)

internal fun selfPairingBlocksFirstBind(state: SelfPairingUiState?): Boolean = when (val recipient = state?.recipient) {
    null, SelfPairingRecipientState.Idle, SelfPairingRecipientState.Connected -> false
    is SelfPairingRecipientState.Blocked -> recipient.pending
    else -> true
}

internal fun selfPairingNeedsPolling(state: SelfPairingUiState): Boolean {
    val source = state.source
    if (source is SelfPairingSourceState.Ready && !source.status.terminal) return true
    return when (val recipient = state.recipient) {
        is SelfPairingRecipientState.WaitingForApproval,
        is SelfPairingRecipientState.AwaitingAccountConfirmation -> true
        is SelfPairingRecipientState.Blocked -> recipient.pending &&
            recipient.code in setOf("self_pairing_unavailable", "self_pairing_rate_limited", "account_device_limit_reached")
        else -> false
    }
}

@Composable
internal fun SelfPairingCard(state: SelfPairingUiState, actions: SelfPairingActions) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val poll = rememberUpdatedState(actions.poll)
    val polling = selfPairingNeedsPolling(state)
    LaunchedEffect(lifecycle, polling) {
        if (polling) lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var interval = 2
            while (isActive) {
                delay(minOf(15_000L, interval * 1000L + Random.nextLong(0, 501)))
                interval = poll.value().coerceIn(2, 15)
            }
        }
    }
    if (!state.canAddPhone && !state.canScan && state.source == SelfPairingSourceState.Idle && state.recipient == SelfPairingRecipientState.Idle) return
    Surface(shape = MaterialTheme.shapes.large, tonalElevation = 1.dp) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.self_pairing_title), style = MaterialTheme.typography.titleMedium)
            when (val source = state.source) {
                SelfPairingSourceState.Idle -> if (state.canAddPhone) {
                    Text(stringResource(R.string.self_pairing_hint))
                    Button(onClick = actions.start) { Text(stringResource(R.string.self_pairing_add)) }
                }
                SelfPairingSourceState.Working -> Text(stringResource(R.string.self_pairing_working))
                SelfPairingSourceState.ExpiredStart -> {
                    Text(stringResource(R.string.self_pairing_expired))
                    OutlinedButton(onClick = actions.dismissSource) { Text(stringResource(R.string.self_pairing_done)) }
                }
                is SelfPairingSourceState.Blocked -> {
                    PairingError(source.code)
                    Button(onClick = actions.retrySource) { Text(stringResource(R.string.self_pairing_retry)) }
                    if (source.code == "SELF_PAIRING_SOURCE_CHANGED") OutlinedButton(onClick = actions.dismissSource) { Text(stringResource(R.string.self_pairing_done)) }
                }
                is SelfPairingSourceState.Ready -> {
                    val status = source.status
                    source.qrPayload?.let { payload ->
                        Text(stringResource(R.string.self_pairing_qr_hint))
                        Box(Modifier.widthIn(max = 304.dp).fillMaxWidth().background(Color.White).padding(12.dp)) {
                            InvitationQrCode(payload, Modifier.fillMaxWidth().aspectRatio(1f))
                        }
                    }
                    when (status.state) {
                        "CLAIMED" -> {
                            Text(requireNotNull(status.deviceName), style = MaterialTheme.typography.titleSmall)
                            ComparisonCode(requireNotNull(status.comparisonCode))
                            Button(onClick = { actions.decide("APPROVE", status.comparisonCode) }) { Text(stringResource(R.string.self_pairing_codes_match)) }
                            OutlinedButton(onClick = { actions.decide("REJECT", null) }) { Text(stringResource(R.string.self_pairing_reject)) }
                        }
                        "APPROVED" -> Text(stringResource(R.string.self_pairing_approved))
                        "EXCHANGED" -> Text(stringResource(R.string.self_pairing_source_complete))
                        "EXPIRED" -> Text(stringResource(R.string.self_pairing_expired))
                        "CANCELLED", "REJECTED" -> Text(stringResource(R.string.self_pairing_cancelled))
                    }
                    if (status.terminal) OutlinedButton(onClick = actions.dismissSource) { Text(stringResource(R.string.self_pairing_done)) }
                    else {
                        Expiry(status.expiresAt)
                        OutlinedButton(onClick = { actions.decide("CANCEL", null) }) { Text(stringResource(R.string.self_pairing_cancel)) }
                    }
                }
            }
            when (val recipient = state.recipient) {
                SelfPairingRecipientState.Idle -> if (state.canScan) {
                    Button(onClick = actions.scan) { Text(stringResource(R.string.self_pairing_scan)) }
                }
                SelfPairingRecipientState.Working -> Text(stringResource(R.string.self_pairing_working))
                is SelfPairingRecipientState.AwaitingTrust -> {
                    Text(stringResource(R.string.self_pairing_trust), style = MaterialTheme.typography.titleSmall)
                    Text(recipient.serverLabel)
                    Text(recipient.identity.apiOrigin)
                    Text(recipient.identity.thumbprint.chunked(8).joinToString(" "), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                    Button(onClick = actions.trust) { Text(stringResource(R.string.self_pairing_trust_confirm)) }
                    OutlinedButton(onClick = actions.dismissRecipient) { Text(stringResource(R.string.self_pairing_cancel)) }
                }
                is SelfPairingRecipientState.WaitingForApproval -> {
                    ComparisonCode(requireNotNull(recipient.status.comparisonCode))
                    Text(stringResource(R.string.self_pairing_wait_approval))
                    Expiry(recipient.status.expiresAt)
                }
                is SelfPairingRecipientState.AwaitingAccountConfirmation -> {
                    Text(stringResource(R.string.self_pairing_account, requireNotNull(recipient.status.accountLabel)), style = MaterialTheme.typography.titleSmall)
                    Text(stringResource(R.string.self_pairing_hint))
                    Button(onClick = { actions.confirmAccount(requireNotNull(recipient.status.accountId)) }) { Text(stringResource(R.string.self_pairing_account_confirm)) }
                }
                is SelfPairingRecipientState.Blocked -> {
                    PairingError(recipient.code)
                    Button(onClick = actions.retryRecipient) { Text(stringResource(R.string.self_pairing_retry)) }
                    if (!recipient.pending) OutlinedButton(onClick = actions.dismissRecipient) { Text(stringResource(R.string.self_pairing_done)) }
                }
                is SelfPairingRecipientState.Finished -> {
                    Text(stringResource(if (recipient.status == "EXPIRED") R.string.self_pairing_expired else R.string.self_pairing_cancelled))
                    OutlinedButton(onClick = actions.dismissRecipient) { Text(stringResource(R.string.self_pairing_done)) }
                }
                SelfPairingRecipientState.Connected -> {
                    Text(stringResource(R.string.self_pairing_complete))
                    OutlinedButton(onClick = actions.dismissRecipient) { Text(stringResource(R.string.self_pairing_done)) }
                }
            }
        }
    }
}

@Composable
private fun Expiry(instant: Instant) {
    val locale = LocalConfiguration.current.locales[0]
    val value = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT)
        .withLocale(locale).withZone(ZoneId.systemDefault()).format(instant)
    Text(stringResource(R.string.self_pairing_expires, value), style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun ComparisonCode(code: String) {
    Text(stringResource(R.string.self_pairing_compare))
    Text(code.chunked(4).joinToString(" "), style = MaterialTheme.typography.headlineMedium, fontFamily = FontFamily.Monospace)
}

@Composable
private fun PairingError(code: String) {
    val message = when (code) {
        "SELF_PAIRING_SOURCE_CHANGED" -> R.string.self_pairing_source_changed
        "FIRST_BIND_CEREMONY_BUSY", "SELF_PAIRING_ACTIVE_PROFILE_FORBIDDEN" -> R.string.self_pairing_busy
        "account_device_limit_reached" -> R.string.self_pairing_limit
        "capability_missing" -> R.string.self_pairing_unavailable
        "SELF_PAIRING_SOURCE_CANCEL_REQUIRED" -> R.string.self_pairing_cancel_other
        "SELF_PAIRING_KEY_LOST" -> R.string.self_pairing_key_lost
        else -> R.string.self_pairing_error
    }
    Text(stringResource(message), color = MaterialTheme.colorScheme.error)
}
