package app.autplay.application.sync

import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.AppliedServerEventEntity
import app.autplay.data.local.entity.DeferredServerEventEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.SyncConflictEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.SyncRuntimeStatusEntity
import app.autplay.data.local.entity.SyncBootstrapStateEntity
import app.autplay.data.local.entity.TombstoneEntity
import app.autplay.data.local.entity.AggregateRedirectEntity
import app.autplay.domain.LocalId
import java.util.UUID
import kotlin.math.min
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.booleanOrNull

/** Bounded, payload-free state rendered by the Sync Status surface. */
data class SyncStatus(
    val pending: Int,
    val deadLetters: Int,
    val conflicts: Int,
    val lastSuccessAtMs: Long?,
    val bootstrapState: String,
    val lastErrorCode: String? = null,
)

/** Transport values deliberately retain unknown event strings for safe deferred persistence. */
data class SyncAck(val eventId: String, val outcome: String, val errorCode: String? = null, val retryAfterMs: Long? = null, val retryable: Boolean = false, val originalOutcome: String? = null, val aggregateType: String? = null, val aggregateLocalId: String? = null, val aggregateServerId: String? = null, val serverRowVersion: Long? = null, val redirectAliasServerId: String? = null, val redirectCanonicalServerId: String? = null)
data class RemoteEvent(val eventId: String, val sequence: Long, val eventType: String, val schemaVersion: Int, val payloadJson: String, val aggregateType: String = "UNKNOWN", val aggregateServerId: String? = null, val serverRowVersion: Long? = null, val operation: String = "UPSERT", val tombstoneId: String? = null, val retainUntilMs: Long? = null, val redirectServerId: String? = null)
data class PullPage(val nextCursor: String, val hasMore: Boolean, val events: List<RemoteEvent>)
data class BootstrapPage(val snapshotId: String, val nextPageToken: String?, val snapshotCursor: String?, val hasMore: Boolean, val events: List<RemoteEvent>)

/** The port makes process-death and timeout behavior testable without a real network. */
interface SyncTransport {
    suspend fun push(binding: ClientEventBinding, events: List<OfflineJournalEventEntity>): List<SyncAck>
    suspend fun pull(binding: ClientEventBinding, cursor: String?): PullPage
    suspend fun bootstrap(binding: ClientEventBinding, snapshotId: String?, pageToken: String?, pendingCount: Int): BootstrapPage
}
/** A 410/invalid opaque cursor asks for a snapshot; journal rows are intentionally untouched. */
class InvalidCursorException : IllegalStateException("CURSOR_INVALID")

/**
 * P09 local coordinator. Leasing and every cursor advance are Room transactions; network calls are
 * deliberately outside them so a process death can only replay immutable event IDs.
 */
class SyncCoordinator(
    private val database: AutPlayDatabase,
    private val transport: SyncTransport,
    private val nowMs: () -> Long = System::currentTimeMillis,
    private val token: () -> String = { UUID.randomUUID().toString() },
) {
    suspend fun run(binding: ClientEventBinding): Boolean {
        val cursor = requireCursor(binding)
        database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, null, nowMs(), null))
        try {
            push(binding, cursor)
            var hasMore: Boolean
            var pages = 0
            do {
                val refreshed = requireCursor(binding)
                val outcome = if (refreshed.bootstrapState != "READY") bootstrap(binding, refreshed) else pull(binding, refreshed)
                if (!outcome.completed) return false
                hasMore = outcome.hasMore
                pages++
            } while (hasMore && pages < MAX_DRAIN_PAGES)
            if (hasMore) {
                database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, "PULL_CAP_REACHED", nowMs(), null))
                return false
            }
            compact(binding)
            database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, null, nowMs(), nowMs()))
            return true
        } catch (error: IllegalStateException) {
            database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, error.message?.take(100), nowMs(), null))
            throw error
        }
    }

    private suspend fun push(binding: ClientEventBinding, cursor: SyncCursorEntity) {
        val journal = database.journalDao()
        val now = nowMs()
        database.withWriteTransaction { journal.recoverExpiredLeases(cursor.journalLineageId, now) }
        val candidates = database.withWriteTransaction { journal.nextPending(cursor.journalLineageId, now, PUSH_BATCH_LIMIT) }
        if (candidates.isEmpty()) return
        val lease = token()
        val leased = database.withWriteTransaction {
            candidates.takeWhile { journal.lease(cursor.journalLineageId, it.eventId, lease, now + LEASE_MS) == 1 }
        }
        if (leased.isEmpty()) return
        val response = try { transport.push(binding, leased) } catch (error: Exception) {
            database.withWriteTransaction { leased.forEach { retry(cursor, it, lease, "NETWORK_UNAVAILABLE", null) } }
            throw error
        }
        val acknowledgements = response.associateBy { it.eventId }
        database.withWriteTransaction {
            leased.forEach { event ->
                val ack = acknowledgements[event.eventId]
                val outcome = ack?.let { if (it.outcome == "DUPLICATE") it.originalOutcome ?: "REJECTED" else it.outcome }
                if (ack == null) retry(cursor, event, lease, "PARTIAL_ACK", null)
                else if (!ackPreflight(binding, event, ack, outcome)) retry(cursor, event, lease, "ACK_PROTOCOL_INVALID", null)
                else when (outcome) {
                    "APPLIED", "DUPLICATE" -> {
                        applyAckProjection(binding, event, ack)
                        journal.acknowledge(cursor.journalLineageId, event.eventId, lease, nowMs())
                        database.syncDao().advanceAck(binding.serverProfileId.value, cursor.journalLineageId, event.deviceSequence, nowMs())
                    }
                    "CONFLICT" -> {
                        // A conflict is recoverable user intent, not a dead letter and never retried blindly.
                        journal.finishAttempt(cursor.journalLineageId, event.eventId, lease, "CONFLICT", null, ack.errorCode ?: "POLICY_REVIEW")
                        database.syncDao().upsertConflict(SyncConflictEntity(conflictId(binding.serverProfileId.value, event.eventId), binding.serverProfileId.value, event.aggregateType, event.aggregateLocalId, event.eventId, null, ack.errorCode ?: "POLICY_REVIEW", null, null, "OPEN", null, nowMs(), null))
                    }
                    "REJECTED" -> if (!ack.retryable) {
                        journal.finishAttempt(cursor.journalLineageId, event.eventId, lease, "DEAD_LETTER", null, ack.errorCode)
                    } else retry(cursor, event, lease, ack.errorCode ?: "REJECTED", ack.retryAfterMs)
                    else -> retry(cursor, event, lease, "UNKNOWN_ACK_OUTCOME", null)
                }
            }
        }
    }

    private suspend fun pull(binding: ClientEventBinding, cursor: SyncCursorEntity): DrainOutcome {
        val page = try { transport.pull(binding, cursor.opaqueCursor) } catch (_: InvalidCursorException) {
            database.withWriteTransaction {
                database.syncDao().upsertCursor(cursor.copy(bootstrapState = "RESET_REQUIRED", updatedAtMs = nowMs()))
                database.syncDao().upsertBootstrapState(SyncBootstrapStateEntity(binding.serverProfileId.value, null, null, null, "RESET_REQUIRED", nowMs()))
            }
            return DrainOutcome(false, false)
        } catch (error: Exception) { throw error }
        return DrainOutcome(applyPage(binding, cursor, page), page.hasMore)
    }

    private suspend fun bootstrap(binding: ClientEventBinding, cursor: SyncCursorEntity): DrainOutcome {
        val pending = database.journalDao().nextPending(cursor.journalLineageId, Long.MAX_VALUE, PUSH_BATCH_LIMIT).size
        val bootstrap = database.syncDao().bootstrapState(binding.serverProfileId.value)
        val page = transport.bootstrap(binding, bootstrap?.snapshotId, bootstrap?.pageToken, pending)
        if (page.events.any { !isProjectionSupported(it) }) {
            database.withWriteTransaction {
                page.events.filterNot(::isProjectionSupported).forEach { event -> database.syncDao().defer(DeferredServerEventEntity(binding.serverProfileId.value, event.eventId, event.sequence, event.eventType, event.schemaVersion, event.payloadJson, nowMs(), "UPGRADE_REQUIRED")) }
                database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, "UPGRADE_REQUIRED", nowMs(), null))
            }
            return DrainOutcome(false, false)
        }
        database.withWriteTransaction {
            applyEvents(binding, page.events)
            database.syncDao().upsertBootstrapState(SyncBootstrapStateEntity(
                binding.serverProfileId.value, page.snapshotId, page.nextPageToken, page.snapshotCursor,
                if (page.hasMore) "BOOTSTRAPPING" else "READY", nowMs(),
            ))
            database.syncDao().upsertCursor(cursor.copy(
                // Incremental cursor is untouched until the final snapshot cutover.
                opaqueCursor = if (page.hasMore) cursor.opaqueCursor else page.snapshotCursor,
                bootstrapSnapshotId = page.snapshotId,
                bootstrapState = if (page.hasMore) "BOOTSTRAPPING" else "READY",
                lastSyncAtMs = if (page.hasMore) cursor.lastSyncAtMs else nowMs(),
                updatedAtMs = nowMs(),
            ))
        }
        return DrainOutcome(true, page.hasMore)
    }

    private suspend fun applyPage(binding: ClientEventBinding, cursor: SyncCursorEntity, page: PullPage): Boolean {
        val unsupported = page.events.filterNot(::isProjectionSupported)
        if (unsupported.isNotEmpty()) {
            // Preserve evidence but fail closed: cursor must not skip a page this client cannot apply.
            database.withWriteTransaction {
                unsupported.forEach { event ->
                    database.syncDao().defer(DeferredServerEventEntity(binding.serverProfileId.value, event.eventId, event.sequence, event.eventType, event.schemaVersion, event.payloadJson, nowMs(), "UNKNOWN_EVENT_VERSION"))
                }
                database.syncDao().upsertRuntimeStatus(SyncRuntimeStatusEntity(binding.serverProfileId.value, "UPGRADE_REQUIRED", nowMs(), null))
            }
            return false
        }
        database.withWriteTransaction {
            applyEvents(binding, page.events)
            database.syncDao().upsertCursor(cursor.copy(
                opaqueCursor = page.nextCursor,
                lastPulledServerSequence = maxOf(cursor.lastPulledServerSequence, page.events.maxOfOrNull { it.sequence } ?: cursor.lastPulledServerSequence),
                lastSyncAtMs = nowMs(), updatedAtMs = nowMs(),
            ))
        }
        return true
    }

    /** Unknown schema/type survives as deferred evidence; known event application is idempotent. */
    private suspend fun applyEvents(binding: ClientEventBinding, events: List<RemoteEvent>) {
        require(events.size <= PULL_BATCH_LIMIT)
        var previous = 0L
        events.forEach { event ->
            if (event.sequence > 0) {
                // Server sequences are global; events filtered for this owner may legitimately gap.
                require(event.sequence > previous) { "SERVER_SEQUENCE_NOT_ASCENDING" }
                previous = event.sequence
            }
            if (database.syncDao().isServerEventKnown(binding.serverProfileId.value, event.eventId)) return@forEach
            if (isProjectionSupported(event)) {
                check(applyProjection(binding, event)) { "PROJECTION_INCOMPLETE" }
                if (event.sequence > 0) database.syncDao().markApplied(AppliedServerEventEntity(binding.serverProfileId.value, event.eventId, event.sequence, nowMs()))
            } else {
                database.syncDao().defer(DeferredServerEventEntity(binding.serverProfileId.value, event.eventId, event.sequence, event.eventType, event.schemaVersion, event.payloadJson, nowMs(), "UNKNOWN_EVENT_VERSION"))
            }
        }
    }

    private fun isProjectionSupported(event: RemoteEvent): Boolean = event.schemaVersion == 1 && KNOWN_EVENT_TYPES.contains(event.eventType)

    /** Only CLEAN rows accept a remote projection. DIRTY rows retain local intent and get a conflict. */
    private suspend fun applyProjection(binding: ClientEventBinding, event: RemoteEvent): Boolean {
        if (event.eventType in setOf("RECOMMENDATION_IMPRESSION_RECORDED", "RECOMMENDATION_FEEDBACK_RECORDED")) {
            database.syncDao().insertInteractionFact(app.autplay.data.local.entity.RecommendationInteractionFactEntity(binding.serverProfileId.value, event.eventId, event.eventType, event.payloadJson, nowMs()))
            return true
        }
        if (event.eventType == "USER_TRACK_PREFERENCE_SET") return applyPreferenceProjection(binding, event)
        if (event.eventType == "LISTENING_EVENT_RECORDED") return applyListeningProjection(binding, event)
        val serverId = event.aggregateServerId ?: return false
        val profile = binding.serverProfileId.value
        // Tombstones are authoritative facts even when the deleted relation was never pulled
        // (or its parent is unavailable). Never manufacture a live aggregate for a delete.
        if (event.operation == "DELETE") {
            val local = localIdFor(profile, event.aggregateType, serverId) ?: remoteLocalId(profile, serverId)
            if (localIdFor(profile, event.aggregateType, serverId) != null && isDirty(event.aggregateType, local)) {
                database.syncDao().upsertConflict(SyncConflictEntity(conflictId(profile, event.eventId), profile, event.aggregateType, local, null, event.eventId, "DELETE_VS_EDIT", null, event.payloadJson, "OPEN", null, nowMs(), null))
                return true
            }
            database.syncDao().upsertTombstone(TombstoneEntity(event.tombstoneId ?: token(), profile, event.aggregateType, local, serverId, event.eventId, nowMs(), event.retainUntilMs ?: nowMs() + TOMBSTONE_RETAIN_MS, true))
            if (localIdFor(profile, event.aggregateType, serverId) != null) applyDeleteProjection(event.aggregateType, local)
            return true
        }
        if (event.operation == "REDIRECT") {
            val canonical = event.redirectServerId ?: return false
            if (event.aggregateType == "RECORDING") {
                database.syncDao().upsertRedirect(AggregateRedirectEntity(profile, "RECORDING", remoteLocalId(profile, serverId), serverId, remoteLocalId(profile, canonical), canonical, event.sequence, nowMs()))
                return true
            }
            val local = localIdFor(binding.serverProfileId.value, event.aggregateType, serverId) ?: adoptServerAggregate(binding, event) ?: return false
            database.syncDao().upsertRedirect(AggregateRedirectEntity(profile, event.aggregateType, local, serverId, local, canonical, event.sequence, nowMs()))
            return true
        }
        val local = localIdFor(binding.serverProfileId.value, event.aggregateType, serverId) ?: adoptServerAggregate(binding, event) ?: return false
        val dirty = isDirty(event.aggregateType, local)
        if (dirty) {
            database.syncDao().upsertConflict(SyncConflictEntity(conflictId(profile, event.eventId), profile, event.aggregateType, local, null, event.eventId, if (event.operation == "DELETE") "DELETE_VS_EDIT" else "STALE_VERSION", null, event.payloadJson, "OPEN", null, nowMs(), null))
            return true
        }
        if (event.operation == "UPSERT") applyPayloadProjection(event.aggregateType, local, event.payloadJson)
        updateCleanVersion(event.aggregateType, local, event.serverRowVersion)
        return true
    }

    private suspend fun applyAckProjection(binding: ClientEventBinding, event: OfflineJournalEventEntity, ack: SyncAck) {
        val type = ack.aggregateType ?: event.aggregateType
        val local = ack.aggregateLocalId ?: event.aggregateLocalId
        val server = ack.aggregateServerId
        // A duplicate replay must apply exactly the original terminal outcome, never manufacture a second projection.
        if (ack.outcome == "DUPLICATE" && ack.originalOutcome !in setOf("APPLIED", null)) return
        when (type) {
            "USER_TRACK_REF" -> database.libraryDao().trackRef(local)?.let { database.libraryDao().upsertTrackRef(it.copy(serverUserTrackRefId = server ?: it.serverUserTrackRefId, serverRowVersion = ack.serverRowVersion ?: it.serverRowVersion, syncState = "CLEAN", serverProfileId = binding.serverProfileId.value)) }
            "LIBRARY_ENTRY" -> database.libraryDao().entry(local)?.let { database.libraryDao().upsertEntry(it.copy(serverLibraryEntryId = server ?: it.serverLibraryEntryId, serverRowVersion = ack.serverRowVersion ?: it.serverRowVersion, syncState = "CLEAN", serverProfileId = binding.serverProfileId.value)) }
            "PLAYLIST" -> database.playlistDao().playlist(local)?.let { database.playlistDao().upsertPlaylist(it.copy(serverPlaylistId = server ?: it.serverPlaylistId, serverRowVersion = ack.serverRowVersion ?: it.serverRowVersion, syncState = "CLEAN", serverProfileId = binding.serverProfileId.value)) }
            "PLAYLIST_ENTRY" -> database.playlistDao().entry(local)?.let { database.playlistDao().upsertEntry(it.copy(serverPlaylistEntryId = server ?: it.serverPlaylistEntryId, serverRowVersion = ack.serverRowVersion ?: it.serverRowVersion, syncState = "CLEAN", serverProfileId = binding.serverProfileId.value)) }
        }
        if (ack.redirectAliasServerId != null && ack.redirectCanonicalServerId != null) {
            database.syncDao().upsertRedirect(AggregateRedirectEntity(binding.serverProfileId.value, type, local, ack.redirectAliasServerId, local, ack.redirectCanonicalServerId, 0, nowMs()))
        }
    }

    /** Reject malformed or cross-profile acknowledgements before they can alter local ownership. */
    private suspend fun ackPreflight(binding: ClientEventBinding, event: OfflineJournalEventEntity, ack: SyncAck, outcome: String?): Boolean {
        if (ack.eventId != event.eventId || ack.aggregateType != event.aggregateType || ack.aggregateLocalId != event.aggregateLocalId) return false
        if (outcome != "APPLIED") return true
        val serverId = ack.aggregateServerId ?: return false
        if (ack.serverRowVersion == null || runCatching { UUID.fromString(serverId) }.isFailure) return false
        val profile = binding.serverProfileId.value
        return when (event.aggregateType) {
            "USER_TRACK_REF" -> database.libraryDao().trackRef(event.aggregateLocalId)?.let { (it.serverProfileId == profile || it.serverProfileId == LEGACY_PROFILE) && (it.serverUserTrackRefId == null || it.serverUserTrackRefId == serverId) } ?: false
            "LIBRARY_ENTRY" -> database.libraryDao().entry(event.aggregateLocalId)?.let { (it.serverProfileId == profile || it.serverProfileId == LEGACY_PROFILE) && (it.serverLibraryEntryId == null || it.serverLibraryEntryId == serverId) } ?: false
            "PLAYLIST" -> database.playlistDao().playlist(event.aggregateLocalId)?.let { (it.serverProfileId == profile || it.serverProfileId == LEGACY_PROFILE) && (it.serverPlaylistId == null || it.serverPlaylistId == serverId) } ?: false
            "PLAYLIST_ENTRY" -> database.playlistDao().entry(event.aggregateLocalId)?.let { (it.serverProfileId == profile || it.serverProfileId == LEGACY_PROFILE) && (it.serverPlaylistEntryId == null || it.serverPlaylistEntryId == serverId) } ?: false
            else -> true
        }
    }

    private suspend fun localIdFor(profile: String, type: String, serverId: String): String? = when (type) {
        "USER_TRACK_REF" -> database.libraryDao().trackRefByServerId(profile, serverId)?.localUserTrackRefId
        "LIBRARY_ENTRY" -> database.libraryDao().entryByServerId(profile, serverId)?.localLibraryEntryId
        "PLAYLIST" -> database.playlistDao().playlistByServerId(profile, serverId)?.localPlaylistId
        "PLAYLIST_ENTRY" -> database.playlistDao().entryByServerId(profile, serverId)?.localPlaylistEntryId
        else -> null
    }

    /** Remote IDs are profile-namespaced locally so equal server UUIDs cannot cross profile boundaries. */
    private suspend fun adoptServerAggregate(binding: ClientEventBinding, event: RemoteEvent): String? {
        val serverId = event.aggregateServerId ?: return null
        val profile = binding.serverProfileId.value
        val localId = remoteLocalId(profile, serverId)
        when (event.aggregateType) {
            "USER_TRACK_REF" -> {
                val existing = database.libraryDao().trackRef(localId)
                if (existing != null && existing.serverUserTrackRefId != serverId) return idCollision(binding, event)
                database.libraryDao().upsertTrackRef(app.autplay.data.local.entity.UserTrackRefEntity(localId, serverId, null, null, "UNRESOLVED", null, null, null, null, null, "CLEAN", event.serverRowVersion, 0, nowMs(), nowMs(), null, profile))
            }
            "PLAYLIST" -> {
                val existing = database.playlistDao().playlist(localId)
                if (existing != null && existing.serverPlaylistId != serverId) return idCollision(binding, event)
                database.playlistDao().upsertPlaylist(app.autplay.data.local.entity.PlaylistEntity(localId, serverId, "Remote playlist", null, "PRIVATE", "MANUAL", null, null, "CLEAN", event.serverRowVersion, 0, nowMs(), nowMs(), null, profile))
            }
            "LIBRARY_ENTRY" -> {
                val existing = database.libraryDao().entry(localId)
                if (existing != null && existing.serverLibraryEntryId != serverId) return idCollision(binding, event)
                val parentServerId = payloadId(event, "server_user_track_ref_id") ?: return null
                val trackId = localIdFor(profile, "USER_TRACK_REF", parentServerId) ?: adoptServerAggregate(binding, RemoteEvent("$serverId-parent", event.sequence, "USER_TRACK_REF_CREATED", 1, "{}", "USER_TRACK_REF", parentServerId, 1)) ?: return null
                database.libraryDao().upsertEntry(app.autplay.data.local.entity.LibraryEntryEntity(localId, serverId, trackId, nowMs(), "SYNC", "AVAILABLE", "CLEAN", event.serverRowVersion, 0, null, nowMs(), profile))
            }
            "PLAYLIST_ENTRY" -> {
                val existing = database.playlistDao().entry(localId)
                if (existing != null && existing.serverPlaylistEntryId != serverId) return idCollision(binding, event)
                val playlistServerId = payloadId(event, "server_playlist_id") ?: return null
                val trackServerId = payloadId(event, "server_user_track_ref_id") ?: return null
                val playlistId = localIdFor(profile, "PLAYLIST", playlistServerId) ?: adoptServerAggregate(binding, RemoteEvent("$serverId-playlist", event.sequence, "PLAYLIST_CREATED", 1, "{}", "PLAYLIST", playlistServerId, 1)) ?: return null
                val trackId = localIdFor(profile, "USER_TRACK_REF", trackServerId) ?: adoptServerAggregate(binding, RemoteEvent("$serverId-track", event.sequence, "USER_TRACK_REF_CREATED", 1, "{}", "USER_TRACK_REF", trackServerId, 1)) ?: return null
                database.playlistDao().upsertEntry(app.autplay.data.local.entity.PlaylistEntryEntity(localId, serverId, playlistId, trackId, "U$serverId", "U$serverId", null, nowMs(), "CLEAN", event.serverRowVersion, 0, null, profile))
            }
            else -> return null
        }
        return localId
    }

    private suspend fun idCollision(binding: ClientEventBinding, event: RemoteEvent): Nothing {
        database.syncDao().upsertConflict(SyncConflictEntity(conflictId(binding.serverProfileId.value, event.eventId), binding.serverProfileId.value, event.aggregateType, event.aggregateServerId ?: "unknown", null, event.eventId, "ID_COLLISION", null, event.payloadJson, "OPEN", null, nowMs(), null))
        throw IllegalStateException("ID_COLLISION")
    }

    private fun payloadId(event: RemoteEvent, key: String): String? = runCatching {
        Json.parseToJsonElement(event.payloadJson).jsonObject[key]?.jsonPrimitive?.content
    }.getOrNull()
    private fun payloadString(payload: String, key: String): String? = runCatching {
        Json.parseToJsonElement(payload).jsonObject[key]?.jsonPrimitive?.content
    }.getOrNull()
    private fun conflictId(profile: String, eventId: String): String = UUID.nameUUIDFromBytes("$profile:$eventId".toByteArray()).toString()
    private fun remoteLocalId(profile: String, serverId: String): String = UUID.nameUUIDFromBytes("$profile:$serverId".toByteArray()).toString()
    private fun payloadLong(payload: String, key: String): Long? = runCatching { Json.parseToJsonElement(payload).jsonObject[key]?.jsonPrimitive?.longOrNull }.getOrNull()
    private fun payloadBoolean(payload: String, key: String): Boolean? = runCatching { Json.parseToJsonElement(payload).jsonObject[key]?.jsonPrimitive?.booleanOrNull }.getOrNull()
    private suspend fun isDirty(type: String, local: String): Boolean = when (type) {
        "USER_TRACK_REF" -> database.libraryDao().trackRef(local)?.syncState == "DIRTY"
        "LIBRARY_ENTRY" -> database.libraryDao().entry(local)?.syncState == "DIRTY"
        "PLAYLIST" -> database.playlistDao().playlist(local)?.syncState == "DIRTY"
        "PLAYLIST_ENTRY" -> database.playlistDao().entry(local)?.syncState == "DIRTY"
        else -> false
    }
    private suspend fun updateCleanVersion(type: String, local: String, version: Long?) {
        when (type) {
            "USER_TRACK_REF" -> database.libraryDao().trackRef(local)?.let { database.libraryDao().upsertTrackRef(it.copy(serverRowVersion = version, syncState = "CLEAN")) }
            "LIBRARY_ENTRY" -> database.libraryDao().entry(local)?.let { database.libraryDao().upsertEntry(it.copy(serverRowVersion = version, syncState = "CLEAN")) }
            "PLAYLIST" -> database.playlistDao().playlist(local)?.let { database.playlistDao().upsertPlaylist(it.copy(serverRowVersion = version, syncState = "CLEAN")) }
            "PLAYLIST_ENTRY" -> database.playlistDao().entry(local)?.let { database.playlistDao().upsertEntry(it.copy(serverRowVersion = version, syncState = "CLEAN")) }
        }
    }
    private suspend fun applyPayloadProjection(type: String, local: String, payload: String) {
        when (type) {
            "USER_TRACK_REF" -> database.libraryDao().trackRef(local)?.let { row -> database.libraryDao().upsertTrackRef(row.copy(rawTitle = payloadString(payload, "title") ?: row.rawTitle, rawArtist = payloadString(payload, "artist") ?: row.rawArtist)) }
            "PLAYLIST" -> database.playlistDao().playlist(local)?.let { row -> database.playlistDao().upsertPlaylist(row.copy(name = payloadString(payload, "name") ?: row.name, description = payloadString(payload, "description") ?: row.description)) }
            "PLAYLIST_ENTRY" -> database.playlistDao().entry(local)?.let { row -> payloadString(payload, "position_key")?.let { database.playlistDao().upsertEntry(row.copy(positionKey = it, activePositionKey = it)) } }
        }
    }
    private suspend fun applyDeleteProjection(type: String, local: String) {
        val now = nowMs()
        when (type) {
            "USER_TRACK_REF" -> database.libraryDao().trackRef(local)?.let { database.libraryDao().upsertTrackRef(it.copy(deletedAtMs = now, syncState = "CLEAN", updatedAtMs = now)) }
            "LIBRARY_ENTRY" -> database.libraryDao().entry(local)?.let { database.libraryDao().upsertEntry(it.copy(removedAtMs = now, availabilityStatus = "REMOVED", syncState = "CLEAN", updatedAtMs = now)) }
            "PLAYLIST" -> database.playlistDao().playlist(local)?.let { database.playlistDao().upsertPlaylist(it.copy(deletedAtMs = now, syncState = "CLEAN", updatedAtMs = now)) }
            "PLAYLIST_ENTRY" -> database.playlistDao().entry(local)?.let { database.playlistDao().upsertEntry(it.copy(removedAtMs = now, activePositionKey = null, syncState = "CLEAN")) }
        }
    }
    private suspend fun applyPreferenceProjection(binding: ClientEventBinding, event: RemoteEvent): Boolean {
        val track = payloadString(event.payloadJson, "local_user_track_ref_id") ?: return false
        if (database.libraryDao().trackRef(track)?.serverProfileId != binding.serverProfileId.value) return false
        val preference = payloadString(event.payloadJson, "preference") ?: return false
        database.libraryDao().upsertPreference(app.autplay.data.local.entity.UserTrackPreferenceEntity(track, preference, null, payloadBoolean(event.payloadJson, "excluded_from_taste") ?: false, "CLEAN", 0, nowMs(), binding.serverProfileId.value))
        return true
    }
    private suspend fun applyListeningProjection(binding: ClientEventBinding, event: RemoteEvent): Boolean {
        val track = payloadString(event.payloadJson, "local_user_track_ref_id") ?: return false
        if (database.libraryDao().trackRef(track)?.serverProfileId != binding.serverProfileId.value) return false
        if (database.historyDao().event(binding.serverProfileId.value, event.eventId) != null) return true
        val played = payloadLong(event.payloadJson, "played_ms") ?: return false
        database.historyDao().insertOnce(app.autplay.data.local.entity.ListeningEventEntity(event.eventId, track, payloadString(event.payloadJson, "recording_id"), nowMs(), played, payloadLong(event.payloadJson, "track_duration_ms"), null, payloadString(event.payloadJson, "event_origin") ?: "ORGANIC", payloadString(event.payloadJson, "context") ?: "GENERAL", null, payloadString(event.payloadJson, "explicit_feedback") ?: "NONE", payloadBoolean(event.payloadJson, "excluded_from_taste") ?: false, "CLEAN", nowMs(), serverProfileId = binding.serverProfileId.value))
        return true
    }

    private suspend fun retry(cursor: SyncCursorEntity, event: OfflineJournalEventEntity, lease: String, code: String, retryAfterMs: Long?) {
        val decision = SyncAttemptPolicy.afterRetry(event.attemptCount, retryAfterMs)
        database.journalDao().finishAttempt(cursor.journalLineageId, event.eventId, lease, decision.state, if (decision.state == "PENDING") nowMs() + decision.delayMs else null, code)
    }

    private suspend fun compact(binding: ClientEventBinding) {
        val cursor = requireCursor(binding)
        val safeThrough = (cursor.lastAckedDeviceSequence - COMPACTION_SAFETY_WINDOW).coerceAtLeast(0)
        if (safeThrough == 0L) return
        database.withWriteTransaction {
            val rows = database.journalDao().compactable(cursor.journalLineageId, 1, COMPACTION_BATCH_LIMIT)
                .filter { it.deviceSequence <= safeThrough }
            if (rows.isNotEmpty()) database.journalDao().deleteTerminal(rows.map { it.eventId })
            database.syncDao().compactTombstones(binding.serverProfileId.value, nowMs())
        }
    }

    private suspend fun requireCursor(binding: ClientEventBinding): SyncCursorEntity =
        checkNotNull(database.syncDao().cursor(binding.serverProfileId.value)) { "SYNC_CURSOR_NOT_BOUND" }

    private companion object {
        const val LEGACY_PROFILE = "legacy-unscoped"
        const val MAX_DRAIN_PAGES = 10
        const val PUSH_BATCH_LIMIT = 100
        const val PULL_BATCH_LIMIT = 500
        const val LEASE_MS = 60_000L
        const val BASE_BACKOFF_MS = 1_000L
        const val MAX_BACKOFF_MS = 3_600_000L
        const val MAX_ATTEMPTS = 8
        const val COMPACTION_SAFETY_WINDOW = 100L
        const val COMPACTION_BATCH_LIMIT = 500
        const val TOMBSTONE_RETAIN_MS = 30L * 24 * 60 * 60 * 1000
        val KNOWN_EVENT_TYPES = setOf("USER_TRACK_REF_CREATED", "LIBRARY_ENTRY_UPSERTED", "USER_TRACK_PREFERENCE_SET", "PLAYLIST_CREATED", "PLAYLIST_METADATA_PATCHED", "PLAYLIST_ENTRY_UPSERTED", "PLAYLIST_ENTRY_MOVED", "AGGREGATE_DELETED", "AGGREGATE_REDIRECT", "LISTENING_EVENT_RECORDED", "RECOMMENDATION_IMPRESSION_RECORDED", "RECOMMENDATION_FEEDBACK_RECORDED")
    }
}

private data class DrainOutcome(val completed: Boolean, val hasMore: Boolean)

/** Pure deterministic retry policy covered by host tests; immutable event ID/hash are never changed. */
object SyncAttemptPolicy {
    const val MAX_ATTEMPTS = 8
    private const val BASE_BACKOFF_MS = 1_000L
    private const val MAX_BACKOFF_MS = 3_600_000L
    data class Decision(val state: String, val delayMs: Long)
    fun afterRetry(previousAttempts: Int, retryAfterMs: Long?): Decision {
        require(previousAttempts >= 0)
        val attempt = previousAttempts + 1
        val delay = retryAfterMs ?: min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * (1L shl min(attempt, 12)))
        return Decision(if (attempt >= MAX_ATTEMPTS) "DEAD_LETTER" else "PENDING", delay)
    }
}

class SyncStatusRepository(private val database: AutPlayDatabase) {
    fun observe(binding: ClientEventBinding): Flow<SyncStatus> = flow {
        val cursor = database.syncDao().cursor(binding.serverProfileId.value)
        if (cursor == null) {
            emit(SyncStatus(0, 0, 0, null, "NOT_BOUND"))
            return@flow
        }
        val runtime = database.syncDao().runtimeStatus(binding.serverProfileId.value)
        combine(
            database.journalDao().observePendingCount(cursor.journalLineageId),
            database.journalDao().observeDeadLetterCount(cursor.journalLineageId),
            database.syncDao().observeOpenConflictCount(binding.serverProfileId.value),
        ) { pending, dead, conflicts -> SyncStatus(pending, dead, conflicts, runtime?.lastSuccessAtMs ?: cursor.lastSyncAtMs, cursor.bootstrapState, runtime?.lastErrorCode) }.collect { emit(it) }
    }
}
