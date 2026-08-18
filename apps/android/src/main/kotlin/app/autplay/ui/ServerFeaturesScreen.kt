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
import androidx.compose.ui.unit.dp
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

/** Online diagnostics/actions. The primary library and player remain Room/local-first. */
@Composable
fun ServerFeaturesScreen(
    isBound: Boolean,
    selectedTrackLabel: String?,
    selectedTrackUploadEligible: Boolean,
    state: ServerFeaturesUiState,
    onRefreshHealth: () -> Unit,
    onRefreshLibrary: () -> Unit,
    onSearch: (String) -> Unit,
    onChooseServerImport: () -> Unit,
    onRefreshImport: () -> Unit,
    onLoadNextImport: () -> Unit,
    onCancelImport: () -> Unit,
    onResumeImport: () -> Unit,
    onReviewImport: (RemoteImportEntry, String) -> Unit,
    onUploadSelectedTrack: () -> Unit,
    onCancelUpload: () -> Unit,
    onRecommendations: (Boolean) -> Unit,
    onExactReplay: () -> Unit,
    onAlgorithmicReplay: () -> Unit,
) {
    var searchText by remember { mutableStateOf("") }
    Text("Personal server", style = MaterialTheme.typography.titleLarge)
    Text(
        if (isBound) "Authenticated server profile active. Local playback and edits remain available offline."
        else "Bind a server profile before using online surfaces.",
    )
    state.busyAction?.let { Text("Working: $it") }
    state.stableMessage?.let { Text(it) }

    Section("Connection") {
        state.health?.let { health ->
            Text("API ${if (health.apiReady) "ready" else "unavailable"} · Stream ${if (health.streamLive) "live" else "unavailable"}")
        } ?: Text("Not checked")
        Button(enabled = isBound && state.busyAction == null, onClick = onRefreshHealth) {
            Text("Check API and stream")
        }
    }

    Section("Server snapshot") {
        val snapshot = state.library
        Text(
            snapshot?.let { "${it.entries.size} entries · ${it.playlists.size} playlists · ${it.history.size} listens" }
                ?: "Not loaded. This is an online reconciliation view, not the primary library.",
        )
        Button(enabled = isBound && state.busyAction == null, onClick = onRefreshLibrary) {
            Text("Load server snapshot")
        }
        OutlinedTextField(
            value = searchText,
            onValueChange = { searchText = it.take(200) },
            label = { Text("Search server IDs/status") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedButton(
            enabled = isBound && searchText.isNotBlank() && state.busyAction == null,
            onClick = { onSearch(searchText) },
        ) { Text("Search server") }
        state.searchResults.take(20).forEach { entry ->
            Text("${entry.availabilityStatus} · ${entry.userTrackRefId.take(8)}…")
        }
    }

    Section("Server import") {
        Text("Upload a user-owned CSV, JSON or HTML export (maximum 2 MiB).")
        Button(enabled = isBound && state.busyAction == null, onClick = onChooseServerImport) {
            Text("Choose export file")
        }
        state.importReport?.let { report ->
            Text("${report.state}: ${report.progressCurrent}/${report.progressTotal}")
            Text(report.counts.entries.joinToString(" · ") { "${it.key} ${it.value}" })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = state.busyAction == null, onClick = onRefreshImport) {
                    Text("Refresh")
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = onCancelImport) {
                    Text("Cancel")
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = onResumeImport) {
                    Text("Resume")
                }
            }
            if (report.nextAfter != null) {
                OutlinedButton(enabled = state.busyAction == null, onClick = onLoadNextImport) {
                    Text("Load next import rows")
                }
            }
            report.entries.forEach { entry ->
                Text("${entry.sourceRowKey} · ${entry.resolverState ?: entry.status} · candidates ${entry.candidateCount}")
                entry.errorCode?.let { Text("Error: $it") }
                if (
                    entry.decisionId != null &&
                    entry.resolverState in setOf("REVIEW_REQUIRED", "NO_MATCH", "DEFERRED_EVIDENCE")
                ) {
                    Text("Candidate evidence is not exposed by this server response; blind accept is disabled.")
                    OutlinedButton(
                        enabled = state.busyAction == null,
                        onClick = { onReviewImport(entry, "KEEP_UNRESOLVED") },
                    ) { Text("Keep unresolved") }
                    if (entry.resolverState in setOf("NO_MATCH", "DEFERRED_EVIDENCE")) {
                        OutlinedButton(
                            enabled = state.busyAction == null,
                            onClick = { onReviewImport(entry, "CREATE_RECORDING") },
                        ) { Text("Create Recording") }
                    }
                }
            }
        } ?: Text("No remote import selected")
    }

    Section("Vault") {
        Text(selectedTrackLabel ?: "Select a local track in Library first")
        Text(state.uploadStatus ?: "No active upload")
        Button(
            enabled = isBound && selectedTrackUploadEligible && state.busyAction == null,
            onClick = onUploadSelectedTrack,
        ) { Text("Offer selected track to Vault") }
        OutlinedButton(enabled = state.uploadStatus != null, onClick = onCancelUpload) {
            Text("Cancel upload")
        }
    }

    Section("Online recommendations") {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = isBound && state.busyAction == null, onClick = { onRecommendations(false) }) {
                Text("Recommendations")
            }
            OutlinedButton(enabled = isBound && state.busyAction == null, onClick = { onRecommendations(true) }) {
                Text("Online Home")
            }
        }
        state.recommendation?.let { result ->
            Text("${result.replay} · request ${result.requestId.take(8)}…")
            result.items.take(25).forEach { item ->
                Text("#${item.sourceRank} ${item.recordingId.take(8)}… · ${item.reasonCode}")
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = state.busyAction == null, onClick = onExactReplay) {
                    Text("Exact replay")
                }
                OutlinedButton(enabled = state.busyAction == null, onClick = onAlgorithmicReplay) {
                    Text("Algorithmic replay")
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
