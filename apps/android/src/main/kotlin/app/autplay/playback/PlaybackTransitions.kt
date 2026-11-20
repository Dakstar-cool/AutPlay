package app.autplay.playback

internal object PlaybackTransitions {
    /** Preload samples without changing source boundaries, encoder delay or encoder padding. */
    fun preloadDurationUs(enabled: Boolean, queueType: String? = null): Long =
        if (enabled || queueType == "WAVE" || queueType == "GUEST_WAVE") 8_000_000L else 0L
}
