package app.autplay.desktop.playback

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.DesktopSleepTimerState
import app.autplay.application.desktop.DesktopTastePolicy
import app.autplay.application.desktop.DesktopTasteState
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.desktop.planDesktopUpcomingEdit
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.desktop.media.DesktopMediaActorTarget
import app.autplay.desktop.media.DesktopMediaPlaybackPort
import app.autplay.desktop.media.DesktopMediaCopySelection
import app.autplay.application.desktop.media.DesktopMediaLimits
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.platform.PlaybackPort
import app.autplay.playback.presentation.PlaybackCommandGate
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.RepeatModePresentation
import java.util.Collections
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** Serial application owner. Native facts and file capabilities never become queue identities. */
public class DesktopPlaybackController(
    private val store: DesktopLibraryStore,
    private val engine: DesktopAudioEngine,
    private val scope: CoroutineScope,
    timerNowNanos: () -> Long = System::nanoTime,
    periodicObservations: Flow<Unit>? = null,
    private val media: DesktopMediaPlaybackPort? = null,
    private val libraryState: StateFlow<DesktopLibrarySnapshot> = store.state,
) : PlaybackPort {
    private val mutex: Mutex = Mutex()
    private val closeMutex: Mutex = Mutex()
    private val contextGate: Any = Any()
    private val contextFenced: AtomicBoolean = AtomicBoolean(false)
    private val contextEpoch: java.util.concurrent.atomic.AtomicLong = java.util.concurrent.atomic.AtomicLong(1)
    private var contextCheckpointed: Boolean = false
    private inner class Command(
        val observesTimer: Boolean = true,
        val projectsAfterAction: Boolean = true,
        val ownerRevision: Long = contextEpoch.get(),
        val action: suspend () -> Unit,
    )
    private val commands: Channel<Command> = Channel(64)
    private val mutableState: MutableStateFlow<PlaybackPresentationState> = MutableStateFlow(PlaybackPresentationState())
    override val state: StateFlow<PlaybackPresentationState> = mutableState.asStateFlow()
    private val mutablePlayerState: MutableStateFlow<DesktopPlayerState> = MutableStateFlow(DesktopPlayerState())
    public val playerState: StateFlow<DesktopPlayerState> = mutablePlayerState.asStateFlow()
    private val mutableError: MutableStateFlow<String?> = MutableStateFlow(null)
    public val error: StateFlow<String?> = mutableError.asStateFlow()
    private val timerPlan: DesktopSleepTimerPlan = DesktopSleepTimerPlan(timerNowNanos)
    private val mutableSleepTimer: MutableStateFlow<DesktopSleepTimerState> = MutableStateFlow(DesktopSleepTimerState())
    public val sleepTimer: StateFlow<DesktopSleepTimerState> = mutableSleepTimer.asStateFlow()
    private val mutableTaste: MutableStateFlow<DesktopTasteState> = MutableStateFlow(DesktopTasteState())
    public val taste: StateFlow<DesktopTasteState> = mutableTaste.asStateFlow()
    private val tasteError: AtomicReference<String?> = AtomicReference(null)
    private var queue: DesktopQueue? = null
    private data class QueueEditorShape(
        val queueId: String,
        val currentEntryId: String,
        val sessionId: String?,
        val generation: Long,
        val queueType: String,
        val repeatMode: String,
        val shuffleEnabled: Boolean,
        val entries: List<DesktopQueueEntry>,
        val shuffleOrder: List<String>,
    )
    private var queueEditorShape: QueueEditorShape? = null
    private var queueEditorRevision: Long = 0
    private var queueEditorTarget: DesktopQueueEditTarget? = null
    private var generation: Long = 0
    private var mediaSelectionRevision: Long = 0
    private var mediaIntentRevision: Long = 0
    private var mediaSleepRevision: Long = 0
    private var mediaTimerRevision: Long = 0
    private var mediaWantsPlay: Boolean = false
    private var mediaCopyQueue: Pair<String, DesktopMediaCopySelection>? = null
    private var endedGeneration: Long? = null
    private var facts: DesktopAudioEvent = DesktopAudioEvent(0, "IDLE")
    private var sourceAvailable: Boolean = false
    private var endAdvanceAllowed: Boolean = false
    private var pendingSeekTarget: Long? = null
    private var pendingSeekStartedNanos: Long = 0
    private val snapshotUnavailable: AtomicBoolean = AtomicBoolean(false)
    @Volatile private var closed: Boolean = false
    private var lastElapsedNanos: Long = System.nanoTime()
    private var lastCheckpointNanos: Long = lastElapsedNanos
    private val jobs: MutableList<Job> = mutableListOf()

    init {
        jobs += scope.launch(Dispatchers.IO) {
            mutex.withLock {
                if (closed || contextFenced.get()) return@withLock
                try {
                    queue = libraryState.value.queue
                    if (queue?.currentEntryId != null && validModes()) {
                        // A restored logical session keeps its identity and always opens paused.
                        openCurrent(autoplay = false)
                    } else {
                        if (queue != null && !validModes()) setError("UNSUPPORTED_QUEUE_MODE")
                        project()
                    }
                } catch (failure: Exception) {
                    handleFailure(failure)
                }
            }
            for (action in commands) {
                mutex.withLock {
                    if (!closed && !contextFenced.get() && action.ownerRevision == contextEpoch.get()) try {
                        if (action.observesTimer) expireSleepTimer()
                        action.action()
                        if (action.projectsAfterAction) project(observeSleepTimer = action.observesTimer)
                    } catch (failure: Exception) {
                        handleFailure(failure)
                    }
                }
            }
        }
        jobs += scope.launch(Dispatchers.IO) {
            (media?.state ?: engine.state).collect { event -> commands.send(Command { accept(event) }) }
        }
        if (media != null) jobs += scope.launch(Dispatchers.IO) {
            media.prepared.collect { prepared -> commands.send(Command {
                val target = mediaTarget()
                if (target != null && target.sameSelection(prepared.target)) media.admitPrepared(target)
            }) }
        }
        jobs += scope.launch(Dispatchers.IO) {
            // Library metadata observations never execute a timer/native command themselves.
            libraryState.collect { commands.send(Command(observesTimer = false) {}) }
        }
        jobs += scope.launch(Dispatchers.IO) {
            val observations = periodicObservations ?: flow {
                while (true) { delay(1_000); emit(Unit) }
            }
            observations.collect {
                commands.send(Command {
                    if (!snapshotUnavailable.get()) {
                        accumulate()
                        if (pendingSeekTarget != null && System.nanoTime() - pendingSeekStartedNanos >= SEEK_CONFIRM_NANOS) {
                            timerPlan.clear()
                            invalidateEngine()
                            facts = DesktopAudioEvent(generation, "ERROR", queue?.positionMs ?: 0)
                            setError("AUDIO_ERROR")
                            checkpoint()
                        }
                        if (facts.isPlaying && System.nanoTime() - lastCheckpointNanos >= CHECKPOINT_NANOS) checkpoint()
                    }
                })
            }
        }
    }

    override fun play(): Unit = dispatch {
        requestPlay()
    }

    /** Host calls after actual old-owner drain and explicit J/standalone selection. */
    public fun restoreSelectedContext(): Unit = dispatch {
        timerPlan.clear()
        invalidateEngine()
        queue = libraryState.value.queue
        mediaCopyQueue = null
        if (queue?.currentEntryId != null && validModes()) openCurrent(autoplay = false) else project()
    }

    /** No IO/await: a queued command from the former context can never run in its successor. */
    public fun isContextFenced(): Boolean = contextFenced.get()

    public fun beginContextFence(): Unit = synchronized(contextGate) {
        contextFenced.set(true)
        contextEpoch.incrementAndGet()
        Unit
    }

    /** The old queue/checkpoint still belongs to its actual selected owner until this returns. */
    public suspend fun fenceSelectedContext(): Long {
        val fence = synchronized(contextGate) { beginContextFence(); contextEpoch.get() }
        mutex.withLock {
            if (closed || contextCheckpointed) return@withLock
            timerPlan.clear()
            accumulate()
            try { if (media != null) publishMediaIntent(false) else engine.pause(generation) }
            finally { checkpoint() }
            contextCheckpointed = true
            project()
        }
        return fence
    }

    /** Host already settled old native/persistence owners and selected the real new library. */
    public suspend fun restoreSelectedContextAfterDrain(expectedFence: Long? = null,
        restoreAllowed: () -> Boolean = { true }): Unit = mutex.withLock {
        synchronized(contextGate) {
            check(!closed && contextFenced.get() && restoreAllowed() &&
                (expectedFence == null || expectedFence == contextEpoch.get())) { "PLAYBACK_CONTEXT_NOT_FENCED" }
        }
        timerPlan.clear()
        invalidateEngine()
        queue = libraryState.value.queue
        mediaCopyQueue = null
        contextCheckpointed = false
        synchronized(contextGate) {
            check(restoreAllowed() && (expectedFence == null || expectedFence == contextEpoch.get())) { "PLAYBACK_CONTEXT_NOT_FENCED" }
            contextFenced.set(false)
        }
        if (queue?.currentEntryId != null && validModes()) openCurrent(autoplay = false) else project()
    }

    /** Capture once in the UI; validate again in the actor before observing even a due timer. */
    public fun transport(target: DesktopPlaybackTarget, action: DesktopTransportAction, positionMs: Long? = null): Unit {
        tryTransport(target, action, positionMs)
    }

    /** True acknowledges bounded enqueue only; the actor still owns identity and execution gates. */
    public fun tryTransport(target: DesktopPlaybackTarget, action: DesktopTransportAction, positionMs: Long? = null): Boolean {
        if (closed || snapshotUnavailable.get() || contextFenced.get()) return false
        return commands.trySend(Command(observesTimer = false, projectsAfterAction = false) {
            if (snapshotUnavailable.get() || !matchesTarget(target)) return@Command
            expireSleepTimer()
            // A failing expiry pause/checkpoint can retire the native opening or reload the queue.
            if (matchesTarget(target)) when (action) {
                DesktopTransportAction.PLAY -> requestPlay()
                DesktopTransportAction.PAUSE -> requestPause()
                DesktopTransportAction.PREVIOUS -> requestPrevious()
                DesktopTransportAction.NEXT -> requestNext()
                DesktopTransportAction.SEEK -> positionMs?.let { requestSeek(it) }
            }
            project()
        }).isSuccess
    }

    private fun matchesTarget(target: DesktopPlaybackTarget): Boolean =
        target.queueId == queue?.id && target.entryId == currentEntry()?.id &&
            target.sessionId == queue?.sessionId && target.generation == generation

    /** Home continuation targets one paused context, even behind queued navigation. */
    public fun resumeCurrentQueue(expectedQueueId: String, expectedEntryId: String, expectedSessionId: String?): Unit =
        dispatch(Command(observesTimer = false) {
            val current = queue
            if (current != null && current.id == expectedQueueId && current.currentEntryId == expectedEntryId &&
                current.sessionId == expectedSessionId && !facts.isPlaying && facts.status.uppercase() != "ENDED") {
                expireSleepTimer()
                requestPlay()
            }
        })

    override fun pause(): Unit = dispatch {
        requestPause()
    }

    override fun seekTo(positionMs: Long): Unit = dispatch {
        requestSeek(positionMs)
    }

    override fun next(): Unit = dispatch {
        requestNext()
    }

    override fun previous(): Unit = dispatch {
        requestPrevious()
    }

    public fun scheduleSleepTimer(expectedQueueId: String, durationMs: Long): Unit = dispatch {
        if (queue?.id != expectedQueueId || !sleepTimerCommandsEnabled()) return@dispatch
        timerPlan.schedule(expectedQueueId, durationMs)
        publishMediaIntent(mediaWantsPlay)
    }

    public fun stopAfterCurrent(expectedQueueId: String, expectedEntryId: String, expectedSessionId: String): Unit = dispatch {
        if (queue?.id != expectedQueueId || currentEntry()?.id != expectedEntryId ||
            queue?.sessionId != expectedSessionId || !sleepTimerCommandsEnabled()) return@dispatch
        timerPlan.stopAfter(expectedQueueId, expectedEntryId, expectedSessionId)
        publishMediaIntent(mediaWantsPlay)
    }

    public fun cancelSleepTimer(expectedPlanId: String): Unit = dispatch {
        // Cancellation remains admitted when native readiness/source gates are unavailable.
        timerPlan.cancel(expectedPlanId)
        publishMediaIntent(mediaWantsPlay)
    }

    /** IDs describe the composed listen; duplicate entries and replay retire that target. */
    public fun setCurrentListenTasteExcluded(
        expectedQueueId: String,
        expectedEntryId: String,
        expectedListeningEventId: String,
        excluded: Boolean,
    ): Unit = dispatchTaste {
        if (queue?.id != expectedQueueId || currentEntry()?.id != expectedEntryId ||
            queue?.sessionId != expectedListeningEventId || queue?.sessionStartedAtMs == null) {
            tasteError.set("TASTE_LISTEN_STALE")
            return@dispatchTaste
        }
        if (!tasteCommandsEnabled()) return@dispatchTaste
        if (media != null) {
            queue = requireNotNull(queue).copy(listenTasteState = if (excluded) "EXCLUDED" else "INCLUDED")
            media.persistTaste(requireNotNull(queue), listeningEvent(false), true, excluded)
            tasteError.set(null)
            return@dispatchTaste
        }
        updateTaste("TASTE_LISTEN_STALE") {
            store.setCurrentListenTasteExcluded(expectedQueueId, expectedEntryId, expectedListeningEventId, excluded)
        }
    }

    /** Queue session means the whole queue ID, independently of its current logical listen. */
    public fun setQueueSessionTasteExcluded(expectedQueueId: String, excluded: Boolean): Unit = dispatchTaste {
        if (queue?.id != expectedQueueId) {
            tasteError.set("TASTE_SESSION_STALE")
            return@dispatchTaste
        }
        if (!tasteCommandsEnabled()) return@dispatchTaste
        if (media != null) {
            queue = requireNotNull(queue).copy(sessionTasteState = if (excluded) "EXCLUDED" else "INCLUDED")
            media.persistTaste(requireNotNull(queue), listeningEvent(false), false, excluded)
            tasteError.set(null)
            return@dispatchTaste
        }
        updateTaste("TASTE_SESSION_STALE") { store.setQueueSessionTasteExcluded(expectedQueueId, excluded) }
    }

    private suspend fun updateTaste(staleCode: String, action: suspend () -> DesktopQueue?): Unit {
        try {
            val confirmed = action()
            if (snapshotUnavailable.get()) return
            if (confirmed == null) {
                tasteError.set(staleCode)
                return
            }
            // The store may return an older position/elapsed checkpoint. Only its confirmed
            // annotations belong to this command; the actor still owns all execution fields.
            queue = queue?.copy(sessionTasteState = confirmed.sessionTasteState, listenTasteState = confirmed.listenTasteState)
            tasteError.set(null)
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure)) {
                // Let the actor's single global failure boundary stop/reload exactly once,
                // including cancellation carrying a suppressed refresh-refusal marker.
                throw failure
            } else if (failure is CancellationException) {
                throw failure
            } else {
                // A transaction refusal with a successful snapshot refresh is metadata-only.
                // Keep the current owner, timer and elapsed baseline; never replay stale state.
                tasteError.set("TASTE_EXCLUSION_UNAVAILABLE")
            }
        }
    }

    public fun playTracks(trackIds: List<String>, queueType: String = "LIBRARY"): Unit {
        if (trackIds.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
            dispatch { setError("QUEUE_LIMIT") }
            return
        }
        // Caller-owned mutable lists must not alter a queued request after its size was admitted.
        val selectedIds = trackIds.take(DesktopLocalLimits.MAX_QUEUE_ENTRIES + 1)
        dispatch { replaceQueue(selectedIds, queueType) }
    }

    public fun playAccountCopy(localTrackId: String, copyId: String, acquisitionScopeId: String): Unit = dispatch {
        if (media == null) return@dispatch
        replaceQueue(listOf(localTrackId), "LIBRARY", DesktopMediaCopySelection(copyId, acquisitionScopeId))
    }

    private suspend fun replaceQueue(trackIds: List<String>, queueType: String, copy: DesktopMediaCopySelection? = null): Unit {
        if (trackIds.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
            setError("QUEUE_LIMIT")
            return
        }
        if (queueType !in ORDINARY_TYPES) {
            setError("UNSUPPORTED_QUEUE_MODE")
            return
        }
        if (trackIds.isEmpty() || copy == null && !admitCollectionTracks(trackIds, libraryState.value)) return
        val entries = trackIds.map { DesktopQueueEntry(newId(), it) }
        val previous = queue
        val candidate = newSession(DesktopQueue(newId(), entries, entries.first().id, queueType))
        mediaCopyQueue = copy?.let { candidate.id to it }
        // Storage rechecks new entry/track mappings in its transaction. A removal after this
        // snapshot must refuse selection before the old native owner or timer is changed.
        saveQueue(candidate, listeningEvent(finalized = false,
            current = previous?.let { accumulatedQueue(it, System.nanoTime()) }))
        // Reuse the untouched old queue/clock baseline through the stop-request boundary:
        // native playback during admission belongs to its immutable final event.
        val previousEvent = listeningEvent(finalized = true,
            current = previous?.let { accumulatedQueue(it, System.nanoTime()) })
        invalidateEngine()
        queue = candidate
        timerPlan.clear()
        // Admission and finalization are separate commits. Interruption can leave the
        // admitted candidate with an unfinished old checkpoint; never guess rollback/end.
        if (previousEvent != null) saveQueue(candidate, previousEvent)
        checkpoint()
        openCurrent(autoplay = true)
    }

    /** Appending the same track deliberately allocates a distinct queue-entry ID. */
    public fun enqueue(trackId: String): Unit = dispatch {
        insertTrack(trackId, afterCurrent = false)
    }

    /** Insert into both original and active order without reopening the current item. */
    public fun playNext(trackId: String): Unit = dispatch {
        insertTrack(trackId, afterCurrent = true)
    }

    /** Freeze raw occurrences before dispatch; one refused reference rejects the whole request. */
    public fun enqueueTracks(trackIds: List<String>): Unit = dispatchTrackInsertion(trackIds, afterCurrent = false)

    /** Keep the selected occurrence order contiguous in both queue orders. */
    public fun playTracksNext(trackIds: List<String>): Unit = dispatchTrackInsertion(trackIds, afterCurrent = true)

    private fun dispatchTrackInsertion(trackIds: List<String>, afterCurrent: Boolean): Unit {
        val selectedIds = trackIds.take(DesktopLocalLimits.MAX_QUEUE_ENTRIES + 1)
        // Refusal and empty requests cannot expire a timer or change the current native owner.
        dispatch(Command(observesTimer = false) {
            if (selectedIds.isEmpty() || !admitInsertion(selectedIds.size, afterCurrent)) return@Command
            insertTracks(selectedIds, libraryState.value, afterCurrent, queueType = "LIBRARY")
        })
    }

    /** Resolve the complete persisted playlist inside the actor, preserving duplicate occurrences. */
    public fun enqueuePlaylist(playlistId: String): Unit = dispatch {
        insertPlaylist(playlistId, afterCurrent = false)
    }

    /** Insert one contiguous playlist batch after current in original and effective order. */
    public fun playPlaylistNext(playlistId: String): Unit = dispatch {
        insertPlaylist(playlistId, afterCurrent = true)
    }

    private suspend fun insertTrack(trackId: String, afterCurrent: Boolean): Unit {
        val snapshot = libraryState.value
        if (!admitInsertion(1, afterCurrent)) return
        insertTracks(listOf(trackId), snapshot, afterCurrent, queueType = "LIBRARY")
    }

    private suspend fun insertPlaylist(playlistId: String, afterCurrent: Boolean): Unit {
        val snapshot = libraryState.value
        val playlist = snapshot.playlists.firstOrNull { it.id == playlistId } ?: return
        if (playlist.entries.isEmpty() || !admitInsertion(playlist.entries.size, afterCurrent)) return
        // Capacity admission bounds this copy; the UI never supplies a displayed/truncated list.
        insertTracks(playlist.entries.map { it.trackId }, snapshot, afterCurrent, queueType = "PLAYLIST")
    }

    private fun admitInsertion(trackCount: Int, afterCurrent: Boolean): Boolean {
        val current = queue
        if (current != null && mediaCopyQueue?.first == current.id) {
            setQueueEditError("PLAYBACK_OPERATION_FAILED")
            return false
        }
        if (current != null && !validModes()) {
            setQueueEditError("UNSUPPORTED_QUEUE_MODE")
            return false
        }
        // Admit capacity and selection before allocating IDs or accumulating the active session.
        if (trackCount > DesktopLocalLimits.MAX_QUEUE_ENTRIES - (current?.entries?.size ?: 0)) {
            setQueueEditError("QUEUE_LIMIT")
            return false
        }
        if (afterCurrent && current != null && current.entries.isNotEmpty() && current.currentEntryId == null) {
            setQueueEditError("QUEUE_CURRENT_MISSING")
            return false
        }
        return true
    }

    private suspend fun insertTracks(
        trackIds: List<String>,
        snapshot: DesktopLibrarySnapshot,
        afterCurrent: Boolean,
        queueType: String,
    ): Unit {
        if (!admitCollectionTracks(trackIds, snapshot)) return
        val current = queue
        val additions = trackIds.map { DesktopQueueEntry(newId(), it) }
        if (current == null || current.entries.isEmpty()) {
            val candidate = newSession(DesktopQueue(newId(), additions, additions.first().id, queueType))
            if (!persistInsertion(candidate)) return
            queue = candidate
            lastElapsedNanos = System.nanoTime()
            lastCheckpointNanos = lastElapsedNanos
            timerPlan.clear()
            clearQueueLimitError()
            openCurrent(autoplay = false, clearError = false)
        } else {
            val entries = current.entries.toMutableList()
            val activeOrder = order().toMutableList()
            val originalIndex = if (afterCurrent) entries.indexOfFirst { it.id == current.currentEntryId } + 1 else entries.size
            val activeIndex = if (afterCurrent) activeOrder.indexOf(current.currentEntryId) + 1 else activeOrder.size
            entries.addAll(originalIndex, additions)
            activeOrder.addAll(activeIndex, additions.map { it.id })
            val now = System.nanoTime()
            val candidate = accumulatedQueue(current, now).copy(entries = entries, shuffleOrder = activeOrder)
            if (!persistInsertion(candidate)) return
            queue = candidate
            lastElapsedNanos = now
            lastCheckpointNanos = System.nanoTime()
            clearQueueLimitError()
        }
    }

    private suspend fun persistInsertion(candidate: DesktopQueue): Boolean {
        try {
            saveQueue(candidate, listeningEvent(finalized = false, current = candidate))
            return true
        } catch (failure: Exception) {
            if (desktopSnapshotUnavailable(failure) || failure is CancellationException) throw failure
            // A refreshed transaction refusal keeps the previous owner and elapsed baseline.
            setQueueEditError(if (failure is DesktopStorageException) when (failure.code) {
                "desktop_track_not_in_collection" -> failure.code
                "desktop_queue_limit" -> "QUEUE_LIMIT"
                else -> "PLAYBACK_OPERATION_FAILED"
            } else "PLAYBACK_OPERATION_FAILED")
            return false
        }
    }

    private fun admitCollectionTracks(trackIds: List<String>, snapshot: DesktopLibrarySnapshot): Boolean {
        val states = snapshot.tracks.associate { it.id to it.collectionState }
        if (trackIds.any { states[it] != "ACTIVE" }) {
            setQueueEditError("desktop_track_not_in_collection")
            return false
        }
        return true
    }

    /** One captured editor owner admits the entire upcoming selection before any side effect. */
    public fun editUpcoming(
        target: DesktopQueueEditTarget,
        action: DesktopQueueGroupAction,
        entryIds: List<String>,
    ): Unit {
        val selectedIds = entryIds.take(DesktopLocalLimits.MAX_QUEUE_ENTRIES + 1)
        dispatch(Command(observesTimer = false, projectsAfterAction = false) {
            // Recompute from actor state, including changes not yet published to the UI.
            val editor = queueEditorState()
            if (editor.target != target) return@Command
            val plan = planDesktopUpcomingEdit(editor.entries, editor.currentEntryId, selectedIds, action) ?: return@Command
            val current = queue ?: return@Command
            val retainedEntries = if (plan.removedEntryIds.isEmpty()) current.entries
                else current.entries.filterNot { it.id in plan.removedEntryIds }
            val byId = retainedEntries.associateBy { it.id }
            val now = System.nanoTime()
            val candidate = accumulatedQueue(current, now).copy(
                entries = if (current.shuffleEnabled) retainedEntries else plan.activeEntryIds.map { byId.getValue(it) },
                shuffleOrder = plan.activeEntryIds.toList(),
            )
            if (persistInsertion(candidate)) {
                queue = candidate
                lastElapsedNanos = now
                lastCheckpointNanos = System.nanoTime()
                clearQueueLimitError()
            }
            // Neither success nor an ordinary refreshed refusal observes/consumes a timer.
            project(observeSleepTimer = false)
        }, reportBusy = false)
    }

    /** Only adjacent upcoming entries can swap; IDs are revalidated when the actor executes. */
    public fun moveUpcoming(entryId: String, direction: Int): Unit = dispatch {
        if (direction != -1 && direction != 1) return@dispatch
        val current = queue ?: return@dispatch
        if (!validModes()) return@dispatch
        val ordered = order()
        val currentIndex = ordered.indexOf(current.currentEntryId)
        val index = ordered.indexOf(entryId)
        val adjacentIndex = index + direction
        if (currentIndex < 0 || index <= currentIndex || adjacentIndex <= currentIndex || adjacentIndex !in ordered.indices) return@dispatch
        val movedOrder = ordered.toMutableList().apply {
            this[index] = ordered[adjacentIndex]
            this[adjacentIndex] = ordered[index]
        }
        val movedEntries = if (current.shuffleEnabled) current.entries else current.entries.toMutableList().apply {
            this[index] = current.entries[adjacentIndex]
            this[adjacentIndex] = current.entries[index]
        }
        accumulate()
        queue = requireNotNull(queue).copy(entries = movedEntries, shuffleOrder = movedOrder)
        checkpoint()
    }

    /** Retain past and current entries in both orders; repeat policy and media bytes stay intact. */
    public fun clearUpcoming(): Unit = dispatch {
        val current = queue ?: return@dispatch
        if (!validModes()) return@dispatch
        val ordered = order()
        val currentIndex = ordered.indexOf(current.currentEntryId)
        if (currentIndex < 0 || currentIndex == ordered.lastIndex) return@dispatch
        val retainedIds = ordered.take(currentIndex + 1).toHashSet()
        accumulate()
        queue = requireNotNull(queue).copy(
            entries = current.entries.filter { it.id in retainedIds },
            shuffleOrder = current.shuffleOrder.filter { it in retainedIds },
        )
        checkpoint()
    }

    public fun selectEntry(entryId: String): Unit = dispatch {
        if (!validModes() || queue?.entries?.none { it.id == entryId } != false) return@dispatch
        if (entryId == queue?.currentEntryId && facts.status.uppercase() != "ENDED") {
            if (sourceAvailable) requestPlay() else openCurrent(autoplay = true)
        } else select(entryId, autoplay = true, forceNewSession = facts.status.uppercase() == "ENDED")
    }

    public fun removeEntry(entryId: String): Unit = dispatch {
        val current = queue ?: return@dispatch
        if (!validModes() || current.entries.none { it.id == entryId }) return@dispatch
        accumulate()
        val event = if (entryId == current.currentEntryId) listeningEvent(finalized = true) else null
        val wasPlaying = facts.isPlaying && endAdvanceAllowed
        val ordered = order()
        val index = ordered.indexOf(entryId)
        val remaining = current.entries.filterNot { it.id == entryId }
        val newOrder = ordered.filterNot { it == entryId }
        if (entryId == current.currentEntryId) {
            timerPlan.clearStopAfter()
            if (newOrder.isEmpty()) timerPlan.clear()
            invalidateEngine()
            val replacement = newOrder.getOrNull(index) ?: newOrder.lastOrNull()
            queue = requireNotNull(queue).copy(entries = remaining, shuffleOrder = newOrder,
                currentEntryId = replacement, positionMs = 0, sessionId = null, sessionStartedAtMs = null, playedMs = 0,
                listenTasteState = DesktopTastePolicy.forNewListen(current.listenTasteState))
            if (replacement != null) startSession()
            saveQueue(requireNotNull(queue), event)
            checkpoint()
            if (replacement != null) openCurrent(wasPlaying) else project()
        } else {
            queue = requireNotNull(queue).copy(entries = remaining, shuffleOrder = newOrder)
            checkpoint()
        }
    }

    public fun setShuffle(enabled: Boolean): Unit = dispatch {
        if (!validModes()) return@dispatch
        accumulate()
        queue = queue?.let { current ->
            val ids = current.entries.map { it.id }
            current.copy(shuffleEnabled = enabled, shuffleOrder = if (enabled) {
                listOfNotNull(current.currentEntryId) + ids.filterNot { it == current.currentEntryId }.shuffled()
            } else ids)
        }
        checkpoint()
    }

    public fun setRepeat(mode: String): Unit = dispatch {
        if (!validModes() || mode !in REPEAT_MODES) return@dispatch
        accumulate()
        queue = queue?.copy(repeatMode = mode)
        checkpoint()
    }

    /** Orderly close checkpoints the active session without finalizing it or losing restart identity. */
    public suspend fun close(): Unit = closeMutex.withLock {
        if (closed) return@withLock
        try {
            mutex.withLock {
                val alreadyRetired = snapshotUnavailable.get() && facts.status == "IDLE"
                try {
                    timerPlan.clear()
                    accumulate()
                    // Checkpoint still runs if the native process disappeared before pause.
                    try {
                        if (!alreadyRetired) {
                            if (media != null) publishMediaIntent(false) else engine.pause(generation)
                        }
                    } finally { checkpoint() }
                } finally {
                    closed = true
                    if (!alreadyRetired) invalidateEngine()
                    project()
                }
            }
        } finally {
            // This also runs if cancellation happened while waiting for the command mutex.
            withContext(NonCancellable) {
                closed = true
                mutablePlayerState.update { it.copy(queueEditor = DesktopQueueEditorState()) }
                mutableSleepTimer.value = DesktopSleepTimerState()
                mutableTaste.update { it.copy(listenCommandsEnabled = false, sessionCommandsEnabled = false) }
                jobs.forEach { it.cancel() }
                commands.close()
                try {
                    withTimeout(5_000) { jobs.joinAll() }
                } finally {
                    if (media != null) media.close() else engine.close()
                }
            }
        }
    }

    private fun dispatch(action: suspend () -> Unit): Unit = dispatch(Command(action = action))

    private fun dispatchTaste(action: suspend () -> Unit): Unit = dispatch(Command(observesTimer = false, action = action), tasteCommand = true)

    private fun dispatch(command: Command, tasteCommand: Boolean = false, reportBusy: Boolean = true): Unit {
        if (closed || snapshotUnavailable.get() || contextFenced.get()) return
        val admitted = Command(command.observesTimer, command.projectsAfterAction, command.ownerRevision) { if (!snapshotUnavailable.get()) command.action() }
        if (!commands.trySend(admitted).isSuccess && !closed && !snapshotUnavailable.get()) {
            if (tasteCommand) {
                tasteError.set("TASTE_EXCLUSION_UNAVAILABLE")
                mutableTaste.update { it.copy(errorCode = tasteError.get()) }
            } else if (reportBusy) {
                // A target-bound request cannot report a side effect before the actor validates it.
                setQueueEditError("PLAYBACK_COMMAND_BUSY")
            }
        }
    }

    /** A stale store projection remains display-only until an explicit application restart. */
    public fun reportSnapshotUnavailable(): Unit {
        if (closed) return
        if (!snapshotUnavailable.compareAndSet(false, true)) return
        setError("desktop_snapshot_unavailable")
        mutablePlayerState.update { it.copy(queueEditor = DesktopQueueEditorState()) }
        mutableTaste.update { it.copy(listenCommandsEnabled = false, sessionCommandsEnabled = false) }
        scope.launch(Dispatchers.IO) {
            mutex.withLock {
                if (!closed) {
                    timerPlan.clear()
                    invalidateEngine()
                    queue = libraryState.value.queue
                    project()
                }
            }
        }
    }

    private suspend fun requestPlay(): Unit {
        if (!allowed()) return
        if (facts.status.uppercase() == "ENDED") {
            select(queue?.currentEntryId ?: return, autoplay = true, forceNewSession = true)
        } else {
            val trackId = currentEntry()?.trackId ?: return
            if (media != null) {
                if (queue?.sessionId == null) {
                    startSession()
                    checkpoint()
                    openCurrent(autoplay = true)
                } else {
                    endAdvanceAllowed = true
                    publishMediaIntent(true)
                }
                return
            }
            if (store.resolveManagedFile(trackId) == null) failSource() else {
                if (queue?.sessionId == null) {
                    startSession()
                    checkpoint()
                }
                endAdvanceAllowed = true
                nativeCommand { engine.play(generation) }
            }
        }
    }

    private suspend fun requestPause(): Unit {
        if (!allowed(sourceRequired = false)) return
        accumulate()
        endAdvanceAllowed = false
        if (media != null) {
            publishMediaIntent(false)
            checkpoint()
            return
        }
        if (nativeCommand { engine.pause(generation) }) checkpoint()
    }

    private suspend fun requestSeek(positionMs: Long): Unit {
        if (!allowed(seek = true)) return
        val duration = facts.durationMs ?: return
        seekCurrent(positionMs.coerceIn(0, duration.coerceAtLeast(0)))
    }

    private suspend fun requestNext(): Unit {
        if (!allowed()) return
        neighbor(1)?.let { select(it, autoplay = facts.isPlaying && endAdvanceAllowed, forceNewSession = true) }
    }

    private suspend fun requestPrevious(): Unit {
        if (!allowed()) return
        if (facts.status.uppercase() != "ENDED" && facts.positionMs > 3_000 && (facts.durationMs ?: 0) > 0) {
            if (pendingSeekTarget == null) seekCurrent(0)
        } else neighbor(-1)?.let { select(it, autoplay = facts.isPlaying && endAdvanceAllowed, forceNewSession = true) }
    }

    private suspend fun seekCurrent(target: Long): Unit {
        accumulate()
        pendingSeekTarget = target
        pendingSeekStartedNanos = System.nanoTime()
        if (media != null) {
            mediaTarget()?.let { media.seek(it, target) }
            checkpoint()
            return
        }
        if (nativeCommand { engine.seek(generation, target) }) {
            // Dispatch acknowledgement is not seek completion. Persist only observed position.
            checkpoint()
        }
    }

    private suspend fun accept(event: DesktopAudioEvent): Unit {
        if (snapshotUnavailable.get()) return
        if (!validModes() || event.generation != generation || currentEntry() == null) return
        // Do not let a repeated EOF or an out-of-order callback resurrect a finished generation.
        if (endedGeneration == event.generation) return
        accumulate()
        val wasOpening = facts.status.uppercase() in setOf("BUFFERING", "OPENING")
        val terminal = isTerminalFailure(event)
        val seekTarget = pendingSeekTarget
        val seekConfirmed = seekTarget != null && event.status.uppercase() == "READY" &&
            event.positionMs.coerceAtLeast(0) in
                (seekTarget - SEEK_POSITION_TOLERANCE_MS).coerceAtLeast(0)..
                    (seekTarget + minOf(SEEK_POSITION_TOLERANCE_MS, Long.MAX_VALUE - seekTarget))
        facts = when {
            event.status.uppercase() in setOf("EOF", "ENDED") -> event.copy(status = "ENDED", isPlaying = false)
            else -> event
        }
        // BUFFERING is an intermediate frame, including the adapter's initial zero position.
        // It must not overwrite the saved open/seek checkpoint before native readiness.
        if ((!terminal || !wasOpening) && event.status.uppercase() !in setOf("BUFFERING", "OPENING") &&
            (seekTarget == null || seekConfirmed || event.status.uppercase() in setOf("EOF", "ENDED"))) {
            queue = queue?.copy(positionMs = event.positionMs.coerceAtLeast(0))
        }
        if (terminal) {
            timerPlan.clear()
            invalidateEngine()
            facts = event.copy(generation = generation,
                status = if (event.status.uppercase() == "UNAVAILABLE") "UNAVAILABLE" else "ERROR", isPlaying = false)
            setError(if (event.status.uppercase() == "UNAVAILABLE") "AUDIO_UNAVAILABLE" else "AUDIO_ERROR")
            checkpoint()
            return
        }
        if (event.status.uppercase() == "ENDED" || event.status.uppercase() == "EOF") {
            endedGeneration = event.generation
            pendingSeekTarget = null
            // A matching plan owns EOF before Repeat ONE/ALL or paused-restore handling.
            if (timerPlan.consumeAfter(queue?.id, currentEntry()?.id, queue?.sessionId)) {
                endAdvanceAllowed = false
                finishCurrentAtEnd()
                return
            }
            // An item opened at its saved end while paused must never start another item.
            if (!endAdvanceAllowed) {
                checkpoint()
                return
            }
            val next = if (queue?.repeatMode == "ONE") queue?.currentEntryId else neighbor(1)
            if (next != null && validModes()) select(next, autoplay = true, forceNewSession = true)
            else {
                timerPlan.clear()
                finishCurrentAtEnd()
            }
        } else {
            if (seekConfirmed) {
                pendingSeekTarget = null
            }
            val openingFrame = wasOpening && event.status.uppercase() in setOf("BUFFERING", "OPENING")
            if (seekConfirmed || (pendingSeekTarget == null && !event.isPlaying && !openingFrame)) checkpoint()
        }
        val track = libraryState.value.tracks.firstOrNull { it.id == currentEntry()?.trackId }
        if (track != null && event.generation == generation && event.status.uppercase() == "READY") {
            // Imported/user labels stay authoritative; native execution owns only duration.
            val duration = event.durationMs?.takeIf { it >= 0 }
            if (duration != null && duration != track.durationMs) {
                val owner = media
                if (owner != null) queue?.let { owner.persistDuration(it, track.id, duration) }
                else store.updateTrackDuration(track.id, duration)
            }
        }
    }

    private suspend fun select(entryId: String, autoplay: Boolean, forceNewSession: Boolean = false): Unit {
        val current = queue ?: return
        if (current.entries.none { it.id == entryId }) return
        accumulate()
        val oldEvent = listeningEvent(finalized = true)
        val same = entryId == current.currentEntryId && !forceNewSession
        if (!same) timerPlan.clearStopAfter()
        invalidateEngine()
        queue = requireNotNull(queue).copy(currentEntryId = entryId, positionMs = if (same) current.positionMs else 0,
            sessionId = if (same) current.sessionId else null,
            sessionStartedAtMs = if (same) current.sessionStartedAtMs else null, playedMs = if (same) queue?.playedMs ?: 0 else 0,
            listenTasteState = if (same) current.listenTasteState else DesktopTastePolicy.forNewListen(current.listenTasteState))
        if (queue?.sessionId == null) startSession()
        saveQueue(requireNotNull(queue), if (same) listeningEvent(false) else oldEvent)
        checkpoint()
        openCurrent(autoplay)
    }

    private suspend fun openCurrent(autoplay: Boolean, clearError: Boolean = true): Unit {
        if (snapshotUnavailable.get()) return
        val entry = currentEntry() ?: return
        if (!validModes()) return
        generation = DesktopMediaLimits.next(generation)
        endedGeneration = null
        sourceAvailable = false
        endAdvanceAllowed = autoplay
        pendingSeekTarget = null
        facts = DesktopAudioEvent(generation, "BUFFERING", queue?.positionMs ?: 0)
        lastElapsedNanos = System.nanoTime()
        if (media != null) {
            if (autoplay && queue?.sessionId == null) { startSession(); checkpoint() }
            // A restored ended queue has no active listen. Wait for explicit Play to create
            // the next real listen rather than inventing one merely to resolve a source.
            if (queue?.sessionId == null) {
                mediaWantsPlay = false
                facts = DesktopAudioEvent(generation, "PAUSED", queue?.positionMs ?: 0)
                sourceAvailable = true
                project()
                return
            }
            mediaSelectionRevision = DesktopMediaLimits.next(mediaSelectionRevision)
            mediaIntentRevision = DesktopMediaLimits.next(mediaIntentRevision)
            mediaWantsPlay = autoplay
            sourceAvailable = true
            if (clearError) setError(null)
            synchronized(contextGate) {
                // A synchronous shutdown/authority fence wins even during context restoration.
                if (!contextFenced.get()) mediaTarget()?.let(media::select)
            }
            project()
            return
        }
        val path = store.resolveManagedFile(entry.trackId)
        if (path == null) {
            failSource()
            return
        }
        if (autoplay && queue?.sessionId == null) {
            startSession()
            checkpoint()
        }
        if (snapshotUnavailable.get()) return
        if (clearError) setError(null)
        sourceAvailable = true
        nativeCommand { engine.open(path, generation, queue?.positionMs ?: 0, autoplay) }
        // A terminal adapter can reject open synchronously; do not leave the projection buffering.
        val opened = engine.state.value
        if (opened.generation == generation && isTerminalFailure(opened)) accept(opened)
        project()
    }

    private suspend fun failSource(): Unit {
        timerPlan.clear()
        accumulate()
        invalidateEngine()
        facts = DesktopAudioEvent(generation, "ERROR", queue?.positionMs ?: 0)
        setError("SOURCE_UNAVAILABLE")
        checkpoint()
    }

    private fun invalidateEngine(): Unit {
        val obsoleteGeneration = generation
        generation = DesktopMediaLimits.next(generation)
        endedGeneration = null
        sourceAvailable = false
        endAdvanceAllowed = false
        pendingSeekTarget = null
        facts = DesktopAudioEvent(generation, "IDLE")
        lastElapsedNanos = System.nanoTime()
        try {
            if (media != null) {
                mediaWantsPlay = false
                media.retire(obsoleteGeneration)
            } else engine.stop(obsoleteGeneration)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            timerPlan.clear()
            setError("AUDIO_ERROR")
        }
    }

    private suspend fun nativeCommand(action: () -> Unit): Boolean {
        if (snapshotUnavailable.get()) return false
        try {
            action()
            return true
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            timerPlan.clear()
            accumulate()
            invalidateEngine()
            facts = DesktopAudioEvent(generation, "ERROR", queue?.positionMs ?: 0)
            setError("AUDIO_ERROR")
            checkpoint()
            return false
        }
    }

    private fun startSession(): Unit {
        timerPlan.clearStopAfter()
        queue = queue?.let(::newSession)
    }

    private fun newSession(current: DesktopQueue): DesktopQueue =
        current.copy(sessionId = newId(), sessionStartedAtMs = System.currentTimeMillis(), playedMs = 0,
            listenTasteState = DesktopTastePolicy.forNewListen(current.listenTasteState))

    private suspend fun finishCurrentAtEnd(): Unit {
        val finalEvent = listeningEvent(finalized = true)
        queue = queue?.let { it.copy(sessionId = null, sessionStartedAtMs = null, playedMs = 0,
            listenTasteState = DesktopTastePolicy.forNewListen(it.listenTasteState)) }
        saveQueue(requireNotNull(queue), finalEvent)
    }

    private fun sleepTimerCommandsEnabled(): Boolean = !closed && queue?.sessionId != null &&
        (facts.status.uppercase() in setOf("READY", "PLAYING", "PAUSED") ||
            media != null && facts.status.uppercase() in setOf("BUFFERING", "OPENING")) && allowed()

    /** The existing actor/ticker observes expiry; this is not a hard OS or wall-clock deadline. */
    private suspend fun expireSleepTimer(): Unit {
        if (closed || snapshotUnavailable.get()) {
            timerPlan.clear()
            return
        }
        timerPlan.retainFor(queue?.id, currentEntry()?.id, queue?.sessionId)
        if (!timerPlan.consumeDue()) return
        accumulate()
        endAdvanceAllowed = false
        if (media != null) {
            mediaTimerRevision = DesktopMediaLimits.next(mediaTimerRevision)
            publishMediaIntent(false)
            checkpoint()
            return
        }
        // Keep native facts authoritative while awaiting pause; the logical autoplay gate
        // prevents late playing observations/navigation from reopening a playing item.
        if (nativeCommand { engine.pause(generation) }) checkpoint()
    }

    /** Synchronous lifecycle admission closes even if the command actor is doing persistence. */
    public fun mediaLifecycleVeto(): Unit {
        media?.lifecycleVeto()
        dispatch {
            mediaSleepRevision = DesktopMediaLimits.next(mediaSleepRevision)
            mediaWantsPlay = false
            endAdvanceAllowed = false
            timerPlan.clear()
            accumulate()
            checkpoint()
        }
    }

    private fun publishMediaIntent(wantsPlay: Boolean): Unit {
        val owner = media ?: return
        mediaWantsPlay = wantsPlay
        mediaIntentRevision = DesktopMediaLimits.next(mediaIntentRevision)
        mediaTarget()?.let(owner::intent)
    }

    private fun mediaTarget(): DesktopMediaActorTarget? {
        val current = queue ?: return null
        val entry = currentEntry() ?: return null
        if (generation == 0L || mediaSelectionRevision == 0L || mediaIntentRevision == 0L) return null
        return DesktopMediaActorTarget(current.id, entry.id, current.sessionId, entry.trackId, generation,
            mediaSelectionRevision, mediaIntentRevision, mediaSleepRevision, mediaTimerRevision,
            current.positionMs, mediaWantsPlay, mediaCopyQueue?.takeIf { it.first == current.id }?.second)
    }

    private fun accumulate(): Unit {
        val now = System.nanoTime()
        queue = queue?.let { accumulatedQueue(it, now) }
        lastElapsedNanos = now
    }

    /** Candidate admission must not consume elapsed time or assign speculative controller state. */
    private fun accumulatedQueue(current: DesktopQueue, now: Long): DesktopQueue {
        val elapsed = ((now - lastElapsedNanos).coerceAtLeast(0) / 1_000_000)
        if (!snapshotUnavailable.get() && facts.isPlaying && current.sessionId != null) {
            val played = current.playedMs.coerceAtLeast(0)
            return current.copy(playedMs = played + elapsed.coerceAtMost(Long.MAX_VALUE - played))
        }
        return current
    }

    private suspend fun checkpoint(): Unit {
        if (snapshotUnavailable.get()) return
        accumulate()
        queue?.let { saveQueue(it, listeningEvent(finalized = false)) }
        lastCheckpointNanos = System.nanoTime()
    }

    private suspend fun saveQueue(value: DesktopQueue, event: DesktopListeningEvent?): Unit {
        val owner = media
        if (owner != null) owner.persistQueue(value, event, mediaCopyQueue?.takeIf { it.first == value.id }?.second)
        else store.saveQueue(value, event)
    }

    private fun listeningEvent(finalized: Boolean, current: DesktopQueue? = queue): DesktopListeningEvent? {
        current ?: return null
        val entry = current.entries.firstOrNull { it.id == current.currentEntryId } ?: return null
        return DesktopListeningEvent(current.sessionId ?: return null, entry.id, entry.trackId,
            current.sessionStartedAtMs ?: return null, current.playedMs, current.positionMs, finalized,
            current.sessionTasteState, current.listenTasteState)
    }

    private fun currentEntry(): DesktopQueueEntry? = queue?.entries?.firstOrNull { it.id == queue?.currentEntryId }

    private fun validModes(): Boolean = queue?.let {
        it.queueType in ORDINARY_TYPES && it.repeatMode in REPEAT_MODES &&
            it.entries.size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES && it.entries.map { entry -> entry.id }.distinct().size == it.entries.size &&
            (it.currentEntryId == null || it.entries.any { entry -> entry.id == it.currentEntryId }) &&
            it.shuffleOrder.size == it.entries.size && it.shuffleOrder.toSet() == it.entries.map { entry -> entry.id }.toSet()
    } ?: false

    private fun order(): List<String> = queue?.let { if (it.shuffleEnabled) it.shuffleOrder else it.entries.map { entry -> entry.id } } ?: emptyList()

    private fun neighbor(direction: Int): String? {
        if (!validModes()) return null
        val ids = order()
        val index = ids.indexOf(queue?.currentEntryId)
        if (index < 0 || ids.isEmpty()) return null
        return ids.getOrNull(index + direction) ?: if (queue?.repeatMode == "ALL") {
            if (direction > 0) ids.first() else ids.last()
        } else null
    }

    private fun context(): ActiveQueueContext = queue?.let {
        ActiveQueueContext.Loaded(it.id, it.currentEntryId, it.queueType)
    } ?: ActiveQueueContext.Absent

    private fun allowed(sourceRequired: Boolean = true, seek: Boolean = false): Boolean = PlaybackCommandGate.evaluate(
        context(), currentEntry()?.id, commandAvailable = !snapshotUnavailable.get() && !contextFenced.get() && validModes() &&
            (facts.status.uppercase() in COMMAND_STATUSES || media != null && facts.status.uppercase() == "BUFFERING"),
        sourceAvailable = !sourceRequired || sourceAvailable,
        seekable = if (seek) pendingSeekTarget == null && (facts.durationMs ?: 0) > 0 && facts.status.uppercase() != "ENDED" else null,
    ) == PlaybackControlGate.Allowed

    private fun tasteCommandsEnabled(): Boolean = !closed && !snapshotUnavailable.get() && !contextFenced.get() &&
        validModes() && queue?.entries?.isNotEmpty() == true

    /** Only execution identity/topology/modes retire a revision; labels and clocks do not. */
    private fun queueEditorState(): DesktopQueueEditorState {
        val current = queue
        val currentEntryId = current?.currentEntryId
        val shape = if (!closed && !snapshotUnavailable.get() && !contextFenced.get() && current != null && validModes() &&
            currentEntryId != null && current.id.isNotBlank() &&
            current.entries.all { it.id.isNotBlank() && it.trackId.isNotBlank() }) {
            QueueEditorShape(current.id, currentEntryId, current.sessionId, generation,
                current.queueType, current.repeatMode, current.shuffleEnabled,
                Collections.unmodifiableList(current.entries.toList()),
                Collections.unmodifiableList(current.shuffleOrder.toList()))
        } else null
        if (shape != queueEditorShape) {
            queueEditorShape = shape
            // Exhaustion permanently disables new owners rather than recycling a revision.
            if (queueEditorRevision < Long.MAX_VALUE) {
                queueEditorRevision += 1
                queueEditorTarget = shape?.let {
                    DesktopQueueEditTarget(it.queueId, it.currentEntryId, it.sessionId, it.generation, queueEditorRevision)
                }
            } else queueEditorTarget = null
        }
        shape ?: return DesktopQueueEditorState()
        val target = queueEditorTarget ?: return DesktopQueueEditorState()
        val byId = shape.entries.associateBy { it.id }
        val activeEntries = if (shape.shuffleEnabled) shape.shuffleOrder.map { byId.getValue(it) } else shape.entries
        return DesktopQueueEditorState(
            target = target,
            entries = Collections.unmodifiableList(activeEntries.toList()),
            currentEntryId = shape.currentEntryId,
            shuffleEnabled = shape.shuffleEnabled,
        )
    }

    private fun project(observeSleepTimer: Boolean = true): Unit {
        val entry = currentEntry()
        val track = libraryState.value.tracks.firstOrNull { it.id == entry?.trackId }
        val status = facts.status.uppercase()
        val playback = PlaybackPresentationState(
            mediaId = entry?.id, title = track?.title, artist = track?.artist,
            positionMs = queue?.positionMs ?: 0, durationMs = facts.durationMs ?: track?.durationMs,
            isSeekable = (facts.durationMs ?: 0) > 0, isPlaying = facts.isPlaying,
            connection = PlaybackConnectionState.Connected,
            playbackStatus = when (status) {
                "READY", "PLAYING", "PAUSED" -> PlaybackStatus.Ready
                "BUFFERING", "OPENING" -> PlaybackStatus.Buffering
                "ENDED", "EOF" -> PlaybackStatus.Ended
                else -> PlaybackStatus.Idle
            },
            source = if (sourceAvailable) PlaybackSourcePresentation.Local else PlaybackSourcePresentation.Unknown,
            context = context(),
            controls = PlaybackCommandGate.evaluate(context(), entry?.id,
                commandAvailable = !snapshotUnavailable.get() && !contextFenced.get() && validModes() &&
                    (status in COMMAND_STATUSES || media != null && status == "BUFFERING"), sourceAvailable = sourceAvailable),
            seekEnabled = allowed(seek = true), shuffleEnabled = !snapshotUnavailable.get() && !contextFenced.get() && validModes(),
            repeatEnabled = !snapshotUnavailable.get() && !contextFenced.get() && validModes(),
            shuffleModeEnabled = queue?.shuffleEnabled == true,
            repeatMode = when (queue?.repeatMode) { "ONE" -> RepeatModePresentation.One; "ALL" -> RepeatModePresentation.All; else -> RepeatModePresentation.Off },
            previousMediaId = neighbor(-1), nextMediaId = neighbor(1), localTrackRefId = entry?.trackId,
        )
        mutablePlayerState.value = DesktopPlayerState(
            playback = playback,
            target = queue?.let { current -> entry?.let { DesktopPlaybackTarget(current.id, it.id, current.sessionId, generation) } },
            queueEditor = queueEditorState(),
        )
        mutableState.value = playback
        if (observeSleepTimer) {
            timerPlan.retainFor(queue?.id, entry?.id, queue?.sessionId)
            mutableSleepTimer.value = timerPlan.project(queue?.id, entry?.id, queue?.sessionId, sleepTimerCommandsEnabled())
        }
        mutableTaste.value = DesktopTasteState(
            queueId = queue?.id, currentEntryId = entry?.id, listeningEventId = queue?.sessionId,
            sessionTasteState = queue?.sessionTasteState ?: DesktopTastePolicy.INCLUDED,
            listenTasteState = queue?.listenTasteState ?: DesktopTastePolicy.INCLUDED,
            listenCommandsEnabled = tasteCommandsEnabled() && entry != null &&
                queue?.sessionId != null && queue?.sessionStartedAtMs != null,
            sessionCommandsEnabled = tasteCommandsEnabled(), errorCode = tasteError.get(),
        )
    }

    private fun handleFailure(failure: Exception): Unit {
        if (desktopSnapshotUnavailable(failure)) snapshotUnavailable.set(true)
        if (failure is CancellationException && !snapshotUnavailable.get()) throw failure
        if (!snapshotUnavailable.get() && failure is DesktopStorageException &&
            failure.code == "desktop_track_not_in_collection") {
            setQueueEditError(failure.code)
            project()
            return
        }
        timerPlan.clear()
        // Never expose exception text, which can contain a personal filesystem path.
        sourceAvailable = false
        runCatching { invalidateEngine() }
        setError("PLAYBACK_OPERATION_FAILED")
        // Failed persistence must not leave a speculative replacement as the controller's queue.
        queue = libraryState.value.queue
        project()
        if (failure is CancellationException) throw failure
    }

    private fun setError(code: String?): Unit {
        mutableError.update { current ->
            if (snapshotUnavailable.get() || current == "desktop_snapshot_unavailable") "desktop_snapshot_unavailable" else code
        }
    }

    private fun setQueueEditError(code: String): Unit {
        mutableError.update { current ->
            when {
                snapshotUnavailable.get() || current == "desktop_snapshot_unavailable" -> "desktop_snapshot_unavailable"
                current?.startsWith("AUDIO_") == true || current == "SOURCE_UNAVAILABLE" -> current
                else -> code
            }
        }
    }

    private fun clearQueueLimitError(): Unit {
        mutableError.update { current ->
            if (snapshotUnavailable.get()) "desktop_snapshot_unavailable" else if (current == "QUEUE_LIMIT") null else current
        }
    }

    private companion object {
        const val CHECKPOINT_NANOS: Long = 15_000_000_000
        const val SEEK_CONFIRM_NANOS: Long = 10_000_000_000
        // Matches the existing native paused/near-EOF seek acceptance window.
        const val SEEK_POSITION_TOLERANCE_MS: Long = 150
        val ORDINARY_TYPES: Set<String> = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
        val REPEAT_MODES: Set<String> = setOf("OFF", "ONE", "ALL")
        val COMMAND_STATUSES: Set<String> = setOf("READY", "PLAYING", "PAUSED", "ENDED")
        fun isTerminalFailure(event: DesktopAudioEvent): Boolean =
            event.status.uppercase() in setOf("ERROR", "CLOSED", "UNAVAILABLE") || event.errorCode != null
        fun newId(): String = UUID.randomUUID().toString()
    }
}
