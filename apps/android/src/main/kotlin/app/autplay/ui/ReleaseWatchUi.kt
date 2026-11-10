package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.releasewatch.WatchReason
import app.autplay.application.releasewatch.ArtistAutoDownloadReason
import app.autplay.application.releasewatch.ArtistAutoDownloadStatus

public enum class ReleaseWatchUiStatus { Disconnected, Unavailable, Loading, Ready, Offline, Session, Error }

public data class WatchedArtistUiItem(
    val id: String,
    val name: String,
    val reason: WatchReason,
    val autoDownload: ArtistAutoDownloadStatus? = null,
)

public data class OwnerAutoDownloadUiState(
    val status: ReleaseWatchUiStatus = ReleaseWatchUiStatus.Unavailable,
    val enabled: Boolean = false,
    val executionEnabled: Boolean = false,
    val confirmationAvailable: Boolean = false,
    val busy: Boolean = false,
    val commandFailed: Boolean = false,
)

public data class ReleaseWatchUiState(
    val status: ReleaseWatchUiStatus = ReleaseWatchUiStatus.Unavailable,
    val artists: List<WatchedArtistUiItem> = emptyList(),
    val excludedArtistIds: Set<String> = emptySet(),
    val busyArtistId: String? = null,
    val autoDownload: OwnerAutoDownloadUiState = OwnerAutoDownloadUiState(),
) {
    public fun reasonFor(id: String): WatchReason? = artists.firstOrNull { it.id == id }?.reason
    public fun canEdit(): Boolean = status == ReleaseWatchUiStatus.Ready && busyArtistId == null
}

@Composable
internal fun OwnerAutoDownloadControls(
    state: OwnerAutoDownloadUiState,
    onSetEnabled: (Boolean) -> Unit,
    onRetry: () -> Unit,
) {
    var confirmEnable by remember(
        state.status,
        state.enabled,
        state.executionEnabled,
        state.confirmationAvailable,
    ) { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            stringResource(R.string.release_watch_auto_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() },
        )
        if (state.status == ReleaseWatchUiStatus.Ready) {
            val label = when {
                state.enabled && state.executionEnabled -> R.string.release_watch_auto_enabled
                state.enabled -> R.string.release_watch_auto_paused
                !state.executionEnabled -> R.string.release_watch_auto_gate_off
                else -> R.string.release_watch_auto_off
            }
            Text(stringResource(label), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            if (state.commandFailed) Text(stringResource(R.string.release_watch_auto_save_failed))
            if (state.enabled) {
                OutlinedButton(
                    onClick = { onSetEnabled(false) },
                    enabled = !state.busy,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(if (state.busy) R.string.release_watch_saving else R.string.release_watch_auto_disable)) }
            } else {
                OutlinedButton(
                    onClick = { confirmEnable = true },
                    enabled = state.executionEnabled && state.confirmationAvailable && !state.busy,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(R.string.release_watch_auto_enable)) }
                if (state.executionEnabled && !state.confirmationAvailable) {
                    Text(stringResource(R.string.release_watch_auto_confirmation_unavailable))
                }
            }
        } else {
            val message = when (state.status) {
                ReleaseWatchUiStatus.Disconnected -> R.string.release_watch_disconnected
                ReleaseWatchUiStatus.Unavailable -> R.string.release_watch_auto_unavailable
                ReleaseWatchUiStatus.Loading -> R.string.release_watch_auto_loading
                ReleaseWatchUiStatus.Offline -> R.string.release_watch_offline
                ReleaseWatchUiStatus.Session -> R.string.release_watch_session
                ReleaseWatchUiStatus.Error -> R.string.release_watch_auto_error
                ReleaseWatchUiStatus.Ready -> error("handled above")
            }
            Text(stringResource(message), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            if (state.status !in setOf(ReleaseWatchUiStatus.Loading, ReleaseWatchUiStatus.Disconnected)) {
                OutlinedButton(onClick = onRetry, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.action_retry))
                }
            }
        }
    }
    if (confirmEnable && state.status == ReleaseWatchUiStatus.Ready && !state.enabled &&
        state.executionEnabled && state.confirmationAvailable && !state.busy
    ) {
        AlertDialog(
            onDismissRequest = { confirmEnable = false },
            title = { Text(stringResource(R.string.release_watch_auto_confirm_title)) },
            text = { Text(stringResource(R.string.release_watch_auto_consequence)) },
            confirmButton = {
                Button(onClick = {
                    confirmEnable = false
                    onSetEnabled(true)
                }) { Text(stringResource(R.string.release_watch_auto_confirm)) }
            },
            dismissButton = {
                OutlinedButton(onClick = { confirmEnable = false }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }
}

@Composable
internal fun ArtistAutoDownloadMessage(value: ArtistAutoDownloadStatus?) {
    val message = when {
        value == null -> R.string.release_watch_artist_auto_unavailable
        value.reason == ArtistAutoDownloadReason.READY && value.effectiveOptIn && value.executionEnabled ->
            R.string.release_watch_artist_auto_ready
        value.reason == ArtistAutoDownloadReason.OWNER_CONSENT_OFF -> R.string.release_watch_artist_auto_owner_off
        value.reason == ArtistAutoDownloadReason.ARTIST_REVIEW_REQUIRED -> R.string.release_watch_artist_auto_review
        value.reason == ArtistAutoDownloadReason.WATCH_POLICY_PENDING -> R.string.release_watch_artist_auto_pending
        value.reason == ArtistAutoDownloadReason.STAGED_GATE_OFF -> R.string.release_watch_artist_auto_gate_off
        else -> R.string.release_watch_artist_auto_unavailable
    }
    Text(stringResource(message), color = AutPlayTokens.colors.mutedText)
}

@Composable
internal fun ArtistFollowAction(
    artistId: String,
    state: ReleaseWatchUiState,
    onSetFollow: (String, Boolean) -> Unit,
    showContext: Boolean = true,
) {
    val reason = state.reasonFor(artistId)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (showContext && state.status == ReleaseWatchUiStatus.Ready) {
            when {
                reason != null -> Text(stringResource(reason.labelResource()), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                artistId in state.excludedArtistIds -> Text(stringResource(R.string.release_watch_excluded))
            }
        }
        if (state.status == ReleaseWatchUiStatus.Ready) {
            OutlinedButton(
                onClick = { onSetFollow(artistId, reason == null) },
                enabled = state.canEdit(),
                modifier = Modifier.heightIn(min = 48.dp),
            ) {
                Text(stringResource(when {
                    state.busyArtistId == artistId -> R.string.release_watch_saving
                    reason == null -> R.string.release_watch_follow
                    else -> R.string.release_watch_unfollow
                }))
            }
        }
    }
}

@Composable
internal fun ArtistFollowMenu(
    artistId: String,
    state: ReleaseWatchUiState,
    onSetFollow: (String, Boolean) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val followed = state.reasonFor(artistId) != null
    Column {
        OutlinedButton(
            onClick = { expanded = true },
            enabled = state.canEdit(),
            modifier = Modifier.heightIn(min = 48.dp),
        ) { Text(stringResource(R.string.release_watch_actions)) }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text(stringResource(if (followed) R.string.release_watch_unfollow else R.string.release_watch_follow)) },
                onClick = {
                    expanded = false
                    onSetFollow(artistId, !followed)
                },
            )
        }
    }
}

@Composable
internal fun ReleaseWatchStatusMessage(state: ReleaseWatchUiState, onRetry: () -> Unit) {
    val label = when (state.status) {
        ReleaseWatchUiStatus.Disconnected -> R.string.release_watch_disconnected
        ReleaseWatchUiStatus.Unavailable -> R.string.release_watch_unavailable
        ReleaseWatchUiStatus.Loading -> R.string.release_watch_loading
        ReleaseWatchUiStatus.Offline -> R.string.release_watch_offline
        ReleaseWatchUiStatus.Session -> R.string.release_watch_session
        ReleaseWatchUiStatus.Error -> R.string.release_watch_error
        ReleaseWatchUiStatus.Ready -> return
    }
    Row(
        modifier = Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(stringResource(label), modifier = Modifier.weight(1f))
        if (state.status != ReleaseWatchUiStatus.Loading && state.status != ReleaseWatchUiStatus.Disconnected) {
            OutlinedButton(onClick = onRetry, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(stringResource(R.string.action_retry))
            }
        }
    }
}

internal fun WatchReason.labelResource(): Int = when (this) {
    WatchReason.MANUAL_FOLLOW -> R.string.release_watch_manual_reason
    WatchReason.LISTENING_HISTORY -> R.string.release_watch_history_reason
}
