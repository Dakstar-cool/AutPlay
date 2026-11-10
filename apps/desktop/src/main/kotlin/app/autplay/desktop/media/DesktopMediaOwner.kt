package app.autplay.desktop.media

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.media.*
import app.autplay.desktop.audio.DesktopAudioOwnerArbiter
import app.autplay.desktop.audio.DesktopAudioOwnerPhase
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** Sole process media coordinator. The queue actor retains every logical playback decision. */
internal class DesktopMediaOwner(
    private val sources: DesktopMediaActorSourcePort,
    private val resolver: DesktopMediaSourcePort,
    private val native: DesktopMediaNativeOwner,
    private val work: DesktopMediaWorkOwner,
    private val persistence: DesktopMediaQueueOwner,
    initialLocal: DesktopAudioEngine?,
    private val createLocal: () -> DesktopAudioEngine,
) : DesktopMediaPlaybackPort {
    private val gate = Any()
    private val applying = Mutex()
    private val closed = AtomicBoolean(false)
    private val veto = AtomicBoolean(false)
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private val intentWake = Channel<Unit>(Channel.CONFLATED)
    private val facts = MutableStateFlow(DesktopAudioEvent(0, "IDLE"))
    private val ready = MutableSharedFlow<DesktopMediaPrepared>(replay = 1, extraBufferCapacity = 1)
    override val state = facts.asStateFlow()
    override val prepared = ready.asSharedFlow()
    private var latest: DesktopMediaActorTarget? = null
    private var acknowledged: DesktopMediaActorTarget? = null
    @Volatile private var local: DesktopAudioEngine? = initialLocal
    private var localFacts: Job? = null
    @Volatile private var active: Candidate? = null
    private var quiescent = kotlinx.coroutines.CompletableDeferred<Unit>().apply { complete(Unit) }
    private var pendingSeek: Pair<DesktopMediaActorTarget, Long>? = null
    private var transition: Job? = null
    private var seekRevision = 0L
    private val done = kotlinx.coroutines.CompletableDeferred<Unit>()

    private class Candidate(val target: DesktopMediaActorTarget, val source: DesktopMediaActorSource,
        val handle: MediaSourceHandle?, val native: DesktopMediaNativeOwner.Source?) {
        var activated = false
        var failedRetained = false
    }

    init {
        native.onAccessRenewed = { intentWake.trySend(Unit); Unit }
        persistence.onFailure = { queue, failure ->
            val desired = synchronized(gate) { latest?.takeIf { it.queueId == queue.id } }
            if (desired != null) {
                active?.native?.let { native.retire(it, Wire.RetireReason.ERROR) }
                publishFailure(desired, failure)
            }
        }
        initialLocal?.let(::observeLocal)
        work.scope.launch {
            for (ignored in wake) {
                if (closed.get()) break
                val desired = synchronized(gate) { latest }
                if (desired == null || veto.get()) {
                    val completion = synchronized(gate) { quiescent }
                    try {
                        mediaCheck(drainActive(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                        local?.let { engine ->
                            withContext(NonCancellable) { engine.close() }
                            mediaCheck(DesktopAudioOwnerArbiter.state.value.phase == DesktopAudioOwnerPhase.NONE,
                                MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                            localFacts?.cancel(); local = null
                        }
                        completion.complete(Unit)
                    } catch (failure: Exception) { completion.completeExceptionally(failure) }
                    continue
                }
                val previous = active
                if (previous != null && previous.target.sameSelection(desired)) {
                    try { applyLatest(previous) }
                    catch (failure: Exception) { publishFailure(desired, failure) }
                    continue
                }
                val execution = work.scope.launch(start = CoroutineStart.LAZY) {
                  try {
                    if (!drainActive()) return@launch
                    persistence.awaitPending()
                    // Protected reads retain their actual result even if a newer selection
                    // arrives. Resolver work below has its own cancellable/late-result owner.
                    var source = withContext(NonCancellable) { sources.capture(desired) }
                    if (!isCurrent(desired)) return@launch
                    var handle: MediaSourceHandle? = null
                    var slot: DesktopMediaNativeOwner.Source? = null
                    try {
                        when (source) {
                            is DesktopMediaActorSource.Standalone -> {
                                if (local == null) {
                                    mediaCheck(DesktopAudioOwnerArbiter.state.value.phase == DesktopAudioOwnerPhase.NONE,
                                        MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF)
                                    local = createLocal().also(::observeLocal)
                                }
                            }
                            is DesktopMediaActorSource.Account -> {
                                var accountSource: DesktopMediaActorSource.Account = source
                                // K/J, source GET, HEAD and resource work are outside the actor lock.
                                var result = resolver.resolve(accountSource.capture, accountSource.intent)
                                val localCapture = accountSource.capture as? SelectionCapture.AccountLocal
                                val playbackIntent = accountSource.intent as? SourceIntent.VaultPlayback
                                if (localCapture != null && playbackIntent != null && result is SourceResolution.Unknown &&
                                    result.failure.code == MediaCode.SOURCE_UNKNOWN && result.localAvailability == LocalAvailability.ABSENT) {
                                    val remote = sources.network(localCapture, playbackIntent)
                                        ?: throw mediaFailure(MediaCode.SOURCE_UNKNOWN, MediaStage.RESOLVE)
                                    accountSource = remote
                                    result = resolver.resolve(remote.capture, remote.intent)
                                }
                                when (result) {
                                    is SourceResolution.Allow -> handle = result.handle
                                    is SourceResolution.Deny -> throw MediaSourceException(result.failure)
                                    is SourceResolution.Unknown -> throw MediaSourceException(result.failure)
                                    is SourceResolution.Offline -> throw mediaFailure(MediaCode.OFFLINE, MediaStage.RESOLVE)
                                }
                                if (handle is VaultPermit) {
                                    accountSource = DesktopMediaActorSource.Account((handle as VaultPermit).capture, accountSource.intent)
                                    slot = native.findSource(requireNotNull(handle).copyId, accountSource.capture)
                                        ?: throw mediaFailure(MediaCode.RESOURCE_STALE, MediaStage.HANDOFF)
                                } else {
                                    val capture = when (val captured = accountSource.capture) {
                                        is SelectionCapture.AccountLocal -> captured
                                        is SelectionCapture.VaultNetwork -> SelectionCapture.AccountLocal(captured.fence, captured.network.local)
                                        else -> throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
                                    }
                                    slot = native.installLocal(capture, accountSource.intent, requireNotNull(handle))
                                }
                                source = accountSource
                            }
                        }
                        val candidate = Candidate(desired, source, handle, slot)
                        if (!isCurrent(desired)) { drain(candidate); return@launch }
                        active = candidate
                        // Completion goes back through the original queue actor. Only that actor
                        // can acknowledge this candidate after queued Pause/timer controls reduce.
                        mediaCheck(ready.tryEmit(DesktopMediaPrepared(desired)), MediaCode.CAPACITY_EXCEEDED, MediaStage.HANDOFF)
                    } catch (failure: Exception) {
                        withContext(NonCancellable) {
                            if (slot != null) { native.retire(slot, Wire.RetireReason.ERROR); native.release(slot) }
                            handle?.let { it.retire(); it.awaitReleased() }
                        }
                        throw failure
                    }
                } catch (failure: Exception) {
                    if (failure !is CancellationException && isCurrent(desired)) publishFailure(desired, failure)
                  }
                }
                synchronized(gate) {
                    transition = execution
                    if (!isCurrent(desired)) execution.cancel()
                }
                execution.start()
                execution.join()
                synchronized(gate) { if (transition === execution) transition = null }
            }
            withContext(NonCancellable) {
                try {
                    mediaCheck(drainActive(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                    local?.close()
                    local = null
                    persistence.close()
                    val actual = native.close()
                    mediaCheck(actual.kind == RemoteMediaReleaseKind.DRAINED && actual.processExitConfirmed &&
                        actual.pipeReaderSettled && actual.pipeWriterSettled && actual.hostCallbacksSettled,
                        MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                    done.complete(Unit)
                    synchronized(gate) { quiescent }.complete(Unit)
                } catch (failure: Exception) {
                    done.completeExceptionally(failure)
                    synchronized(gate) { quiescent }.completeExceptionally(failure)
                }
            }
        }
        work.scope.launch {
            for (ignored in intentWake) {
                val candidate = active ?: continue
                try { applyLatest(candidate) }
                catch (failure: Exception) { publishFailure(candidate.target, failure) }
            }
        }
        work.scope.launch {
            for (event in native.actorEvents) {
                val candidate = active ?: continue
                val desired = synchronized(gate) { latest }
                if (candidate.native?.key != event.source || desired == null || !candidate.target.sameSelection(desired) ||
                    event.fence != fence(desired) || candidate.native.audio?.id != event.audioLeaseId || veto.get()) continue
                facts.value = DesktopAudioEvent(desired.generation, event.status.name, event.positionMs,
                    event.durationMs, event.isPlaying, event.code?.name)
            }
        }
        work.scope.launch {
            while (!closed.get()) {
                delay(5_000)
                if (closed.get()) break
                val candidate = active ?: continue
                candidate.native?.let { slot ->
                    try { if (!slot.retiring && !slot.accessWait && slot.resource == null) native.renew(slot) }
                    catch (failure: Exception) {
                        native.retire(slot, Wire.RetireReason.AUTHORITY)
                        publishFailure(candidate.target, failure)
                    }
                }
            }
        }
    }

    override fun select(target: DesktopMediaActorTarget): Unit {
        if (closed.get() || veto.get()) return
        synchronized(gate) {
            val previous = latest
            mediaCheck(previous == null || target.desiredIntentRevision > previous.desiredIntentRevision,
                MediaCode.INTENT_STALE, MediaStage.HANDOFF)
            latest = target; acknowledged = null
            transition?.cancel()
            active?.native?.let { native.retire(it, Wire.RetireReason.SELECTION) }
        }
        native.captureIntent(fence(target), target.wantsPlay)
        wake.trySend(Unit)
    }
    override fun intent(target: DesktopMediaActorTarget): Unit {
        if (closed.get()) return
        synchronized(gate) {
            val previous = latest ?: return
            if (!previous.sameSelection(target) || target.desiredIntentRevision <= previous.desiredIntentRevision) return
            latest = target
        }
        native.captureIntent(fence(target), target.wantsPlay)
        if (!target.wantsPlay) synchronized(gate) {
            active?.takeIf { it.native == null && it.target.sameSelection(target) }?.let { local?.pause(it.target.generation) }
        }
        intentWake.trySend(Unit)
    }
    override fun admitPrepared(target: DesktopMediaActorTarget): Unit {
        synchronized(gate) {
            val desired = latest ?: return
            if (!desired.sameSelection(target) || veto.get() || closed.get()) return
            acknowledged = desired
        }
        wake.trySend(Unit)
    }
    override fun seek(target: DesktopMediaActorTarget, positionMs: Long): Unit {
        synchronized(gate) {
            val desired = latest ?: return
            if (!desired.sameSelection(target) || positionMs !in 0..Wire.MAX_POSITION) return
            pendingSeek = desired to positionMs
        }
        intentWake.trySend(Unit)
    }
    override fun retire(generation: Long): Unit {
        synchronized(gate) {
            if (latest?.generation != generation) return
            latest = null; acknowledged = null
            transition?.cancel()
            active?.native?.let { native.retire(it, Wire.RetireReason.SELECTION) }
            active?.takeIf { it.native == null }?.let { local?.stop(it.target.generation) }
        }
        wake.trySend(Unit)
    }
    override fun lifecycleVeto(): Unit {
        veto.set(true)
        synchronized(gate) {
            if (quiescent.isCompleted && !closed.get()) quiescent = kotlinx.coroutines.CompletableDeferred()
            latest = null; acknowledged = null; active?.native?.let { native.retire(it, Wire.RetireReason.SLEEP) }
            transition?.cancel()
            active?.takeIf { it.native == null }?.let { local?.stop(it.target.generation) }
        }
        wake.trySend(Unit)
    }
    override fun persistQueue(queue: DesktopQueue, event: DesktopListeningEvent?, copySelection: DesktopMediaCopySelection?): Unit =
        persistence.enqueue(queue, event, copySelection = copySelection)
    override fun persistTaste(queue: DesktopQueue, event: DesktopListeningEvent?, listen: Boolean, excluded: Boolean): Unit =
        persistence.enqueue(queue, event, listen to excluded)
    override fun persistDuration(queue: DesktopQueue, trackId: String, durationMs: Long): Unit =
        persistence.duration(queue, trackId, durationMs)
    suspend fun awaitPersistence(): Unit = persistence.awaitPending()
    suspend fun awaitQuiescent(): Unit = withContext(NonCancellable) {
        // Once closed, only the actual final native/persistence close can establish settlement.
        synchronized(gate) { if (closed.get()) done else quiescent }.await()
    }
    suspend fun quiesceLocalForRenewal(): Unit = applying.withLock {
        val candidate = active ?: return@withLock
        if (candidate.source is DesktopMediaActorSource.Account && candidate.native?.capture is SelectionCapture.AccountLocal) {
            mediaCheck(drain(candidate), MediaCode.DRAIN_UNCONFIRMED, MediaStage.AUTH)
            active = null
        }
    }
    fun continueAfterRenewal(): Unit { wake.trySend(Unit); intentWake.trySend(Unit) }
    /** Explicit foreground continuation after the real lifecycle/K/J reconciliation. */
    fun allowExplicitContinuation(): Unit { mediaCheck(!closed.get() && active == null, MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF); veto.set(false) }

    private fun isCurrent(target: DesktopMediaActorTarget): Boolean = synchronized(gate) {
        !closed.get() && !veto.get() && latest?.sameSelection(target) == true
    }
    private suspend fun applyLatest(candidate: Candidate): Unit = applying.withLock {
        val desired = synchronized(gate) { latest?.takeIf { candidate.target.sameSelection(it) } } ?: return
        if (!isCurrent(desired)) return
        val prepared = synchronized(gate) { acknowledged?.sameSelection(desired) == true }
        if (!prepared) return
        val slot = candidate.native
        // ACCESS_WAIT retains this exact source. Completion re-reduces the latest actor tuple.
        if (slot?.accessWait == true) return
        if (!candidate.activated) {
            if (slot != null) {
                // Local close returns only after its actual typed release callback updates arbiter.
                local?.let { engine ->
                    withContext(NonCancellable) { engine.close() }
                    mediaCheck(DesktopAudioOwnerArbiter.state.value.phase == DesktopAudioOwnerPhase.NONE,
                        MediaCode.DRAIN_UNCONFIRMED, MediaStage.HANDOFF)
                    localFacts?.cancel(); local = null
                }
                if (!isCurrent(desired)) return
                native.updateIntent(fence(desired), desired.wantsPlay)
                native.activatePaused(slot, desired.positionMs, fence(desired))
            } else {
                val standalone = candidate.source as DesktopMediaActorSource.Standalone
                val engine = local ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.HANDOFF)
                if (!isCurrent(desired)) return
                engine.open(standalone.managedPath, desired.generation, desired.positionMs, false)
            }
            candidate.activated = true
        }
        // Read the actor's latest tuple AFTER every suspension/ACK, preserving a newer Pause.
        val current = synchronized(gate) { latest?.takeIf { candidate.target.sameSelection(it) } } ?: return
        if (slot != null) {
            native.updateIntent(fence(current), current.wantsPlay)
            val stillCurrent = synchronized(gate) { latest == current && !veto.get() && !closed.get() }
            if (!stillCurrent) return
            if (current.wantsPlay) native.play(slot, fence(current)) else native.pause(slot, fence(current))
        } else synchronized(gate) {
            if (latest == current && !veto.get() && !closed.get()) {
                if (current.wantsPlay) local?.play(current.generation) else local?.pause(current.generation)
            }
        }
        val seek = synchronized(gate) { pendingSeek?.takeIf { it.first.sameSelection(current) }?.also { pendingSeek = null } }
        if (seek != null) {
            if (slot != null) { seekRevision = DesktopMediaLimits.next(seekRevision); native.seek(slot, fence(current), seekRevision, seek.second) }
            else synchronized(gate) { if (latest?.sameSelection(current) == true) local?.seek(current.generation, seek.second) }
        }
    }
    private suspend fun drainActive(): Boolean = applying.withLock {
        val previous = active ?: return@withLock true
        val released = drain(previous)
        if (released) active = null else previous.failedRetained = true
        released
    }
    suspend fun releaseRetainedHandlesForExplicitReconciliation(): Unit = withContext(NonCancellable) {
        mediaCheck(closed.get() && veto.get(), MediaCode.OWNER_STALE, MediaStage.DRAIN)
        val candidate = active
        candidate?.native?.let { mediaCheck(native.exceptionalSourceSettled(it), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN) }
        candidate?.handle?.let { handle ->
            handle.retire()
            val actual = handle.awaitReleased()
            mediaCheck(actual == MediaHandleRelease.RELEASED || handle is VaultPermit && candidate.native != null &&
                native.exceptionalSourceSettled(candidate.native), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        }
        local?.close()
        mediaCheck(DesktopAudioOwnerArbiter.state.value.phase in setOf(DesktopAudioOwnerPhase.NONE, DesktopAudioOwnerPhase.FAILED_RETAINED),
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        persistence.close()
        active = null; local = null
    }
    private suspend fun drain(candidate: Candidate): Boolean = withContext(NonCancellable) {
        candidate.native?.let { slot ->
            native.retire(slot, Wire.RetireReason.SELECTION)
            if (native.release(slot) != MediaHandleRelease.RELEASED) return@withContext false
        }
        if (candidate.native == null && candidate.activated) local?.stop(candidate.target.generation)
        candidate.handle?.let { handle ->
            handle.retire()
            if (handle.awaitReleased() != MediaHandleRelease.RELEASED) return@withContext false
        }
        true
    }
    private fun observeLocal(engine: DesktopAudioEngine): Unit {
        localFacts?.cancel()
        localFacts = work.scope.launch {
            engine.state.collect { event ->
                val desired = synchronized(gate) { latest }
                if (desired != null && event.generation == desired.generation && active?.native == null && !closed.get()) facts.value = event
            }
        }
    }
    private fun publishFailure(target: DesktopMediaActorTarget, failure: Exception): Unit {
        if (!isCurrent(target)) return
        val code = (failure as? MediaSourceException)?.failure?.code ?: MediaCode.DRAIN_UNCONFIRMED
        facts.value = DesktopAudioEvent(target.generation, "ERROR", target.positionMs, errorCode = code.name)
    }
    override suspend fun close(): Unit {
        if (closed.compareAndSet(false, true)) { lifecycleVeto(); wake.trySend(Unit); wake.close(); intentWake.close() }
        withContext(NonCancellable) { done.await() }
    }
    private fun fence(target: DesktopMediaActorTarget): Wire.IntentFence = Wire.IntentFence(target.desiredIntentRevision,
        target.selectionRevision, target.sleepFenceRevision, target.timerFenceRevision)
}
