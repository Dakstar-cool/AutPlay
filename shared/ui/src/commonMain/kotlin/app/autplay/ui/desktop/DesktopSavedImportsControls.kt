package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportProgress

@Composable
internal fun DesktopSavedImportsControls(input: DesktopSavedImportsInput, progress: DesktopImportProgress?,
    phase: DesktopImportPhase, cancel: DesktopImportReviewActions, language: DesktopLanguage,
    isNavigationCurrent: () -> Boolean) {
    val current by rememberDesktopBrowseReference(input)
    val lifetime = remember { DesktopSavedImportsLifetime() }
    val revision = lifetime.observe(input)
    DisposableEffect(lifetime) { onDispose { lifetime.retire() } }
    val valid = desktopSavedImportsGuard(lifetime, revision, input, { current }, isNavigationCurrent)
    val validMapping = desktopSavedImportsGuard(lifetime, revision, input, { current }, isNavigationCurrent, mapping = true)
    val state = input.state
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.fillMaxWidth().heightIn(max = 280.dp).verticalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(onClick = { if (valid()) input.owner.onRefresh(state) }, enabled = input.available()) {
                Text((if (state.open) DesktopSavedImportText.Refresh else DesktopSavedImportText.Show).label(language))
            }
            progress?.takeIf { it.jobId != null }?.let { value ->
                Text(DesktopSavedImportText.LastReported.label(language), style = MaterialTheme.typography.bodySmall)
                value.jobPhase?.let { Text(desktopImportJobPhaseLabel(it).label(language)) }
                if (phase == DesktopImportPhase.IDLE) Text(desktopImportProgressSummary(value, language))
                value.errorCode?.let {
                    Text(desktopImportErrorText(it).label(language), color = MaterialTheme.colorScheme.error)
                    if (it == "import_job_reconciliation_required" || desktopTasteRequiresReopen(it)) {
                        Text(DesktopSavedImportText.Reconcile.label(language))
                    }
                }
            }
            if (state.open) {
                Text(DesktopSavedImportText.Window.label(language), style = MaterialTheme.typography.bodySmall)
                val status = when {
                    state.closed -> DesktopSavedImportText.Closed.label(language)
                    input.blocked -> DesktopImportText.Reopen.label(language)
                    state.loading -> DesktopSavedImportText.Loading.label(language)
                    state.errorCode != null -> DesktopSavedImportText.Failed.label(language)
                    !state.initialized -> DesktopSavedImportText.Start.label(language)
                    state.jobs.isEmpty() -> DesktopSavedImportText.Empty.label(language)
                    else -> null
                }
                status?.let { Text(it, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) }
                state.lastJob?.let { job ->
                    Text(DesktopSavedImportText.HistoryRead.label(language))
                    Text(desktopImportJobSummary(job, language), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(desktopImportJobPhaseLabel(job.phase).label(language))
                    Text(desktopImportJobCounts(job, language), style = MaterialTheme.typography.bodySmall)
                }
                if (!state.loading && state.initialized && state.errorCode == null) {
                    LazyColumn(Modifier.fillMaxWidth().heightIn(max = 180.dp)) {
                        items(state.jobs.take(16), key = { it.id }) { job ->
                            Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                                Text(desktopImportJobSummary(job, language), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text(desktopImportJobPhaseLabel(job.phase).label(language))
                                Text(desktopImportJobCounts(job, language), style = MaterialTheme.typography.bodySmall)
                                job.errorCode?.let { Text(desktopImportErrorText(it).label(language)) }
                                OutlinedButton(onClick = { if (valid() && input.canResume(job)) input.owner.onResume(state, job) },
                                    enabled = input.canResume(job)) { Text(DesktopSavedImportText.Resume.label(language)) }
                            }
                        }
                    }
                }
                if (state.next != null) Text(DesktopSavedImportText.More.label(language), style = MaterialTheme.typography.bodySmall)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { if (valid()) input.owner.onNext(state) },
                        enabled = input.available() && !state.loading && state.errorCode == null && state.next != null) {
                        Text(DesktopSavedImportText.Next.label(language))
                    }
                    if (state.errorCode == "desktop_import_jobs_load_failed") {
                        OutlinedButton(onClick = { if (valid()) input.owner.onRetry(state) }, enabled = input.available() && !state.loading) {
                            Text(DesktopSavedImportText.Retry.label(language))
                        }
                    }
                    TextButton(onClick = { if (valid()) input.owner.onDismiss(state) }, enabled = input.available()) {
                        Text(DesktopSavedImportText.Hide.label(language))
                    }
                }
            }
        }
    }
    if (phase == DesktopImportPhase.REVIEWING && input.mapping != null) {
        DesktopImportMappingControls(input, language, validMapping, cancel)
    }
}

@Composable
private fun DesktopImportMappingControls(input: DesktopSavedImportsInput, language: DesktopLanguage,
    valid: () -> Boolean, cancel: DesktopImportReviewActions) {
    val review = input.mapping ?: return
    val preview = review.preview
    var draft by remember(DesktopImportReferenceKey(preview)) { mutableStateOf(DesktopImportMappingDraft()) }
    val captured = draft
    val draftCurrent = desktopImportMappingDraftGuard(captured, { draft }, valid)
    fun change(update: (DesktopImportMappingDraft) -> DesktopImportMappingDraft) {
        if (draftCurrent()) draft = update(captured).copy(revision = captured.revision + 1)
    }
    val oldIds = desktopMappingCandidateIds(review, captured.candidateQuery)
    val newIds = desktopMappingFileIds(review, captured.fileQuery)
    val oldPage = desktopBrowsePage(oldIds.size, captured.candidatePage)
    val newPage = desktopBrowsePage(newIds.size, captured.filePage)
    val candidates = preview.job?.candidates.orEmpty().associateBy { it.candidate.id }
    val files = preview.files.associateBy { it.id }
    val candidateNumbers = preview.job?.candidates.orEmpty().mapIndexed { index, item -> item.candidate.id to index + 1 }.toMap()
    val fileNumbers = preview.files.mapIndexed { index, item -> item.id to index + 1 }.toMap()
    val mappings = review.mappings.associate { it.candidateId to it.selectionId }
    val title = DesktopSavedImportText.Mapping.label(language)
    AlertDialog(onDismissRequest = cancel.cancel, modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) },
        text = {
            Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(DesktopSavedImportText.Instructions.label(language))
                preview.job?.let { Text(desktopImportJobSummary(it, language), maxLines = 2, overflow = TextOverflow.Ellipsis) }
                preview.job?.let { Text(desktopImportJobCounts(it, language)) }
                preview.errorCode?.let { Text(desktopImportErrorText(it).label(language), color = MaterialTheme.colorScheme.error) }
                Text("${DesktopText.Failed.inLanguage(language)}: ${desktopImportCount(preview.failed)}")
                OutlinedTextField(captured.candidateQuery, onValueChange = { value ->
                    if (value.length <= 256) change { it.copy(candidateQuery = value, candidatePage = 0, candidateId = null) }
                }, singleLine = true, label = { Text(DesktopSavedImportText.SearchOld.label(language)) })
                DesktopBrowsePager(oldPage, DesktopSavedImportText.Old.label(language), language, input.available(),
                    onPage = { page -> change { it.copy(candidatePage = page, candidateId = null) } })
                LazyColumn(Modifier.fillMaxWidth().heightIn(min = 80.dp, max = 160.dp)) {
                    items(desktopBrowseSlice(oldIds, oldPage), key = { it }) { id ->
                        val candidate = candidates.getValue(id).candidate
                        val selected = mappings[id]?.let(files::get)
                        Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text("#${candidateNumbers.getValue(id)} ${desktopImportSafeTitle(candidate.title, language)} | ${desktopImportBytes(candidate.byteSize, language)}")
                            if (selected != null) {
                                Text("→ #${fileNumbers.getValue(selected.id)} ${desktopImportSafeTitle(selected.title, language)} | ${desktopImportBytes(selected.byteSize, language)}")
                                if (candidate.title != selected.title || candidate.byteSize != selected.byteSize) {
                                    Text(DesktopSavedImportText.Changed.label(language), color = MaterialTheme.colorScheme.error)
                                }
                                TextButton(onClick = { if (draftCurrent()) input.owner.onMap(review, id, null) },
                                    modifier = Modifier.semantics { contentDescription = "${DesktopSavedImportText.Remove.label(language)}: #${candidateNumbers.getValue(id)}" }) {
                                    Text(DesktopSavedImportText.Remove.label(language))
                                }
                            }
                            OutlinedButton(onClick = { change { it.copy(candidateId = id) } }, enabled = input.available(),
                                modifier = Modifier.semantics { contentDescription = "${DesktopSavedImportText.Old.label(language)}: " +
                                    "${desktopImportSafeTitle(candidate.title, language)}, ${desktopImportBytes(candidate.byteSize, language)}, #${candidateNumbers.getValue(id)}" }) {
                                Text((if (captured.candidateId == id) DesktopSavedImportText.Selected else DesktopSavedImportText.Choose).label(language))
                            }
                        }
                    }
                }
                OutlinedTextField(captured.fileQuery, onValueChange = { value ->
                    if (value.length <= 256) change { it.copy(fileQuery = value, filePage = 0, selectionId = null) }
                }, singleLine = true, label = { Text(DesktopSavedImportText.SearchNew.label(language)) })
                DesktopBrowsePager(newPage, DesktopSavedImportText.New.label(language), language, input.available(),
                    onPage = { page -> change { it.copy(filePage = page, selectionId = null) } })
                LazyColumn(Modifier.fillMaxWidth().heightIn(min = 80.dp, max = 160.dp)) {
                    items(desktopBrowseSlice(newIds, newPage), key = { it }) { id ->
                        val file = files.getValue(id)
                        Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text("#${fileNumbers.getValue(id)} ${desktopImportSafeTitle(file.title, language)} | ${desktopImportBytes(file.byteSize, language)}")
                            if (review.mappings.any { it.selectionId == id }) Text(DesktopSavedImportText.Mapped.label(language))
                            OutlinedButton(onClick = { change { it.copy(selectionId = id) } }, enabled = input.available(),
                                modifier = Modifier.semantics { contentDescription = "${DesktopSavedImportText.New.label(language)}: " +
                                    "${desktopImportSafeTitle(file.title, language)}, ${desktopImportBytes(file.byteSize, language)}, #${fileNumbers.getValue(id)}" }) {
                                Text((if (captured.selectionId == id) DesktopSavedImportText.Selected else DesktopSavedImportText.Choose).label(language))
                            }
                        }
                    }
                }
                OutlinedButton(onClick = {
                    if (draftCurrent() && desktopMappingPairAvailable(review, captured)) {
                        input.owner.onMap(review, checkNotNull(captured.candidateId), captured.selectionId)
                        draft = captured.copy(candidateId = null, selectionId = null, revision = captured.revision + 1)
                    }
                }, enabled = input.available() && desktopMappingPairAvailable(review, captured)) {
                    Text(DesktopSavedImportText.Assign.label(language))
                }
                Text("${DesktopSavedImportText.Mapped.label(language)}: ${review.mappings.size}")
                Text(DesktopSavedImportText.Frozen.label(language), style = MaterialTheme.typography.bodySmall)
                if (input.blocked) Text(DesktopImportText.Reopen.label(language), color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            TextButton(onClick = { if (draftCurrent() && input.canConfirm()) input.owner.onConfirm(review) },
                enabled = input.canConfirm()) { Text(DesktopSavedImportText.Confirm.label(language)) }
        },
        dismissButton = { TextButton(onClick = cancel.cancel, enabled = cancel.canCancel) { Text(DesktopSavedImportText.Cancel.label(language)) } })
}
