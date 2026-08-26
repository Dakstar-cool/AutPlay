package app.autplay.application.playback

import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.QueueEntryEntity
import app.autplay.data.local.entity.QueueSnapshotEntity
import app.autplay.domain.LocalId
import app.autplay.application.sync.P07PayloadCodec
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Bounded, local-first editor for the active ordinary queue. Wave rows are never touched. */
class QueueEditorRepository(
    private val database: AutPlayDatabase,
    private val playback: PlaybackSessionOwner,
) {
    fun observeActive(expectedProfileId: String?, limit: Int = MAX_QUEUE_ENTRIES): Flow<OrdinaryQueueProjection?> = combine(
        database.queueDao().activeSnapshot(),
        database.queueDao().activeEntries(limit),
    ) { snapshot, entries -> snapshot to entries }.map { (snapshot, entries) ->
        snapshot?.takeIf { it.serverProfileId == expectedProfileId }?.let {
            projection(it, entries.filter { entry -> entry.queueSnapshotId == it.queueSnapshotId })
        }
    }

    suspend fun loadActive(expectedProfileId: String?, limit: Int = MAX_QUEUE_ENTRIES): OrdinaryQueueProjection? =
        database.queueDao().activeSnapshotOnce()?.takeIf { it.serverProfileId == expectedProfileId }
            ?.let { loadProjection(it, limit) }

    suspend fun addNext(entry: NewPlaybackQueueEntry, expectedProfileId: String?, expectedSnapshotId: LocalId?): QueueEditResult {
        if (database.queueDao().activeSnapshotOnce() == null) return createUserQueue(entry, expectedProfileId, expectedSnapshotId)
        return edit(expectedProfileId, expectedSnapshotId) { snapshot, entries, currentIndex ->
            val insertion = (currentIndex + 1).coerceIn(0, entries.size)
            entries.toMutableList().apply { add(insertion, entry.toEntity(snapshot.queueSnapshotId, System.currentTimeMillis())) }
        }
    }

    suspend fun addToEnd(entry: NewPlaybackQueueEntry, expectedProfileId: String?, expectedSnapshotId: LocalId?): QueueEditResult {
        if (database.queueDao().activeSnapshotOnce() == null) return createUserQueue(entry, expectedProfileId, expectedSnapshotId)
        return edit(expectedProfileId, expectedSnapshotId) { snapshot, entries, _ ->
            entries.toMutableList().apply { add(entry.toEntity(snapshot.queueSnapshotId, System.currentTimeMillis())) }
        }
    }

    suspend fun removeUpcoming(entryId: LocalId, expectedProfileId: String?, expectedSnapshotId: LocalId): QueueEditResult =
        edit(expectedProfileId, expectedSnapshotId) { _, entries, currentIndex ->
            val index = entries.indexOfFirst { it.queueEntryId == entryId.value }
            if (index <= currentIndex) throw QueueEditFailure("QUEUE_ENTRY_NOT_UPCOMING")
            entries.filterIndexed { itemIndex, _ -> itemIndex != index }
        }

    suspend fun moveUpcoming(entryId: LocalId, beforeEntryId: LocalId?, expectedProfileId: String?, expectedSnapshotId: LocalId): QueueEditResult =
        edit(expectedProfileId, expectedSnapshotId) { _, entries, currentIndex ->
            val from = entries.indexOfFirst { it.queueEntryId == entryId.value }
            if (from <= currentIndex) throw QueueEditFailure("QUEUE_ENTRY_NOT_UPCOMING")
            val mutable = entries.toMutableList()
            val moving = mutable.removeAt(from)
            val destination = beforeEntryId?.let { id -> mutable.indexOfFirst { it.queueEntryId == id.value } }
                ?: mutable.size
            if (destination <= currentIndex || destination < 0) throw QueueEditFailure("QUEUE_TARGET_NOT_UPCOMING")
            mutable.add(destination, moving)
            mutable
        }

    suspend fun clearUpcoming(expectedProfileId: String?, expectedSnapshotId: LocalId): QueueEditResult =
        edit(expectedProfileId, expectedSnapshotId) { _, entries, currentIndex -> entries.take(currentIndex + 1) }

    private suspend fun edit(
        expectedProfileId: String?,
        expectedSnapshotId: LocalId?,
        transform: (QueueSnapshotEntity, List<QueueEntryEntity>, Int) -> List<QueueEntryEntity>,
    ): QueueEditResult {
        val changed = database.withWriteTransaction {
            val snapshot = database.queueDao().activeSnapshotOnce()
                ?: throw QueueEditFailure("QUEUE_ACTIVE_NOT_FOUND")
            if (expectedSnapshotId == null || snapshot.queueSnapshotId != expectedSnapshotId.value) {
                throw QueueEditFailure("QUEUE_SNAPSHOT_STALE")
            }
            if (snapshot.queueType !in EDITABLE_TYPES) throw QueueEditFailure("QUEUE_TYPE_NOT_EDITABLE")
            if (snapshot.serverProfileId != expectedProfileId) throw QueueEditFailure("QUEUE_PROFILE_MISMATCH")
            val rows = database.queueDao().entries(snapshot.queueSnapshotId, MAX_QUEUE_ENTRIES)
            if (rows.isEmpty() || rows.size >= MAX_QUEUE_ENTRIES && snapshot.currentEntryId == null) {
                throw QueueEditFailure("QUEUE_SNAPSHOT_INVALID")
            }
            val currentIndex = rows.indexOfFirst { it.queueEntryId == snapshot.currentEntryId }
            if (currentIndex < 0) throw QueueEditFailure("QUEUE_CURRENT_ENTRY_STALE")
            val next = transform(snapshot, rows, currentIndex)
            if (next.isEmpty() || next.size > MAX_QUEUE_ENTRIES || next.map { it.queueEntryId }.distinct().size != next.size) {
                throw QueueEditFailure("QUEUE_EDIT_INVALID")
            }
            next.forEach { row ->
                val track = database.libraryDao().trackRef(row.localUserTrackRefId)
                    ?: throw QueueEditFailure("QUEUE_TRACK_NOT_FOUND")
                if (!matchesOwnerProfile(track.serverProfileId, expectedProfileId) || track.deletedAtMs != null) {
                    throw QueueEditFailure("QUEUE_PROFILE_MISMATCH")
                }
            }
            database.queueDao().deleteEntriesForSnapshot(snapshot.queueSnapshotId)
            database.queueDao().insertEntries(next.mapIndexed { index, row -> row.copy(position = index.toLong()) })
            if (snapshot.queueType != "USER") {
                check(database.queueDao().promoteActiveSnapshot(snapshot.queueSnapshotId, "USER", null, System.currentTimeMillis()) == 1)
            }
            snapshot.queueSnapshotId
        }
        // This happens strictly after the Room commit. A process crash here restores Room truth.
        playback.dispatch(PlaybackCommand.RefreshQueue(LocalId(changed)))
        return QueueEditResult(LocalId(changed), created = false)
    }

    private suspend fun createUserQueue(entry: NewPlaybackQueueEntry, expectedProfileId: String?, expectedSnapshotId: LocalId?): QueueEditResult {
        if (expectedSnapshotId != null) throw QueueEditFailure("QUEUE_SNAPSHOT_STALE")
        val snapshotId = LocalId.random()
        database.withWriteTransaction {
            if (database.queueDao().activeSnapshotOnce() != null) throw QueueEditFailure("QUEUE_SNAPSHOT_STALE")
            val nowMs = System.currentTimeMillis()
            val row = entry.toEntity(snapshotId.value, nowMs)
            val track = database.libraryDao().trackRef(row.localUserTrackRefId)
                ?: throw QueueEditFailure("QUEUE_TRACK_NOT_FOUND")
            if (!matchesOwnerProfile(track.serverProfileId, expectedProfileId) || track.deletedAtMs != null) {
                throw QueueEditFailure("QUEUE_PROFILE_MISMATCH")
            }
            database.queueDao().insertSnapshot(
                QueueSnapshotEntity(
                    queueSnapshotId = snapshotId.value, queueType = "USER", sourceContextId = null,
                    currentEntryId = row.queueEntryId, currentPositionMs = 0, shuffleMode = "OFF",
                    repeatMode = "OFF", seed = null, generationVersion = "l1-v1", isActive = true,
                    activeSlot = "ACTIVE", createdAtMs = nowMs, updatedAtMs = nowMs,
                    serverProfileId = expectedProfileId, listeningContext = "GENERAL",
                ),
            )
            database.queueDao().insertEntries(listOf(row.copy(position = 0)))
        }
        return QueueEditResult(snapshotId, created = true)
    }

    private suspend fun loadProjection(snapshot: QueueSnapshotEntity, limit: Int): OrdinaryQueueProjection {
        require(limit in 1..MAX_QUEUE_ENTRIES) { "QUEUE_LIMIT_INVALID" }
        return projection(snapshot, database.queueDao().entries(snapshot.queueSnapshotId, limit))
    }

    private suspend fun projection(snapshot: QueueSnapshotEntity, entries: List<QueueEntryEntity>): OrdinaryQueueProjection {
        val currentIndex = entries.indexOfFirst { it.queueEntryId == snapshot.currentEntryId }
        return OrdinaryQueueProjection(
            snapshotId = LocalId(snapshot.queueSnapshotId), queueType = snapshot.queueType,
            currentEntryId = snapshot.currentEntryId?.let(::LocalId),
            entries = entries.mapIndexed { index, entry ->
                val track = database.libraryDao().trackRef(entry.localUserTrackRefId)
                OrdinaryQueueEntry(LocalId(entry.queueEntryId), LocalId(entry.localUserTrackRefId), track?.rawTitle, track?.rawArtist, index == currentIndex, index > currentIndex)
            },
            canPrevious = currentIndex > 0,
            canNext = currentIndex >= 0 && currentIndex + 1 < entries.size,
        )
    }

    private fun NewPlaybackQueueEntry.toEntity(snapshotId: String, nowMs: Long): QueueEntryEntity {
        val canonical = recommendationAttributionJson?.let(P07PayloadCodec::canonicalize)
        val requestId = canonical?.let {
            Json.parseToJsonElement(it).jsonObject["recommendation_request_id"]?.jsonPrimitive?.content
                ?: throw QueueEditFailure("QUEUE_ATTRIBUTION_INVALID")
        }
        if (!TOKEN_UPPER.matches(sourceOrigin) || !TOKEN_UPPER.matches(sourceAudioPolicy)) {
            throw QueueEditFailure("QUEUE_ENTRY_TOKEN_INVALID")
        }
        if ((sourceOrigin == "RECOMMENDED") != (canonical != null)) throw QueueEditFailure("QUEUE_ATTRIBUTION_INVALID")
        return QueueEntryEntity(
            queueEntryId.value, snapshotId, trackRefId.value, Long.MAX_VALUE, sourceOrigin, requestId,
            sourceAudioPolicy, nowMs, canonical,
        )
    }

    /** Unpaired local library rows retain the explicit legacy owner sentinel, not a null profile. */
    private fun matchesOwnerProfile(trackProfileId: String, expectedProfileId: String?): Boolean =
        trackProfileId == (expectedProfileId ?: LEGACY_UNSCOPED_PROFILE)

    companion object {
        const val MAX_QUEUE_ENTRIES = 10_000
        private const val LEGACY_UNSCOPED_PROFILE = "legacy-unscoped"
        private val EDITABLE_TYPES = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
        private val TOKEN_UPPER = Regex("^[A-Z][A-Z0-9_]{0,99}$")
    }
}

data class OrdinaryQueueProjection(val snapshotId: LocalId, val queueType: String, val currentEntryId: LocalId?, val entries: List<OrdinaryQueueEntry>, val canPrevious: Boolean, val canNext: Boolean)
data class OrdinaryQueueEntry(val queueEntryId: LocalId, val trackRefId: LocalId, val title: String?, val artist: String?, val isCurrent: Boolean, val isUpcoming: Boolean)
data class QueueEditResult(val snapshotId: LocalId, val created: Boolean)
class QueueEditFailure(val code: String) : IllegalStateException(code)
