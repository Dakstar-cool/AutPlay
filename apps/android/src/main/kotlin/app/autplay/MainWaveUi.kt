package app.autplay

import android.content.Intent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.application.guestroom.GUEST_DOCUMENT_MIME_TYPE
import app.autplay.application.guestroom.GuestRoomDocumentCodec
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.wave.WaveCoordinator
import app.autplay.application.wave.WaveUiState
import app.autplay.data.settings.NonSecretSettings
import app.autplay.domain.wave.WaveRuntimeState
import app.autplay.playback.presentation.WavePlaybackCommandOutcome
import app.autplay.ui.AutPlayDialog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import java.util.Locale
import java.util.UUID

internal data class WaveRoomActions(
    val create: (List<String>) -> Unit = {},
    val join: (String) -> Unit = {},
    val shareCode: () -> Unit = {},
    val inviteGuest: () -> Unit = {},
    val addTrack: (String) -> Unit = {},
    val start: () -> Unit = {},
    val pause: () -> Unit = {},
    val next: () -> Unit = {},
    val refresh: () -> Unit = {},
    val leave: () -> Unit = {},
    val close: () -> Unit = {},
    val openProfile: () -> Unit = {},
)

@Composable
internal fun WaveFrontendScreen(
    coordinator: WaveCoordinator?,
    isProfileBound: Boolean,
    localTrackRefId: String?,
    resolveServerRecordingId: suspend (String) -> String?,
    onStartPlayback: suspend () -> WavePlaybackCommandOutcome,
    onPausePlayback: suspend () -> WavePlaybackCommandOutcome,
    onError: (String) -> Unit,
    settings: NonSecretSettings,
    libraryEntries: List<CoreLibraryEntrySummary> = emptyList(),
    onOpenProfile: () -> Unit = {},
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val state by (coordinator?.uiState ?: flowOf(WaveUiState())).collectAsState(WaveUiState())
    var pending by remember(coordinator) { mutableStateOf(false) }
    var message by remember(coordinator, state.roomId) { mutableStateOf<Int?>(null) }
    val shareTitle = stringResource(R.string.wave_share_title)
    val shareText = state.roomCode?.let { stringResource(R.string.wave_share_text, it) }
    val guestShareTitle = stringResource(R.string.guest_share_title)
    fun launchAction(errorCode: String, operation: suspend () -> Unit) {
        if (pending) return
        pending = true
        message = null
        scope.launch {
            try { operation() }
            catch (error: CancellationException) { throw error }
            catch (_: Exception) { message = R.string.wave_room_action_failed; onError(errorCode) }
            finally { pending = false }
        }
    }
    WaveRoomScreen(
        state = state,
        available = isProfileBound && coordinator != null,
        pending = pending,
        message = message,
        libraryEntries = libraryEntries,
        selectedTrackRefId = localTrackRefId,
        guestInvitesAvailable = settings.serverBaseUrl != null && settings.m5Binding != null,
        actions = WaveRoomActions(
            create = { ids -> launchAction("WAVE_CREATE_UNAVAILABLE") { checkNotNull(coordinator).create(ids) } },
            join = { code -> launchAction("WAVE_JOIN_UNAVAILABLE") { checkNotNull(coordinator).joinByCode(code) } },
            shareCode = {
                launchAction("WAVE_SHARE_UNAVAILABLE") {
                    val share = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, shareText)
                    context.startActivity(Intent.createChooser(share, shareTitle))
                }
            },
            inviteGuest = {
                launchAction("GUEST_INVITATION_UNAVAILABLE") {
                    val document = checkNotNull(coordinator).issueGuestDocument().use { receipt ->
                        val binding = checkNotNull(settings.m5Binding)
                        GuestRoomDocumentCodec.encode(receipt, checkNotNull(settings.serverBaseUrl), binding.serverInstanceId, binding.identityEpoch)
                    }
                    val share = Intent(Intent.ACTION_SEND).setType(GUEST_DOCUMENT_MIME_TYPE).putExtra(Intent.EXTRA_TEXT, document)
                    context.startActivity(Intent.createChooser(share, guestShareTitle))
                }
            },
            addTrack = { id ->
                launchAction("WAVE_QUEUE_UNAVAILABLE") {
                    val recordingId = resolveServerRecordingId(id)
                    if (recordingId == null) { message = R.string.wave_room_track_needs_sync }
                    else checkNotNull(coordinator).enqueueRecording(recordingId)
                }
            },
            start = {
                launchAction("WAVE_START_UNAVAILABLE") {
                    message = when (onStartPlayback()) {
                        WavePlaybackCommandOutcome.Started -> R.string.wave_start_scheduled
                        WavePlaybackCommandOutcome.WaitingForReadyDevices -> R.string.wave_waiting_devices
                        else -> R.string.wave_room_action_failed
                    }
                }
            },
            pause = {
                launchAction("WAVE_PAUSE_UNAVAILABLE") {
                    message = if (onPausePlayback() == WavePlaybackCommandOutcome.Paused) R.string.wave_room_paused else R.string.wave_room_action_failed
                }
            },
            next = { launchAction("WAVE_SKIP_UNAVAILABLE") { checkNotNull(coordinator).prepareNextQueued(); message = R.string.wave_room_next_prepared } },
            refresh = { state.roomId?.let { id -> launchAction("WAVE_REFRESH_UNAVAILABLE") { checkNotNull(coordinator).recover(id) } } },
            leave = { launchAction("WAVE_LEAVE_UNAVAILABLE") { checkNotNull(coordinator).leave() } },
            close = { launchAction("WAVE_CLOSE_UNAVAILABLE") { checkNotNull(coordinator).closeRoom() } },
            openProfile = onOpenProfile,
        ),
        hostTransfer = {
            WaveHostTransferControls(state, {
                pending = true
                try { checkNotNull(coordinator).transferHost(it.deviceId) }
                finally { pending = false }
            }, onError, enabled = !pending)
        },
    )
}

@Composable
internal fun WaveRoomScreen(
    state: WaveUiState,
    available: Boolean,
    pending: Boolean = false,
    message: Int? = null,
    libraryEntries: List<CoreLibraryEntrySummary> = emptyList(),
    selectedTrackRefId: String? = null,
    guestInvitesAvailable: Boolean = false,
    actions: WaveRoomActions = WaveRoomActions(),
    hostTransfer: @Composable () -> Unit = {},
) {
    var inviteIds by remember { mutableStateOf("") }
    var roomCode by remember { mutableStateOf("") }
    var chooseTrack by remember(state.roomId) { mutableStateOf(false) }
    var confirmClose by remember(state.roomId) { mutableStateOf(false) }
    val ids = parseWaveInviteIds(inviteIds)
    Column(Modifier.fillMaxWidth().testTag("wave-room-screen"), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(stringResource(R.string.wave_intro), style = MaterialTheme.typography.bodyLarge)
        if (!available) {
            WaveRoomCard {
                Text(stringResource(R.string.wave_requires_server))
                Button(onClick = actions.openProfile, modifier = Modifier.fillMaxWidth().testTag("wave-connect")) {
                    Text(stringResource(R.string.wave_room_connect))
                }
            }
        } else if (state.roomId == null) {
            if (state.state == WaveRuntimeState.CLOSED) Text(stringResource(R.string.wave_state_closed))
            WaveRoomCard {
                Text(stringResource(R.string.wave_create_room), style = MaterialTheme.typography.titleLarge)
                Text(stringResource(R.string.wave_room_access_help))
                OutlinedTextField(
                    value = inviteIds, onValueChange = { inviteIds = it.take(300) },
                    label = { Text(stringResource(R.string.wave_invite_codes)) },
                    supportingText = { Text(stringResource(R.string.wave_room_invite_help)) },
                    isError = ids == null, enabled = !pending,
                    modifier = Modifier.fillMaxWidth().testTag("wave-invite-ids"),
                )
                Button(
                    onClick = { ids?.let(actions.create) }, enabled = !pending && ids != null,
                    modifier = Modifier.fillMaxWidth().testTag("wave-create"),
                ) { Text(stringResource(R.string.wave_create_room)) }
            }
            WaveRoomCard {
                Text(stringResource(R.string.wave_join_room), style = MaterialTheme.typography.titleLarge)
                OutlinedTextField(
                    value = roomCode, onValueChange = { roomCode = normalizeWaveRoomCode(it) },
                    label = { Text(stringResource(R.string.wave_room_code_label)) }, singleLine = true,
                    enabled = !pending, modifier = Modifier.fillMaxWidth().testTag("wave-code-input"),
                )
                Button(onClick = { actions.join(roomCode) }, enabled = !pending && isValidWaveRoomCode(roomCode),
                    modifier = Modifier.fillMaxWidth().testTag("wave-join")) { Text(stringResource(R.string.wave_join_room)) }
            }
        } else {
            WaveRoomCard {
                Text(if (state.message == "WAVE_PAUSED") stringResource(R.string.wave_room_paused) else waveRoomStateText(state.state), style = MaterialTheme.typography.titleLarge)
                Text(stringResource(if (state.isHost) R.string.wave_you_control else R.string.wave_host_controls))
                state.roomCode?.let { code ->
                    Text(stringResource(R.string.wave_room_code, code), modifier = Modifier.testTag("wave-room-code"))
                    OutlinedButton(onClick = actions.shareCode, enabled = !pending, modifier = Modifier.fillMaxWidth()) {
                        Text(stringResource(R.string.wave_share_code))
                    }
                }
                if (state.isHost && guestInvitesAvailable) {
                    OutlinedButton(onClick = actions.inviteGuest, enabled = !pending, modifier = Modifier.fillMaxWidth().testTag("wave-invite-guest")) {
                        Text(stringResource(R.string.guest_invite_without_account))
                    }
                }
                if (state.state in setOf(WaveRuntimeState.DEGRADED, WaveRuntimeState.REJOINING)) {
                    Text(stringResource(R.string.wave_connection_problem_body))
                    OutlinedButton(onClick = actions.refresh, enabled = !pending, modifier = Modifier.testTag("wave-refresh")) {
                        Text(stringResource(R.string.wave_room_retry))
                    }
                }
            }
            WaveRoomCard {
                Text(stringResource(R.string.wave_room_queue_count, state.queue.size), style = MaterialTheme.typography.titleLarge)
                if (state.queue.isEmpty()) Text(stringResource(R.string.wave_room_queue_empty))
                state.queue.forEachIndexed { index, item ->
                    Column(Modifier.fillMaxWidth().testTag("wave-queue-${item.queueEntryId}")) {
                        Text(item.title?.takeIf(String::isNotBlank) ?: stringResource(R.string.wave_room_track_number, index + 1), style = MaterialTheme.typography.titleMedium)
                        item.artist?.takeIf(String::isNotBlank)?.let { Text(it) }
                        if (item.queueEntryId == state.currentQueueEntryId) Text(stringResource(R.string.wave_room_current_track), style = MaterialTheme.typography.labelLarge)
                        Text(stringResource(if (!item.checked) R.string.wave_room_track_pending else if (item.available) R.string.wave_room_track_available else R.string.wave_room_track_unavailable), style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (state.isHost) {
                    OutlinedButton(onClick = { chooseTrack = true }, enabled = !pending && state.queue.size < 100,
                        modifier = Modifier.fillMaxWidth().testTag("wave-add-track")) { Text(stringResource(R.string.wave_room_choose_track)) }
                    Button(onClick = actions.start, enabled = !pending && state.queue.isNotEmpty(),
                        modifier = Modifier.fillMaxWidth().testTag("wave-start")) { Text(stringResource(R.string.wave_start_playback)) }
                    OutlinedButton(onClick = actions.pause, enabled = !pending && state.queue.isNotEmpty(),
                        modifier = Modifier.fillMaxWidth().testTag("wave-pause")) { Text(stringResource(R.string.wave_pause_playback)) }
                    val currentIndex = state.queue.indexOfFirst { it.queueEntryId == state.currentQueueEntryId }.coerceAtLeast(0)
                    OutlinedButton(onClick = actions.next, enabled = !pending && currentIndex + 1 < state.queue.size,
                        modifier = Modifier.fillMaxWidth().testTag("wave-next")) { Text(stringResource(R.string.wave_room_next_track)) }
                }
            }
            if (state.isHost) {
                hostTransfer()
                OutlinedButton(onClick = { confirmClose = true }, enabled = !pending,
                    modifier = Modifier.fillMaxWidth().testTag("wave-close")) { Text(stringResource(R.string.wave_close_room)) }
            } else {
                OutlinedButton(onClick = actions.leave, enabled = !pending,
                    modifier = Modifier.fillMaxWidth().testTag("wave-leave")) { Text(stringResource(R.string.wave_leave_room)) }
            }
        }
        if (pending) Text(stringResource(R.string.wave_room_working), modifier = Modifier.testTag("wave-pending"))
        message?.let { Text(stringResource(it), modifier = Modifier.testTag("wave-action-message")) }
    }
    if (confirmClose) AutPlayDialog(
        onDismissRequest = { confirmClose = false },
        title = { Text(stringResource(R.string.wave_close_room)) },
        text = { Text(stringResource(R.string.wave_room_confirm_close)) },
        confirmButton = { TextButton(onClick = { confirmClose = false; actions.close() }, modifier = Modifier.testTag("wave-close-confirm")) { Text(stringResource(R.string.wave_close_room)) } },
        dismissButton = { TextButton(onClick = { confirmClose = false }) { Text(stringResource(R.string.action_cancel)) } },
    )
    if (chooseTrack) WaveTrackPicker(
        entries = libraryEntries.filterNot { it.removed }.sortedByDescending { it.localUserTrackRefId == selectedTrackRefId },
        onDismiss = { chooseTrack = false },
        onChoose = { chooseTrack = false; actions.addTrack(it) },
    )
}

@Composable
private fun WaveRoomCard(content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }
    }
}

@Composable
private fun WaveTrackPicker(entries: List<CoreLibraryEntrySummary>, onDismiss: () -> Unit, onChoose: (String) -> Unit) {
    var query by remember { mutableStateOf("") }
    val matches = entries.filter { query.isBlank() || "${it.title.orEmpty()} ${it.artistName.orEmpty()}".contains(query, ignoreCase = true) }.take(100)
    AutPlayDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.wave_room_choose_track)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = query, onValueChange = { query = it.take(200) }, label = { Text(stringResource(R.string.nav_search)) }, modifier = Modifier.testTag("wave-track-search"))
                Column(Modifier.verticalScroll(rememberScrollState()).weight(1f, fill = false)) {
                    if (matches.isEmpty()) Text(stringResource(R.string.wave_room_library_empty))
                    matches.forEachIndexed { index, entry ->
                        TextButton(onClick = { onChoose(entry.localUserTrackRefId) }, modifier = Modifier.fillMaxWidth().testTag("wave-pick-track-$index")) {
                            Column(Modifier.fillMaxWidth()) {
                                Text(entry.title?.takeIf(String::isNotBlank) ?: stringResource(R.string.wave_room_track_number, index + 1))
                                entry.artistName?.takeIf(String::isNotBlank)?.let { Text(it) }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_cancel)) } },
    )
}

@Composable
private fun waveRoomStateText(state: WaveRuntimeState): String = stringResource(when (state) {
    WaveRuntimeState.IDLE -> R.string.wave_state_ready
    WaveRuntimeState.PREFLIGHT -> R.string.wave_state_preparing
    WaveRuntimeState.SCHEDULED -> R.string.wave_state_scheduled
    WaveRuntimeState.PLAYING -> R.string.wave_state_playing
    WaveRuntimeState.DEGRADED -> R.string.wave_state_connection_problem
    WaveRuntimeState.REJOINING -> R.string.wave_state_reconnecting
    WaveRuntimeState.CLOSED -> R.string.wave_state_closed
})

internal fun parseWaveInviteIds(value: String): List<String>? {
    val ids = value.split(Regex("[,;\\s]+")).filter(String::isNotBlank)
    if (ids.size > 7 || ids.any { !it.matches(Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) }) return null
    val normalized = ids.map { UUID.fromString(it).toString() }
    return normalized.takeIf { it.distinct().size == it.size }
}

internal fun normalizeWaveRoomCode(value: String): String = value.trim().uppercase(Locale.ROOT).filter { it in 'A'..'Z' || it in '0'..'9' }.take(10)
internal fun isValidWaveRoomCode(value: String): Boolean = value.matches(Regex("[0-9A-HJKMNP-TV-Z]{10}"))
