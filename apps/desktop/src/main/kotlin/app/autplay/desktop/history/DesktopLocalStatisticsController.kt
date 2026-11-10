package app.autplay.desktop.history

import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalStatisticsState
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

/** Explicit reads only. Playback, navigation and database invalidation do not refresh this view. */
public class DesktopLocalStatisticsController(
    private val store: DesktopLibraryStore,
    scope: CoroutineScope,
    private val onSnapshotUnavailable: () -> Unit,
    private val canRead: () -> Boolean = { true },
    private val clockMs: () -> Long = System::currentTimeMillis,
) {
    private class Request(val throughInclusiveMs: Long)

    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopLocalStatisticsState> = MutableStateFlow(DesktopLocalStatisticsState())
    public val state: StateFlow<DesktopLocalStatisticsState> = mutableState.asStateFlow()
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
            // Cancellation cannot promise that a synchronous native database step has ended.
            readJob.join()
            synchronized(lock) { if (activeRead === readJob) activeRead = null }
        }
    }

    public fun refresh(expected: DesktopLocalStatisticsState): Unit = synchronized(lock) {
        if (!admissionOpen() || mutableState.value !== expected) return@synchronized
        val cutoff = try { clockMs().also { require(it >= 0) } } catch (_: Exception) {
            retire()
            publish(loading = false, errorCode = "desktop_local_statistics_load_failed")
            return@synchronized
        }
        admit(Request(cutoff))
    }

    /** A retry keeps the failed snapshot cutoff; another Refresh captures a new cutoff. */
    public fun retry(expected: DesktopLocalStatisticsState): Unit = synchronized(lock) {
        if (!admissionOpen() || mutableState.value !== expected || expected.loading ||
            expected.errorCode != "desktop_local_statistics_load_failed") return@synchronized
        val failed = failedRequest ?: return@synchronized
        admit(Request(failed.throughInclusiveMs))
    }

    public fun refuse(): Unit = synchronized(lock) {
        if (refused) return@synchronized
        refused = true
        retire()
        publish(loading = false, errorCode = "desktop_snapshot_unavailable")
    }

    public fun beginClose(): Unit = synchronized(lock) {
        if (closed) return@synchronized
        closed = true
        retire()
        requests.close()
        publish(loading = false, errorCode = mutableState.value.errorCode)
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

    private fun admit(request: Request): Unit {
        retire()
        currentRequest = request
        publish(loading = true, errorCode = null)
        requests.trySend(request)
    }

    private fun publish(loading: Boolean, errorCode: String?): Unit {
        val current = mutableState.value
        mutableState.value = DesktopLocalStatisticsState(current.snapshot, loading, errorCode, current.revision + 1)
    }

    private suspend fun read(request: Request): Unit {
        try {
            val snapshot = withTimeout(30_000) {
                try { store.readLocalStatistics(request.throughInclusiveMs) }
                catch (failure: Exception) {
                    // Report a sticky storage marker before cancellation can replace its cause.
                    if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                    throw failure
                }
            }
            require(snapshot.throughInclusiveMs == request.throughInclusiveMs)
            synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRequest = null
                mutableState.value = DesktopLocalStatisticsState(snapshot, revision = mutableState.value.revision + 1)
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
            else synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRequest = null
                val cancelled = failure is CancellationException && failure !is TimeoutCancellationException
                failedRequest = if (cancelled) null else request
                publish(loading = false, errorCode = if (cancelled) null else "desktop_local_statistics_load_failed")
            }
        }
    }

    private fun reportSnapshotUnavailable(): Unit {
        refuse()
        val report = synchronized(lock) {
            if (snapshotReported) false else { snapshotReported = true; true }
        }
        if (report) onSnapshotUnavailable()
    }
}
