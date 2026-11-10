package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportMappingReview
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopSavedImportsState
import app.autplay.application.desktop.desktopImportCandidateEligible
import app.autplay.application.desktop.desktopImportMappingValid

public data class DesktopSavedImportsCallbacks(
    val onRefresh: (DesktopSavedImportsState) -> Unit = {},
    val onNext: (DesktopSavedImportsState) -> Unit = {},
    val onRetry: (DesktopSavedImportsState) -> Unit = {},
    val onDismiss: (DesktopSavedImportsState) -> Unit = {},
    val onResume: (DesktopSavedImportsState, DesktopImportJob) -> Unit = { _, _ -> },
    val onMap: (DesktopImportMappingReview, String, String?) -> Unit = { _, _, _ -> },
    val onConfirm: (DesktopImportMappingReview) -> Unit = {},
    /** Live host reference checks also fence changes that precede Compose recomposition. */
    val isCurrent: (DesktopSavedImportsState) -> Boolean = { false },
    val isMappingCurrent: (DesktopImportMappingReview) -> Boolean = { false },
)

internal class DesktopImportReferenceKey(private val value: Any?) {
    override fun equals(other: Any?): Boolean = other is DesktopImportReferenceKey && value === other.value
    override fun hashCode(): Int = 0
}

internal class DesktopSavedImportsInput(
    val state: DesktopSavedImportsState,
    val mapping: DesktopImportMappingReview?,
    val owner: DesktopSavedImportsCallbacks,
    val library: DesktopLibrarySnapshot?,
    val section: DesktopSection,
    val navigationRevision: Long,
    val selectionRevision: Long,
    val blocked: Boolean,
    val importing: Boolean,
) {
    fun matches(other: DesktopSavedImportsInput): Boolean = state === other.state && mapping === other.mapping &&
        owner === other.owner && library === other.library && section == other.section &&
        navigationRevision == other.navigationRevision && selectionRevision == other.selectionRevision &&
        blocked == other.blocked && importing == other.importing

    fun available(): Boolean = library != null && !blocked && !state.closed
    fun canResume(job: DesktopImportJob): Boolean = available() && !importing && !state.loading && state.errorCode == null &&
        state.open && state.jobs.any { it === job } && job.revision != null &&
        job.phase != DesktopImportJobPhase.UNSUPPORTED && job.phase != DesktopImportJobPhase.COMPLETED &&
        job.candidates.any(::desktopImportCandidateEligible)
    fun canConfirm(): Boolean = available() && mapping?.let {
        desktopImportPreviewValid(it.preview) && desktopImportMappingValid(it)
    } == true
}

/** Observation increments even across A-B-A inputs. Disposal permanently retires old closures. */
internal class DesktopSavedImportsLifetime {
    private var previous: DesktopSavedImportsInput? = null
    private var revision: Long = 0
    private var active: Boolean = true
    fun observe(input: DesktopSavedImportsInput): Long {
        if (previous?.matches(input) != true) { previous = input; revision += 1 }
        return revision
    }
    fun matches(captured: Long): Boolean = active && revision == captured
    fun retire(): Unit { active = false }
}

internal fun desktopSavedImportsGuard(lifetime: DesktopSavedImportsLifetime, revision: Long,
    captured: DesktopSavedImportsInput, latest: () -> DesktopSavedImportsInput, navigation: () -> Boolean,
    mapping: Boolean = false): () -> Boolean = {
    lifetime.matches(revision) && navigation() && captured.matches(latest()) && captured.available() &&
        (if (mapping) captured.mapping?.let(captured.owner.isMappingCurrent) == true else captured.owner.isCurrent(captured.state))
}

/** Query, page and selection share one monotonic draft revision; edits invalidate retained closures immediately. */
internal data class DesktopImportMappingDraft(
    val candidateQuery: String = "", val fileQuery: String = "",
    val candidatePage: Int = 0, val filePage: Int = 0,
    val candidateId: String? = null, val selectionId: String? = null, val revision: Long = 0,
)

internal fun desktopImportMappingDraftGuard(captured: DesktopImportMappingDraft,
    latest: () -> DesktopImportMappingDraft, isCurrent: () -> Boolean): () -> Boolean =
    { captured === latest() && isCurrent() }

internal fun desktopMappingCandidateIds(review: DesktopImportMappingReview, query: String): List<String> =
    review.preview.job?.candidates.orEmpty().filter { desktopImportCandidateEligible(it) &&
        (it.candidate.title.contains(query.take(256), ignoreCase = true) || it.candidate.id.contains(query.take(256), ignoreCase = true)) }
        .take(DESKTOP_IMPORT_PREVIEW_LIMIT).map { it.candidate.id }

internal fun desktopMappingFileIds(review: DesktopImportMappingReview, query: String): List<String> =
    review.preview.files.filter { it.title.contains(query.take(256), ignoreCase = true) || it.id.contains(query.take(256), ignoreCase = true) }
        .take(DESKTOP_IMPORT_PREVIEW_LIMIT).map { it.id }

internal fun desktopMappingPairAvailable(review: DesktopImportMappingReview, draft: DesktopImportMappingDraft): Boolean =
    draft.candidateId != null && draft.selectionId != null &&
        draft.candidateId in desktopMappingCandidateIds(review, draft.candidateQuery) &&
        draft.selectionId in desktopMappingFileIds(review, draft.fileQuery) &&
        review.mappings.none { it.selectionId == draft.selectionId && it.candidateId != draft.candidateId }

internal fun desktopImportJobPhaseLabel(phase: DesktopImportJobPhase): DesktopSavedImportText = when (phase) {
    DesktopImportJobPhase.REVIEW_REQUIRED -> DesktopSavedImportText.Review
    DesktopImportJobPhase.RESELECTION_REQUIRED -> DesktopSavedImportText.Reselect
    DesktopImportJobPhase.PAUSED -> DesktopSavedImportText.Paused
    DesktopImportJobPhase.CANCELLED -> DesktopSavedImportText.Cancelled
    DesktopImportJobPhase.COMPLETED -> DesktopSavedImportText.Completed
    DesktopImportJobPhase.UNSUPPORTED -> DesktopSavedImportText.Unsupported
}

internal fun desktopImportJobCounts(job: DesktopImportJob, language: DesktopLanguage): String {
    val candidates = job.candidates.take(DESKTOP_IMPORT_PREVIEW_LIMIT)
    return listOf(
        DesktopText.Imported.inLanguage(language) to candidates.count { it.outcome == DesktopImportJobOutcome.IMPORTED },
        DesktopText.Duplicates.inLanguage(language) to candidates.count { it.outcome == DesktopImportJobOutcome.DUPLICATE },
        DesktopText.Failed.inLanguage(language) to candidates.count { it.outcome == DesktopImportJobOutcome.FAILED },
        DesktopSavedImportText.Unsettled.label(language) to candidates.count { it.outcome == null },
    ).joinToString(" | ") { (label, count) -> "$label: $count" }
}

/** A bounded file-name preview helps choose a saved job without exposing protocol bookkeeping. */
internal fun desktopImportJobSummary(job: DesktopImportJob, language: DesktopLanguage): String {
    val shown = job.candidates.take(3)
    if (shown.isEmpty()) return desktopImportJobPhaseLabel(job.phase).label(language)
    val names = shown.joinToString(" · ") { desktopImportSafeTitle(it.candidate.title, language) }
    val remaining = job.candidates.size - shown.size
    return if (remaining > 0) "$names (+$remaining)" else names
}

internal fun desktopImportSafeId(id: String): String = id.takeIf { value -> value.length in 1..128 &&
    value.all { it in 'a'..'z' || it in 'A'..'Z' || it in '0'..'9' || it == '-' } } ?: "?"

internal enum class DesktopSavedImportText(val russian: String, val english: String) {
    Show("Сохранённые импорты", "Saved imports"),
    Refresh("Обновить с начала", "Refresh from start"),
    Next("Следующая страница", "Next page"),
    Retry("Повторить чтение страницы", "Retry page read"),
    Hide("Скрыть список", "Hide list"),
    Loading("Загрузка сохранённых импортов...", "Loading saved imports..."),
    Empty("На этой странице нет незавершённых импортов.", "No unfinished imports on this page."),
    More("Есть следующие страницы, даже если эта страница пуста.", "More pages are available, even when this page is empty."),
    Start("Обновите список, чтобы увидеть сохранённые импорты.", "Refresh to read saved imports."),
    Failed("Не удалось прочитать страницу. Повторите чтение или обновите список.", "Could not read this page. Retry or refresh."),
    Closed("Библиотека закрывается. Чтение импортов остановлено.", "The library is closing. Import reads are stopped."),
    Window("Показана одна страница, до 16 импортов. Обновление возвращает к началу.", "One page of up to 16 imports is shown. Refresh returns to the start."),
    Resume("Выбрать файлы заново", "Select files again"),
    Review("Нужно подтверждение", "Review required"),
    Reselect("Нужен новый выбор файлов", "New file selection required"),
    Paused("Приостановлен", "Paused"),
    Cancelled("Отменён; сохранённые треки остаются", "Cancelled; saved tracks retained"),
    Completed("Завершён", "Completed"),
    Unsupported("История не поддерживается; сохранена только для чтения", "Unsupported history retained read-only"),
    Unsettled("Без результата", "Unsettled"),
    Job("Импорт", "Import"),
    LastReported("Последний полученный отчёт", "Last received report"),
    Revision("Ревизия", "Revision"),
    HistoryRead("Сохранённый результат последнего импорта", "Saved result of the last import"),
    Mapping("Сопоставьте файлы для продолжения", "Map files to resume"),
    Instructions("Выберите старый кандидат и новый файл, затем назначьте пару. Совпадения не назначаются автоматически. Можно подтвердить часть кандидатов. Каждый импортированный или повторный трек остаётся сохранённым.", "Select an old candidate and a new file, then assign the pair. No matches are assigned automatically. You may confirm a subset. Every imported or duplicate track remains saved."),
    Old("Старые незавершённые кандидаты", "Old unsettled candidates"),
    New("Новые выбранные файлы", "Newly selected files"),
    SearchOld("Найти старый кандидат", "Find old candidate"),
    SearchNew("Найти новый файл", "Find new file"),
    Choose("Выбрать", "Select"),
    Selected("Выбрано", "Selected"),
    Assign("Назначить выбранную пару", "Assign selected pair"),
    Remove("Убрать назначение", "Remove mapping"),
    Changed("Название или размер изменились — проверьте обе записи.", "Title or size changed: review both observations."),
    Mapped("Назначено", "Mapped"),
    Confirm("Подтвердить назначенные пары на всех страницах", "Confirm mapped pairs on all pages"),
    Frozen("Подтверждение относится к этой ревизии истории и всему показанному сопоставлению. Оставшиеся кандидаты потребуют нового выбора и подтверждения.", "Confirmation applies to this history revision and the entire displayed mapping. Remaining candidates require new selection and confirmation."),
    Reconcile("Результат записи мог сохраниться. Счётчики отражают только подтверждённые ответы. Обновите историю явно. Перед новым импортом переоткройте библиотеку.", "A write may have persisted. Counts reflect acknowledged responses only. Refresh history explicitly. Reopen the library before another import."),
    Cancel("Закрыть новый предпросмотр", "Dismiss new preview");

    fun label(language: DesktopLanguage): String = if (language == DesktopLanguage.Russian) russian else english
}
