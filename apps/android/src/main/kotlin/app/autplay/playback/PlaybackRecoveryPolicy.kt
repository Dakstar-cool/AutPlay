package app.autplay.playback

/** No URI or credential is retained in a recovery ticket. */
internal data class PlaybackRecoveryOwner(val generation: Long, val entryId: String, val profileId: String)

internal data class PlaybackRecoveryTicket(val owner: PlaybackRecoveryOwner, val positionMs: Long, val revision: Long)

/** Monotonic, bounded recovery budget; brief READY pulses do not replenish it. */
internal class PlaybackRecoveryPolicy {
    private var owner: PlaybackRecoveryOwner? = null
    private var attempts = 0
    private var dueAt: Long? = null
    private var stalledAt: Long? = null
    private var progressingAt: Long? = null
    private var failedAt: Long? = null
    private var terminal = false
    private var revision = 0L
    private var exhausted = false

    fun takeExhaustion(): Boolean = exhausted.also { exhausted = false }

    fun owns(ticket: PlaybackRecoveryTicket): Boolean = owner == ticket.owner && revision == ticket.revision

    fun cancel() {
        revision++
        owner = null
        attempts = 0
        dueAt = null
        stalledAt = null
        progressingAt = null
        failedAt = null
        terminal = false
        exhausted = false
    }

    fun error(current: PlaybackRecoveryOwner, now: Long, transient: Boolean) {
        select(current)
        progressingAt = null
        if (!transient) {
            terminal = true
            dueAt = null
        } else schedule(now)
    }

    fun tick(
        current: PlaybackRecoveryOwner?, now: Long, positionMs: Long,
        wantsPlayback: Boolean, buffering: Boolean, progressing: Boolean, connected: Boolean,
    ): PlaybackRecoveryTicket? {
        if (current == null || !wantsPlayback) {
            cancel()
            return null
        }
        select(current)
        if (terminal) return null
        if (progressing) {
            stalledAt = null
            dueAt = null
            if (progressingAt == null) progressingAt = now
            if (now - requireNotNull(progressingAt) >= STABLE_PLAYBACK_MS) {
                attempts = 0
                failedAt = null
            }
        } else {
            progressingAt = null
            if (buffering) {
                if (stalledAt == null) stalledAt = now
                if (now - requireNotNull(stalledAt) >= STALL_MS) schedule(now)
            } else stalledAt = null
        }
        if (failedAt?.let { now - it >= RECOVERY_WINDOW_MS } == true) {
            terminal = true
            exhausted = true
            dueAt = null
        }
        val deadline = dueAt ?: return null
        if (!connected || now < deadline || terminal) return null
        attempts++
        dueAt = null
        stalledAt = now
        return PlaybackRecoveryTicket(current, positionMs.coerceAtLeast(0), revision)
    }

    private fun select(current: PlaybackRecoveryOwner) {
        if (owner != current) {
            cancel()
            owner = current
        }
    }

    private fun schedule(now: Long) {
        if (terminal || dueAt != null) return
        if (attempts >= MAX_ATTEMPTS) {
            terminal = true
            exhausted = true
            return
        }
        if (failedAt == null) failedAt = now
        dueAt = now + (1_000L shl attempts)
    }

    companion object {
        const val MAX_ATTEMPTS = 4
        const val STALL_MS = 20_000L
        const val STABLE_PLAYBACK_MS = 30_000L
        const val RECOVERY_WINDOW_MS = 90_000L
    }
}
