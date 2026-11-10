package app.autplay.desktop.library

import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopCopyInspectionLimits
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.matches
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import java.util.Collections
import java.util.UUID
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

/** Explicit attribute observations only. No snapshot collector, playback capability or repair. */
public class DesktopCopyInspectionController(
    private val store: DesktopLibraryStore,
    scope: CoroutineScope,
    private val onSnapshotUnavailable: () -> Unit,
    private val canRead: () -> Boolean = { true },
    private val reader: suspend (String) -> DesktopCopyInspection? = { store.inspectManagedCopy(it) },
) {
    private class Request(val id: String, val trackIds: List<String>, val snapshot: DesktopLibrarySnapshot)
    private data class Identity(val trackId: String, val sha256: String, val byteSize: Long)
    private class ReadFailure(val code: String) : Exception()

    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopCopyInspectionState> = MutableStateFlow(DesktopCopyInspectionState())
    public val state: StateFlow<DesktopCopyInspectionState> = mutableState.asStateFlow()
    private val requests: Channel<Request> = Channel(Channel.CONFLATED)
    // Published maps are immutable; invalidation swaps references without scanning under the lock.
    private var cache: Map<Identity, DesktopCopyInspection> = emptyMap()
    private var closed: Boolean = false
    private var refused: Boolean = false
    private var snapshotReported: Boolean = false
    private var currentRequest: Request? = null
    private var activeRead: Job? = null
    private val validId: Regex = Regex("[A-Za-z0-9_-]{1,128}")
    private val owner: Job = scope.launch(Dispatchers.IO) {
        for (request in requests) {
            val job = synchronized(lock) {
                if (!owns(request)) null else launch(start = CoroutineStart.LAZY) { read(request) }
                    .also { activeRead = it }
            } ?: continue
            job.start()
            // A cancelled blocking/non-cooperative read must settle before its replacement begins.
            job.join()
            synchronized(lock) { if (activeRead === job) activeRead = null }
        }
    }

    public fun inspect(trackIds: List<String>): Unit {
        if (trackIds.isEmpty()) return
        // At most 1001 references and one snapshot reference are captured on the caller's thread.
        val bounded = trackIds.take(DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS + 1)
        synchronized(lock) {
            if (!admissionOpen()) return
            retire()
            val request = Request(UUID.randomUUID().toString(), bounded, store.state.value)
            currentRequest = request
            mutableState.value = DesktopCopyInspectionState(
                requestId = request.id, busy = true,
                requested = minOf(bounded.size, DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS),
            )
            // A synchronous StateFlow observer may already have replaced/invalidated this request.
            if (currentRequest === request) requests.trySend(request)
        }
    }

    public fun cancel(requestId: String): Unit = synchronized(lock) {
        if (closed || currentRequest?.id != requestId) return@synchronized
        retire()
        mutableState.value = mutableState.value.copy(requestId = null, busy = false)
    }

    /** Every settled importer/chooser attempt invalidates advisory evidence, even after cancellation. */
    public fun invalidate(): Unit = synchronized(lock) {
        retire()
        cache = emptyMap()
        mutableState.value = DesktopCopyInspectionState(
            errorCode = if (refused) "desktop_snapshot_unavailable" else null,
        )
    }

    public fun refuse(): Unit = synchronized(lock) {
        if (refused) return@synchronized
        refused = true
        retire()
        cache = emptyMap()
        mutableState.value = mutableState.value.copy(
            requestId = null, busy = false, results = emptyMap(), errorCode = "desktop_snapshot_unavailable",
        )
    }

    public fun beginClose(): Unit = synchronized(lock) {
        if (closed) return@synchronized
        closed = true
        retire()
        requests.close()
        cache = emptyMap()
        mutableState.value = mutableState.value.copy(requestId = null, busy = false, results = emptyMap())
    }

    /** The host must join this owner before closing its store or cancelling its parent scope. */
    public suspend fun close(): Unit = withContext(NonCancellable) {
        beginClose()
        owner.join()
    }

    private fun admissionOpen(): Boolean = !closed && !refused && canRead()
    private fun owns(request: Request): Boolean = admissionOpen() && currentRequest === request

    private fun retire(): Unit {
        currentRequest = null
        activeRead?.cancel()
    }

    private suspend fun read(request: Request): Unit {
        try {
            // This one deadline includes validation, baseline lookup, all reads and projections.
            withTimeout(30_000) {
                try {
                    if (request.trackIds.size > DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS) {
                        throw ReadFailure("desktop_copy_inspection_limit")
                    }
                    if (request.trackIds.any { !validId.matches(it) }) {
                        throw ReadFailure("desktop_copy_inspection_invalid")
                    }
                    val ids = request.trackIds.toSet()
                    val baselines = findTracks(request.snapshot, ids)
                    if (baselines.size != ids.size) throw ReadFailure("desktop_copy_inspection_missing")
                    val previous = synchronized(lock) { if (owns(request)) cache else null } ?: return@withTimeout
                    val observations = LinkedHashMap(previous)
                    var completed = 0
                    var stale = false
                    for (id in ids) {
                        currentCoroutineContext().ensureActive()
                        val baseline = checkNotNull(baselines[id])
                        val observed = try { reader(id) }
                        catch (failure: Exception) {
                            // Observe markers before prompt cancellation/timeout can replace them.
                            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                            throw failure
                        }
                        currentCoroutineContext().ensureActive()
                        // Replace all old byte identities for this track in the worker, never on UI.
                        observations.keys.removeAll { it.trackId == id }
                        if (observed != null && observed.matches(baseline)) {
                            observations[Identity(id, baseline.sha256, baseline.byteSize)] = observed
                        } else stale = true // No observation is distinct from UNAVAILABLE.
                        while (observations.size > DesktopCopyInspectionLimits.MAX_CACHED_RESULTS) {
                            val oldest = observations.entries.iterator()
                            oldest.next()
                            oldest.remove()
                        }
                        completed++
                        if (completed % 16 == 0 || completed == ids.size) {
                            publish(request, observations, ids.size, completed,
                                if (completed == ids.size && stale) "desktop_copy_inspection_stale" else null)
                        }
                    }
                } catch (failure: Exception) {
                    if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                    throw failure
                }
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
            else synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRequest = null
                mutableState.value = mutableState.value.copy(
                    busy = false, errorCode = when {
                        failure is TimeoutCancellationException -> "desktop_copy_inspection_timeout"
                        failure is CancellationException -> null
                        failure is ReadFailure -> failure.code
                        else -> "desktop_copy_inspection_read_failed"
                    },
                )
            }
        }
    }

    private suspend fun findTracks(snapshot: DesktopLibrarySnapshot, ids: Set<String>): Map<String, DesktopTrack> {
        val found = HashMap<String, DesktopTrack>()
        if (ids.isEmpty()) return found
        for ((index, track) in snapshot.tracks.withIndex()) {
            if (index % 128 == 0) currentCoroutineContext().ensureActive()
            if (track.id in ids) found[track.id] = track
            if (found.size == ids.size) break
        }
        return found
    }

    /** All bounded cache copies and potentially large snapshot lookups happen outside the lock. */
    private suspend fun publish(
        request: Request, observations: Map<Identity, DesktopCopyInspection>,
        requested: Int, completed: Int, errorCode: String?,
    ): Unit {
        val snapshot = store.state.value
        val tracks = findTracks(snapshot, observations.keys.mapTo(HashSet()) { it.trackId })
        val retained = LinkedHashMap<Identity, DesktopCopyInspection>()
        val results = LinkedHashMap<String, DesktopCopyInspection>()
        for ((identity, observation) in observations) {
            val track = tracks[identity.trackId] ?: continue
            if (observation.matches(track)) {
                retained[identity] = observation
                results[identity.trackId] = observation
            }
        }
        val frozenCache = Collections.unmodifiableMap(retained)
        val frozenResults = Collections.unmodifiableMap(results)
        currentCoroutineContext().ensureActive()
        synchronized(lock) {
            if (!owns(request)) return@synchronized
            // A concurrently replaced snapshot cannot receive a projection of its predecessor.
            val sameSnapshot = store.state.value === snapshot
            cache = if (sameSnapshot) frozenCache else emptyMap()
            if (completed == requested) currentRequest = null
            mutableState.value = mutableState.value.copy(
                busy = completed < requested, requested = requested, completed = completed,
                results = if (sameSnapshot) frozenResults else emptyMap(), errorCode = errorCode,
            )
        }
    }

    private fun reportSnapshotUnavailable(): Unit {
        refuse()
        val report = synchronized(lock) {
            if (snapshotReported) false else { snapshotReported = true; true }
        }
        // Global refusal can acquire other owner locks; never call it while holding this lock.
        if (report) onSnapshotUnavailable()
    }
}
