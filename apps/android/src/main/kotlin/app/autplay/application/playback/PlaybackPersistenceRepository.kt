package app.autplay.application.playback

import androidx.room3.withWriteTransaction
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.library.SliceMutationResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.P07PayloadCodec
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.QueueEntryEntity
import app.autplay.data.local.entity.QueueSnapshotEntity
import app.autplay.domain.LocalId
import app.autplay.playback.LogicalListeningCheckpoint
import app.autplay.playback.LogicalListeningSession
import app.autplay.playback.PlaybackQueueEntry
import app.autplay.playback.PlaybackQueueSnapshot
import app.autplay.playback.PlaybackRecommendationAttribution
import app.autplay.playback.PlaybackSessionOwnerBinding
import app.autplay.playback.QueueMediaItemMapper
import app.autplay.playback.QueueRestore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class NewPlaybackQueueEntry(
    val queueEntryId: LocalId,
    val trackRefId: LocalId,
    val sourceOrigin: String,
    val sourceAudioPolicy: String,
    val recommendationAttributionJson: String? = null,
)

data class RestoredPlaybackQueue(
    val snapshot: QueueSnapshotEntity,
    val entries: List<QueueEntryEntity>,
    val media: QueueRestore,
)

/** Owns Room queue/session transactions; it never persists a URL, token, or byte progress. */
class PlaybackPersistenceRepository(
    private val database: AutPlayDatabase,
    private val history: LibraryVerticalSliceRepository = LibraryVerticalSliceRepository(database),
) {
    suspend fun activateQueue(
        snapshotId: LocalId,
        entries: List<NewPlaybackQueueEntry>,
        queueType: String,
        sourceContextId: String?,
        serverProfileId: String?,
        listeningContext: String,
        nowMs: Long,
        startEntryId: LocalId? = null,
    ) {
        require(entries.isNotEmpty() && entries.size <= MAX_QUEUE_ENTRIES)
        require(entries.map { it.queueEntryId }.distinct().size == entries.size)
        require(TOKEN_UPPER.matches(queueType) && TOKEN_UPPER.matches(listeningContext))
        val rows = entries.mapIndexed { index, entry ->
            val attribution = entry.recommendationAttributionJson?.let(P07PayloadCodec::canonicalize)
            val parsed = attribution?.let(::parseAttribution)
            require((entry.sourceOrigin == "RECOMMENDED") == (parsed != null))
            QueueEntryEntity(
                queueEntryId = entry.queueEntryId.value,
                queueSnapshotId = snapshotId.value,
                localUserTrackRefId = entry.trackRefId.value,
                position = index.toLong(),
                sourceOrigin = entry.sourceOrigin,
                recommendationRequestId = parsed?.recommendationRequestId,
                sourceAudioPolicy = entry.sourceAudioPolicy,
                createdAtMs = nowMs,
                recommendationAttributionJson = attribution,
            )
        }
        val currentEntryId = startEntryId?.value ?: rows.first().queueEntryId
        require(rows.any { it.queueEntryId == currentEntryId })
        database.withWriteTransaction {
            database.queueDao().deactivateOtherSnapshots(snapshotId.value, nowMs)
            database.queueDao().insertSnapshot(
                QueueSnapshotEntity(
                    queueSnapshotId = snapshotId.value,
                    queueType = queueType,
                    sourceContextId = sourceContextId,
                    currentEntryId = currentEntryId,
                    currentPositionMs = 0,
                    shuffleMode = "OFF",
                    repeatMode = "OFF",
                    seed = null,
                    generationVersion = "p08-v1",
                    isActive = true,
                    activeSlot = "ACTIVE",
                    createdAtMs = nowMs,
                    updatedAtMs = nowMs,
                    serverProfileId = serverProfileId,
                    listeningContext = listeningContext,
                ),
            )
            database.queueDao().insertEntries(rows)
        }
    }

    /**
     * Startup recovery finalizes sessions orphaned when a replacement queue was committed before
     * the service could receive its stop callback. The old snapshot retains owner and context, so
     * recovery never retargets the event to the new active queue/profile.
     */
    suspend fun restoreActive(nowMs: Long = System.currentTimeMillis()): RestoredPlaybackQueue? {
        recoverStaleInactiveSessions(nowMs)
        val snapshot = database.queueDao().activeSnapshotOnce() ?: return null
        val entries = database.queueDao().entries(snapshot.queueSnapshotId, MAX_QUEUE_ENTRIES)
        if (entries.isEmpty()) return null
        val core = PlaybackQueueSnapshot(
            snapshotId = LocalId(snapshot.queueSnapshotId),
            entries = entries.map(::toCore),
            currentEntryId = snapshot.currentEntryId?.let(::LocalId),
            currentPositionMs = snapshot.currentPositionMs,
        )
        return RestoredPlaybackQueue(snapshot, entries, QueueMediaItemMapper.restore(core))
    }

    suspend fun startSession(
        entryId: LocalId,
        positionMs: Long,
        nowMs: Long,
        ownerBinding: PlaybackSessionOwnerBinding?,
    ): LogicalListeningCheckpoint {
        val snapshot = requireNotNull(database.queueDao().activeSnapshotOnce()) { "ACTIVE_QUEUE_NOT_FOUND" }
        val entry = requireNotNull(database.queueDao().entry(entryId.value)) { "QUEUE_ENTRY_NOT_FOUND" }
        require(entry.queueSnapshotId == snapshot.queueSnapshotId)
        val checkpoint = LogicalListeningSession.start(toCore(entry), LocalId.random(), nowMs, positionMs, ownerBinding)
        persistCheckpoint(snapshot, checkpoint, positionMs, nowMs)
        return checkpoint
    }

    suspend fun recoverSession(): LogicalListeningCheckpoint? {
        val snapshot = database.queueDao().activeSnapshotOnce() ?: return null
        return checkpointFromSnapshot(snapshot)
    }

    /** Finalizes every persisted session on an inactive snapshot using its stable event identity. */
    suspend fun recoverStaleInactiveSessions(nowMs: Long): List<SliceMutationResult> {
        require(nowMs >= 0)
        return database.queueDao().inactiveSnapshotsWithActiveSessions(MAX_STALE_SESSIONS).map { snapshot ->
            val checkpoint = checkpointFromSnapshot(snapshot) ?: return@map null
            finalizeSession(
                current = checkpoint,
                endPositionMs = snapshot.currentPositionMs,
                durationMs = null,
                observedPlaybackDeltaMs = 0,
                nowMs = nowMs,
            )
        }.filterNotNull()
    }

    private suspend fun checkpointFromSnapshot(snapshot: QueueSnapshotEntity): LogicalListeningCheckpoint? {
        val eventId = snapshot.activeListeningEventId ?: return null
        val entryId = snapshot.currentEntryId ?: return null
        val entry = database.queueDao().entry(entryId) ?: return null
        val ownerValues = listOf(
            snapshot.activeSessionUserId,
            snapshot.activeSessionDeviceId,
            snapshot.activeSessionServerProfileId,
        )
        require(ownerValues.all { it == null } || ownerValues.all { it != null }) { "PLAYBACK_SESSION_OWNER_INCOMPLETE" }
        return LogicalListeningCheckpoint(
            listeningEventId = LocalId(eventId),
            queueEntryId = LocalId(entryId),
            trackRefId = LocalId(entry.localUserTrackRefId),
            startedAtMs = snapshot.activeSessionStartedAtMs ?: return null,
            startPositionMs = snapshot.activeSessionStartPositionMs ?: return null,
            lastObservedPositionMs = snapshot.currentPositionMs,
            accumulatedPlayedMs = snapshot.activeSessionObservedPlayedMs ?: 0,
            attribution = entry.recommendationAttributionJson?.let(::parseAttribution),
            ownerBinding = snapshot.activeSessionUserId?.let { userId ->
                PlaybackSessionOwnerBinding(
                    userId,
                    requireNotNull(snapshot.activeSessionDeviceId),
                    requireNotNull(snapshot.activeSessionServerProfileId),
                )
            },
        )
    }

    suspend fun checkpoint(
        current: LogicalListeningCheckpoint,
        positionMs: Long,
        observedPlaybackDeltaMs: Long,
        shuffleMode: String,
        repeatMode: String,
        seed: Long?,
        nowMs: Long,
    ): LogicalListeningCheckpoint {
        val next = LogicalListeningSession.checkpoint(current, positionMs, observedPlaybackDeltaMs)
        val entry = requireNotNull(database.queueDao().entry(next.queueEntryId.value))
        val snapshot = requireNotNull(database.queueDao().snapshot(entry.queueSnapshotId))
        require(database.queueDao().checkpoint(
            snapshot.queueSnapshotId,
            next.queueEntryId.value,
            positionMs,
            shuffleMode,
            repeatMode,
            seed,
            next.listeningEventId.value,
            next.startedAtMs,
            next.startPositionMs,
            next.accumulatedPlayedMs,
            next.ownerBinding?.userId,
            next.ownerBinding?.deviceId,
            next.ownerBinding?.serverProfileId,
            nowMs,
        ) == 1)
        return next
    }

    suspend fun finalizeSession(
        current: LogicalListeningCheckpoint,
        endPositionMs: Long,
        durationMs: Long?,
        observedPlaybackDeltaMs: Long,
        nowMs: Long,
    ): SliceMutationResult {
        val (_, event) = LogicalListeningSession.finalizeOnce(
            current,
            endPositionMs,
            durationMs,
            observedPlaybackDeltaMs,
        )
        val finalized = requireNotNull(event)
        val queueEntry = requireNotNull(database.queueDao().entry(finalized.queueEntryId.value))
        val origin = queueEntry.sourceOrigin
        val snapshot = requireNotNull(database.queueDao().snapshot(queueEntry.queueSnapshotId))
        val result = history.recordListening(
            binding = current.ownerBinding?.toClientBinding(),
            listeningEventId = finalized.listeningEventId,
            trackRefId = finalized.trackRefId,
            playedMs = finalized.playedMs,
            durationMs = finalized.durationMs,
            excluded = false,
            origin = origin,
            attributionJson = queueEntry.recommendationAttributionJson,
            context = snapshot.listeningContext,
            now = nowMs,
            startedAtMs = finalized.startedAtMs,
            sessionStartPositionMs = current.startPositionMs,
            sessionEndPositionMs = finalized.endPositionMs,
        )
        database.queueDao().clearFinalizedSession(
            snapshot.queueSnapshotId,
            finalized.listeningEventId.value,
            finalized.queueEntryId.value,
            endPositionMs,
            nowMs,
        )
        return result
    }

    private suspend fun persistCheckpoint(
        snapshot: QueueSnapshotEntity,
        checkpoint: LogicalListeningCheckpoint,
        positionMs: Long,
        nowMs: Long,
    ) {
        require(database.queueDao().checkpoint(
            snapshot.queueSnapshotId,
            checkpoint.queueEntryId.value,
            positionMs,
            snapshot.shuffleMode,
            snapshot.repeatMode,
            snapshot.seed,
            checkpoint.listeningEventId.value,
            checkpoint.startedAtMs,
            checkpoint.startPositionMs,
            checkpoint.accumulatedPlayedMs,
            checkpoint.ownerBinding?.userId,
            checkpoint.ownerBinding?.deviceId,
            checkpoint.ownerBinding?.serverProfileId,
            nowMs,
        ) == 1)
    }

    private fun toCore(row: QueueEntryEntity): PlaybackQueueEntry = PlaybackQueueEntry(
        queueEntryId = LocalId(row.queueEntryId),
        trackRefId = LocalId(row.localUserTrackRefId),
        position = row.position,
        attribution = row.recommendationAttributionJson?.let(::parseAttribution),
    )

    private fun parseAttribution(raw: String): PlaybackRecommendationAttribution {
        val canonical = P07PayloadCodec.canonicalize(raw)
        val value: JsonObject = Json.parseToJsonElement(canonical).jsonObject
        return PlaybackRecommendationAttribution(
            recommendationRequestId = value.required("recommendation_request_id"),
            recordingId = value.required("recording_id"),
            sourceRank = value.required("source_rank").toInt(),
            source = value.required("source"),
            surface = value.required("surface"),
            localImpressionId = LocalId(value.required("impression_event_local_id")),
            serverImpressionId = value["impression_event_server_id"]?.jsonPrimitive?.content
                ?.takeUnless { it == "null" },
        )
    }

    private fun JsonObject.required(name: String): String =
        requireNotNull(this[name]) { "RECOMMENDATION_ATTRIBUTION_INVALID" }.jsonPrimitive.content

    private fun PlaybackSessionOwnerBinding.toClientBinding(): ClientEventBinding = ClientEventBinding(
        UserId(userId),
        DeviceId(deviceId),
        ServerProfileId(serverProfileId),
    )

    private companion object {
        const val MAX_QUEUE_ENTRIES = 10_000
        const val MAX_STALE_SESSIONS = 100
        val TOKEN_UPPER = Regex("^[A-Z][A-Z0-9_]{0,99}$")
    }
}
