package app.autplay.application.wave

import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.foundation.layout.Column
import androidx.compose.ui.res.stringResource
import app.autplay.R
import app.autplay.domain.wave.WaveRuntimeState

/** Minimal observable surface. Host operations are explicit callbacks; membership/authorization stays server-owned. */
@Composable
fun WaveSurface(coordinator: WaveCoordinator, onHostPlay: () -> Unit, onHostPause: () -> Unit, onClose: () -> Unit) {
    val state by coordinator.uiState.collectAsState()
    Column {
        Text(stringResource(R.string.wave_title))
        if (state.state in setOf(WaveRuntimeState.DEGRADED, WaveRuntimeState.REJOINING)) Text(stringResource(R.string.wave_connection_problem_body))
        if (state.state == WaveRuntimeState.PREFLIGHT) Text(stringResource(R.string.wave_preparing_body))
        if (state.isHost) {
            Button(onClick = onHostPlay) { Text(stringResource(R.string.wave_start_playback)) }
            Button(onClick = onHostPause) { Text(stringResource(R.string.wave_pause_playback)) }
            Button(onClick = onClose) { Text(stringResource(R.string.wave_close_room)) }
        } else if (state.roomId != null) Text(stringResource(R.string.wave_host_controls))
    }
}
