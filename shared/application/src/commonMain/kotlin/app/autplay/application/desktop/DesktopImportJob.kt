package app.autplay.application.desktop

object DesktopImportJobLimits {
    const val CANDIDATES: Int = 2_048
    const val TITLE_CHARS: Int = 512
    const val FILE_BYTES: Long = 2L * 1024 * 1024 * 1024
    const val BATCH_BYTES: Long = 8L * 1024 * 1024 * 1024
    const val FACT_BYTES: Int = 262_144
    const val FACTS: Int = 16_384
    const val HISTORY_BYTES: Long = 16L * 1024 * 1024
    const val FACT_PAGE: Int = 32
    const val JOB_PAGE: Int = 16
    const val ATTEMPTS: Int = 32
    const val ACTIVE_PERMITS: Int = 32
}

/** UUIDs are opaque local identities. Titles and sizes are display observations, never byte identity. */
data class DesktopImportJobCandidate(val id: String, val title: String, val byteSize: Long)
data class DesktopImportJobManifest(val jobId: String, val candidates: List<DesktopImportJobCandidate>)

/** An explicit user mapping from an old candidate to one file in a NEW process-local preview.
 * The adapter retains the exact selected file and observed attributes; none belong in these facts.
 * A changed title/size is allowed only through this newly displayed, explicitly confirmed preview.
 */
data class DesktopImportJobReselection(
    val candidateId: String, val selectionId: String, val title: String, val byteSize: Long,
)

data class DesktopImportJobAttempt(
    val jobId: String, val expectedRevision: Long, val commandId: String,
    val attemptId: String, val previewId: String, val selections: List<DesktopImportJobReselection>,
)

data class DesktopImportJobSettlement(
    val jobId: String, val expectedRevision: Long, val commandId: String,
    val attemptId: String, val candidateId: String, val selectionId: String,
)

enum class DesktopImportJobFailure { SOURCE_CHANGED, SOURCE_UNAVAILABLE, UNSUPPORTED, COPY_FAILED }
enum class DesktopImportJobStopReason { PAUSED, CANCELLED }
data class DesktopImportJobStop(
    val jobId: String, val expectedRevision: Long, val commandId: String, val reason: DesktopImportJobStopReason,
)

enum class DesktopImportJobOutcome { IMPORTED, DUPLICATE, FAILED }
data class DesktopImportJobCandidateState(
    val candidate: DesktopImportJobCandidate,
    val outcome: DesktopImportJobOutcome? = null,
    val trackId: String? = null,
    val sha256: String? = null,
    val byteSize: Long? = null,
    val failure: DesktopImportJobFailure? = null,
    /** Last settled observation; a failed older attempt does not settle a newly confirmed mapping. */
    val settledAttemptId: String? = null,
)

/** Stored attempts never grant source access. Every read still requires reselection after interruption.
 * A successful subset does not finish the other candidates. Cancel preserves every committed outcome.
 */
enum class DesktopImportJobPhase { REVIEW_REQUIRED, RESELECTION_REQUIRED, PAUSED, CANCELLED, COMPLETED, UNSUPPORTED }
data class DesktopImportJob(
    val id: String, val revision: Long?, val phase: DesktopImportJobPhase,
    val candidates: List<DesktopImportJobCandidateState> = emptyList(),
    val attemptId: String? = null,
    /** Historical exact subset/mapping for review only. Replaying it never recreates source access. */
    val confirmedAttempt: DesktopImportJobAttempt? = null,
    /** Safe fixed code only. Unsupported histories remain retained and refuse all writes. */
    val errorCode: String? = null,
)

/** Process-local capability minted ONLY by confirmImportJobAttempt. Never persist or reconstruct it.
 * It is not a filesystem grant: the adapter must still bind preview/selection to its exact source.
 */
data class DesktopImportJobPermit(val jobId: String, val attemptId: String, val previewId: String, val nonce: String)
data class DesktopImportJobAuthorization(val job: DesktopImportJob, val permit: DesktopImportJobPermit?)

/** Lexical UUID order is traversal only, never mutation order. A page scans at most JOB_PAGE jobs.
 * Completed jobs are omitted, so an empty page MAY have next. Follow next until null for full coverage.
 * Concurrent insertions before the cursor require a fresh traversal; cursors confer no authority.
 */
data class DesktopImportJobCursor(val afterJobId: String)
data class DesktopImportJobPage(val jobs: List<DesktopImportJob>, val next: DesktopImportJobCursor?)
