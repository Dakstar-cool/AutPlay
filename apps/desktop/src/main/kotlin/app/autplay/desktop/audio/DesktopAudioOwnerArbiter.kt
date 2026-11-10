package app.autplay.desktop.audio

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import app.autplay.desktop.media.RemoteMediaReleaseKind
import app.autplay.desktop.media.RemoteMediaSourceRelease
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.CompletionStage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

internal enum class DesktopAudioOwnerPhase { NONE, LOCAL, REMOTE, DRAINING, FAILED_RETAINED }
internal enum class DesktopAudioOwnerKind { LOCAL, REMOTE }
internal data class DesktopAudioOwnerState(val phase: DesktopAudioOwnerPhase, val revision: Long)

/** Reference identity, rather than an equal UUID supplied by another caller, owns the slot. */
internal class DesktopAudioOwnerLease internal constructor(
    val id: UUID,
    val kind: DesktopAudioOwnerKind,
    val source: Wire.SourceKey?,
) {
    override fun toString(): String = "DesktopAudioOwnerLease(REDACTED)"
}

/** Exact local handoff ticket; timeout retirement cannot affect a newer owner. */
internal class DesktopLocalAudioDrain internal constructor(
    val leaseId: UUID,
    val result: CompletionStage<MacAudioOwnerRelease>,
    private val failed: () -> Unit,
    private val recover: ((UUID) -> Boolean) -> Boolean,
) {
    fun retainFailure(): Unit = failed()
    fun releaseForExplicitAttempt(currentAttempt: (UUID) -> Boolean): Boolean = recover(currentAttempt)
    override fun toString(): String = "DesktopLocalAudioDrain(REDACTED)"
}

/**
 * One process-wide logical audio lease above both native engines. A remote helper may continue
 * a download without owning this lease. No acquisition performs IO or decides queue/listen/intent.
 * The media coordinator must separately validate its latest actor/K/J/lifecycle admission.
 */
internal object DesktopAudioOwnerArbiter {
    private val lock: Any = Any()
    private val mutableState = MutableStateFlow(DesktopAudioOwnerState(DesktopAudioOwnerPhase.NONE, 0))
    val state: StateFlow<DesktopAudioOwnerState> = mutableState.asStateFlow()
    private var lease: DesktopAudioOwnerLease? = null
    private var localRelease: MacAudioOwnerRelease? = null
    private var localEngine: MacAudioEngine? = null
    private var remoteRelease: RemoteMediaSourceRelease? = null

    fun acquireRemote(source: Wire.SourceKey): DesktopAudioOwnerLease? = acquire(DesktopAudioOwnerKind.REMOTE, source)

    /** Used by the actual local host; failed construction cannot silently free an unknown owner. */
    fun createLocalEngine(helper: Path): DesktopAudioEngine {
        if (System.getProperty("os.name") != "Mac OS X") return UnavailableLocalEngine("AUDIO_UNSUPPORTED_PLATFORM")
        val admitted = acquire(DesktopAudioOwnerKind.LOCAL, null)
            ?: return UnavailableLocalEngine("AUDIO_OWNER_ALREADY_ACTIVE")
        val engine = try { MacAudioEngine(helper) } catch (_: Throwable) {
            retainFailure(admitted)
            return UnavailableLocalEngine("AUDIO_OWNER_START_FAILED")
        }
        synchronized(lock) { if (lease === admitted) localEngine = engine }
        engine.ownerReleaseResult().whenComplete { result, failure ->
            if (failure != null || result == null) retainFailure(admitted)
            else recordLocalRelease(admitted, result)
        }
        return LeasedLocalEngine(admitted, engine)
    }

    /** The coordinator awaits this actual typed result outside the playback actor mutex. */
    fun requestCurrentLocalRelease(): DesktopLocalAudioDrain? {
        val captured = synchronized(lock) {
            val current = lease ?: return@synchronized null
            val engine = localEngine ?: return@synchronized null
            if (current.kind != DesktopAudioOwnerKind.LOCAL) return@synchronized null
            if (mutableState.value.phase != DesktopAudioOwnerPhase.FAILED_RETAINED) publish(DesktopAudioOwnerPhase.DRAINING)
            current to engine
        } ?: return null
        val result = captured.second.requestOwnerRelease().thenApply { proof ->
            // Publish the logical release before this handoff stage can run a successor inline.
            recordLocalRelease(captured.first, proof)
            proof
        }
        return DesktopLocalAudioDrain(captured.first.id, result,
            { retainFailure(captured.first) },
            { guard -> releaseFailedForExplicitAttempt(captured.first) { guard(it.id) } })
    }

    private fun acquire(kind: DesktopAudioOwnerKind, source: Wire.SourceKey?): DesktopAudioOwnerLease? = synchronized(lock) {
        if (lease != null || mutableState.value.phase != DesktopAudioOwnerPhase.NONE ||
            mutableState.value.revision == Long.MAX_VALUE) return@synchronized null
        val acquired = DesktopAudioOwnerLease(UUID.randomUUID(), kind, source)
        lease = acquired
        localRelease = null
        remoteRelease = null
        publish(if (kind == DesktopAudioOwnerKind.LOCAL) DesktopAudioOwnerPhase.LOCAL else DesktopAudioOwnerPhase.REMOTE)
        acquired
    }

    fun isCurrent(candidate: DesktopAudioOwnerLease, source: Wire.SourceKey? = candidate.source): Boolean = synchronized(lock) {
        lease === candidate && candidate.source == source && mutableState.value.phase ==
            (if (candidate.kind == DesktopAudioOwnerKind.LOCAL) DesktopAudioOwnerPhase.LOCAL else DesktopAudioOwnerPhase.REMOTE)
    }

    fun beginDrain(candidate: DesktopAudioOwnerLease): Boolean = synchronized(lock) {
        if (lease !== candidate) return@synchronized false
        if (mutableState.value.phase != DesktopAudioOwnerPhase.FAILED_RETAINED) publish(DesktopAudioOwnerPhase.DRAINING)
        true
    }

    fun retainFailure(candidate: DesktopAudioOwnerLease): Unit = synchronized(lock) {
        if (lease === candidate) publish(DesktopAudioOwnerPhase.FAILED_RETAINED)
    }

    private fun recordLocalRelease(candidate: DesktopAudioOwnerLease, result: MacAudioOwnerRelease): Unit = synchronized(lock) {
        if (lease !== candidate || candidate.kind != DesktopAudioOwnerKind.LOCAL) return@synchronized
        localRelease = result
        if (result.kind == MacAudioOwnerReleaseKind.DRAINED && result.fullySettled &&
            mutableState.value.phase != DesktopAudioOwnerPhase.FAILED_RETAINED) release()
        else publish(DesktopAudioOwnerPhase.FAILED_RETAINED)
    }

    /** Supply only the exact captured retireSource ticket's result, never a status or timeout. */
    fun recordRemoteRelease(candidate: DesktopAudioOwnerLease, source: Wire.SourceKey,
        result: RemoteMediaSourceRelease): Unit = synchronized(lock) {
        if (lease !== candidate || candidate.kind != DesktopAudioOwnerKind.REMOTE || candidate.source != source) return@synchronized
        remoteRelease = result
        if (result.kind == RemoteMediaReleaseKind.DRAINED && result.nativeDrained &&
            result.sourcePipeWritesSettled && result.hostCallbacksSettled &&
            mutableState.value.phase != DesktopAudioOwnerPhase.FAILED_RETAINED) release()
        else publish(DesktopAudioOwnerPhase.FAILED_RETAINED)
    }

    /**
     * Called only for a visible, explicit new attempt after original owner reconciliation.
     * The required nonblocking caller guard proves that intent; it is not a default-success hook.
     * This frees a settled failure slot and grants no new audio or credential authority.
     */
    fun releaseFailedForExplicitAttempt(candidate: DesktopAudioOwnerLease,
        currentAttempt: (DesktopAudioOwnerLease) -> Boolean): Boolean {
        if (!currentAttempt(candidate)) return false
        return synchronized(lock) {
            if (lease !== candidate || mutableState.value.phase != DesktopAudioOwnerPhase.FAILED_RETAINED) return@synchronized false
            val actualSettlement = when (candidate.kind) {
                DesktopAudioOwnerKind.LOCAL -> localRelease?.fullySettled == true
                DesktopAudioOwnerKind.REMOTE -> remoteRelease?.let {
                    (it.nativeDrained || it.exceptionalProcessExitConfirmed) &&
                        it.sourcePipeWritesSettled && it.hostCallbacksSettled
                } == true
            }
            if (!actualSettlement) return@synchronized false
            release()
            true
        }
    }

    private fun release(): Unit {
        lease = null
        localEngine = null
        localRelease = null
        remoteRelease = null
        publish(DesktopAudioOwnerPhase.NONE)
    }

    private fun publish(phase: DesktopAudioOwnerPhase): Unit {
        val revision = mutableState.value.revision
        if (revision == Long.MAX_VALUE) {
            // Exhaustion can never reopen admission, even after a successful physical drain.
            mutableState.value = DesktopAudioOwnerState(DesktopAudioOwnerPhase.FAILED_RETAINED, revision)
        } else mutableState.value = DesktopAudioOwnerState(phase, revision + 1)
    }

    private fun ownsLocal(candidate: DesktopAudioOwnerLease): Boolean = synchronized(lock) {
        lease === candidate && candidate.kind == DesktopAudioOwnerKind.LOCAL
    }

    private class LeasedLocalEngine(
        private val captured: DesktopAudioOwnerLease,
        private val engine: MacAudioEngine,
    ) : DesktopAudioEngine {
        override val state: StateFlow<DesktopAudioEvent> = engine.state
        override fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit {
            if (isCurrent(captured)) engine.open(managedPath, generation, positionMs, autoplay)
            else if (ownsLocal(captured)) engine.reportUnavailableSelection(generation)
        }
        override fun play(generation: Long): Unit { if (isCurrent(captured)) engine.play(generation) }
        override fun pause(generation: Long): Unit { if (ownsLocal(captured)) engine.pause(generation) }
        override fun seek(generation: Long, positionMs: Long): Unit { if (isCurrent(captured)) engine.seek(generation, positionMs) }
        override fun stop(generation: Long): Unit { if (ownsLocal(captured)) engine.stop(generation) }
        override suspend fun close(): Unit {
            beginDrain(captured)
            engine.requestOwnerRelease()
            try { engine.close() } finally {
                // CompletionStage dependents have no execution order. Read the original promise
                // and settle the arbiter here rather than racing its separately registered observer.
                val actual = engine.completedOwnerRelease()
                if (actual == null) retainFailure(captured) else recordLocalRelease(captured, actual)
            }
        }
    }

    private class UnavailableLocalEngine(private val code: String) : DesktopAudioEngine {
        private val facts = MutableStateFlow(DesktopAudioEvent(0, "UNAVAILABLE", errorCode = code))
        override val state: StateFlow<DesktopAudioEvent> = facts.asStateFlow()
        override fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit {
            if (generation >= 0) facts.value = DesktopAudioEvent(generation, "UNAVAILABLE", errorCode = code)
        }
        override fun play(generation: Long): Unit = Unit
        override fun pause(generation: Long): Unit = Unit
        override fun seek(generation: Long, positionMs: Long): Unit = Unit
        override fun stop(generation: Long): Unit = Unit
        override suspend fun close(): Unit = Unit
    }
}
