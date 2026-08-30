package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
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
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.artist.ArtistSummary
import app.autplay.application.server.DiscoveryPolicyCommand
import app.autplay.application.server.RemoteDiscoveryCandidate
import app.autplay.application.server.RemoteDiscoveryPolicy
import app.autplay.application.server.RemoteDiscoveryRun
import app.autplay.application.server.RemoteDiscoverySnapshot

private val supportedDiscoveryModes = setOf("DISABLED", "MANUAL_ONLY", "SCHEDULED")
private val supportedImportModes = setOf("REVIEW_REQUIRED", "AUTO_IMPORT")

data class DiscoveryAutomationUiState(
    val snapshot: RemoteDiscoverySnapshot? = null,
    val selectedRunId: String? = null,
    val candidates: List<RemoteDiscoveryCandidate> = emptyList(),
    val pendingOperation: PendingDiscoveryOperation? = null,
)

data class PendingDiscoveryOperation(val key: String, val operationId: String)

data class DiscoveryAutomationActions(
    val refresh: () -> Unit = {},
    val savePolicy: (DiscoveryPolicyCommand) -> Unit = {},
    val runNow: (RemoteDiscoveryPolicy) -> Unit = {},
    val openRun: (RemoteDiscoveryRun) -> Unit = {},
    val candidateAction: (RemoteDiscoveryCandidate, String) -> Unit = { _, _ -> },
)

@Composable
fun DiscoveryAutomationPanel(
    isBound: Boolean,
    busy: Boolean,
    localArtists: List<ArtistSummary>,
    state: DiscoveryAutomationUiState,
    actions: DiscoveryAutomationActions,
) {
    var artistMenuOpen by remember { mutableStateOf(false) }
    var artistId by remember { mutableStateOf("") }
    var providerArtistId by remember { mutableStateOf("") }
    var discoveryMode by remember { mutableStateOf("MANUAL_ONLY") }
    var importMode by remember { mutableStateOf("REVIEW_REQUIRED") }
    var expectedRevision by remember { mutableStateOf<Int?>(null) }
    var confirmAutoImport by remember { mutableStateOf(false) }

    Text(stringResource(R.string.discovery_automation_body))
    if (state.pendingOperation != null) {
        Text(stringResource(R.string.discovery_automation_pending_resolution))
    }
    Button(enabled = isBound && !busy, onClick = actions.refresh) {
        Text(stringResource(R.string.action_refresh))
    }

    val policies = state.snapshot?.policies.orEmpty()
    val selectedPolicy = policies.firstOrNull { it.canonicalArtistId == artistId }
    val editorCanMutate = discoveryPolicyEditorCanMutate(
        isBound = isBound,
        snapshotLoaded = state.snapshot != null,
        busy = busy,
        selectedPolicy = selectedPolicy,
    )
    if (state.snapshot == null) {
        Text(stringResource(R.string.discovery_automation_not_loaded))
    } else if (policies.isEmpty()) {
        Text(stringResource(R.string.discovery_automation_no_policies))
    }
    policies.forEach { policy ->
        val artistName = localArtists.firstOrNull {
            it.key.artistId.value == policy.canonicalArtistId
        }?.name ?: stringResource(R.string.discovery_automation_unknown_artist)
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(artistName, style = MaterialTheme.typography.titleSmall)
            Text(
                stringResource(
                    R.string.discovery_automation_policy_summary,
                    discoveryModeLabel(policy.discoveryMode),
                    importModeLabel(policy.importMode),
                ),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    enabled = !busy && state.pendingOperation == null &&
                        discoveryPolicyModesAreSupported(policy),
                    onClick = {
                        artistId = policy.canonicalArtistId
                        providerArtistId = policy.providerArtistId
                        discoveryMode = policy.discoveryMode
                        importMode = policy.importMode
                        expectedRevision = policy.revision
                    },
                ) { Text(stringResource(R.string.action_edit)) }
                OutlinedButton(
                    enabled = !busy &&
                        policy.discoveryMode in setOf("MANUAL_ONLY", "SCHEDULED") &&
                        pendingAllows(state, discoveryRunOperationKey(policy.policyId)),
                    onClick = { actions.runNow(policy) },
                ) { Text(stringResource(R.string.discovery_automation_run_now)) }
            }
        }
    }

    Text(stringResource(R.string.discovery_automation_policy_editor), style = MaterialTheme.typography.titleSmall)
    Box {
        OutlinedButton(
            modifier = Modifier.testTag("discovery_artist_picker"),
            enabled = isBound && state.snapshot != null && !busy && localArtists.isNotEmpty(),
            onClick = { artistMenuOpen = true },
        ) {
            Text(
                localArtists.firstOrNull { it.key.artistId.value == artistId }?.name
                    ?: stringResource(R.string.discovery_automation_choose_artist),
            )
        }
        DropdownMenu(expanded = artistMenuOpen, onDismissRequest = { artistMenuOpen = false }) {
            localArtists.take(100).forEach { artist ->
                DropdownMenuItem(
                    modifier = Modifier.testTag("discovery_artist_${artist.key.artistId.value}"),
                    text = { Text(artist.name) },
                    onClick = {
                        artistId = artist.key.artistId.value
                        val existing = policies.firstOrNull {
                            it.canonicalArtistId == artistId
                        }
                        providerArtistId = existing?.providerArtistId.orEmpty()
                        discoveryMode = existing?.discoveryMode ?: "MANUAL_ONLY"
                        importMode = existing?.importMode ?: "REVIEW_REQUIRED"
                        expectedRevision = existing?.revision
                        artistMenuOpen = false
                    },
                )
            }
        }
    }
    OutlinedTextField(
        value = providerArtistId,
        onValueChange = { providerArtistId = it.filter(Char::isDigit).take(20) },
        enabled = editorCanMutate,
        label = { Text(stringResource(R.string.discovery_automation_provider_artist_id)) },
        supportingText = { Text(stringResource(R.string.discovery_automation_provider_artist_help)) },
        modifier = Modifier.fillMaxWidth(),
    )
    Text(stringResource(R.string.discovery_automation_discovery_mode))
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        listOf("DISABLED", "MANUAL_ONLY", "SCHEDULED").forEach { mode ->
            FilterChip(
                selected = discoveryMode == mode,
                enabled = editorCanMutate,
                onClick = { discoveryMode = mode },
                label = { Text(discoveryModeLabel(mode)) },
            )
        }
    }
    Text(stringResource(R.string.discovery_automation_import_mode))
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        listOf("REVIEW_REQUIRED", "AUTO_IMPORT").forEach { mode ->
            FilterChip(
                selected = importMode == mode,
                enabled = editorCanMutate,
                onClick = { importMode = mode },
                label = { Text(importModeLabel(mode)) },
            )
        }
    }
    val policyDraft = DiscoveryPolicyCommand(
        artistId,
        providerArtistId,
        discoveryMode,
        importMode,
        expectedRevision,
    )
    Button(
        enabled = editorCanMutate && artistId.isNotBlank() && providerArtistId.isNotBlank() &&
            discoveryMode in supportedDiscoveryModes &&
            importMode in supportedImportModes &&
            pendingAllows(state, discoveryPolicyOperationKey(policyDraft)),
        onClick = {
            if (importMode == "AUTO_IMPORT") confirmAutoImport = true
            else actions.savePolicy(policyDraft)
        },
    ) { Text(stringResource(R.string.action_save)) }

    state.snapshot?.runs.orEmpty().forEach { run ->
        Text(stringResource(R.string.discovery_automation_run_summary, run.state, run.observedCount, run.selectedCount))
        OutlinedButton(enabled = !busy, onClick = { actions.openRun(run) }) {
            Text(stringResource(R.string.discovery_automation_show_candidates))
        }
        if (run.errorCode != null) Text(stringResource(R.string.discovery_automation_run_failed))
    }
    if (state.selectedRunId != null && state.candidates.isEmpty()) {
        Text(stringResource(R.string.discovery_automation_no_candidates))
    }
    state.candidates.forEach { candidate ->
        CandidateRow(candidate, busy, state, actions)
    }

    if (confirmAutoImport) {
        AlertDialog(
            onDismissRequest = { confirmAutoImport = false },
            title = { Text(stringResource(R.string.discovery_automation_auto_import_title)) },
            text = { Text(stringResource(R.string.discovery_automation_auto_import_consequence)) },
            confirmButton = {
                Button(
                    enabled = editorCanMutate,
                    onClick = {
                        confirmAutoImport = false
                        actions.savePolicy(policyDraft)
                    },
                ) { Text(stringResource(R.string.discovery_automation_confirm_auto_import)) }
            },
            dismissButton = {
                OutlinedButton(onClick = { confirmAutoImport = false }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }
}

@Composable
private fun CandidateRow(
    candidate: RemoteDiscoveryCandidate,
    busy: Boolean,
    state: DiscoveryAutomationUiState,
    actions: DiscoveryAutomationActions,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
        Text(candidate.title, style = MaterialTheme.typography.titleSmall)
        Text(stringResource(R.string.discovery_automation_candidate_summary, candidate.artist, candidate.disposition))
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            OutlinedButton(
                enabled = !busy && pendingAllows(
                    state,
                    discoveryCandidateOperationKey(candidate.candidateId, "SELECT_CANDIDATE"),
                ),
                onClick = { actions.candidateAction(candidate, "SELECT_CANDIDATE") },
            ) { Text(stringResource(R.string.discovery_automation_select)) }
            OutlinedButton(
                enabled = !busy && pendingAllows(
                    state,
                    discoveryCandidateOperationKey(candidate.candidateId, "IGNORE_CANDIDATE"),
                ),
                onClick = { actions.candidateAction(candidate, "IGNORE_CANDIDATE") },
            ) { Text(stringResource(R.string.discovery_automation_ignore)) }
            if (candidate.acquisitionState == "FAILED") {
                OutlinedButton(
                    enabled = !busy && pendingAllows(
                        state,
                        discoveryCandidateOperationKey(candidate.candidateId, "RETRY_CANDIDATE"),
                    ),
                    onClick = { actions.candidateAction(candidate, "RETRY_CANDIDATE") },
                ) { Text(stringResource(R.string.action_retry)) }
            }
        }
    }
}

@Composable
private fun discoveryModeLabel(mode: String): String = when (mode) {
    "DISABLED" -> stringResource(R.string.discovery_automation_disabled)
    "SCHEDULED" -> stringResource(R.string.discovery_automation_scheduled)
    "MANUAL_ONLY" -> stringResource(R.string.discovery_automation_manual)
    else -> stringResource(R.string.discovery_automation_unsupported_mode, mode)
}

@Composable
private fun importModeLabel(mode: String): String = when (mode) {
    "AUTO_IMPORT" -> stringResource(R.string.discovery_automation_auto_import)
    "REVIEW_REQUIRED" -> stringResource(R.string.discovery_automation_review_required)
    else -> stringResource(R.string.discovery_automation_unsupported_mode, mode)
}

fun discoveryPolicyOperationKey(command: DiscoveryPolicyCommand): String = listOf(
    "SET_ARTIST_POLICY",
    command.canonicalArtistId,
    command.providerArtistId,
    command.discoveryMode,
    command.importMode,
    command.expectedRevision?.toString() ?: "null",
).joinToString("|")

fun discoveryRunOperationKey(policyId: String): String = "START_DISCOVERY|$policyId"

fun discoveryCandidateOperationKey(candidateId: String, action: String): String =
    "$action|$candidateId"

fun discoveryPolicyModesAreSupported(policy: RemoteDiscoveryPolicy): Boolean =
    policy.discoveryMode in supportedDiscoveryModes && policy.importMode in supportedImportModes

fun discoveryPolicyEditorCanMutate(
    isBound: Boolean,
    snapshotLoaded: Boolean,
    busy: Boolean,
    selectedPolicy: RemoteDiscoveryPolicy?,
): Boolean = isBound && snapshotLoaded && !busy &&
    (selectedPolicy == null || discoveryPolicyModesAreSupported(selectedPolicy))

private fun pendingAllows(state: DiscoveryAutomationUiState, key: String): Boolean =
    state.pendingOperation?.key in setOf(null, key)
