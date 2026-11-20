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
    suspend fun synchronizePreparedPlayback(anchor: WavePlaybackAnchor, startPlayback: Boolean) {
        if (startPlayback) schedulePreparedPlayAtElapsedRealtime(anchor.startElapsedRealtimeMs)
    }
    fun isWaveSynchronizationDegraded(): Boolean = false
    suspend fun seekPreparedTo(positionMs: Long) = Unit
    suspend fun restoreQueueAfterRoom(snapshotId: String?) = Unit
    suspend fun pause()
}

/** Process-local authority lease; no clocks or source grants are persisted with the queue. */
data class WavePlaybackAnchor(
    val queueSnapshotId: String,
    val queueEntryId: String,
    val commandSequence: Long,
    val startElapsedRealtimeMs: Long,
    val validUntilElapsedRealtimeMs: Long,
) {
    init {
        require(queueSnapshotId.isNotBlank() && queueEntryId.isNotBlank())
        require(commandSequence > 0 && validUntilElapsedRealtimeMs > startElapsedRealtimeMs)
    }

    fun owns(queueType: String?, snapshotId: String?, entryId: String?): Boolean =
        queueType in setOf("WAVE", "GUEST_WAVE") &&
            queueSnapshotId == snapshotId && queueEntryId == entryId

    fun renewLease(next: WavePlaybackAnchor): WavePlaybackAnchor {
        require(queueSnapshotId == next.queueSnapshotId && queueEntryId == next.queueEntryId &&
            commandSequence == next.commandSequence)
        // A later wall-clock estimate must not move an already accepted monotonic start.
        return copy(validUntilElapsedRealtimeMs = next.validUntilElapsedRealtimeMs)
    }
}
