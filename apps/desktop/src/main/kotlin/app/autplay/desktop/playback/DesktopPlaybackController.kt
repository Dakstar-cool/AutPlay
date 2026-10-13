package app.autplay.desktop.playback

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.platform.PlaybackPort
import app.autplay.playback.presentation.PlaybackCommandGate
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.RepeatModePresentation
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
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
) : PlaybackPort {
    private val mutex: Mutex = Mutex()
    private val closeMutex: Mutex = Mutex()
    private val commands: Channel<suspend () -> Unit> = Channel(64)
    private val mutableState: MutableStateFlow<PlaybackPresentationState> = MutableStateFlow(PlaybackPresentationState())
    override val state: StateFlow<PlaybackPresentationState> = mutableState.asStateFlow()
    private val mutableError: MutableStateFlow<String?> = MutableStateFlow(null)
    public val error: StateFlow<String?> = mutableError.asStateFlow()
    private var queue: DesktopQueue? = null
    private var generation: Long = 0
    private var endedGeneration: Long? = null
    private var facts: DesktopAudioEvent = DesktopAudioEvent(0, "IDLE")
    private var sourceAvailable: Boolean = false
    private var endAdvanceAllowed: Boolean = false
    @Volatile private var closed: Boolean = false
    private var lastElapsedNanos: Long = System.nanoTime()
    private var lastCheckpointNanos: Long = lastElapsedNanos
    private val jobs: MutableList<Job> = mutableListOf()

    init {
        jobs += scope.launch {
            mutex.withLock {
                if (closed) return@withLock
                try {
                    queue = store.state.value.queue
                    if (queue?.currentEntryId != null && validModes()) {
                        // A restored logical session keeps its identity and always opens paused.
                        openCurrent(autoplay = false)
                    } else {
                        if (queue != null && !validModes()) mutableError.value = "UNSUPPORTED_QUEUE_MODE"
                        project()
                    }
                } catch (failure: Exception) {
                    handleFailure(failure)
                }
            }
            for (action in commands) {
                mutex.withLock {
                    if (!closed) try {
                        action()
                        project()
                    } catch (failure: Exception) {
                        handleFailure(failure)
                    }
                }
            }
        }
        jobs += scope.launch {
            engine.state.collect { event -> commands.send { accept(event) } }
        }
        jobs += scope.launch {
            store.state.collect { commands.send { project() } }
        }
        jobs += scope.launch {
            while (true) {
                delay(1_000)
                commands.send {
                    accumulate()
                    if (facts.isPlaying && System.nanoTime() - lastCheckpointNanos >= CHECKPOINT_NANOS) checkpoint()
                }
            }
        }
    }

    override fun play(): Unit = dispatch {
        requestPlay()
    }

    override fun pause(): Unit = dispatch {
        if (!allowed(sourceRequired = false)) return@dispatch
        accumulate()
        endAdvanceAllowed = false
        if (nativeCommand { engine.pause(generation) }) checkpoint()
    }

    override fun seekTo(positionMs: Long): Unit = dispatch {
        if (!allowed(seek = true)) return@dispatch
        val duration = facts.durationMs ?: return@dispatch
        val target = positionMs.coerceIn(0, duration.coerceAtLeast(0))
        accumulate()
        if (nativeCommand { engine.seek(generation, target) }) {
            queue = queue?.copy(positionMs = target)
            checkpoint()
        }
    }

    override fun next(): Unit = dispatch {
        if (!allowed()) return@dispatch
        neighbor(1)?.let { select(it, autoplay = facts.isPlaying, forceNewSession = true) }
    }

    override fun previous(): Unit = dispatch {
        if (!allowed()) return@dispatch
        if (facts.status.uppercase() != "ENDED" && facts.positionMs > 3_000 && (facts.durationMs ?: 0) > 0) {
            accumulate()
            if (nativeCommand { engine.seek(generation, 0) }) {
                queue = queue?.copy(positionMs = 0)
                checkpoint()
            }
        } else neighbor(-1)?.let { select(it, autoplay = facts.isPlaying, forceNewSession = true) }
    }

    public fun playTracks(trackIds: List<String>, queueType: String = "LIBRARY"): Unit = dispatch {
        if (queueType !in ORDINARY_TYPES) return@dispatch
        val known = store.state.value.tracks.map { it.id }.toHashSet()
        val entries = trackIds.take(MAX_QUEUE_SIZE).filter { it in known }.map { DesktopQueueEntry(newId(), it) }
        if (entries.isEmpty()) return@dispatch
        accumulate()
        val previousEvent = listeningEvent(finalized = true)
        invalidateEngine()
        queue = DesktopQueue(newId(), entries, entries.first().id, queueType)
        startSession()
        store.saveQueue(requireNotNull(queue), previousEvent)
        checkpoint()
        openCurrent(autoplay = true)
    }

    /** Appending the same track deliberately allocates a distinct queue-entry ID. */
    public fun enqueue(trackId: String): Unit = dispatch {
        if (store.state.value.tracks.none { it.id == trackId }) return@dispatch
        val entry = DesktopQueueEntry(newId(), trackId)
        val current = queue
        if (current == null || current.entries.isEmpty()) {
            queue = DesktopQueue(newId(), listOf(entry), entry.id)
            startSession()
            checkpoint()
            openCurrent(autoplay = false)
        } else if (validModes() && current.entries.size < MAX_QUEUE_SIZE) {
            accumulate()
            queue = current.copy(entries = current.entries + entry, shuffleOrder = order() + entry.id,
                playedMs = queue?.playedMs ?: current.playedMs)
            checkpoint()
        } else mutableError.value = "QUEUE_LIMIT_OR_MODE"
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
        val wasPlaying = facts.isPlaying
        val ordered = order()
        val index = ordered.indexOf(entryId)
        val remaining = current.entries.filterNot { it.id == entryId }
        val newOrder = ordered.filterNot { it == entryId }
        if (entryId == current.currentEntryId) {
            invalidateEngine()
            val replacement = newOrder.getOrNull(index) ?: newOrder.lastOrNull()
            queue = requireNotNull(queue).copy(entries = remaining, shuffleOrder = newOrder,
                currentEntryId = replacement, positionMs = 0, sessionId = null, sessionStartedAtMs = null, playedMs = 0)
            if (replacement != null) startSession()
            store.saveQueue(requireNotNull(queue), event)
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
                try {
                    accumulate()
                    // Checkpoint still runs if the native process disappeared before pause.
                    try { engine.pause(generation) } finally { checkpoint() }
                } finally {
                    closed = true
                    invalidateEngine()
                }
            }
        } finally {
            // This also runs if cancellation happened while waiting for the command mutex.
            withContext(NonCancellable) {
                closed = true
                jobs.forEach { it.cancel() }
                commands.close()
                try {
                    withTimeout(5_000) { jobs.joinAll() }
                } finally {
                    withTimeout(5_000) { engine.close() }
                }
            }
        }
    }

    private fun dispatch(action: suspend () -> Unit): Unit {
        if (!commands.trySend(action).isSuccess && !closed) mutableError.value = "PLAYBACK_COMMAND_BUSY"
    }

    private suspend fun requestPlay(): Unit {
        if (!allowed()) return
        if (facts.status.uppercase() == "ENDED") {
            select(queue?.currentEntryId ?: return, autoplay = true, forceNewSession = true)
        } else {
            val trackId = currentEntry()?.trackId ?: return
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

    private suspend fun accept(event: DesktopAudioEvent): Unit {
        if (event.generation != generation || currentEntry() == null) return
        // Do not let a repeated EOF or an out-of-order callback resurrect a finished generation.
        if (endedGeneration == event.generation) return
        accumulate()
        facts = if (event.status.uppercase() in setOf("EOF", "ENDED")) event.copy(status = "ENDED", isPlaying = false) else event
        queue = queue?.copy(positionMs = event.positionMs.coerceAtLeast(0))
        if (event.status.uppercase() in setOf("ERROR", "CLOSED", "UNAVAILABLE") || event.errorCode != null) {
            invalidateEngine()
            facts = event.copy(generation = generation, status = "ERROR", isPlaying = false)
            mutableError.value = if (event.status.uppercase() == "UNAVAILABLE") "AUDIO_UNAVAILABLE" else "AUDIO_ERROR"
            checkpoint()
            return
        }
        if (event.status.uppercase() == "ENDED" || event.status.uppercase() == "EOF") {
            endedGeneration = event.generation
            // An item opened at its saved end while paused must never start another item.
            val next = if (!endAdvanceAllowed) null else if (queue?.repeatMode == "ONE") queue?.currentEntryId else neighbor(1)
            if (next != null && validModes()) select(next, autoplay = true, forceNewSession = true)
            else {
                val finalEvent = listeningEvent(finalized = true)
                queue = queue?.copy(sessionId = null, sessionStartedAtMs = null, playedMs = 0)
                store.saveQueue(requireNotNull(queue), finalEvent)
            }
        } else if (!event.isPlaying) checkpoint()
        val track = store.state.value.tracks.firstOrNull { it.id == currentEntry()?.trackId }
        if (track != null && event.generation == generation && event.status.uppercase() == "READY") {
            val title = event.title?.takeIf { it.isNotBlank() }?.take(512) ?: track.title
            val artist = event.artist?.take(512) ?: track.artist
            val duration = event.durationMs?.takeIf { it >= 0 } ?: track.durationMs
            if (title != track.title || artist != track.artist || duration != track.durationMs) {
                store.updateTrackMetadata(track.id, title, artist, duration)
            }
        }
    }

    private suspend fun select(entryId: String, autoplay: Boolean, forceNewSession: Boolean = false): Unit {
        val current = queue ?: return
        if (current.entries.none { it.id == entryId }) return
        accumulate()
        val oldEvent = listeningEvent(finalized = true)
        val same = entryId == current.currentEntryId && !forceNewSession
        invalidateEngine()
        queue = requireNotNull(queue).copy(currentEntryId = entryId, positionMs = if (same) current.positionMs else 0,
            sessionId = if (same) current.sessionId else null,
            sessionStartedAtMs = if (same) current.sessionStartedAtMs else null, playedMs = if (same) queue?.playedMs ?: 0 else 0)
        if (queue?.sessionId == null) startSession()
        store.saveQueue(requireNotNull(queue), if (same) listeningEvent(false) else oldEvent)
        checkpoint()
        openCurrent(autoplay)
    }

    private suspend fun openCurrent(autoplay: Boolean): Unit {
        val entry = currentEntry() ?: return
        if (!validModes()) return
        generation += 1
        endedGeneration = null
        sourceAvailable = false
        endAdvanceAllowed = autoplay
        facts = DesktopAudioEvent(generation, "BUFFERING", queue?.positionMs ?: 0)
        lastElapsedNanos = System.nanoTime()
        val path = store.resolveManagedFile(entry.trackId)
        if (path == null) {
            failSource()
            return
        }
        if (autoplay && queue?.sessionId == null) {
            startSession()
            checkpoint()
        }
        mutableError.value = null
        sourceAvailable = true
        nativeCommand { engine.open(path, generation, queue?.positionMs ?: 0, autoplay) }
        project()
    }

    private suspend fun failSource(): Unit {
        accumulate()
        invalidateEngine()
        facts = DesktopAudioEvent(generation, "ERROR", queue?.positionMs ?: 0)
        mutableError.value = "SOURCE_UNAVAILABLE"
        checkpoint()
    }

    private fun invalidateEngine(): Unit {
        val obsoleteGeneration = generation
        generation += 1
        endedGeneration = null
        sourceAvailable = false
        endAdvanceAllowed = false
        facts = DesktopAudioEvent(generation, "IDLE")
        lastElapsedNanos = System.nanoTime()
        try {
            engine.stop(obsoleteGeneration)
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            mutableError.value = "AUDIO_ERROR"
        }
    }

    private suspend fun nativeCommand(action: () -> Unit): Boolean {
        try {
            action()
            return true
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            accumulate()
            invalidateEngine()
            facts = DesktopAudioEvent(generation, "ERROR", queue?.positionMs ?: 0)
            mutableError.value = "AUDIO_ERROR"
            checkpoint()
            return false
        }
    }

    private fun startSession(): Unit {
        queue = queue?.copy(sessionId = newId(), sessionStartedAtMs = System.currentTimeMillis(), playedMs = 0)
    }

    private fun accumulate(): Unit {
        val now = System.nanoTime()
        val elapsed = ((now - lastElapsedNanos).coerceAtLeast(0) / 1_000_000)
        if (facts.isPlaying && queue?.sessionId != null) {
            queue = queue?.let { it.copy(playedMs = (it.playedMs.coerceAtLeast(0) + elapsed).coerceAtLeast(it.playedMs)) }
        }
        lastElapsedNanos = now
    }

    private suspend fun checkpoint(): Unit {
        accumulate()
        queue?.let { store.saveQueue(it, listeningEvent(finalized = false)) }
        lastCheckpointNanos = System.nanoTime()
    }

    private fun listeningEvent(finalized: Boolean): DesktopListeningEvent? {
        val current = queue ?: return null
        val entry = currentEntry() ?: return null
        return DesktopListeningEvent(current.sessionId ?: return null, entry.id, entry.trackId,
            current.sessionStartedAtMs ?: return null, current.playedMs, current.positionMs, finalized)
    }

    private fun currentEntry(): DesktopQueueEntry? = queue?.entries?.firstOrNull { it.id == queue?.currentEntryId }

    private fun validModes(): Boolean = queue?.let {
        it.queueType in ORDINARY_TYPES && it.repeatMode in REPEAT_MODES &&
            it.entries.size <= MAX_QUEUE_SIZE && it.entries.map { entry -> entry.id }.distinct().size == it.entries.size &&
            (!it.shuffleEnabled || (it.shuffleOrder.size == it.entries.size && it.shuffleOrder.toSet() == it.entries.map { entry -> entry.id }.toSet()))
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
        context(), currentEntry()?.id, commandAvailable = validModes() && facts.status.uppercase() in COMMAND_STATUSES,
        sourceAvailable = !sourceRequired || sourceAvailable,
        seekable = if (seek) (facts.durationMs ?: 0) > 0 && facts.status.uppercase() != "ENDED" else null,
    ) == PlaybackControlGate.Allowed

    private fun project(): Unit {
        val entry = currentEntry()
        val track = store.state.value.tracks.firstOrNull { it.id == entry?.trackId }
        val status = facts.status.uppercase()
        mutableState.value = PlaybackPresentationState(
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
                commandAvailable = validModes() && status in COMMAND_STATUSES, sourceAvailable = sourceAvailable),
            seekEnabled = allowed(seek = true), shuffleEnabled = validModes(), repeatEnabled = validModes(),
            shuffleModeEnabled = queue?.shuffleEnabled == true,
            repeatMode = when (queue?.repeatMode) { "ONE" -> RepeatModePresentation.One; "ALL" -> RepeatModePresentation.All; else -> RepeatModePresentation.Off },
            previousMediaId = neighbor(-1), nextMediaId = neighbor(1), localTrackRefId = entry?.trackId,
        )
    }

    private fun handleFailure(failure: Exception): Unit {
        if (failure is CancellationException) throw failure
        // Never expose exception text, which can contain a personal filesystem path.
        mutableError.value = "PLAYBACK_OPERATION_FAILED"
        sourceAvailable = false
        runCatching { invalidateEngine() }
        // Failed persistence must not leave a speculative replacement as the controller's queue.
        queue = store.state.value.queue
        project()
    }

    private companion object {
        const val CHECKPOINT_NANOS: Long = 15_000_000_000
        const val MAX_QUEUE_SIZE: Int = 10_000
        val ORDINARY_TYPES: Set<String> = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
        val REPEAT_MODES: Set<String> = setOf("OFF", "ONE", "ALL")
        val COMMAND_STATUSES: Set<String> = setOf("READY", "PLAYING", "PAUSED", "ENDED")
        fun newId(): String = UUID.randomUUID().toString()
    }
}
