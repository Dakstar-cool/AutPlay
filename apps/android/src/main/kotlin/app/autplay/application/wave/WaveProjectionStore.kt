package app.autplay.application.wave

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.WavePreflightEntity
import app.autplay.data.local.entity.WaveQueueProjectionEntity
import app.autplay.data.local.entity.WaveRoomEntity

/** Storage-neutral room state used by the coordinator. */
data class StoredWaveRoom(
    val roomId: String,
    val profileId: String,
    val roomEpoch: String,
    val queueVersion: Long,
    val role: String,
    val state: String,
    val lastSequence: Long,
    val updatedAtMs: Long,
)

data class StoredWavePreflight(
    val queueEntryId: String,
    val serverRecordingId: String,
    val localTrackRefId: String?,
    val queueVersion: Long,
    val availability: String,
    val finalReady: Boolean,
    val checkedAtMs: Long,
)

data class StoredWaveQueueEntry(
    val sequence: Long,
    val position: Long,
    val queueEntryId: String,
    val serverRecordingId: String,
    val localTrackRefId: String?,
    val ready: Boolean,
)

/** Explicit projection boundary: guest and account-bound Wave state must never share tables. */
interface WaveProjectionStore {
    suspend fun room(roomId: String): StoredWaveRoom?
    suspend fun queue(roomId: String, sequence: Long, limit: Int): List<StoredWaveQueueEntry>
    suspend fun advance(roomId: String, roomEpoch: String, sequence: Long, nowMs: Long): Int
    suspend fun replaceSnapshot(
        room: StoredWaveRoom,
        preflight: List<StoredWavePreflight>,
        queue: List<StoredWaveQueueEntry>,
    )
}

/** Account-bound P13 storage adapter. */
class RoomWaveProjectionStore(private val database: AutPlayDatabase) : WaveProjectionStore {
    override suspend fun room(roomId: String): StoredWaveRoom? =
        database.waveDao().room(roomId)?.let {
            StoredWaveRoom(
                it.roomId,
                it.serverProfileId,
                it.roomEpoch,
                it.queueVersion,
                it.role,
                it.state,
                it.lastSequence,
                it.updatedAtMs,
            )
        }

    override suspend fun queue(
        roomId: String,
        sequence: Long,
        limit: Int,
    ): List<StoredWaveQueueEntry> = database.waveDao().queue(roomId, sequence, limit).map {
        StoredWaveQueueEntry(
            it.sequence,
            it.position,
            it.queueEntryId,
            it.serverRecordingId,
            it.localUserTrackRefId,
            it.ready,
        )
    }

    override suspend fun advance(
        roomId: String,
        roomEpoch: String,
        sequence: Long,
        nowMs: Long,
    ): Int = database.waveDao().advanceSequence(roomId, sequence, nowMs)

    override suspend fun replaceSnapshot(
        room: StoredWaveRoom,
        preflight: List<StoredWavePreflight>,
        queue: List<StoredWaveQueueEntry>,
    ) {
        database.waveDao().replaceSnapshot(
            WaveRoomEntity(
                room.roomId,
                room.profileId,
                room.roomEpoch,
                room.queueVersion,
                room.role,
                room.state,
                room.lastSequence,
                room.updatedAtMs,
            ),
            preflight.map {
                WavePreflightEntity(
                    room.roomId,
                    it.queueEntryId,
                    it.serverRecordingId,
                    it.localTrackRefId,
                    it.queueVersion,
                    it.availability,
                    it.finalReady,
                    it.checkedAtMs,
                )
            },
            queue.map {
                WaveQueueProjectionEntity(
                    room.roomId,
                    it.sequence,
                    it.position,
                    it.queueEntryId,
                    it.serverRecordingId,
                    it.localTrackRefId,
                    it.ready,
                )
            },
        )
    }
}
