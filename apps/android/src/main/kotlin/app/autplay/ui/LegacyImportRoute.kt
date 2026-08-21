package app.autplay.ui

import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import app.autplay.R
import app.autplay.application.importing.ImportReviewAction
import app.autplay.application.importing.ImportReviewItem
import app.autplay.data.local.entity.LocalImportJobEntity
import app.autplay.data.local.entity.LocalMatchCandidateEntity

internal data class LegacyImportRouteState(
    val job: LocalImportJobEntity?,
    val items: List<ImportReviewItem>,
    val selectedItem: ImportReviewItem?,
    val candidates: List<LocalMatchCandidateEntity>,
)

internal data class LegacyImportRouteActions(
    val chooseAudio: () -> Unit,
    val selectEntry: (String) -> Unit,
    val review: (ImportReviewAction, String?) -> Unit,
)

@Composable
internal fun LegacyImportRoute(
    state: LegacyImportRouteState,
    actions: LegacyImportRouteActions,
) {
    Text(stringResource(R.string.import_intro))
    Button(onClick = actions.chooseAudio) { Text(stringResource(R.string.import_choose_audio)) }
    state.job?.let { job ->
        Text(stringResource(R.string.import_summary, job.totalEntries))
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
