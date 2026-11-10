package app.autplay.desktop.metadata

import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopMetadataPreview
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.data.desktop.DesktopEmbeddedMetadataReader
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import java.nio.file.Path
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
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** Explicit managed-copy inspection. No library collector, playback command or automatic label edit. */
public class DesktopMetadataController(
    private val store: DesktopLibraryStore,
    scope: CoroutineScope,
    private val onSnapshotUnavailable: () -> Unit,
    private val canRead: () -> Boolean = { true },
    private val reader: (Path, Long) -> DesktopEmbeddedMetadata = DesktopEmbeddedMetadataReader::read,
) {
    private sealed interface Command
    private class Read(val id: String, val trackId: String, val snapshot: DesktopLibrarySnapshot) : Command
    private class Apply(val preview: DesktopMetadataPreview) : Command
    private data class ArtworkKey(val trackId: String, val sha256: String, val byteSize: Long)
    private class Inspection(
        val provenance: DesktopTrackTextProvenance, val embedded: DesktopEmbeddedMetadata,
    )

    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopMetadataState> = MutableStateFlow(DesktopMetadataState())
    public val state: StateFlow<DesktopMetadataState> = mutableState.asStateFlow()
    private val commands: Channel<Command> = Channel(Channel.CONFLATED)
    private val cache: LinkedHashMap<ArtworkKey, DesktopArtwork> = LinkedHashMap()
    private var cacheBytes: Long = 0
    private var closed: Boolean = false
    private var refused: Boolean = false
    private var snapshotReported: Boolean = false
    private var currentRead: Read? = null
    private var activeRead: Job? = null
    private var conflictedPreviewId: String? = null
    private val owner: Job = scope.launch(Dispatchers.IO) {
        for (command in commands) when (command) {
            is Read -> {
                val job = synchronized(lock) {
                    if (!owns(command)) null else launch(start = CoroutineStart.LAZY) { read(command) }
                        .also { activeRead = it }
                } ?: continue
                job.start()
                // Replacement and close wait even when a blocking reader ignores interruption.
                job.join()
                synchronized(lock) { if (activeRead === job) activeRead = null }
            }
            is Apply -> withContext(NonCancellable) { applyConfirmed(command.preview) }
        }
    }

    public fun inspect(trackId: String): Unit = synchronized(lock) {
        if (!admissionOpen() || mutableState.value.applying) return@synchronized
        retireRead()
        conflictedPreviewId = null
        // Retain the immutable snapshot reference now; its potentially large lookup belongs to IO.
        val request = Read(UUID.randomUUID().toString(), trackId, store.state.value)
        currentRead = request
        mutableState.value = mutableState.value.copy(
            requestId = request.id, trackId = trackId, busy = true, preview = null, errorCode = null,
        )
        commands.trySend(request)
    }

    public fun cancel(requestId: String): Unit = synchronized(lock) {
        val current = mutableState.value
        if (closed || current.applying || !current.busy || current.requestId != requestId) return@synchronized
        retireRead()
        mutableState.value = current.copy(requestId = null, trackId = null, busy = false, preview = null)
    }

    /** Refusal still permits dismissing a read-only preview, but never an admitted write. */
    public fun dismiss(previewId: String): Unit = synchronized(lock) {
        val current = mutableState.value
        if (closed || current.busy || current.applying ||
            (current.preview?.id ?: current.requestId) != previewId) return@synchronized
        conflictedPreviewId = null
        mutableState.value = current.copy(
            requestId = null, trackId = null, preview = null,
            errorCode = if (refused) "desktop_snapshot_unavailable" else null,
        )
    }

    public fun apply(previewId: String): Unit = synchronized(lock) {
        val current = mutableState.value
        val preview = current.preview ?: return@synchronized
        if (!admissionOpen() || current.busy || current.applying || preview.id != previewId ||
            conflictedPreviewId == previewId || !preview.canApply) return@synchronized
        // No replacement command can enter while applying; close drains this accepted command.
        if (commands.trySend(Apply(preview)).isSuccess) {
            mutableState.value = current.copy(applying = true, errorCode = null)
        }
    }

    public fun refuse(): Unit = synchronized(lock) {
        if (refused) return@synchronized
        refused = true
        retireRead()
        mutableState.value = mutableState.value.copy(busy = false, errorCode = "desktop_snapshot_unavailable")
    }

    public fun beginClose(): Unit = synchronized(lock) {
        if (closed) return@synchronized
        closed = true
        retireRead()
        commands.close()
        cache.clear()
        cacheBytes = 0
        mutableState.value = mutableState.value.copy(busy = false, artworks = emptyMap())
    }

    public suspend fun close(): Unit = withContext(NonCancellable) {
        beginClose()
        owner.join()
    }

    private fun admissionOpen(): Boolean = !closed && !refused && canRead()
    private fun owns(request: Read): Boolean = admissionOpen() && currentRead === request

    private fun retireRead(): Unit {
        currentRead = null
        activeRead?.cancel()
    }

    private suspend fun read(request: Read): Unit {
        try {
            val inspected = withTimeout(30_000) {
                try {
                    val track = findTrack(request.snapshot, request.trackId) ?: return@withTimeout null
                    val baseline = track.textBaseline()
                    val provenance = store.loadTrackTextProvenance(track.id) ?: return@withTimeout null
                    // Never combine snapshot labels with evidence for a different before-image.
                    if (provenance.baseline != baseline) throw DesktopStorageException("desktop_metadata_conflict")
                    val capability = store.resolveMetadataFile(baseline.trackId)
                    val embedded = if (capability == null) DesktopEmbeddedMetadata("UNAVAILABLE")
                    else runInterruptible(Dispatchers.IO) {
                        try { reader(Path.of(capability), baseline.byteSize) }
                        catch (failure: Exception) {
                            // Observe markers before prompt cancellation can replace the exception.
                            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                            throw failure
                        }
                    }
                    val current = findTrack(store.state.value, baseline.trackId)
                    if (current?.textBaseline() != baseline) throw DesktopStorageException("desktop_metadata_conflict")
                    Inspection(provenance, embedded)
                } catch (failure: Exception) {
                    if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
                    throw failure
                }
            }
            synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRead = null
                if (inspected == null) {
                    mutableState.value = mutableState.value.copy(busy = false, errorCode = "desktop_metadata_missing")
                    return@synchronized
                }
                val baseline = inspected.provenance.baseline
                val embedded = inspected.embedded
                val preview = DesktopMetadataPreview(
                    request.id, baseline, embedded, embedded.title ?: baseline.title, embedded.artist ?: baseline.artist,
                    inspected.provenance,
                )
                cacheArtwork(baseline, embedded.artwork)
                mutableState.value = mutableState.value.copy(
                    busy = false, preview = preview, artworks = artworkProjection(), errorCode = null,
                )
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
            else synchronized(lock) {
                if (!owns(request)) return@synchronized
                currentRead = null
                val cancelled = failure is CancellationException && failure !is TimeoutCancellationException
                mutableState.value = mutableState.value.copy(
                    busy = false, errorCode = when {
                        cancelled -> null
                        failure is TimeoutCancellationException -> "desktop_metadata_timeout"
                        failure is DesktopStorageException && failure.code == "desktop_metadata_conflict" -> "desktop_metadata_conflict"
                        else -> "desktop_metadata_read_failed"
                    },
                )
            }
        }
    }

    private suspend fun findTrack(snapshot: DesktopLibrarySnapshot, trackId: String): DesktopTrack? {
        for ((index, track) in snapshot.tracks.withIndex()) {
            if (index % 128 == 0) currentCoroutineContext().ensureActive()
            if (track.id == trackId) return track
        }
        return null
    }

    private fun DesktopTrack.textBaseline(): DesktopTrackTextBaseline =
        DesktopTrackTextBaseline(id, title, artist, sha256, byteSize)

    private suspend fun applyConfirmed(preview: DesktopMetadataPreview): Unit {
        synchronized(lock) {
            // Refusal can win after UI admission but before this command begins. Close still drains
            // accepted writes; once this start boundary is passed, their outcome must settle.
            if (refused) {
                mutableState.value = mutableState.value.copy(applying = false, errorCode = "desktop_snapshot_unavailable")
                return
            }
        }
        try {
            // The store returns only after its CAS transaction AND snapshot refresh settle.
            // No timeout/cancel path may imply rollback or release the store underneath this write.
            val application = preview.application ?: throw DesktopStorageException("desktop_metadata_conflict")
            store.applyEmbeddedTrackText(application)
            synchronized(lock) {
                mutableState.value = mutableState.value.copy(
                    requestId = null, trackId = null, applying = false, preview = null,
                    errorCode = if (refused) "desktop_snapshot_unavailable" else null,
                )
            }
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) reportSnapshotUnavailable()
            synchronized(lock) {
                val code = if (failure is DesktopStorageException && failure.code == "desktop_metadata_conflict") {
                    conflictedPreviewId = preview.id
                    "desktop_metadata_conflict"
                } else "desktop_metadata_apply_failed"
                // The frozen preview remains reviewable; a conflict needs a new explicit inspection.
                mutableState.value = mutableState.value.copy(
                    applying = false, errorCode = if (refused) "desktop_snapshot_unavailable" else code,
                )
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

    /** Called only while owning the current request and lock. No disk or decoded-image cache. */
    private fun cacheArtwork(baseline: DesktopTrackTextBaseline, artwork: DesktopArtwork?): Unit {
        val existing = cache.entries.iterator()
        while (existing.hasNext()) {
            val entry = existing.next()
            if (entry.key.trackId == baseline.trackId) {
                cacheBytes -= entry.value.byteSize
                existing.remove()
            }
        }
        if (artwork != null) {
            cache[ArtworkKey(baseline.trackId, baseline.sha256, baseline.byteSize)] = artwork
            cacheBytes += artwork.byteSize
        }
        while (cache.size > DesktopEmbeddedMetadataLimits.MAX_CACHE_ENTRIES ||
            cacheBytes > DesktopEmbeddedMetadataLimits.MAX_CACHE_BYTES) {
            val oldest = cache.entries.iterator()
            cacheBytes -= oldest.next().value.byteSize
            oldest.remove()
        }
    }

    private fun artworkProjection(): Map<String, DesktopArtwork> =
        Collections.unmodifiableMap(cache.entries.associate { it.key.trackId to it.value })
}
