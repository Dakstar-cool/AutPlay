package app.autplay.desktop.systemmedia

import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.desktop.validDesktopEmbeddedText
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/** Separate from the frozen local-audio IPC. No queue identity crosses this ABI. */
public object DesktopSystemMediaContract {
    public const val ABI_VERSION: Int = 1
    public const val PLAY: Int = 1
    public const val PAUSE: Int = 2
    public const val PREVIOUS: Int = 4
    public const val NEXT: Int = 8
    public const val SEEK: Int = 16
    public const val ALL_COMMANDS: Int = 31
    public const val MAX_POSITION_MS: Long = 604_800_000
    public const val CLOSE_TIMEOUT_MS: Long = 5_000
}

/** Host must capture all fields from one coherent current-entry publication, then publish in order. */
public data class DesktopSystemMediaSnapshot(
    val target: DesktopPlaybackTarget,
    val title: String,
    val artist: String,
    val positionMs: Long,
    val durationMs: Long?,
    val isPlaying: Boolean,
    val allowedCommands: Int,
    val artwork: DesktopArtwork? = null,
)

/** Immutable bounded display payload; target token is process-local and never an OS metadata key. */
public data class DesktopSystemMediaPayload(
    val targetToken: Long,
    val title: String,
    val artist: String,
    val positionMs: Long,
    val durationMs: Long?,
    val isPlaying: Boolean,
    val allowedCommands: Int,
    val artwork: DesktopArtwork?,
)

public data class DesktopSystemMediaCommand(
    val target: DesktopPlaybackTarget,
    val action: DesktopTransportAction,
    val positionMs: Long? = null,
)

/** True means accepted by the controller's bounded queue, never proof of execution or sound. */
public fun interface DesktopSystemMediaListener {
    public fun onCommand(command: Int, targetToken: Long, seekPositionMs: Long): Boolean
}

/** Injectable without library loading, AppKit, a Desktop instance or native execution. */
public interface DesktopSystemMediaApi {
    public fun abiVersion(): Int
    /** Positive opaque lease; <= 0 refuses ownership. Must not retain listener on refusal. */
    public fun acquire(expectedAbi: Int, listener: DesktopSystemMediaListener): Long
    /** Acceptance of a bounded latest-value mailbox, not an OS acknowledgement. Null clears. */
    public fun publish(lease: Long, payload: DesktopSystemMediaPayload?): Boolean
    /** Synchronously fences callbacks and drops the global reference; AppKit cleanup is queued. */
    public fun retire(lease: Long): Unit
    /** True only after own handlers/metadata are removed and the native lease is released. */
    public fun isReleased(lease: Long): Boolean
}

/** Stable diagnostic facts only. Acquired is not proof of system-control eligibility. */
public data class DesktopSystemMediaState(
    val acquired: Boolean = false,
    val closing: Boolean = false,
    val closed: Boolean = false,
    val errorCode: String? = null,
)

/**
 * One process owner. Publication never executes transport. The host listener must be a bounded,
 * nonblocking enqueue through its own closing gate and existing identified transport actor.
 * An already admitted callback can finish after retire; its captured target must still be checked
 * by that actor. Never resolve the command against a subsequently read current player state.
 */
public class DesktopSystemMediaOwner(
    private val api: DesktopSystemMediaApi?,
    onCommand: (DesktopSystemMediaCommand) -> Boolean,
) {
    private val lock: Any = Any()
    private var listener: ((DesktopSystemMediaCommand) -> Boolean)? = onCommand
    private var lease: Long = 0
    private var ownsProcessLease: Boolean = false
    private var nextToken: Long = 0
    private var current: DesktopSystemMediaSnapshot? = null
    private var currentToken: Long = 0
    private var facts: DesktopSystemMediaState = DesktopSystemMediaState()
    public val state: DesktopSystemMediaState get() = synchronized(lock) { facts }

    init {
        synchronized(lock) {
            if (api == null) fail("system_media_unavailable")
            else if (!PROCESS_LEASE.compareAndSet(false, true)) fail("system_media_owner_active")
            else {
                ownsProcessLease = true
                try {
                    if (api.abiVersion() != DesktopSystemMediaContract.ABI_VERSION) fail("system_media_abi_mismatch")
                    else {
                        lease = api.acquire(DesktopSystemMediaContract.ABI_VERSION, DesktopSystemMediaListener(::receive))
                        if (lease <= 0) fail("system_media_acquire_failed")
                        else facts = DesktopSystemMediaState(acquired = true)
                    }
                } catch (_: Exception) { fail("system_media_acquire_failed") }
                catch (_: LinkageError) { fail("system_media_abi_mismatch") }
                if (lease <= 0) releaseProcessLease()
            }
        }
    }

    /** Null on absent/retired/unavailable current item. Metadata and position do not allocate audio. */
    public fun publish(snapshot: DesktopSystemMediaSnapshot?): Boolean = synchronized(lock) {
        if (!facts.acquired || facts.closing || facts.errorCode != null) return@synchronized false
        if (snapshot != null && !valid(snapshot)) {
            failAndRetire("system_media_invalid_publication")
            return@synchronized false
        }
        val old = current
        val sameMeaning = old != null && snapshot != null && old.target == snapshot.target &&
            old.allowedCommands == snapshot.allowedCommands && toggleAction(old) == toggleAction(snapshot)
        if (snapshot != null && !sameMeaning) {
            if (nextToken == Long.MAX_VALUE) {
                failAndRetire("system_media_token_exhausted")
                return@synchronized false
            }
            currentToken = ++nextToken
        }
        // Retire the old target before native publication/dispatch can race a callback.
        current = snapshot
        if (snapshot == null) currentToken = 0
        val payload = snapshot?.let {
            DesktopSystemMediaPayload(currentToken, it.title, it.artist, it.positionMs, it.durationMs,
                it.isPlaying, it.allowedCommands, it.artwork)
        }
        val accepted = try { checkNotNull(api).publish(lease, payload) }
        catch (_: Exception) { false } catch (_: LinkageError) { false }
        if (!accepted) failAndRetire("system_media_publication_failed")
        accepted
    }

    /** Call before cancelling host jobs or taking down controller/store ownership. No AppKit wait. */
    public fun retire(): Unit = synchronized(lock) {
        if (!facts.closing && !facts.closed) {
            facts = facts.copy(closing = true)
            current = null
            currentToken = 0
            listener = null
            if (lease > 0) {
                try { api?.retire(lease) }
                catch (_: Exception) { facts = facts.copy(errorCode = "system_media_retire_failed") }
                catch (_: LinkageError) { facts = facts.copy(errorCode = "system_media_retire_failed") }
            }
        }
    }

    /** Cancellation cannot skip teardown. Timeout retains the process lease; a later close may retry. */
    public suspend fun close(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        retire()
        val released = withTimeoutOrNull(DesktopSystemMediaContract.CLOSE_TIMEOUT_MS) {
            while (true) {
                val done = synchronized(lock) {
                    if (lease <= 0) true else try { api?.isReleased(lease) == true }
                    catch (_: Exception) { false } catch (_: LinkageError) { false }
                }
                if (done) break
                delay(20)
            }
            true
        } == true
        synchronized(lock) {
            if (released) {
                lease = 0
                releaseProcessLease()
                facts = facts.copy(acquired = false, closing = true, closed = true)
            } else facts = facts.copy(errorCode = "system_media_teardown_timeout")
        }
    }

    private fun receive(command: Int, token: Long, seekPositionMs: Long): Boolean {
        val delivery = synchronized(lock) {
            val captured = current
            val callback = listener
            if (facts.closing || facts.errorCode != null || captured == null || callback == null ||
                token <= 0 || token != currentToken) return false
            val action = when (command) {
                1 -> DesktopTransportAction.PLAY
                2 -> DesktopTransportAction.PAUSE
                3 -> DesktopTransportAction.PREVIOUS
                4 -> DesktopTransportAction.NEXT
                5 -> DesktopTransportAction.SEEK
                else -> return false
            }
            val bit = when (action) {
                DesktopTransportAction.PLAY -> DesktopSystemMediaContract.PLAY
                DesktopTransportAction.PAUSE -> DesktopSystemMediaContract.PAUSE
                DesktopTransportAction.PREVIOUS -> DesktopSystemMediaContract.PREVIOUS
                DesktopTransportAction.NEXT -> DesktopSystemMediaContract.NEXT
                DesktopTransportAction.SEEK -> DesktopSystemMediaContract.SEEK
            }
            if ((captured.allowedCommands and bit) == 0) return false
            if (action == DesktopTransportAction.SEEK) {
                val duration = captured.durationMs ?: return false
                if (seekPositionMs !in 0..duration) return false
            } else if (seekPositionMs != -1L) return false
            callback to DesktopSystemMediaCommand(captured.target, action,
                if (action == DesktopTransportAction.SEEK) seekPositionMs else null)
        }
        // No owner monitor held across the host gate: orderly Quit may acquire them in reverse order.
        return try { delivery.first(delivery.second) } catch (_: Exception) { false }
    }

    private fun valid(value: DesktopSystemMediaSnapshot): Boolean =
        value.target.generation >= 0 && value.target.queueId.isNotBlank() && value.target.entryId.isNotBlank() &&
            validDesktopEmbeddedText(value.title, allowBlank = false) &&
            validDesktopEmbeddedText(value.artist, allowBlank = true) &&
            value.positionMs in 0..DesktopSystemMediaContract.MAX_POSITION_MS &&
            (value.durationMs == null || value.durationMs in 0..DesktopSystemMediaContract.MAX_POSITION_MS) &&
            (value.durationMs == null || value.positionMs <= value.durationMs) &&
            value.allowedCommands >= 0 &&
            (value.allowedCommands and DesktopSystemMediaContract.ALL_COMMANDS) == value.allowedCommands &&
            ((value.allowedCommands and DesktopSystemMediaContract.SEEK) == 0 || (value.durationMs ?: 0) > 0)

    private fun toggleAction(value: DesktopSystemMediaSnapshot): Int {
        val bit = if (value.isPlaying) DesktopSystemMediaContract.PAUSE else DesktopSystemMediaContract.PLAY
        return if ((value.allowedCommands and bit) != 0) bit else 0
    }

    private fun fail(code: String): Unit {
        current = null
        listener = null
        facts = facts.copy(errorCode = code)
    }

    private fun failAndRetire(code: String): Unit {
        fail(code)
        retire()
    }

    private fun releaseProcessLease(): Unit {
        if (ownsProcessLease) {
            ownsProcessLease = false
            PROCESS_LEASE.set(false)
        }
    }

    private companion object {
        val PROCESS_LEASE: AtomicBoolean = AtomicBoolean(false)
    }
}
