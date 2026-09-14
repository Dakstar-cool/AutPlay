package app.autplay.ui

import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import app.autplay.R
import app.autplay.application.importing.ImportReviewAction
import app.autplay.application.importing.ImportJobControlAction
import app.autplay.application.importing.ImportReviewItem
import app.autplay.data.local.entity.LocalImportJobEntity
import app.autplay.data.local.entity.LocalMatchCandidateEntity

internal data class LegacyImportRouteState(
    val job: LocalImportJobEntity?,
    val items: List<ImportReviewItem>,
    val selectedItem: ImportReviewItem?,
    val candidates: List<LocalMatchCandidateEntity>,
    val pendingControl: ImportJobControlAction? = null,
    val controlError: Boolean = false,
)

internal data class LegacyImportRouteActions(
    val chooseAudio: () -> Unit,
    val selectEntry: (String) -> Unit,
    val review: (ImportReviewAction, String?) -> Unit,
    val controlJob: (ImportJobControlAction) -> Unit = {},
)

internal fun allowedImportJobControlActions(state: String?): Set<ImportJobControlAction> = when (state) {
    "PENDING", "REVIEW_REQUIRED" -> setOf(ImportJobControlAction.PAUSE, ImportJobControlAction.CANCEL)
    "PAUSED" -> setOf(ImportJobControlAction.RESUME, ImportJobControlAction.CANCEL)
    else -> emptySet()
}

@Composable
internal fun LegacyImportRoute(
    state: LegacyImportRouteState,
    actions: LegacyImportRouteActions,
) {
    var confirmCancel by remember(state.job?.importJobId) { mutableStateOf(false) }
    if (confirmCancel) {
        AlertDialog(
            onDismissRequest = { confirmCancel = false },
            title = { Text(stringResource(R.string.import_cancel_title)) },
            text = {
                val count = state.job?.totalEntries ?: 0
                Text(pluralStringResource(R.plurals.import_cancel_confirm, count, count))
            },
            confirmButton = {
                TextButton(
                    modifier = Modifier.testTag("import-cancel-confirm"),
                    onClick = {
                        confirmCancel = false
                        actions.controlJob(ImportJobControlAction.CANCEL)
                    },
                ) { Text(stringResource(R.string.action_cancel)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmCancel = false }) { Text(stringResource(R.string.action_back)) }
            },
        )
    }
    Text(stringResource(R.string.import_intro))
    Button(onClick = actions.chooseAudio) { Text(stringResource(R.string.import_choose_audio)) }
    state.job?.let { job ->
        val allowedControls = allowedImportJobControlActions(job.state)
        Text(stringResource(R.string.import_summary, job.totalEntries))
        Text(stringResource(R.string.import_job_state, job.state.replace('_', ' ')))
        if (ImportJobControlAction.PAUSE in allowedControls) {
            Button(
                modifier = Modifier.testTag("import-pause"),
                enabled = state.pendingControl == null,
                onClick = { actions.controlJob(ImportJobControlAction.PAUSE) },
            ) { Text(stringResource(R.string.import_pause)) }
        }
        if (ImportJobControlAction.RESUME in allowedControls) {
            Button(
                modifier = Modifier.testTag("import-resume"),
                enabled = state.pendingControl == null,
                onClick = { actions.controlJob(ImportJobControlAction.RESUME) },
            ) { Text(stringResource(R.string.import_resume)) }
        }
        if (ImportJobControlAction.CANCEL in allowedControls) {
            Button(
                modifier = Modifier.testTag("import-cancel"),
                enabled = state.pendingControl == null,
                onClick = { confirmCancel = true },
            ) { Text(stringResource(R.string.import_cancel)) }
        }
        if (state.pendingControl != null) Text(stringResource(R.string.import_control_pending))
        if (state.controlError) Text(
            stringResource(R.string.import_control_error),
            modifier = Modifier.testTag("import-control-error"),
        )
        Text(stringResource(R.string.import_review_summary, job.reviewRequiredCount, job.resolvedCount, job.unresolvedCount, job.failedCount))
        state.items.forEach { item ->
            val entry = item.entry
            Text("${entry.rawTitle} — ${entry.rawArtist}")
            Text(importStatusLabel(item.effectiveState))
            Text(
                stringResource(
                    if (entry.persistedUriPermission) R.string.import_file_available else R.string.import_file_access_may_need_refresh,
                ),
            )
            if (item.effectiveState in setOf("REVIEW_REQUIRED", "INTEGRITY_CONFLICT", "DEFERRED_EVIDENCE", "NO_MATCH")) {
                Button(onClick = { actions.selectEntry(entry.importEntryId) }) { Text(stringResource(R.string.import_review_item)) }
            }
        }
    } ?: Text(stringResource(R.string.import_empty))
    state.selectedItem?.let { item ->
        val entry = item.entry
        Text(stringResource(R.string.import_review_title, entry.rawTitle, entry.rawArtist))
        if (item.effectiveState == "INTEGRITY_CONFLICT") {
            Text(stringResource(R.string.import_conflict))
        }
        state.candidates.forEach { candidate ->
            Text(stringResource(R.string.import_candidate, candidate.rank, candidate.titleSnapshot, candidate.artistSnapshot))
            if (candidate.hardConflictsJson != "[]") Text(stringResource(R.string.import_candidate_conflict))
            if (item.effectiveState == "REVIEW_REQUIRED") {
                Button(onClick = { actions.review(ImportReviewAction.ACCEPT, candidate.candidateId) }) {
                    Text(stringResource(R.string.import_accept_candidate, candidate.rank))
                }
                Button(onClick = { actions.review(ImportReviewAction.REJECT, candidate.candidateId) }) {
                    Text(stringResource(R.string.import_reject_candidate, candidate.rank))
                }
            }
        }
        Button(onClick = { actions.review(ImportReviewAction.KEEP_UNRESOLVED, null) }) {
            Text(stringResource(R.string.import_keep_unresolved))
        }
        if (item.effectiveState in setOf("REVIEW_REQUIRED", "NO_MATCH", "DEFERRED_EVIDENCE")) {
            Button(onClick = { actions.review(ImportReviewAction.CREATE_RECORDING, null) }) {
                Text(stringResource(R.string.import_create_new_track))
            }
        }
    }
}

@Composable
private fun importStatusLabel(status: String): String = stringResource(
    when (status) {
        "RESOLVED" -> R.string.import_status_ready
        "MANUAL_UNRESOLVED" -> R.string.import_status_kept_separate
        "INTEGRITY_CONFLICT" -> R.string.import_status_conflict
        else -> R.string.import_status_needs_review
    },
)
