package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import java.util.concurrent.CompletionStage
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Process lifetime settlement registry. A cancelled caller never owns the execution Job. */
internal class DesktopMediaWorkOwner : DesktopMediaSettlementPort {
    private val job = SupervisorJob()
    val scope: CoroutineScope = CoroutineScope(job + Dispatchers.IO)
    private val capacity = AtomicInteger(0)
    private val closing = AtomicBoolean(false)
    private val gate = Any()
    private val work = ConcurrentHashMap.newKeySet<MediaOwnedWork<*>>()
    private val leases = ConcurrentHashMap.newKeySet<MediaOwnedLease>()

    fun <T> start(action: suspend (AtomicBoolean) -> T): MediaOwnedWork<T> {
        val ticket = synchronized(gate) {
            if (closing.get()) throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.RESOLVE)
            if (capacity.incrementAndGet() > 8) {
                capacity.decrementAndGet()
                throw mediaFailure(MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
            }
            Ticket<T>().also { work.add(it) }
        }
        scope.launch {
            try {
                val value = action(ticket.cancelled)
                capacity.decrementAndGet()
                ticket.result.complete(value)
            } catch (failure: Exception) {
                capacity.decrementAndGet()
                ticket.result.completeExceptionally(failure)
            }
        }
        return ticket
    }

    override fun retainWork(work: MediaOwnedWork<*>): Unit { this.work.add(work) }
    override fun retainLease(lease: MediaOwnedLease): Unit { leases.add(lease) }
    override fun releaseWorkAfterTerminal(work: MediaOwnedWork<*>): Unit { this.work.remove(work) }
    override fun releaseLeaseAfterConfirmedSettlement(lease: MediaOwnedLease): Unit { leases.remove(lease) }

    /** No deadline converts retained cleanup into success. The caller may report its own deadline. */
    suspend fun close(): MediaHandleRelease = withContext(NonCancellable) {
        synchronized(gate) { closing.set(true) }
        work.toList().forEach { it.requestCancellation() }
        leases.toList().forEach { it.retire() }
        work.toList().forEach { ticket ->
            try { ticket.awaitTerminal() } catch (_: Exception) { /* Actual terminal failure. */ }
            work.remove(ticket)
        }
        var released = true
        for (lease in leases.toList()) {
            try {
                if (lease.awaitReleased() == MediaHandleRelease.RELEASED) leases.remove(lease)
                else released = false
            } catch (_: Exception) { released = false }
        }
        if (released) MediaHandleRelease.RELEASED else MediaHandleRelease.FAILED_RETAINED
    }

    /** Cancels only idle observers after native/file/SQL and all retained leases settled. */
    fun finish(): Unit {
        mediaCheck(closing.get() && work.isEmpty() && leases.isEmpty() && capacity.get() == 0,
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        job.cancel()
    }

    private class Ticket<T> : MediaOwnedWork<T> {
        val cancelled = AtomicBoolean(false)
        val result = CompletableDeferred<T>()
        override fun requestCancellation(): Unit { cancelled.set(true) }
        override suspend fun awaitTerminal(): T = result.await()
    }
}

/** Cancellation detaches the waiter only. The future and its actual native/IO owner stay alive. */
internal suspend fun <T> CompletionStage<T>.mediaAwait(): T {
    val settled = CompletableDeferred<T>()
    whenComplete { value, failure ->
        if (failure == null) settled.complete(value)
        else settled.completeExceptionally(failure)
    }
    return settled.await()
}

internal fun mediaFailure(code: MediaCode, stage: MediaStage): MediaSourceException =
    MediaSourceException(MediaFailure(code, stage, when (code) {
        MediaCode.RECONCILIATION_REQUIRED, MediaCode.PUBLICATION_UNCERTAIN -> MediaRetryAction.RECONCILE
        MediaCode.OWNER_UNAVAILABLE -> MediaRetryAction.UNLOCK
        MediaCode.AUTH_REQUIRED, MediaCode.ACCESS_RENEWAL_FAILED -> MediaRetryAction.RECONNECT
        else -> MediaRetryAction.NONE
    }))

internal fun mediaCheck(value: Boolean, code: MediaCode, stage: MediaStage): Unit {
    if (!value) throw mediaFailure(code, stage)
}
