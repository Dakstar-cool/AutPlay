package app.autplay.application.desktop

/** Process-local timer limits, matching the existing ordinary-player duration range. */
object DesktopSleepTimerLimits {
    const val MIN_DURATION_MS: Long = 60_000
    const val MAX_DURATION_MS: Long = 12 * 60 * 60 * 1_000L
}

/** Playback-owner projection. Monotonic origins/deadlines never become persisted timestamps.
 * A plan has either remainingMs or stopAfterEntryId; planId fences cancellation of replaced plans.
 * Queue/entry/session identities fence stale UI commands independently of displayed track IDs.
 */
data class DesktopSleepTimerState(
    val queueId: String? = null,
    val currentEntryId: String? = null,
    val sessionId: String? = null,
    val planId: String? = null,
    val remainingMs: Long? = null,
    val stopAfterEntryId: String? = null,
    val commandsEnabled: Boolean = false,
)
