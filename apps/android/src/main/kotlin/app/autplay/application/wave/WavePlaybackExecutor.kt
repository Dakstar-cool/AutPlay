package app.autplay.application.wave

import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WavePrefetchMode

data class WavePreparation(
    val ready: Boolean,
    val source: WaveAvailability,
    val bufferedMs: Long,
)

fun interface WaveSourceProbe {
    suspend fun resolve(localTrackRefId: String): WaveAvailability
}

fun interface WavePrefetchExecutor {
    suspend fun prefetch(
        snapshot: WaveSnapshot,
        mode: WavePrefetchMode,
        unmetered: Boolean,
        nowMs: Long,
    ): Int
}

/** Wave execution boundary: caller pins/prepares the selected source before this monotonic start. */
interface WavePlaybackExecutor {
    suspend fun prepareWaveQueue(queueSnapshotId: String, queueEntryId: String): WavePreparation
    suspend fun schedulePreparedPlayAtElapsedRealtime(targetElapsedRealtimeMs: Long)
    suspend fun pause()
}
