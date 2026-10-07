package app.autplay.playback

/** Boundaries are in the original recording; Media3 positions are relative to [startMs]. */
internal data class PlaybackCutBounds(val startMs: Long = 0, val endMs: Long? = null) {
    fun sourcePosition(playerPositionMs: Long): Long =
        (startMs + playerPositionMs.coerceAtLeast(0)).let { endMs?.let(it::coerceAtMost) ?: it }

    fun playerPosition(sourcePositionMs: Long): Long =
        (sourcePositionMs.coerceAtLeast(startMs).let { endMs?.let(it::coerceAtMost) ?: it } - startMs)
            .coerceAtLeast(0)
}

internal object PlaybackTransitionCuts {
    const val CUT_MS = 3_000L
    private val ordinaryQueueTypes = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")

    // The source validates its real duration before using this prepared default position.
    fun defaultStartMs(enabled: Boolean, queueType: String?): Long =
        if (enabled && queueType in ordinaryQueueTypes) CUT_MS else 0

    fun bounds(
        enabled: Boolean,
        queueType: String?,
        durationMs: Long?,
        seekable: Boolean,
        preserveHead: Boolean,
        hasPlayableSuccessor: Boolean,
        stopAfterItem: Boolean,
        preserveTail: Boolean = false,
    ): PlaybackCutBounds {
        // Short, live, unseekable and unknown sources must never become empty or fail to prepare.
        if (!enabled || queueType !in ordinaryQueueTypes || !seekable ||
            durationMs == null || durationMs <= CUT_MS * 2) return PlaybackCutBounds()
        return PlaybackCutBounds(
            startMs = if (preserveHead) 0 else CUT_MS,
            endMs = (durationMs - CUT_MS).takeIf { hasPlayableSuccessor && !stopAfterItem && !preserveTail },
        )
    }
}
