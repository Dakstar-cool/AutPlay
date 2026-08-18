package app.autplay.application.wave

import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.foundation.layout.Column
import app.autplay.domain.wave.WaveRuntimeState

/** Minimal observable surface. Host operations are explicit callbacks; membership/authorization stays server-owned. */
@Composable
fun WaveSurface(coordinator: WaveCoordinator, onHostPlay: () -> Unit, onHostPause: () -> Unit, onClose: () -> Unit) {
    val state by coordinator.uiState.collectAsState()
    Column {
        Text("Wave: ${state.state}")
        state.message?.let { message -> Text(text = message) }
        if (state.state in setOf(WaveRuntimeState.DEGRADED, WaveRuntimeState.REJOINING)) Text("Wave is degraded; local library and queue are unchanged.")
        if (state.state == WaveRuntimeState.PREFLIGHT) Text("Checking local/download/Vault availability before shared start.")
        if (state.isHost) {
            Button(onClick = onHostPlay) { Text("Wave play") }
            Button(onClick = onHostPause) { Text("Wave pause") }
            Button(onClick = onClose) { Text("Close Wave") }
        } else if (state.roomId != null) Text("Host controls this Wave. Transfer is server-authorized.")
    }
}
