package app.autplay

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import java.text.DateFormat
import java.util.Date
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.application.library.CoreHomePlaylistSummary
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.sync.SyncStatus
import app.autplay.application.wave.WaveCoordinator
import app.autplay.data.settings.NonSecretSettings
import app.autplay.playback.presentation.WavePlaybackCommandOutcome
import app.autplay.ui.LegacyImportRoute
import app.autplay.ui.LegacyImportRouteActions
import app.autplay.ui.LegacyImportRouteState
import app.autplay.ui.AppLanguage
import app.autplay.ui.ServerFeaturesActions
import app.autplay.ui.ServerFeaturesScreen
import app.autplay.ui.ServerFeaturesUiState
import app.autplay.ui.UiDestination
import app.autplay.ui.profilepairing.ProfilePairingActions
import app.autplay.ui.profilepairing.ProfilePairingUiState

internal data class LegacySecondaryRouteState(
    val destination: UiDestination,
    val view: String,
    val playlists: List<CoreHomePlaylistSummary>,
    val libraryEntries: List<CoreLibraryEntrySummary>,
    val selectedTrackRefId: String?,
    val historyCount: Int,
    val importState: LegacyImportRouteState,
    val downloads: List<DownloadIntentPresentation>,
    val isProfileBound: Boolean,
    val syncStatus: SyncStatus,
    val waveCoordinator: WaveCoordinator?,
    val serverUiState: ServerFeaturesUiState,
    val selectedTrackLabel: String?,
    val selectedTrackUploadEligible: Boolean,
    val settings: NonSecretSettings,
    val profilePairing: ProfilePairingUiState,
    val stableError: String?,
)

internal data class LegacySecondaryRouteActions(
    val createPlaylist: () -> Unit,
    val addSelectedTrackToPlaylist: () -> Unit,
    val importActions: LegacyImportRouteActions,
    val downloadSelectedTrack: () -> Unit,
    val retrySync: () -> Unit,
    val resolveServerRecordingId: suspend (String) -> String?,
    val startWavePlayback: suspend () -> WavePlaybackCommandOutcome,
    val pauseWavePlayback: suspend () -> WavePlaybackCommandOutcome,
    val reportError: (String) -> Unit,
    val serverFeatures: ServerFeaturesActions,
    val openSettings: () -> Unit,
    val openSync: () -> Unit,
    val logout: () -> Unit,
    val logoutAll: () -> Unit,
    val revokeCurrentDevice: () -> Unit,
    val disconnectLocally: () -> Unit,
    val profilePairing: ProfilePairingActions,
    val updateSettings: ((NonSecretSettings) -> NonSecretSettings) -> Unit,
    val changeAppLanguage: (AppLanguage) -> Unit,
    val chooseLibraryRoot: () -> Unit,
    val rescanLibraryRoot: () -> Unit,
    val exportSettings: () -> Unit,
    val importSettings: () -> Unit,
    val navigate: (UiDestination) -> Unit,
)

@Composable
internal fun LegacySecondaryRouteRenderer(
    state: LegacySecondaryRouteState,
    actions: LegacySecondaryRouteActions,
    contentPadding: PaddingValues,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(contentPadding)
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text = stringResource(state.destination.labelRes), style = MaterialTheme.typography.headlineSmall)
        when (state.view) {
            "Playlists" -> {
                Text(stringResource(R.string.playlists_count, state.playlists.size))
                Button(onClick = actions.createPlaylist) { Text(stringResource(R.string.playlist_create)) }
                val playlist = state.playlists.firstOrNull()
                val entry = state.libraryEntries.firstOrNull {
                    it.localUserTrackRefId == state.selectedTrackRefId
                }
                if (playlist != null && entry != null) {
                    Button(onClick = actions.addSelectedTrackToPlaylist) {
                        Text(stringResource(R.string.playlist_add_selected))
                    }
                }
            }
            "History" -> Text(stringResource(R.string.history_count, state.historyCount))
            "Import" -> LegacyImportRoute(state.importState, actions.importActions)
            "Downloads" -> {
                Text(stringResource(R.string.downloads_count, state.downloads.size))
                state.downloads.firstOrNull()?.let { Text(downloadStateLabel(it.state)) }
                val canDownload = state.isProfileBound && state.libraryEntries.any {
                    it.localUserTrackRefId == state.selectedTrackRefId
                }
                Button(enabled = canDownload, onClick = actions.downloadSelectedTrack) {
                    Text(stringResource(R.string.download_selected_track))
                }
            }
            "Sync" -> {
                if (!state.isProfileBound) {
                    Text(stringResource(R.string.sync_requires_server))
                } else {
                    Text(stringResource(R.string.sync_pending_count, state.syncStatus.pending))
                    Text(stringResource(R.string.sync_attention_count, state.syncStatus.deadLetters + state.syncStatus.conflicts))
                    Text(
                        state.syncStatus.lastSuccessAtMs?.let {
                            stringResource(R.string.sync_last_success, DateFormat.getDateTimeInstance().format(Date(it)))
                        } ?: stringResource(R.string.sync_not_run_yet),
                    )
                    if (state.syncStatus.lastErrorCode != null) Text(stringResource(R.string.sync_last_attempt_failed))
                    Button(onClick = actions.retrySync) { Text(stringResource(R.string.sync_retry)) }
                }
            }
            "Wave" -> WaveFrontendScreen(
                coordinator = state.waveCoordinator,
                isProfileBound = state.isProfileBound,
                localTrackRefId = state.selectedTrackRefId,
                resolveServerRecordingId = actions.resolveServerRecordingId,
                onStartPlayback = actions.startWavePlayback,
                onPausePlayback = actions.pauseWavePlayback,
                onError = actions.reportError,
            )
            "Server" -> ServerFeaturesScreen(
                isBound = state.isProfileBound,
                selectedTrackLabel = state.selectedTrackLabel,
                selectedTrackUploadEligible = state.selectedTrackUploadEligible,
                state = state.serverUiState,
                actions = actions.serverFeatures,
            )
            "Profile" -> ProfileFrontendScreen(
                state = state.profilePairing,
                actions = actions.profilePairing,
            )
            "Settings" -> SettingsFrontendScreen(
                settings = state.settings,
                onUpdate = actions.updateSettings,
                onAppLanguageChange = actions.changeAppLanguage,
                onChooseLibraryRoot = actions.chooseLibraryRoot,
                onRescanLibraryRoot = actions.rescanLibraryRoot,
                onExportSettings = actions.exportSettings,
                onImportSettings = actions.importSettings,
                onNavigate = actions.navigate,
            )
            else -> Text(stringResource(R.string.state_unavailable_body))
        }
        state.stableError?.let { Text(stringResource(R.string.action_failed_friendly)) }
    }
}

@Composable
private fun downloadStateLabel(state: String): String = stringResource(
    when (state) {
        "COMPLETED" -> R.string.download_state_completed
        "FAILED" -> R.string.download_state_failed
        "CANCELLED" -> R.string.download_state_cancelled
        else -> R.string.download_state_in_progress
    },
)
