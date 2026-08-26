package app.autplay.application.guestroom

import app.autplay.application.wave.StoredWavePreflight
import app.autplay.application.wave.StoredWaveQueueEntry
import app.autplay.application.wave.StoredWaveRoom
import app.autplay.application.wave.WaveProjectionStore
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.GuestWavePreflightEntity
import app.autplay.data.local.entity.GuestWaveQueueProjectionEntity

/** S1D Room adapter keyed by guest session; it has no code path to account-bound `wave_*`. */
class GuestWaveProjectionStore(
    private val database: AutPlayDatabase,
    private val guestSessionId: String,
) : WaveProjectionStore {
    override suspend fun room(roomId: String): StoredWaveRoom? =
        database.guestRoomDao().session(guestSessionId)
            ?.takeIf { it.roomId == roomId }
            ?.let {
                StoredWaveRoom(
                    it.roomId,
                    it.localMediaProfileId
                        ?: "guest:${it.serverInstanceId}:${it.identityEpoch}",
                    it.roomEpoch,
                    it.queueVersion,
                    "GUEST",
                    it.roomState,
                    it.lastSequence,
                    it.updatedAtMs,
                )
            }

    override suspend fun queue(
        roomId: String,
        sequence: Long,
        limit: Int,
    ): List<StoredWaveQueueEntry> {
        if (database.guestRoomDao().session(guestSessionId)?.roomId != roomId) return emptyList()
        return database.guestRoomDao().queue(guestSessionId, sequence, limit).map {
            StoredWaveQueueEntry(
                it.sequence,
                it.position,
                it.queueEntryId,
                it.serverRecordingId,
                it.localUserTrackRefId,
                it.ready,
            )
        }
    }

    override suspend fun advance(
        roomId: String,
        roomEpoch: String,
        sequence: Long,
        nowMs: Long,
    ): Int {
        if (database.guestRoomDao().session(guestSessionId)?.roomId != roomId) return 0
        return database.guestRoomDao().advance(guestSessionId, roomEpoch, sequence, nowMs)
    }

    override suspend fun replaceSnapshot(
        room: StoredWaveRoom,
        preflight: List<StoredWavePreflight>,
        queue: List<StoredWaveQueueEntry>,
    ) {
        val current = requireNotNull(database.guestRoomDao().session(guestSessionId)) {
            "GUEST_SESSION_PROJECTION_REQUIRED"
        }
        require(current.roomId == room.roomId) { "GUEST_ROOM_SCOPE_DENIED" }
        database.guestRoomDao().replaceSnapshot(
            current.copy(
                localMediaProfileId = room.profileId.takeUnless { it.startsWith("guest:") },
                roomEpoch = room.roomEpoch,
                queueVersion = room.queueVersion,
                roomState = room.state,
                lastSequence = room.lastSequence,
                updatedAtMs = room.updatedAtMs,
            ),
            preflight.map {
                GuestWavePreflightEntity(
                    guestSessionId,
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
                GuestWaveQueueProjectionEntity(
                    guestSessionId,
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
