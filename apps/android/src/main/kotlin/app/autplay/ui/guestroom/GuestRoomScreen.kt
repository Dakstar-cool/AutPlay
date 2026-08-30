package app.autplay.ui.guestroom

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.guestroom.GuestRoomRuntime
import app.autplay.application.guestroom.GuestRoomRuntimeState
import app.autplay.application.guestroom.GuestRoomStage
import app.autplay.application.wave.WaveUiState
import app.autplay.domain.wave.WaveRuntimeState
import app.autplay.ui.AutPlayCard
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens
import java.text.DateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch

internal data class GuestRoomScreenActions(
    val join: (String) -> Unit = {},
    val cancel: () -> Unit = {},
    val leave: () -> Unit = {},
)

/** Process-scoped route: guest authority stays in [GuestRoomRuntime], never in Compose state. */
@Composable
internal fun GuestRoomFrontendScreen(runtime: GuestRoomRuntime) {
    val scope = rememberCoroutineScope()
    val state by runtime.state.collectAsState()
    val coordinator = runtime.activeCoordinator()
    val idleWaveState = remember { flowOf(WaveUiState()) }
    val waveState by (coordinator?.uiState ?: idleWaveState).collectAsState(
        initial = WaveUiState(),
    )
    val dismiss: () -> Unit = {
        scope.launch {
            if (state.stage == GuestRoomStage.ACTIVE) {
                runCatching { runtime.leave() }
            } else {
                runtime.cancel()
            }
        }
    }
    BackHandler(onBack = dismiss)
    GuestRoomScreen(
        state = state,
        waveState = waveState.state,
        actions = GuestRoomScreenActions(
            join = { name -> scope.launch { runCatching { runtime.redeem(name) } } },
            cancel = dismiss,
            leave = dismiss,
        ),
    )
}

@Composable
internal fun GuestRoomScreen(
    state: GuestRoomRuntimeState,
    waveState: WaveRuntimeState = WaveRuntimeState.IDLE,
    actions: GuestRoomScreenActions = GuestRoomScreenActions(),
) {
    var displayName by remember(state.roomId, state.expiresAtMs) { mutableStateOf("") }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .padding(horizontal = 20.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 680.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            GuestRoomHero()
            GuestRoomFacts(state)
            when (state.stage) {
                GuestRoomStage.DOCUMENT_READY -> GuestJoinCard(
                    displayName = displayName,
                    onDisplayNameChange = { displayName = it.take(40) },
                    onJoin = { actions.join(displayName.trim()) },
                    onCancel = actions.cancel,
                )
                GuestRoomStage.ERROR -> if (state.roomId == null) {
                    GuestTerminalCard(
                        message = guestStateMessage(state.errorCode),
                        onReturn = actions.cancel,
                    )
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        AutPlayStateSurface(
                            kind = AutPlayStateKind.Error,
                            message = guestStateMessage(state.errorCode),
                        )
                        GuestJoinCard(
                            displayName = displayName,
                            onDisplayNameChange = { displayName = it.take(40) },
                            onJoin = { actions.join(displayName.trim()) },
                            onCancel = actions.cancel,
                        )
                    }
                }
                GuestRoomStage.REDEEMING -> GuestJoiningCard(actions.cancel)
                GuestRoomStage.ACTIVE -> GuestActiveCard(
                    displayName = state.displayName.orEmpty(),
                    waveState = waveState,
                    onLeave = actions.leave,
                )
                GuestRoomStage.TERMINAL -> GuestTerminalCard(
                    message = state.errorCode?.let { guestStateMessage(it) }
                        ?: stringResource(R.string.guest_access_ended),
                    onReturn = actions.cancel,
                )
                GuestRoomStage.IDLE -> Unit
            }
        }
    }
}

@Composable
private fun GuestRoomHero() {
    Surface(
        modifier = Modifier.size(72.dp),
        shape = MaterialTheme.shapes.extraLarge,
        color = MaterialTheme.colorScheme.primaryContainer,
    ) {
        Box(contentAlignment = Alignment.Center) {
            AutPlayPlatformIcon(
                icon = AutPlayIcon.Wave,
                contentDescription = null,
                modifier = Modifier.size(34.dp),
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }
    }
    Text(
        text = stringResource(R.string.guest_room_title),
        style = MaterialTheme.typography.headlineMedium,
        textAlign = TextAlign.Center,
    )
    Text(
        text = stringResource(R.string.guest_room_subtitle),
        style = MaterialTheme.typography.bodyLarge,
        color = AutPlayTokens.colors.mutedText,
        textAlign = TextAlign.Center,
    )
}

@Composable
private fun GuestRoomFacts(state: GuestRoomRuntimeState) {
    if (state.roomId == null && state.expiresAtMs == null) return
    val locale = LocalConfiguration.current.locales[0]
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            state.roomId?.let {
                GuestFactRow(
                    stringResource(R.string.guest_room_label),
                    stringResource(R.string.guest_room_identifier, it.take(8)),
                )
            }
            state.expiresAtMs?.let {
                GuestFactRow(
                    stringResource(R.string.guest_access_until_label),
                    formatGuestExpiry(it, locale),
                )
            }
        }
    }
}

internal fun formatGuestExpiry(expiresAtMs: Long, locale: Locale): String =
    DateFormat.getDateTimeInstance(
        DateFormat.MEDIUM,
        DateFormat.SHORT,
        locale,
    ).format(Date(expiresAtMs))

@Composable
private fun GuestFactRow(label: String, value: String) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(label, color = AutPlayTokens.colors.mutedText)
        Text(value, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun GuestJoinCard(
    displayName: String,
    onDisplayNameChange: (String) -> Unit,
    onJoin: () -> Unit,
    onCancel: () -> Unit,
) {
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(
                stringResource(R.string.guest_invitation_ready),
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                stringResource(R.string.guest_media_boundary),
                color = AutPlayTokens.colors.mutedText,
            )
            OutlinedTextField(
                value = displayName,
                onValueChange = onDisplayNameChange,
                modifier = Modifier.fillMaxWidth().testTag("guest-display-name"),
                label = { Text(stringResource(R.string.guest_display_name)) },
                supportingText = { Text(stringResource(R.string.guest_display_name_support)) },
                singleLine = true,
            )
            Button(
                enabled = displayName.trim().isNotEmpty(),
                onClick = onJoin,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) { Text(stringResource(R.string.guest_join)) }
            OutlinedButton(
                onClick = onCancel,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) { Text(stringResource(R.string.action_cancel)) }
        }
    }
}

@Composable
private fun GuestJoiningCard(onCancel: () -> Unit) {
    AutPlayCard {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            CircularProgressIndicator()
            Text(
                stringResource(R.string.guest_joining),
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                stringResource(R.string.guest_joining_detail),
                color = AutPlayTokens.colors.mutedText,
                textAlign = TextAlign.Center,
            )
            OutlinedButton(onClick = onCancel, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(stringResource(R.string.action_cancel))
            }
        }
    }
}

@Composable
private fun GuestActiveCard(
    displayName: String,
    waveState: WaveRuntimeState,
    onLeave: () -> Unit,
) {
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                stringResource(R.string.guest_active_title, displayName),
                style = MaterialTheme.typography.titleLarge,
            )
            Surface(
                shape = MaterialTheme.shapes.small,
                color = MaterialTheme.colorScheme.primaryContainer,
            ) {
                Text(
                    guestWaveStateLabel(waveState),
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    style = MaterialTheme.typography.labelLarge,
                )
            }
            Text(
                stringResource(R.string.guest_host_controls_playback),
                color = AutPlayTokens.colors.mutedText,
            )
            Text(
                stringResource(R.string.guest_media_boundary),
                color = AutPlayTokens.colors.mutedText,
            )
            OutlinedButton(
                onClick = onLeave,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) { Text(stringResource(R.string.wave_leave_room)) }
        }
    }
}

@Composable
private fun GuestTerminalCard(message: String, onReturn: () -> Unit) {
    AutPlayStateSurface(
        kind = AutPlayStateKind.Error,
        message = message,
        actionLabel = stringResource(R.string.guest_return_to_app),
        onAction = onReturn,
    )
}

@Composable
private fun guestStateMessage(code: String?): String = stringResource(
    when (code) {
        "guest_expired" -> R.string.guest_state_expired
        "guest_revoked" -> R.string.guest_state_revoked
        "room_full" -> R.string.guest_state_full
        "guest_document_invalid" -> R.string.guest_state_invalid_document
        else -> R.string.guest_state_unavailable
    },
)

@Composable
private fun guestWaveStateLabel(state: WaveRuntimeState): String = stringResource(
    when (state) {
        WaveRuntimeState.IDLE -> R.string.wave_state_ready
        WaveRuntimeState.PREFLIGHT -> R.string.wave_state_preparing
        WaveRuntimeState.SCHEDULED -> R.string.wave_state_scheduled
        WaveRuntimeState.PLAYING -> R.string.wave_state_playing
        WaveRuntimeState.DEGRADED -> R.string.wave_state_connection_problem
        WaveRuntimeState.REJOINING -> R.string.wave_state_reconnecting
        WaveRuntimeState.CLOSED -> R.string.wave_state_closed
    },
)
