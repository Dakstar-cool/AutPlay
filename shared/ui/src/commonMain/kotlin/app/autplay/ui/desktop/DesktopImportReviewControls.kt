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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopLibrarySnapshot

/** Confirmation names the frozen plan. Cancellation belongs to the captured host lifetime. */
public data class DesktopImportReviewCallbacks(
    val onConfirm: (String) -> Unit = {},
    val onCancel: () -> Unit = {},
    val isCurrent: (DesktopImportReviewState) -> Boolean = { true },
)

internal const val DESKTOP_IMPORT_PREVIEW_LIMIT: Int = 2_048
internal const val DESKTOP_IMPORT_PAGE_SIZE: Int = 100
private const val MAX_IMPORT_REPORT_COUNT: Int = 16_384
private const val MAX_IMPORT_FILE_BYTES: Long = 2L * 1024 * 1024 * 1024
private const val MAX_IMPORT_BATCH_BYTES: Long = 8L * 1024 * 1024 * 1024

internal fun desktopImportCount(value: Int): Int = value.coerceIn(0, MAX_IMPORT_REPORT_COUNT)

/** These codes describe skipped sources or bounded enumeration, with valid candidates retained. */
internal fun desktopImportPreparationWarning(code: String?): Boolean = when (code) {
    null, "import_invalid_selection", "import_source_unreadable", "import_access_denied", "import_source_not_regular",
    "import_symlink_rejected", "import_unsupported_format", "import_empty_file", "import_file_size_limit",
    "import_batch_size_limit", "import_file_limit", "import_entry_limit", "import_depth_limit" -> true
    else -> false
}

internal fun desktopImportPreviewValid(preview: DesktopImportPreview): Boolean =
    preview.id.isNotBlank() && preview.id.length <= 128 && desktopImportPreparationWarning(preview.errorCode) &&
        preview.failed in 0..MAX_IMPORT_REPORT_COUNT && preview.files.size in 1..DESKTOP_IMPORT_PREVIEW_LIMIT &&
        preview.files.all { it.id.isNotBlank() && it.id.length <= 128 && it.byteSize in 1..MAX_IMPORT_FILE_BYTES } &&
        preview.files.map { it.id }.toSet().size == preview.files.size &&
        preview.totalBytes in 1..MAX_IMPORT_BATCH_BYTES &&
        preview.totalBytes == preview.files.sumOf { it.byteSize }

internal data class DesktopImportPage(val index: Int, val pages: Int, val first: Int, val end: Int, val total: Int)

/** Never truncate a valid frozen list. Oversized foreign input is non-confirmable and disclosed. */
internal fun desktopImportPage(fileCount: Int, requestedPage: Int): DesktopImportPage {
    val total = fileCount.coerceAtLeast(0)
    val bounded = total.coerceAtMost(DESKTOP_IMPORT_PREVIEW_LIMIT)
    val pages = ((bounded + DESKTOP_IMPORT_PAGE_SIZE - 1) / DESKTOP_IMPORT_PAGE_SIZE).coerceAtLeast(1)
    val index = requestedPage.coerceIn(0, pages - 1)
    val first = index * DESKTOP_IMPORT_PAGE_SIZE
    return DesktopImportPage(index, pages, first, (first + DESKTOP_IMPORT_PAGE_SIZE).coerceAtMost(bounded), total)
}

/** Render plain labels only; foreign paths, controls, bidi overrides and malformed UTF-16 use a fallback. */
internal fun desktopImportSafeTitle(title: String?, language: DesktopLanguage): String {
    val safe = title?.takeIf { value ->
        value.length <= 512 && validDesktopTrackText(value, title = true) && value.none {
            it.isISOControl() || it == '/' || it == '\\' || it == ':' ||
                it in '\u202A'..'\u202E' || it in '\u2066'..'\u2069' ||
                it == '\u200E' || it == '\u200F' || it == '\u061C' || it == '\uFEFF'
        }
    }
    return safe ?: DesktopImportText.Unnamed.label(language)
}

internal fun desktopImportBytes(bytes: Long, language: DesktopLanguage): String =
    "${bytes.coerceIn(0, DESKTOP_IMPORT_PREVIEW_LIMIT * MAX_IMPORT_FILE_BYTES)} ${DesktopImportText.Bytes.label(language)}"

internal class DesktopImportReviewInput(
    val state: DesktopImportReviewState,
    val owner: DesktopImportReviewCallbacks,
    val library: DesktopLibrarySnapshot?,
    val section: DesktopSection,
    val navigationRevision: Long,
    val reopenRequired: Boolean,
    val hostBusy: Boolean = false,
) {
    fun matches(current: DesktopImportReviewInput): Boolean = state === current.state && owner === current.owner &&
        library === current.library && section == current.section && navigationRevision == current.navigationRevision &&
        reopenRequired == current.reopenRequired && hostBusy == current.hostBusy

    fun canConfirm(): Boolean = library != null && !reopenRequired && state.phase == DesktopImportPhase.REVIEWING &&
        state.preview?.job == null && state.preview?.let(::desktopImportPreviewValid) == true

    fun canCancel(): Boolean = library != null && state.phase in setOf(DesktopImportPhase.SELECTING,
        DesktopImportPhase.PREPARING, DesktopImportPhase.REVIEWING, DesktopImportPhase.IMPORTING)
}

/** One explicit action per phase/plan; replacement, disposal or stale navigation cannot revive it. */
internal class DesktopImportReviewAction {
    private var active: Boolean = true
    var submitted: Boolean = false
        private set

    fun retire(): Unit { active = false }

    fun confirm(captured: DesktopImportReviewInput, current: DesktopImportReviewInput, navigationCurrent: Boolean): Boolean {
        if (!active || submitted || !navigationCurrent || !captured.matches(current) ||
            !captured.owner.isCurrent(captured.state) || !current.canConfirm()) return false
        submitted = true
        captured.state.preview?.id?.let(captured.owner.onConfirm)
        return true
    }

    fun cancel(captured: DesktopImportReviewInput, current: DesktopImportReviewInput, navigationCurrent: Boolean): Boolean {
        if (!active || submitted || !navigationCurrent || !captured.matches(current) ||
            !captured.owner.isCurrent(captured.state) || !current.canCancel()) return false
        submitted = true
        captured.owner.onCancel()
        return true
    }
}

internal class DesktopImportReviewActions(
    val canConfirm: Boolean,
    val canCancel: Boolean,
    val confirm: () -> Unit,
    val cancel: () -> Unit,
    val isCurrent: () -> Boolean = { true },
)

@Composable
internal fun rememberDesktopImportReviewActions(input: DesktopImportReviewInput,
    isNavigationCurrent: () -> Boolean): DesktopImportReviewActions {
    val current by rememberUpdatedState(input)
    val action = remember(input.state.phase, DesktopImportReferenceKey(input.state.preview)) { DesktopImportReviewAction() }
    var submitted by remember(action) { mutableStateOf(false) }
    DisposableEffect(action) { onDispose { action.retire() } }
    return DesktopImportReviewActions(!submitted && input.canConfirm() && isNavigationCurrent() && input.owner.isCurrent(input.state),
        !submitted && input.canCancel() && isNavigationCurrent() && input.owner.isCurrent(input.state),
        confirm = { if (action.confirm(input, current, isNavigationCurrent())) submitted = true },
        cancel = { if (action.cancel(input, current, isNavigationCurrent())) submitted = true },
        isCurrent = { input.matches(current) && input.owner.isCurrent(input.state) && isNavigationCurrent() })
}

internal fun desktopImportPhaseText(phase: DesktopImportPhase): DesktopImportText? = when (phase) {
    DesktopImportPhase.IDLE -> null
    DesktopImportPhase.SELECTING -> DesktopImportText.Selecting
    DesktopImportPhase.PREPARING -> DesktopImportText.Preparing
    DesktopImportPhase.REVIEWING -> DesktopImportText.Review
    DesktopImportPhase.IMPORTING -> DesktopImportText.Importing
    DesktopImportPhase.CANCELLING -> DesktopImportText.Cancelling
}

internal fun desktopImportProgressSummary(progress: DesktopImportProgress, language: DesktopLanguage): String {
    val total = progress.totalFiles.coerceIn(0, DESKTOP_IMPORT_PREVIEW_LIMIT)
    val completed = progress.completedFiles.coerceIn(0, total)
    return "${DesktopImportText.Completed.label(language)}: $completed / $total | " +
        desktopImportSettledCounts(progress.imported, progress.duplicates, progress.failed, language, false)
}

private fun desktopImportSettledCounts(imported: Int, duplicates: Int, failed: Int,
    language: DesktopLanguage, cancelled: Boolean): String =
    "${(if (cancelled) DesktopImportText.SavedBeforeStop.label(language) else DesktopText.Imported.inLanguage(language))}: " +
        "${imported.coerceIn(0, DESKTOP_IMPORT_PREVIEW_LIMIT)} | ${DesktopText.Duplicates.inLanguage(language)}: " +
        "${duplicates.coerceIn(0, DESKTOP_IMPORT_PREVIEW_LIMIT)} | ${DesktopText.Failed.inLanguage(language)}: ${desktopImportCount(failed)}"

internal fun desktopImportWasCancelled(result: DesktopImportResult, errorCode: String?): Boolean =
    result.cancelled || result.errorCode == "IMPORT_CANCELLED" || errorCode == "IMPORT_CANCELLED"

internal fun desktopImportResultSummary(result: DesktopImportResult, errorCode: String?, language: DesktopLanguage): String {
    val cancelled = desktopImportWasCancelled(result, errorCode)
    val prefix = if (cancelled) DesktopText.ImportCancelled.inLanguage(language) + " " +
        DesktopImportText.LastReported.label(language) + ": " else ""
    val jobError = result.errorCode?.takeIf { it.startsWith("import_job_") }?.let {
        " | " + desktopImportErrorText(it).label(language)
    }.orEmpty()
    return prefix + desktopImportSettledCounts(result.imported, result.duplicates, result.failed, language, cancelled) + jobError
}

/** Stable codes only; unknown strings never reach the UI as diagnostic payloads. */
internal fun desktopImportErrorText(code: String): DesktopImportText = when (code) {
    "import_job_mapping_required", "import_job_mapping_invalid" -> DesktopImportText.JobMapping
    "import_job_conflict", "import_job_reselection_required" -> DesktopImportText.JobConflict
    "import_job_missing", "import_job_completed" -> DesktopImportText.JobUnavailable
    "import_job_history_unsupported" -> DesktopImportText.JobUnsupported
    "import_job_limit", "import_job_permit_limit", "import_job_invalid" -> DesktopImportText.JobLimit
    "import_job_reconciliation_required" -> DesktopImportText.JobReconciliation
    "import_job_reselection_unavailable" -> DesktopImportText.JobReselectionUnavailable
    "import_preview_expired", "import_source_changed" -> DesktopImportText.Changed
    "import_busy" -> DesktopImportText.Busy
    "import_file_limit", "import_entry_limit", "import_depth_limit", "import_file_size_limit",
    "import_batch_size_limit", "import_time_limit" -> DesktopImportText.Limit
    "import_source_unreadable", "import_access_denied", "import_source_not_regular", "import_symlink_rejected",
    "import_invalid_selection", "import_empty_file" -> DesktopImportText.SourceUnavailable
    "import_unsupported_format" -> DesktopImportText.Unsupported
    "import_insufficient_space" -> DesktopImportText.NoSpace
    "import_library_reopen_required", "desktop_snapshot_unavailable" -> DesktopImportText.Reopen
    else -> DesktopImportText.Failed
}

@Composable
internal fun DesktopImportReviewControls(state: DesktopImportReviewState, actions: DesktopImportReviewActions,
    reopenRequired: Boolean, language: DesktopLanguage, mappingReview: Boolean = false) {
    val phaseText = desktopImportPhaseText(state.phase) ?: return
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(phaseText.label(language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            if (state.phase == DesktopImportPhase.IMPORTING || state.phase == DesktopImportPhase.CANCELLING) {
                state.progress?.takeIf { it.previewId == state.preview?.id }?.let { progress ->
                    Text(desktopImportProgressSummary(progress, language))
                    progress.currentTitle?.let {
                        Text("${DesktopImportText.Current.label(language)}: ${desktopImportSafeTitle(it, language)}",
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                } ?: Text(DesktopImportText.Waiting.label(language))
            }
            if (state.phase == DesktopImportPhase.CANCELLING) Text(DesktopImportText.Retained.label(language))
            if (state.phase != DesktopImportPhase.CANCELLING) {
                OutlinedButton(onClick = actions.cancel, enabled = actions.canCancel) {
                    Text(DesktopText.CancelImport.inLanguage(language))
                }
            }
        }
    }
    if (state.phase != DesktopImportPhase.REVIEWING || mappingReview || state.preview?.job != null) return
    val preview = state.preview
    var requestedPage by remember(DesktopImportReferenceKey(preview)) { mutableStateOf(0) }
    var pageRevision by remember(DesktopImportReferenceKey(preview)) { mutableStateOf(0L) }
    val capturedRevision = pageRevision
    val page = desktopImportPage(preview?.files?.size ?: 0, requestedPage)
    val currentPreview by rememberUpdatedState(preview)
    val currentPage by rememberUpdatedState(page)
    val title = DesktopImportText.Review.label(language)
    AlertDialog(onDismissRequest = actions.cancel, modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (preview != null) {
                    Text("${DesktopImportText.Files.label(language)}: ${page.total}")
                    Text("${DesktopImportText.TotalBytes.label(language)}: ${desktopImportBytes(preview.totalBytes, language)}")
                    Text("${DesktopText.Failed.inLanguage(language)}: ${desktopImportCount(preview.failed)}")
                    Text(DesktopImportText.Frozen.label(language), style = MaterialTheme.typography.bodySmall)
                    preview.errorCode?.let { Text(desktopErrorMessage(it, language), color = MaterialTheme.colorScheme.error) }
                    if (!desktopImportPreviewValid(preview)) Text(DesktopImportText.Unavailable.label(language))
                    if (page.total > DESKTOP_IMPORT_PREVIEW_LIMIT) Text(DesktopImportText.Limit.label(language))
                    if (page.total > 0) {
                        Text("${DesktopImportText.Shown.label(language)}: ${page.first + 1}-${page.end} / ${page.total}")
                        LazyColumn(Modifier.fillMaxWidth().heightIn(max = 260.dp)) {
                            items(preview.files.subList(page.first, page.end)) { file -> ImportPreviewRow(file, language) }
                        }
                        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = {
                                if (actions.isCurrent() && currentPreview === preview && currentPage == page && pageRevision == capturedRevision) {
                                    pageRevision += 1
                                    requestedPage = page.index - 1
                                }
                            }, enabled = page.index > 0) { Text(DesktopImportText.Previous.label(language)) }
                            Text("${page.index + 1} / ${page.pages}")
                            OutlinedButton(onClick = {
                                if (actions.isCurrent() && currentPreview === preview && currentPage == page && pageRevision == capturedRevision) {
                                    pageRevision += 1
                                    requestedPage = page.index + 1
                                }
                            }, enabled = page.index + 1 < page.pages) { Text(DesktopImportText.Next.label(language)) }
                        }
                    }
                } else Text(DesktopImportText.Unavailable.label(language))
                if (reopenRequired) Text(DesktopImportText.Reopen.label(language), color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            TextButton(onClick = actions.confirm, enabled = actions.canConfirm) { Text(DesktopImportText.Confirm.label(language)) }
        },
        dismissButton = {
            TextButton(onClick = actions.cancel, enabled = actions.canCancel) { Text(DesktopImportText.Dismiss.label(language)) }
        })
}

@Composable
private fun ImportPreviewRow(file: DesktopImportPreviewFile, language: DesktopLanguage) {
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(desktopImportSafeTitle(file.title, language), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(desktopImportBytes(file.byteSize.coerceAtMost(MAX_IMPORT_FILE_BYTES), language), style = MaterialTheme.typography.bodySmall)
    }
}

internal enum class DesktopImportText(val russian: String, val english: String) {
    JobMapping("Назначьте непустой набор уникальных пар из этого предпросмотра.", "Assign a nonempty set of unique pairs from this preview."),
    JobConflict("История изменилась. Обновите список, выберите файлы заново и подтвердите новый предпросмотр.", "History changed. Refresh, select files again and confirm a new preview."),
    JobUnavailable("Импорт отсутствует или уже завершён. Обновите список.", "This import is missing or completed. Refresh the list."),
    JobUnsupported("История импорта не поддерживается и сохранена только для чтения.", "Import history is unsupported and retained read-only."),
    JobLimit("Достигнут лимит истории или попыток импорта. История и треки сохранены.", "An import history or attempt limit was reached. History and tracks are retained."),
    JobReconciliation("Результат записи требует сверки. Остановлено; обновите историю явно. Перед новым импортом переоткройте библиотеку. Сохранённые данные остаются.", "The write result needs reconciliation. Stopped; refresh history explicitly. Reopen before another import. Persisted data is retained."),
    JobReselectionUnavailable("Продолжение этого импорта сейчас недоступно.", "Reselection for this import is unavailable."),
    Selecting("\u0412\u044b\u0431\u043e\u0440 \u0444\u0430\u0439\u043b\u043e\u0432...", "Selecting files..."),
    Preparing("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043a\u0430 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0430...", "Preparing preview..."),
    Review("\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0438\u043c\u043f\u043e\u0440\u0442\u0430", "Review import"),
    Importing("\u0418\u043c\u043f\u043e\u0440\u0442...", "Importing..."),
    Cancelling("\u041e\u0442\u043c\u0435\u043d\u0430 \u0438\u043c\u043f\u043e\u0440\u0442\u0430...", "Cancelling import..."),
    Files("\u0412\u0441\u0435\u0433\u043e \u0444\u0430\u0439\u043b\u043e\u0432", "All files"),
    TotalBytes("\u041e\u0431\u0449\u0438\u0439 \u0440\u0430\u0437\u043c\u0435\u0440", "Total size"),
    Bytes("\u0431\u0430\u0439\u0442", "bytes"),
    Shown("\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e", "Shown"),
    Previous("\u041d\u0430\u0437\u0430\u0434", "Previous"),
    Next("\u0414\u0430\u043b\u0435\u0435", "Next"),
    Confirm("\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c \u0438\u043c\u043f\u043e\u0440\u0442", "Confirm import"),
    Dismiss("\u0417\u0430\u043a\u0440\u044b\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440", "Dismiss preview"),
    Frozen("\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435 \u043e\u0442\u043d\u043e\u0441\u0438\u0442\u0441\u044f \u043a\u043e \u0432\u0441\u0435\u043c\u0443 \u044d\u0442\u043e\u043c\u0443 \u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u043e\u043c\u0443 \u0441\u043f\u0438\u0441\u043a\u0443, \u0432\u043a\u043b\u044e\u0447\u0430\u044f \u0434\u0440\u0443\u0433\u0438\u0435 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u044b. \u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043d\u0430\u0447\u043d\u0451\u0442\u0441\u044f \u043f\u043e\u0441\u043b\u0435 \u043d\u0430\u0436\u0430\u0442\u0438\u044f \u043a\u043d\u043e\u043f\u043a\u0438 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f.", "Confirmation applies to this whole frozen list, including other pages. Copying starts after you press Confirm import."),
    Completed("\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u043e", "Completed"),
    Current("\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u0444\u0430\u0439\u043b", "Current file"),
    Waiting("\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0445\u043e\u0434\u0435 \u0438\u043c\u043f\u043e\u0440\u0442\u0430...", "Waiting for import progress..."),
    SavedBeforeStop("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e \u0434\u043e \u043e\u0442\u043c\u0435\u043d\u044b", "Saved before stopping"),
    LastReported("\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u043d\u044b\u0435 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b", "Last reported counts"),
    Retained("\u0423\u0436\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0435 \u0442\u0440\u0435\u043a\u0438 \u043e\u0441\u0442\u0430\u043d\u0443\u0442\u0441\u044f \u0432 \u0431\u0438\u0431\u043b\u0438\u043e\u0442\u0435\u043a\u0435.", "Tracks already saved will remain in the library."),
    Unnamed("\u0410\u0443\u0434\u0438\u043e\u0444\u0430\u0439\u043b", "Audio file"),
    Unavailable("\u0418\u043c\u043f\u043e\u0440\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d. \u0417\u0430\u043a\u0440\u043e\u0439\u0442\u0435 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0444\u0430\u0439\u043b\u044b \u0441\u043d\u043e\u0432\u0430.", "Import unavailable. Dismiss the preview and select files again."),
    Changed("\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0438\u043b\u0438 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0441\u044f. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0444\u0430\u0439\u043b\u044b \u0441\u043d\u043e\u0432\u0430.", "Source or preview changed. Select files again."),
    Busy("\u0414\u0440\u0443\u0433\u043e\u0439 \u0438\u043c\u043f\u043e\u0440\u0442 \u0435\u0449\u0451 \u043d\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d.", "Another import is still active."),
    Limit("\u0414\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442 \u043b\u0438\u043c\u0438\u0442 \u0438\u043c\u043f\u043e\u0440\u0442\u0430. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043c\u0435\u043d\u044c\u0448\u0435 \u0444\u0430\u0439\u043b\u043e\u0432 (\u043d\u0435 \u0431\u043e\u043b\u0435\u0435 2048).", "Import limit reached. Select fewer files (at most 2048)."),
    SourceUnavailable("\u041d\u0435\u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0438 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b \u0438\u043b\u0438 \u043d\u0435\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u044b \u0434\u043b\u044f \u0438\u043c\u043f\u043e\u0440\u0442\u0430.", "Some sources are unavailable or unsafe to import."),
    Unsupported("\u0424\u043e\u0440\u043c\u0430\u0442 \u0430\u0443\u0434\u0438\u043e \u043d\u0435 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f.", "Audio format is unsupported."),
    NoSpace("\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e\u0433\u043e \u043c\u0435\u0441\u0442\u0430 \u0434\u043b\u044f \u0438\u043c\u043f\u043e\u0440\u0442\u0430.", "Insufficient free space for import."),
    Reopen("\u041f\u0435\u0440\u0435\u043e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0431\u0438\u0431\u043b\u0438\u043e\u0442\u0435\u043a\u0443 \u043f\u0435\u0440\u0435\u0434 \u043d\u043e\u0432\u044b\u043c \u0438\u043c\u043f\u043e\u0440\u0442\u043e\u043c.", "Reopen the library before another import."),
    Failed("\u0418\u043c\u043f\u043e\u0440\u0442 \u043f\u0440\u0435\u0440\u0432\u0430\u043d \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439. \u0423\u0436\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0435 \u0442\u0440\u0435\u043a\u0438 \u043e\u0441\u0442\u0430\u044e\u0442\u0441\u044f \u0432 \u0431\u0438\u0431\u043b\u0438\u043e\u0442\u0435\u043a\u0435.", "Import encountered an error. Tracks already saved remain in the library.");

    fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.Russian -> russian
        DesktopLanguage.English -> english
    }
}
