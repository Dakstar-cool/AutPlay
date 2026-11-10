package app.autplay.desktop.playback

import app.autplay.application.desktop.DesktopSleepTimerLimits
import app.autplay.application.desktop.DesktopSleepTimerState
import java.util.UUID

/** Actor-confined, process-local policy. Only elapsed differences have clock meaning. */
internal class DesktopSleepTimerPlan(private val nowNanos: () -> Long) {
    private sealed interface Plan {
        val id: String
        val queueId: String
    }

    private data class Duration(
        override val id: String,
        override val queueId: String,
        val startedNanos: Long,
        val durationNanos: Long,
    ) : Plan

    private data class AfterEntry(
        override val id: String,
        override val queueId: String,
        val entryId: String,
        val sessionId: String,
    ) : Plan

    private var plan: Plan? = null

    fun schedule(queueId: String, durationMs: Long): Unit {
        if (durationMs !in DesktopSleepTimerLimits.MIN_DURATION_MS..DesktopSleepTimerLimits.MAX_DURATION_MS) return
        plan = Duration(UUID.randomUUID().toString(), queueId, nowNanos(), durationMs * NANOS_PER_MS)
    }

    fun stopAfter(queueId: String, entryId: String, sessionId: String): Unit {
        plan = AfterEntry(UUID.randomUUID().toString(), queueId, entryId, sessionId)
    }

    fun cancel(expectedPlanId: String): Unit {
        if (plan?.id == expectedPlanId) clear()
    }

    fun clear(): Unit { plan = null }

    fun clearStopAfter(): Unit {
        if (plan is AfterEntry) clear()
    }

    fun retainFor(queueId: String?, entryId: String?, sessionId: String?): Unit {
        val current = plan ?: return
        if (current.queueId != queueId || entryId == null || sessionId == null ||
            (current is AfterEntry && (current.entryId != entryId || current.sessionId != sessionId))) clear()
    }

    /** Consume before any native/persistence work, so failures cannot fire a plan twice. */
    fun consumeDue(): Boolean {
        val current = plan as? Duration ?: return false
        if (remainingNanos(current) != 0L) return false
        clear()
        return true
    }

    fun consumeAfter(queueId: String?, entryId: String?, sessionId: String?): Boolean {
        val current = plan as? AfterEntry ?: return false
        if (current.queueId != queueId || current.entryId != entryId || current.sessionId != sessionId) return false
        clear()
        return true
    }

    fun project(queueId: String?, entryId: String?, sessionId: String?, commandsEnabled: Boolean): DesktopSleepTimerState {
        val current = plan
        val remaining = (current as? Duration)?.let {
            val nanos = remainingNanos(it)
            // Round up: a still-armed sub-millisecond remainder is not displayed as already zero.
            nanos / NANOS_PER_MS + if (nanos % NANOS_PER_MS != 0L) 1 else 0
        }
        return DesktopSleepTimerState(queueId, entryId, sessionId, current?.id, remaining,
            (current as? AfterEntry)?.entryId, commandsEnabled)
    }

    private fun remainingNanos(current: Duration): Long {
        // nanoTime can have a signed origin and wrap. A <=12h interval uses subtraction only.
        val elapsed = (nowNanos() - current.startedNanos).coerceAtLeast(0)
        return current.durationNanos - elapsed.coerceAtMost(current.durationNanos)
    }

    private companion object {
        const val NANOS_PER_MS: Long = 1_000_000
    }
}
