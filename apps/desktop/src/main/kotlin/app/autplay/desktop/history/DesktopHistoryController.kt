package app.autplay.desktop.history

import app.autplay.application.desktop.DesktopHistoryCursor
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryPage
import app.autplay.application.desktop.DesktopHistoryState
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** Read-only host window. No snapshot collector, native commands or listening-event writes. */
public class DesktopHistoryController(
    private val store: DesktopLibraryStore,
    scope: CoroutineScope,
    private val onSnapshotUnavailable: () -> Unit,
    private val canRead: () -> Boolean = { true },
) {
    private class Request(val before: DesktopHistoryCursor?, val append: Boolean)

    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopHistoryState> = MutableStateFlow(DesktopHistoryState())
    public val state: StateFlow<DesktopHistoryState> = mutableState.asStateFlow()
    private val requests: Channel<Request> = Channel(Channel.CONFLATED)
    private var closed: Boolean = false
    private var refused: Boolean = false
    private var snapshotReported: Boolean = false
    private var currentRequest: Request? = null
    private var failedRequest: Request? = null
    private var activeRead: Job? = null
    private val owner: Job = scope.launch(Dispatchers.IO) {
        for (request in requests) {
            val readJob = synchronized(lock) {
                if (!owns(request)) null else launch(start = CoroutineStart.LAZY) { read(request) }.also { activeRead = it }
            } ?: continue
            readJob.start()
            // A retired non-cooperative read must finish before another store read begins.
            readJob.join()
            synchronized(lock) { if (activeRead === readJob) activeRead = null }
        }
    }

    /** Retires ownership immediately, even when the old store read ignores cancellation. */
    public fun refresh(): Unit = synchronized(lock) {
        if (!admissionOpen()) return@synchronized
        admit(Request(before = null, append = false))
    }

    public fun loadMore(expectedRevision: Long): Unit = synchronized(lock) {
        val current = mutableState.value
        if (!admissionOpen() || current.revision != expectedRevision || current.loading ||
            !current.initialized || current.errorCode != null) return@synchronized
        val cursor = current.nextCursor ?: return@synchronized
        admit(Request(cursor, append = true))
    }

    /** Retries the exact failed cursor and operation, never a cursor captured by an old callback. */
    public fun retry(expectedRevision: Long): Unit = synchronized(lock) {
        val current = mutableState.value
        if (!admissionOpen() || current.revision != expectedRevision || current.loading ||
            current.errorCode != "desktop_history_load_failed") return@synchronized
        val failed = failedRequest ?: return@synchronized
        admit(Request(failed.before, failed.append))
    }

    /** Host's sticky storage refusal also retires pending and active reads. */
    public fun refuse(): Unit = synchronized(lock) {
        if (refused) return@synchronized
        refused = true
        retire()
        mutableState.value = mutableState.value.copy(
            loading = false, errorCode = "desktop_snapshot_unavailable", revision = mutableState.value.revision + 1,
        )
    }

    /** Synchronous admission fence; close then joins every owned read before the store can close. */
    public fun beginClose(): Unit = synchronized(lock) {
        if (closed) return@synchronized
        closed = true
        retire()
        requests.close()
        mutableState.value = mutableState.value.copy(loading = false, revision = mutableState.value.revision + 1)
    }

    public suspend fun close(): Unit = withContext(NonCancellable) {
        beginClose()
        owner.join()
    }

    private fun admissionOpen(): Boolean = !closed && !refused && canRead()
    private fun owns(request: Request): Boolean = admissionOpen() && currentRequest === request

    private fun retire(): Unit {
        currentRequest = null
        failedRequest = null
        activeRead?.cancel()
    }

    /** Called only with lock held. The conflated mailbox bounds replacement requests to one. */
    private fun admit(request: Request): Unit {
        retire()
        currentRequest = request
        mutableState.value = mutableState.value.copy(
            loading = true, errorCode = null, revision = mutableState.value.revision + 1,
        )
        requests.trySend(request)
    }

    private suspend fun read(request: Request): Unit {
        try {
            // Cooperative timeout; shutdown still joins a read that ignores cancellation.
            val page = withTimeout(30_000) {
                try {
                    store.loadHistoryPage(request.before, DesktopHistoryLimits.PAGE_SIZE)
                } catch (failure: Exception) {
                    // Observe the store exception before timeout/cancellation can replace its cause.
                    if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                    throw failure
                }
            }
            synchronized(lock) {
                if (!owns(request)) return@synchronized
                publish(request, page)
                currentRequest = null
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) {
                // Even a retired read's marker must reach the host's sole sticky refusal path.
                reportSnapshotUnavailable()
            } else synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRequest = null
                val cancelled = failure is CancellationException && failure !is TimeoutCancellationException
                failedRequest = if (cancelled) null else request
                mutableState.value = mutableState.value.copy(
                    loading = false, errorCode = if (cancelled) null else "desktop_history_load_failed",
                )
            }
        }
    }

    private fun reportSnapshotUnavailable(): Unit {
        refuse()
        val report = synchronized(lock) {
            if (snapshotReported) false else {
                snapshotReported = true
                true
            }
        }
        if (report) onSnapshotUnavailable()
    }

    private fun publish(request: Request, page: DesktopHistoryPage): Unit {
        val current = mutableState.value
        val events = (if (request.append) current.events + page.events else page.events)
            .associateBy { it.id }.values
            .sortedWith(compareByDescending<DesktopListeningEvent> { it.startedAtMs }.thenBy { it.id })
        val hidden = request.append && (current.newerItemsHidden || events.size > DesktopHistoryLimits.MAX_VISIBLE_EVENTS)
        mutableState.value = current.copy(
            events = events.takeLast(DesktopHistoryLimits.MAX_VISIBLE_EVENTS), nextCursor = page.nextCursor,
            loading = false, initialized = true, errorCode = null, newerItemsHidden = hidden,
        )
    }
}
