package app.autplay.application.desktop

/** Explicit read-only traversal. One scan page is visible, including empty pages with next. */
data class DesktopSavedImportsState(
    val open: Boolean = false,
    val initialized: Boolean = false,
    val loading: Boolean = false,
    val closed: Boolean = false,
    val revision: Long = 0,
    val jobs: List<DesktopImportJob> = emptyList(),
    val next: DesktopImportJobCursor? = null,
    val errorCode: String? = null,
    /** Explicit refresh also reads the last reported job, even when COMPLETED is omitted by scanning. */
    val lastJob: DesktopImportJob? = null,
)

/** Replaced after EVERY explicit edit. An equal A-B-A mapping never revives an old callback. */
data class DesktopImportMappingReview(
    val preview: DesktopImportPreview,
    val revision: Long = 0,
    val mappings: List<DesktopImportCandidateMapping> = emptyList(),
)

fun desktopImportCandidateEligible(candidate: DesktopImportJobCandidateState): Boolean =
    candidate.outcome != DesktopImportJobOutcome.IMPORTED && candidate.outcome != DesktopImportJobOutcome.DUPLICATE

/** History is evidence only. The adapter separately verifies files and storage CAS at confirmation. */
fun desktopImportMappingValid(review: DesktopImportMappingReview): Boolean {
    val preview = review.preview
    val job = preview.job ?: return false
    if (job.id != preview.jobId || job.revision == null || job.phase == DesktopImportJobPhase.UNSUPPORTED ||
        job.phase == DesktopImportJobPhase.COMPLETED || preview.id.isBlank() || preview.failed !in 0..16_384 ||
        preview.errorCode !in setOf(null, "import_invalid_selection", "import_source_unreadable", "import_access_denied",
            "import_source_not_regular", "import_symlink_rejected", "import_unsupported_format", "import_empty_file",
            "import_file_size_limit", "import_batch_size_limit", "import_file_limit", "import_entry_limit", "import_depth_limit") ||
        job.candidates.size !in 1..DesktopImportJobLimits.CANDIDATES ||
        job.candidates.map { it.candidate.id }.toSet().size != job.candidates.size ||
        preview.files.size !in 1..DesktopImportJobLimits.CANDIDATES ||
        preview.files.any { it.id.isBlank() || it.byteSize !in 1..DesktopImportJobLimits.FILE_BYTES } ||
        preview.files.map { it.id }.toSet().size != preview.files.size ||
        preview.totalBytes !in 1..DesktopImportJobLimits.BATCH_BYTES ||
        preview.totalBytes != preview.files.sumOf { it.byteSize } ||
        review.mappings.size !in 1..DesktopImportJobLimits.CANDIDATES) return false
    val eligible = job.candidates.filter(::desktopImportCandidateEligible).map { it.candidate.id }.toSet()
    val selections = preview.files.map { it.id }.toSet()
    return review.mappings.all { it.candidateId in eligible && it.selectionId != null && it.selectionId in selections } &&
        review.mappings.map { it.candidateId }.toSet().size == review.mappings.size &&
        review.mappings.map { it.selectionId }.toSet().size == review.mappings.size
}
