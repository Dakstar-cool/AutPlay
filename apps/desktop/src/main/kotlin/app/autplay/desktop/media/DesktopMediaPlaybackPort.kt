package app.autplay.desktop.media

import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.media.SelectionCapture
import app.autplay.application.desktop.media.SourceIntent
import app.autplay.application.desktop.media.MediaId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/** Immutable actor output. This is process state; it is never inserted into standalone Room. */
public data class DesktopMediaCopySelection(val copyId: String, val acquisitionScopeId: String) {
    init { MediaId(copyId); MediaId(acquisitionScopeId) }
    override fun toString(): String = "DesktopMediaCopySelection(<redacted>)"
}

public data class DesktopMediaActorTarget(
    val queueId: String, val entryId: String, val listenId: String?, val trackId: String,
    val generation: Long, val selectionRevision: Long, val desiredIntentRevision: Long,
    val sleepFenceRevision: Long, val timerFenceRevision: Long,
    val positionMs: Long, val wantsPlay: Boolean,
    val copySelection: DesktopMediaCopySelection? = null,
) {
    public fun sameSelection(other: DesktopMediaActorTarget): Boolean = queueId == other.queueId &&
        entryId == other.entryId && listenId == other.listenId && trackId == other.trackId &&
        generation == other.generation && selectionRevision == other.selectionRevision && copySelection == other.copySelection
    override fun toString(): String = "DesktopMediaActorTarget(<redacted>)"
}

public class DesktopMediaPrepared(public val target: DesktopMediaActorTarget) {
    override fun toString(): String = "DesktopMediaPrepared(<redacted>)"
}

/** Implemented by the process owner. Every method invoked by the actor is a bounded enqueue/fence. */
public interface DesktopMediaPlaybackPort {
    public val state: StateFlow<DesktopAudioEvent>
    public val prepared: Flow<DesktopMediaPrepared>
    public fun select(target: DesktopMediaActorTarget): Unit
    public fun intent(target: DesktopMediaActorTarget): Unit
    /** Only the queue actor may acknowledge a ready candidate after reducing admitted controls. */
    public fun admitPrepared(target: DesktopMediaActorTarget): Unit
    public fun seek(target: DesktopMediaActorTarget, positionMs: Long): Unit
    public fun retire(generation: Long): Unit
    public fun lifecycleVeto(): Unit
    /** Enqueue immutable persistence effects; an actual owner settles them outside actor locks. */
    public fun persistQueue(queue: DesktopQueue, event: DesktopListeningEvent?, copySelection: DesktopMediaCopySelection? = null): Unit = error("OWNER_UNAVAILABLE")
    public fun persistTaste(queue: DesktopQueue, event: DesktopListeningEvent?, listen: Boolean, excluded: Boolean): Unit = error("OWNER_UNAVAILABLE")
    public fun persistDuration(queue: DesktopQueue, trackId: String, durationMs: Long): Unit = error("OWNER_UNAVAILABLE")
    public suspend fun close(): Unit
}

/** Source identity factory reads the actual selected J/K owner outside the playback actor mutex. */
public interface DesktopMediaActorSourcePort {
    public suspend fun capture(target: DesktopMediaActorTarget): DesktopMediaActorSource
    public suspend fun network(local: SelectionCapture.AccountLocal,
        intent: SourceIntent.VaultPlayback): DesktopMediaActorSource.Account? = null
}
public sealed interface DesktopMediaActorSource {
    /** Standalone store resolution stays with its original verified managed-file owner. */
    public class Standalone(public val managedPath: String) : DesktopMediaActorSource {
        override fun toString(): String = "DesktopMediaActorSource.Standalone(<redacted>)"
    }
    public class Account(public val capture: SelectionCapture, public val intent: SourceIntent) : DesktopMediaActorSource {
        override fun toString(): String = "DesktopMediaActorSource.Account(<redacted>)"
    }
}
