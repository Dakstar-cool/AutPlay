package app.autplay.desktop.library

import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobLimits
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopSavedImportsState
import app.autplay.application.desktop.desktopImportCandidateEligible
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** One read owner, no automatic scan or writes. Retired reads drain before replacement/store close. */
public class DesktopImportJobsController(
    private val store: DesktopLibraryStore,
    scope: CoroutineScope,
    private val onSnapshotUnavailable: () -> Unit,
    private val canRead: () -> Boolean = { true },
) {
    private class Request(val after: DesktopImportJobCursor?, val lastJobId: String? = null)
    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopSavedImportsState> = MutableStateFlow(DesktopSavedImportsState())
    public val state: StateFlow<DesktopSavedImportsState> = mutableState.asStateFlow()
    private val requests: Channel<Request> = Channel(Channel.CONFLATED)
    private var current: Request? = null
    private var failed: Request? = null
    private var active: Job? = null
    private var closed: Boolean = false
    private var refused: Boolean = false
    private var snapshotReported: Boolean = false
    private val owner: Job = scope.launch(Dispatchers.IO) {
        for (request in requests) {
            val read = synchronized(lock) {
                if (!owns(request)) null else launch(start = CoroutineStart.LAZY) { read(request) }.also { active = it }
            } ?: continue
            read.start()
            read.join()
            synchronized(lock) { if (active === read) active = null }
        }
    }

    public fun refresh(expected: DesktopSavedImportsState, lastJobId: String? = null): Unit = synchronized(lock) {
        if (accepts(expected)) admit(Request(null, lastJobId))
    }

    public fun next(expected: DesktopSavedImportsState): Unit = synchronized(lock) {
        if (!accepts(expected) || !expected.open || !expected.initialized || expected.loading || expected.errorCode != null) return@synchronized
        expected.next?.let { admit(Request(it)) }
    }

    public fun retry(expected: DesktopSavedImportsState): Unit = synchronized(lock) {
        if (!accepts(expected) || !expected.open || expected.loading || expected.errorCode != "desktop_import_jobs_load_failed") return@synchronized
        failed?.let { admit(Request(it.after, it.lastJobId)) }
    }

    public fun dismiss(expected: DesktopSavedImportsState): Unit = synchronized(lock) {
        if (!accepts(expected)) return@synchronized
        retire()
        mutableState.value = expected.copy(open = false, loading = false, revision = expected.revision + 1)
    }

    /** Called under host admission before starting a NEW chooser. IDs alone confer no authority. */
    public fun captureResume(expected: DesktopSavedImportsState, job: DesktopImportJob): DesktopImportJob? = synchronized(lock) {
        if (!accepts(expected) || !expected.open || expected.loading || expected.errorCode != null ||
            expected.jobs.none { it === job } || job.revision == null ||
            job.candidates.size !in 1..DesktopImportJobLimits.CANDIDATES || !job.candidates.any(::desktopImportCandidateEligible) ||
            job.phase == DesktopImportJobPhase.COMPLETED || job.phase == DesktopImportJobPhase.UNSUPPORTED) null else job
    }

    /** Invalidate observations after an import without silently rereading or copying anything. */
    public fun invalidate(): Unit = synchronized(lock) {
        if (closed || refused) return@synchronized
        retire()
        mutableState.value = DesktopSavedImportsState(open = mutableState.value.open, revision = mutableState.value.revision + 1)
    }

    public fun refuse(): Unit = synchronized(lock) {
        if (refused) return@synchronized
        refused = true
        retire()
        mutableState.value = mutableState.value.copy(loading = false, errorCode = "desktop_snapshot_unavailable", revision = mutableState.value.revision + 1)
    }

    public fun beginClose(): Unit = synchronized(lock) {
        if (closed) return@synchronized
        closed = true
        retire()
        requests.close()
        mutableState.value = mutableState.value.copy(closed = true, loading = false,
            errorCode = "desktop_import_jobs_closed", revision = mutableState.value.revision + 1)
    }

    public suspend fun close(): Unit = withContext(NonCancellable) { beginClose(); owner.join() }

    private fun accepts(expected: DesktopSavedImportsState): Boolean = !closed && !refused && canRead() && mutableState.value === expected
    private fun owns(request: Request): Boolean = !closed && !refused && canRead() && current === request
    private fun retire(): Unit { current = null; failed = null; active?.cancel() }
    private fun admit(request: Request): Unit {
        retire()
        current = request
        mutableState.value = mutableState.value.copy(open = true, loading = true, errorCode = null, revision = mutableState.value.revision + 1)
        requests.trySend(request)
    }

    private suspend fun read(request: Request): Unit {
        try {
            val (page, lastJob) = withTimeout(30_000) {
                try {
                    val page = store.loadUnfinishedImportJobs(request.after, DesktopImportJobLimits.JOB_PAGE)
                    currentCoroutineContext().ensureActive()
                    page to request.lastJobId?.let { store.loadImportJob(it) }
                }
                catch (failure: Exception) {
                    if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                    throw failure
                }
            }
            synchronized(lock) {
                if (!owns(request)) return@synchronized
                // Empty scan pages still publish next. Never truncate a page and hide later jobs.
                require(page.jobs.size <= DesktopImportJobLimits.JOB_PAGE)
                require(page.next == null || page.next != request.after)
                current = null
                mutableState.value = mutableState.value.copy(jobs = page.jobs.toList(), next = page.next,
                    initialized = true, loading = false, errorCode = null, revision = mutableState.value.revision + 1,
                    lastJob = if (request.after == null) lastJob else mutableState.value.lastJob)
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
            else synchronized(lock) {
                if (!owns(request)) return@synchronized
                current = null
                val cancelled = failure is CancellationException && failure !is TimeoutCancellationException
                failed = if (cancelled) null else request
                mutableState.value = mutableState.value.copy(loading = false,
                    errorCode = if (cancelled) null else "desktop_import_jobs_load_failed", revision = mutableState.value.revision + 1)
            }
        }
    }

    private fun reportSnapshotUnavailable(): Unit {
        refuse()
        val report = synchronized(lock) { if (snapshotReported) false else { snapshotReported = true; true } }
        if (report) onSnapshotUnavailable()
    }
}
