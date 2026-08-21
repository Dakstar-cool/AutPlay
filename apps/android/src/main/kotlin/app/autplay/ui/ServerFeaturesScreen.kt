package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.server.RemoteImportEntry
import app.autplay.application.server.RemoteImportReport
import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.application.server.RemoteLibrarySnapshot
import app.autplay.application.server.ServerHealth
import app.autplay.application.server.ServerRecommendationResult

data class ServerFeaturesUiState(
    val busyAction: String? = null,
    val health: ServerHealth? = null,
    val library: RemoteLibrarySnapshot? = null,
    val searchResults: List<RemoteLibraryEntry> = emptyList(),
    val importReport: RemoteImportReport? = null,
    val recommendation: ServerRecommendationResult? = null,
    val uploadStatus: String? = null,
    val stableMessage: String? = null,
)

data class ServerFeaturesActions(
    val refreshHealth: () -> Unit,
    val refreshLibrary: () -> Unit,
    val search: (String) -> Unit,
    val chooseServerImport: () -> Unit,
    val refreshImport: () -> Unit,
    val loadNextImport: () -> Unit,
    val cancelImport: () -> Unit,
    val resumeImport: () -> Unit,
    val reviewImport: (RemoteImportEntry, String) -> Unit,
    val uploadSelectedTrack: () -> Unit,
    val cancelUpload: () -> Unit,
    val recommendations: (Boolean) -> Unit,
    val exactReplay: () -> Unit,
    val algorithmicReplay: () -> Unit,
)

/** Online diagnostics/actions. The primary library and player remain Room/local-first. */
@Composable
fun ServerFeaturesScreen(
    isBound: Boolean,
    selectedTrackLabel: String?,
    selectedTrackUploadEligible: Boolean,
    state: ServerFeaturesUiState,
    actions: ServerFeaturesActions,
) {
    var searchText by remember { mutableStateOf("") }
    Text(stringResource(R.string.server_title), style = MaterialTheme.typography.titleLarge)
    Text(
        stringResource(if (isBound) R.string.server_connected_body else R.string.server_not_connected_body),
    )
    state.busyAction?.let { Text(stringResource(R.string.action_in_progress)) }
    state.stableMessage?.let { Text(stringResource(R.string.action_failed_friendly)) }

    Section(stringResource(R.string.server_connection_section)) {
        state.health?.let { health ->
            Text(
                stringResource(
                    if (health.apiReady && health.streamLive) R.string.server_connection_ready
                    else R.string.server_connection_limited,
                ),
            )
        } ?: Text(stringResource(R.string.server_connection_not_checked))
        Button(enabled = isBound && state.busyAction == null, onClick = actions.refreshHealth) {
            Text(stringResource(R.string.server_check_connection))
        }
    }

    Section(stringResource(R.string.server_library_section)) {
        val snapshot = state.library
        Text(
            snapshot?.let {
                stringResource(R.string.server_library_summary, it.entries.size, it.playlists.size, it.history.size)
            } ?: stringResource(R.string.server_library_not_loaded),
        )
        Button(enabled = isBound && state.busyAction == null, onClick = actions.refreshLibrary) {
            Text(stringResource(R.string.server_library_refresh))
        }
        OutlinedTextField(
            value = searchText,
            onValueChange = { searchText = it.take(200) },
            label = { Text(stringResource(R.string.server_search_label)) },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedButton(
            enabled = isBound && searchText.isNotBlank() && state.busyAction == null,
            onClick = { actions.search(searchText) },
        ) { Text(stringResource(R.string.search_action)) }
        if (state.searchResults.isNotEmpty()) Text(stringResource(R.string.server_search_results, state.searchResults.size))
    }

    Section(stringResource(R.string.server_import_section)) {
        Text(stringResource(R.string.server_import_body))
        Button(enabled = isBound && state.busyAction == null, onClick = actions.chooseServerImport) {
            Text(stringResource(R.string.server_import_choose))
        }
        state.importReport?.let { report ->
            Text(stringResource(R.string.server_import_progress, report.progressCurrent, report.progressTotal))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.refreshImport) {
                    Text(stringResource(R.string.action_refresh))
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.cancelImport) {
                    Text(stringResource(R.string.action_cancel))
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.resumeImport) {
                    Text(stringResource(R.string.action_continue))
                }
            }
            if (report.nextAfter != null) {
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.loadNextImport) {
                    Text(stringResource(R.string.action_show_more))
                }
            }
            report.entries.forEachIndexed { index, entry ->
                Text(stringResource(R.string.server_import_item, index + 1))
                entry.errorCode?.let { Text(stringResource(R.string.server_import_item_failed)) }
                if (
                    entry.decisionId != null &&
                    entry.resolverState in setOf("REVIEW_REQUIRED", "NO_MATCH", "DEFERRED_EVIDENCE")
                ) {
                    Text(stringResource(R.string.server_import_needs_choice))
                    OutlinedButton(
                        enabled = state.busyAction == null,
                        onClick = { actions.reviewImport(entry, "KEEP_UNRESOLVED") },
                    ) { Text(stringResource(R.string.import_keep_unresolved)) }
                    if (entry.resolverState in setOf("NO_MATCH", "DEFERRED_EVIDENCE")) {
                        OutlinedButton(
                            enabled = state.busyAction == null,
                            onClick = { actions.reviewImport(entry, "CREATE_RECORDING") },
                        ) { Text(stringResource(R.string.import_create_new_track)) }
                    }
                }
            }
        } ?: Text(stringResource(R.string.server_import_empty))
    }

    Section(stringResource(R.string.server_storage_section)) {
        Text(selectedTrackLabel ?: stringResource(R.string.server_storage_select_track))
        Text(stringResource(if (state.uploadStatus == null) R.string.server_storage_idle else R.string.server_storage_in_progress))
        Button(
            enabled = isBound && selectedTrackUploadEligible && state.busyAction == null,
            onClick = actions.uploadSelectedTrack,
        ) { Text(stringResource(R.string.server_storage_upload)) }
        OutlinedButton(enabled = state.uploadStatus != null, onClick = actions.cancelUpload) {
            Text(stringResource(R.string.action_cancel))
        }
    }

    Section(stringResource(R.string.server_recommendations_section)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = isBound && state.busyAction == null, onClick = { actions.recommendations(false) }) {
                Text(stringResource(R.string.server_recommendations_load))
            }
            OutlinedButton(enabled = isBound && state.busyAction == null, onClick = { actions.recommendations(true) }) {
                Text(stringResource(R.string.server_recommendations_home))
            }
        }
        state.recommendation?.let { result ->
            Text(stringResource(R.string.server_recommendations_count, result.items.size))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.exactReplay) {
                    Text(stringResource(R.string.server_recommendations_repeat))
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = actions.algorithmicReplay) {
                    Text(stringResource(R.string.server_recommendations_refresh))
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(top = 20.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        content()
    }
}
