package app.autplay.application.desktop

/** Path-free, process-local review. Source capabilities remain inside the import adapter. */
data class DesktopImportPreviewFile(val id: String, val title: String, val byteSize: Long)

data class DesktopImportPreview(
    val id: String,
    val files: List<DesktopImportPreviewFile>,
    val totalBytes: Long,
    val failed: Int = 0,
    val errorCode: String? = null,
    /** Allocated locally for initial review; no job is persisted until confirmation. */
    val jobId: String? = null,
    /** Frozen read-only history for reselection. Old observations never authorize new files. */
    val job: DesktopImportJob? = null,
)

/** Explicit user choice; null leaves an old candidate unmapped. Never infer this from labels/order. */
data class DesktopImportCandidateMapping(val candidateId: String, val selectionId: String?)

/** Counts describe settled candidates; preparation failures are included in failed. */
data class DesktopImportProgress(
    val previewId: String,
    val totalFiles: Int,
    val completedFiles: Int = 0,
    val imported: Int = 0,
    val duplicates: Int = 0,
    val failed: Int = 0,
    val currentTitle: String? = null,
    val jobId: String? = null,
    val jobRevision: Long? = null,
    val jobPhase: DesktopImportJobPhase? = null,
    /** A job/commit error may leave the current file unsettled; it does not increment failed. */
    val errorCode: String? = null,
)

enum class DesktopImportPhase { IDLE, SELECTING, PREPARING, REVIEWING, IMPORTING, CANCELLING }

data class DesktopImportReviewState(
    val phase: DesktopImportPhase = DesktopImportPhase.IDLE,
    val preview: DesktopImportPreview? = null,
    val progress: DesktopImportProgress? = null,
)

interface DesktopImportReviewPort : DesktopImportPort {
    /** Enumerates and observes attributes only; never creates managed files or DB facts. */
    suspend fun prepareSelected(paths: List<String>): DesktopImportPreview
    /** Consumes one exact plan; never rescans roots. Changed sources fail individually. */
    suspend fun importPrepared(
        previewId: String,
        onProgress: (DesktopImportProgress) -> Unit,
    ): DesktopImportResult
    /** Fresh explicit chooser paths only. Shows old candidates and NEW selection IDs/title/size.
     * Read-only preparation; never restores an earlier source capability or permit.
     */
    suspend fun prepareReselected(jobId: String, paths: List<String>): DesktopImportPreview =
        DesktopImportPreview("", emptyList(), 0, errorCode = "import_job_reselection_unavailable")
    /** Confirmation of the exact displayed preview/revision and explicit subset/mapping.
     * UI must show old versus selected title/size, including changes, before invoking this.
     * Duplicate/unknown IDs and already imported/duplicate candidates are refused as a whole.
     */
    suspend fun importReselected(
        previewId: String,
        mappings: List<DesktopImportCandidateMapping>,
        onProgress: (DesktopImportProgress) -> Unit,
    ): DesktopImportResult = DesktopImportResult(0, 0, 0, "import_job_reselection_unavailable")
    /** Retires only the named plan. Safe for stale completion and shutdown callbacks. */
    fun discardPrepared(previewId: String): Unit
}
