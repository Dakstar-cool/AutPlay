package app.autplay.playback

import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultLoadControl

internal object PlaybackTransitions {
    @UnstableApi
    fun loadControl(): DefaultLoadControl = DefaultLoadControl.Builder()
        .setBufferDurationsMs(15_000, 50_000, 1_000, 2_000)
        .build()

    /** Preload samples without changing source boundaries, encoder delay or encoder padding. */
    fun preloadDurationUs(enabled: Boolean, queueType: String? = null): Long =
        if (enabled || queueType == "WAVE" || queueType == "GUEST_WAVE") 8_000_000L else 0L
}
