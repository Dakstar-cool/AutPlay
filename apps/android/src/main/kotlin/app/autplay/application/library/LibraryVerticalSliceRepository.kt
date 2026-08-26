package app.autplay.application.library

import androidx.room3.withWriteTransaction
import app.autplay.application.importing.ContentUriInspection
import app.autplay.application.importing.ContentUriStatus
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.P04ClientEventHashInput
import app.autplay.application.sync.P04ClientEventHasher
import app.autplay.application.sync.P07PayloadCodec
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.ListeningEventEntity
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.PlaylistEntryEntity
import app.autplay.data.local.entity.UserTrackPreferenceEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.UserId
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject
import app.autplay.domain.library.PlaylistPositionKeys
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

enum class LocalSliceErrorCode { NOT_FOUND, REMOVED, INVALID_URI_STATE, TOO_MANY_ENTRIES }
class LocalSliceException(val code: LocalSliceErrorCode) : IllegalStateException(code.name)

/** Failure seam used by instrumented tests to prove aggregate and event roll back together. */
fun interface SliceFailureInjector { suspend fun afterAggregateWrite() }

data class SliceMutationResult(val changeId: LocalId, val journaled: Boolean, val deviceSequence: Long?)

/**
 * P07 local-first aggregate writer. Every public command enters one Room write transaction and
 * writes exactly one immutable Journal event when bound, otherwise one local mutation outbox.
 * No command makes a network call.
 */
class LibraryVerticalSliceRepository(
    private val database: AutPlayDatabase,
    private val failureInjector: SliceFailureInjector = SliceFailureInjector {},
    private val idFactory: () -> LocalId = { LocalId.random() },
    private val syncScheduler: DeferredWorkScheduler? = null,
) {
    fun playlists(profileId: String? = null, limit: Int = 100): Flow<List<PlaylistEntity>> { require(limit in 1..1000); return profileId?.let { database.playlistDao().activePlaylistsForProfile(it, limit) } ?: database.playlistDao().activeLegacyPlaylists(limit) }
    fun history(profileId: String? = null, limit: Int = 100): Flow<List<ListeningEventEntity>> { require(limit in 1..1000); return profileId?.let { database.historyDao().recentForProfile(it, limit) } ?: database.historyDao().recentLegacy(limit) }
    /** Explicit P09 seam: turns one standalone immutable intent into one new Journal event atomically. */
    suspend fun materializeStandalone(binding: ClientEventBinding, outboxChangeId: LocalId, eventId: LocalId, now: Long): SliceMutationResult {
        require(eventId != outboxChangeId)
        val result = database.withWriteTransaction {
            val source = database.journalDao().outbox(outboxChangeId.value) ?: missing()
            check(source.materializationState == "UNMATERIALIZED" && source.materializedEventId == null) { "OUTBOX_NOT_MATERIALIZABLE" }
            check(P07PayloadCodec.canonicalize(source.payloadJson) == source.payloadJson) { "OUTBOX_NON_CANONICAL" }
            val lineage = resolveLineage(binding, now)
            val sequence = database.journalDao().allocateSequence(lineage.lineageId)
            database.journalDao().insert(journal(lineage, binding, eventId, source.eventType, source.aggregateType, LocalId(source.aggregateLocalId), sequence, source.payloadJson, source.occurredAtMs, observedVersion(source.aggregateType, source.aggregateLocalId)))
            markMaterializedDomain(source.eventType, source.aggregateType, source.aggregateLocalId, source.payloadJson, sequence, binding.serverProfileId.value)
            check(database.journalDao().linkOutboxMaterialized(source.localChangeId, eventId.value, now) == 1) { "OUTBOX_LINK_RACE" }
            SliceMutationResult(eventId, true, sequence)
        }
        scheduleSync(binding, result)
        return result
    }

    suspend fun removeLibrary(binding: ClientEventBinding?, entryId: LocalId, changeId: LocalId, now: Long): SliceMutationResult =
        mutate(binding, changeId, "LIBRARY_ENTRY_UPSERTED", "LIBRARY_ENTRY", entryId, now, "{\"library_entry_local_id\":${q(entryId.value)},\"removed_at_ms\":$now}") { sequence, bound ->
            val existing = database.libraryDao().entry(entryId.value) ?: missing()
            ensureOwned(binding, existing.serverProfileId)
            database.libraryDao().upsertEntry(existing.copy(removedAtMs = now, updatedAtMs = now, syncState = sync(bound), lastLocalSequence = sequence))
        }

    suspend fun restoreLibrary(binding: ClientEventBinding?, entryId: LocalId, changeId: LocalId, now: Long): SliceMutationResult =
        mutate(binding, changeId, "LIBRARY_ENTRY_UPSERTED", "LIBRARY_ENTRY", entryId, now, "{\"library_entry_local_id\":${q(entryId.value)},\"removed_at_ms\":null}") { sequence, bound ->
            val existing = database.libraryDao().entry(entryId.value) ?: missing()
            ensureOwned(binding, existing.serverProfileId)
            database.libraryDao().upsertEntry(existing.copy(removedAtMs = null, updatedAtMs = now, syncState = sync(bound), lastLocalSequence = sequence))
        }

    suspend fun setPreference(binding: ClientEventBinding?, trackRefId: LocalId, changeId: LocalId, preference: String, excluded: Boolean, attributionJson: String?, now: Long): SliceMutationResult {
        require(preference in setOf("NEUTRAL", "LIKED", "DISLIKED"))
        val attribution = attributionJson?.let(::validateAttribution)
        val payload = "{\"attribution\":${attribution ?: "null"},\"excluded_from_taste\":$excluded,\"local_user_track_ref_id\":${q(trackRefId.value)},\"preference\":${q(preference)}}"
        return mutate(binding, changeId, "USER_TRACK_PREFERENCE_SET", "USER_TRACK_PREFERENCE", trackRefId, now, payload) { sequence, bound ->
            val track = database.libraryDao().trackRef(trackRefId.value) ?: missing()
            ensureOwned(binding, track.serverProfileId)
            database.libraryDao().upsertPreference(
                UserTrackPreferenceEntity(
                    trackRefId.value,
                    preference,
                    null,
                    excluded,
                    sync(bound),
                    sequence,
                    now,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
        }
    }

    suspend fun createPlaylist(binding: ClientEventBinding?, playlistId: LocalId, changeId: LocalId, name: String, description: String?, now: Long): SliceMutationResult {
        val metadata = validatedPlaylistMetadata(name, description)
        return mutate(binding, changeId, "PLAYLIST_CREATED", "PLAYLIST", playlistId, now, "{\"description\":${metadata.description?.let(::q) ?: "null"},\"name\":${q(metadata.name)}}") { sequence, bound ->
            database.playlistDao().insertPlaylist(
                PlaylistEntity(
                    playlistId.value, null, metadata.name, metadata.description, "PRIVATE", "MANUAL", null, null,
                    sync(bound), null, sequence, now, now, null,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
        }
    }

    suspend fun updatePlaylistMetadata(binding: ClientEventBinding?, playlistId: LocalId, changeId: LocalId, name: String, description: String?, now: Long): SliceMutationResult {
        val metadata = validatedPlaylistMetadata(name, description)
        return mutate(binding, changeId, "PLAYLIST_METADATA_PATCHED", "PLAYLIST", playlistId, now, "{\"description\":${metadata.description?.let(::q) ?: "null"},\"name\":${q(metadata.name)}}") { sequence, bound ->
            val row = database.playlistDao().playlist(playlistId.value) ?: missing()
            ensureOwned(binding, row.serverProfileId)
            database.playlistDao().upsertPlaylist(row.copy(name = metadata.name, description = metadata.description, updatedAtMs = now, syncState = sync(bound), lastLocalSequence = sequence))
        }
    }

    suspend fun deletePlaylist(binding: ClientEventBinding?, playlistId: LocalId, changeId: LocalId, now: Long): SliceMutationResult =
        mutate(binding, changeId, "AGGREGATE_DELETED", "PLAYLIST", playlistId, now, "{}") { sequence, bound ->
            val row = database.playlistDao().playlist(playlistId.value) ?: missing()
            ensureOwned(binding, row.serverProfileId)
            database.playlistDao().upsertPlaylist(row.copy(deletedAtMs = now, updatedAtMs = now, syncState = sync(bound), lastLocalSequence = sequence))
            val entries = database.playlistDao().activeEntryList(playlistId.value, MAX_PLAYLIST_ENTRIES)
            database.playlistDao().upsertEntries(entries.map { it.copy(removedAtMs = now, activePositionKey = null, syncState = sync(bound), lastLocalSequence = sequence) })
        }

    suspend fun addPlaylistEntry(binding: ClientEventBinding?, playlistId: LocalId, entryId: LocalId, trackRefId: LocalId, changeId: LocalId, beforeEntryId: LocalId?, attributionJson: String?, now: Long): SliceMutationResult {
        val attribution = attributionJson?.let(::validateAttribution)
        val payload = "{\"attribution\":${attribution ?: "null"},\"before_local_playlist_entry_id\":${beforeEntryId?.let { q(it.value) } ?: "null"},\"local_playlist_entry_id\":${q(entryId.value)},\"local_playlist_id\":${q(playlistId.value)},\"local_user_track_ref_id\":${q(trackRefId.value)}}"
        return mutate(binding, changeId, "PLAYLIST_ENTRY_UPSERTED", "PLAYLIST_ENTRY", entryId, now, payload) { sequence, bound ->
            val playlist = database.playlistDao().playlist(playlistId.value) ?: missing()
            ensureOwned(binding, playlist.serverProfileId)
            if (playlist.deletedAtMs != null) removed()
            val track = database.libraryDao().trackRef(trackRefId.value) ?: missing()
            ensureOwned(binding, track.serverProfileId)
            val active = database.playlistDao().activeEntryList(playlistId.value, MAX_PLAYLIST_ENTRIES)
            if (active.size >= MAX_PLAYLIST_ENTRIES) tooMany()
            val index = beforeEntryId?.let { id -> active.indexOfFirst { it.localPlaylistEntryId == id.value }.takeIf { it >= 0 } } ?: active.size
            var ordered = active
            var key = PlaylistPositionKeys.between(ordered.getOrNull(index - 1)?.activePositionKey, ordered.getOrNull(index)?.activePositionKey)
            if (key == null) {
                val keys = PlaylistPositionKeys.rebalance(ordered.map { it.localPlaylistEntryId })
                ordered = ordered.map { it.copy(positionKey = keys.getValue(it.localPlaylistEntryId), activePositionKey = keys.getValue(it.localPlaylistEntryId)) }
                database.playlistDao().upsertEntries(ordered)
                key = PlaylistPositionKeys.between(ordered.getOrNull(index - 1)?.activePositionKey, ordered.getOrNull(index)?.activePositionKey)
            }
            checkNotNull(key)
            database.playlistDao().insertEntry(
                PlaylistEntryEntity(
                    entryId.value, null, playlistId.value, trackRefId.value, key, key, null, now,
                    sync(bound), null, sequence, null,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
        }
    }

    suspend fun removePlaylistEntry(binding: ClientEventBinding?, entryId: LocalId, changeId: LocalId, now: Long): SliceMutationResult =
        mutate(binding, changeId, "AGGREGATE_DELETED", "PLAYLIST_ENTRY", entryId, now, "{\"local_playlist_entry_id\":${q(entryId.value)}}") { sequence, bound ->
            val row = database.playlistDao().entry(entryId.value) ?: missing()
            ensureOwned(binding, row.serverProfileId)
            database.playlistDao().upsertEntry(row.copy(removedAtMs = now, activePositionKey = null, syncState = sync(bound), lastLocalSequence = sequence))
        }

    suspend fun reorderPlaylistEntry(binding: ClientEventBinding?, entryId: LocalId, beforeEntryId: LocalId?, changeId: LocalId, now: Long): SliceMutationResult =
        mutate(binding, changeId, "PLAYLIST_ENTRY_MOVED", "PLAYLIST_ENTRY", entryId, now, "{\"before_local_playlist_entry_id\":${beforeEntryId?.let { q(it.value) } ?: "null"}}") { sequence, bound ->
            val row = database.playlistDao().entry(entryId.value) ?: missing()
            ensureOwned(binding, row.serverProfileId)
            if (row.removedAtMs != null) removed()
            val active = database.playlistDao().activeEntryList(row.localPlaylistId, MAX_PLAYLIST_ENTRIES).filterNot { it.localPlaylistEntryId == entryId.value }
            val index = beforeEntryId?.let { id -> active.indexOfFirst { it.localPlaylistEntryId == id.value }.takeIf { it >= 0 } } ?: active.size
            var ordered = active
            var key = PlaylistPositionKeys.between(ordered.getOrNull(index - 1)?.activePositionKey, ordered.getOrNull(index)?.activePositionKey)
            if (key == null) {
                val keys = PlaylistPositionKeys.rebalance(ordered.map { it.localPlaylistEntryId })
                ordered = ordered.map { it.copy(positionKey = keys.getValue(it.localPlaylistEntryId), activePositionKey = keys.getValue(it.localPlaylistEntryId)) }
                database.playlistDao().upsertEntries(ordered)
                key = PlaylistPositionKeys.between(ordered.getOrNull(index - 1)?.activePositionKey, ordered.getOrNull(index)?.activePositionKey)
            }
            checkNotNull(key)
            database.playlistDao().upsertEntry(row.copy(positionKey = key, activePositionKey = key, syncState = sync(bound), lastLocalSequence = sequence))
        }

    suspend fun recordListening(
        binding: ClientEventBinding?,
        listeningEventId: LocalId,
        trackRefId: LocalId,
        playedMs: Long,
        durationMs: Long?,
        excluded: Boolean,
        origin: String,
        attributionJson: String? = null,
        context: String = "GENERAL",
        now: Long,
        startedAtMs: Long = now,
        sessionStartPositionMs: Long? = null,
        sessionEndPositionMs: Long? = null,
    ): SliceMutationResult {
        require(playedMs in 0..604_800_000 && (durationMs == null || durationMs in 1..604_800_000))
        require(startedAtMs in 0..now)
        require(sessionStartPositionMs == null || sessionStartPositionMs >= 0)
        require(sessionEndPositionMs == null || sessionEndPositionMs >= 0)
        require(Regex("^[A-Z][A-Z0-9_]{0,99}$").matches(origin))
        require(Regex("^[A-Z][A-Z0-9_]{0,49}$").matches(context))
        database.journalDao().event(listeningEventId.value)?.let {
            return SliceMutationResult(listeningEventId, true, it.deviceSequence)
        }
        database.journalDao().outbox(listeningEventId.value)?.let {
            return SliceMutationResult(listeningEventId, false, null)
        }
        val attribution = attributionJson?.let(::validateAttribution)
        require(origin != "RECOMMENDED" || attribution != null)
        val track = database.libraryDao().trackRef(trackRefId.value) ?: missing()
        ensureOwned(binding, track.serverProfileId)
        val ratio = durationMs?.let { (playedMs.toDouble() / it).coerceIn(0.0, 1.0) }
        val payload = "{\"completion_ratio\":${ratio ?: "null"},\"context\":${q(context)},\"event_origin\":${q(origin)},\"excluded_from_taste\":$excluded,\"explicit_feedback\":\"NONE\",\"interaction_type\":\"LISTENING_EVENT_RECORDED\",\"local_user_track_ref_id\":${q(trackRefId.value)},\"played_ms\":$playedMs,\"recommendation\":${attribution ?: "null"},\"recording_id\":${track.serverRecordingId?.let(::q) ?: "null"},\"server_user_track_ref_id\":${track.serverUserTrackRefId?.let(::q) ?: "null"},\"track_duration_ms\":${durationMs ?: "null"}}"
        return mutate(binding, listeningEventId, "LISTENING_EVENT_RECORDED", "LISTENING_EVENT", listeningEventId, now, payload) { sequence, bound ->
            val requestId = attribution?.let { Json.parseToJsonElement(it).jsonObject.getValue("recommendation_request_id").jsonPrimitive.content }
            database.historyDao().insert(
                ListeningEventEntity(
                    listeningEventId.value,
                    trackRefId.value,
                    track.serverRecordingId,
                    startedAtMs,
                    playedMs,
                    durationMs,
                    ratio,
                    origin,
                    context,
                    requestId,
                    "NONE",
                    excluded,
                    sync(bound),
                    now,
                    attribution,
                    sessionStartPositionMs,
                    sessionEndPositionMs,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
        }
    }

    suspend fun importUri(binding: ClientEventBinding?, trackRefId: LocalId, libraryEntryId: LocalId, audioStateId: LocalId, changeId: LocalId, title: String, artist: String, inspection: ContentUriInspection, persistedPermission: Boolean, now: Long): SliceMutationResult {
        if (inspection.status == ContentUriStatus.INVALID) throw LocalSliceException(LocalSliceErrorCode.INVALID_URI_STATE)
        val payload = "{\"artist\":${q(artist)},\"library_entry_local_id\":${q(libraryEntryId.value)},\"title\":${q(title)}}"
        return mutate(binding, changeId, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", trackRefId, now, payload) { sequence, bound ->
            val status = when (inspection.status) { ContentUriStatus.AVAILABLE -> "AVAILABLE"; ContentUriStatus.MISSING -> "MISSING"; ContentUriStatus.PERMISSION_REVOKED -> "PERMISSION_REVOKED"; ContentUriStatus.INVALID -> error("checked") }
            database.libraryDao().upsertTrackRef(
                UserTrackRefEntity(
                    trackRefId.value, null, null, null, "UNRESOLVED", title, artist, null, null,
                    null, sync(bound), null, sequence, now, now, null,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
            val availability = when (inspection.status) { ContentUriStatus.AVAILABLE -> "LOCAL"; ContentUriStatus.MISSING -> "NOT_FOUND"; ContentUriStatus.PERMISSION_REVOKED -> "PENDING"; ContentUriStatus.INVALID -> error("checked") }
            database.libraryDao().upsertEntry(
                LibraryEntryEntity(
                    libraryEntryId.value, null, trackRefId.value, now, "IMPORT", availability,
                    sync(bound), null, sequence, null, now,
                    binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID,
                ),
            )
            database.localAudioDao().upsertState(LocalAudioStateEntity(audioStateId.value, trackRefId.value, null, null, inspection.uri, persistedPermission, null, null, null, null, null, null, null, null, null, null, status, "USER_IMPORT", inspection.byteSize, null, now, now, now))
            database.searchDao().insertContent(TrackSearchContentEntity(localUserTrackRefId = trackRefId.value, title = title, artist = artist, album = null, aliases = null, transliterations = null))
        }
    }

    private suspend fun mutate(binding: ClientEventBinding?, changeId: LocalId, eventType: String, aggregateType: String, aggregateId: LocalId, now: Long, rawPayload: String, aggregate: suspend (Long, Boolean) -> Unit): SliceMutationResult {
        require(now >= 0)
        val payload = P07PayloadCodec.canonicalize(rawPayload)
        val result = database.withWriteTransaction {
            val lineage = binding?.let { resolveLineage(it, now) }
            val sequence = lineage?.let { database.journalDao().allocateSequence(it.lineageId) } ?: 0L
            // Capture the observed remote version before the local row becomes DIRTY. Existing
            // journal records are immutable; a dependent pre-ACK edit remains null and is later
            // surfaced as POLICY_REVIEW rather than being rewritten or silently discarded.
            val baseVersion = lineage?.let { observedVersion(aggregateType, aggregateId.value) }
            aggregate(sequence, lineage != null)
            failureInjector.afterAggregateWrite()
            if (lineage == null) {
                database.journalDao().insertOutbox(LocalMutationOutboxEntity(changeId.value, eventType, 1, aggregateType, aggregateId.value, payload, now, "UNMATERIALIZED"))
                SliceMutationResult(changeId, false, null)
            } else {
                val resolvedBinding = checkNotNull(binding)
                markMaterializedDomain(eventType, aggregateType, aggregateId.value, payload, sequence, resolvedBinding.serverProfileId.value)
                database.journalDao().insert(journal(lineage, resolvedBinding, changeId, eventType, aggregateType, aggregateId, sequence, payload, now, baseVersion))
                SliceMutationResult(changeId, true, sequence)
            }
        }
        scheduleSync(binding, result)
        return result
    }

    private fun validateAttribution(raw: String): String {
        val canonical = P07PayloadCodec.canonicalize(raw)
        val value = Json.parseToJsonElement(canonical) as? JsonObject
            ?: throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID")
        fun requiredString(name: String): String {
            val primitive = value[name] as? JsonPrimitive
                ?: throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID")
            if (!primitive.isString) throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID")
            return primitive.content
        }
        runCatching { UUID.fromString(requiredString("recommendation_request_id")) }
            .getOrElse { throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID") }
        runCatching { UUID.fromString(requiredString("recording_id")) }
            .getOrElse { throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID") }
        val rank = (value["source_rank"] as? JsonPrimitive)?.intOrNull
            ?: throw IllegalArgumentException("RECOMMENDATION_ATTRIBUTION_INVALID")
        require(rank in 1..1_000) { "RECOMMENDATION_ATTRIBUTION_INVALID" }
        val safeToken = Regex("^[a-z][a-z0-9_]{0,99}$")
        require(safeToken.matches(requiredString("source"))) { "RECOMMENDATION_ATTRIBUTION_INVALID" }
        require(safeToken.matches(requiredString("surface"))) { "RECOMMENDATION_ATTRIBUTION_INVALID" }
        return canonical
    }

    private suspend fun resolveLineage(binding: ClientEventBinding, now: Long): JournalLineageEntity {
        val dao = database.journalDao()
        val existing = dao.lineageByDeviceId(binding.deviceId.value)
        if (existing != null) {
            check(existing.userId == binding.userId.value) { "JOURNAL_LINEAGE_USER_MISMATCH" }
            ensureCursor(binding, existing, now)
            return existing
        }
        val epoch = binding.journalEpoch ?: idFactory()
        check(dao.lineageByJournalEpoch(epoch.value) == null) { "JOURNAL_EPOCH_DEVICE_MISMATCH" }
        return JournalLineageEntity(idFactory().value, binding.userId.value, binding.deviceId.value, epoch.value, 1, now).also { dao.insertLineage(it); ensureCursor(binding, it, now) }
    }

    /** Fail-closed event allowlist and in-transaction dirty/sequence materialization. */
    private suspend fun markMaterializedDomain(eventType: String, aggregateType: String, aggregateId: String, payload: String, sequence: Long, profileId: String) {
        val parsed = Json.parseToJsonElement(payload).jsonObject
        when (eventType to aggregateType) {
            "USER_TRACK_REF_CREATED" to "USER_TRACK_REF" -> {
                val track = database.libraryDao().trackRef(aggregateId) ?: missing()
                database.libraryDao().upsertTrackRef(track.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
                parsed["library_entry_local_id"]?.jsonPrimitive?.content?.let { entryId ->
                    val entry = database.libraryDao().entry(entryId) ?: missing()
                    database.libraryDao().upsertEntry(entry.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
                }
            }
            "LIBRARY_ENTRY_UPSERTED" to "LIBRARY_ENTRY", "AGGREGATE_DELETED" to "LIBRARY_ENTRY" -> {
                val entry = database.libraryDao().entry(aggregateId) ?: missing()
                database.libraryDao().upsertEntry(entry.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
            }
            "USER_TRACK_PREFERENCE_SET" to "USER_TRACK_PREFERENCE" -> {
                val preference = database.libraryDao().preference(aggregateId) ?: missing()
                database.libraryDao().upsertPreference(preference.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
            }
            "PLAYLIST_CREATED" to "PLAYLIST", "PLAYLIST_METADATA_PATCHED" to "PLAYLIST", "AGGREGATE_DELETED" to "PLAYLIST" -> {
                val playlist = database.playlistDao().playlist(aggregateId) ?: missing()
                database.playlistDao().upsertPlaylist(playlist.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
            }
            "PLAYLIST_ENTRY_UPSERTED" to "PLAYLIST_ENTRY", "PLAYLIST_ENTRY_MOVED" to "PLAYLIST_ENTRY", "AGGREGATE_DELETED" to "PLAYLIST_ENTRY" -> {
                val entry = database.playlistDao().entry(aggregateId) ?: missing()
                database.playlistDao().upsertEntry(entry.copy(syncState = "DIRTY", lastLocalSequence = sequence, serverProfileId = profileId))
            }
            "LISTENING_EVENT_RECORDED" to "LISTENING_EVENT" -> {
                val event = database.historyDao().event(aggregateId) ?: missing()
                database.historyDao().upsert(event.copy(syncState = "DIRTY", serverProfileId = profileId))
            }
            else -> error("OUTBOX_UNSUPPORTED_EVENT")
        }
    }

    private suspend fun ensureCursor(binding: ClientEventBinding, lineage: JournalLineageEntity, now: Long) {
        val existing = database.syncDao().cursor(binding.serverProfileId.value)
        check(existing == null || (existing.journalLineageId == lineage.lineageId && existing.deviceId == lineage.deviceId && existing.journalEpoch == lineage.journalEpoch)) { "SERVER_PROFILE_LINEAGE_MISMATCH" }
        if (existing == null) database.syncDao().upsertCursor(SyncCursorEntity(binding.serverProfileId.value, lineage.lineageId, lineage.deviceId, lineage.journalEpoch, null, 0, 0, null, "NOT_STARTED", null, now))
    }

    private fun journal(lineage: JournalLineageEntity, binding: ClientEventBinding, id: LocalId, eventType: String, aggregateType: String, aggregateId: LocalId, sequence: Long, payload: String, now: Long, baseVersion: Long?): OfflineJournalEventEntity {
        val hash = P04ClientEventHasher.sha256(P04ClientEventHashInput(id, id.value, binding.copy(userId = UserId(lineage.userId), deviceId = DeviceId(lineage.deviceId)), sequence, eventType, aggregateType, aggregateId, null, baseVersion, Instant.ofEpochMilli(now).toString(), payload))
        return OfflineJournalEventEntity(id.value, lineage.lineageId, id.value, lineage.userId, lineage.deviceId, binding.serverProfileId.value, sequence, eventType, 1, aggregateType, aggregateId.value, null, baseVersion, payload, hash, now, "PENDING", 0, null, null, null, null, null)
    }

    private suspend fun observedVersion(aggregateType: String, aggregateId: String): Long? = when (aggregateType) {
        "USER_TRACK_REF" -> database.libraryDao().trackRef(aggregateId)?.serverRowVersion
        "LIBRARY_ENTRY" -> database.libraryDao().entry(aggregateId)?.serverRowVersion
        // Preferences have no independent server row-version in the P05 physical schema.
        "USER_TRACK_PREFERENCE" -> null
        "PLAYLIST" -> database.playlistDao().playlist(aggregateId)?.serverRowVersion
        "PLAYLIST_ENTRY" -> database.playlistDao().entry(aggregateId)?.serverRowVersion
        else -> null
    }

    private fun sync(bound: Boolean) = if (bound) "DIRTY" else "LOCAL_ONLY"
    private fun ensureOwned(binding: ClientEventBinding?, rowProfileId: String) {
        if (rowProfileId != (binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID)) missing()
    }
    /** Runs only after Room commits; WorkManager enqueue failure cannot roll back local intent. */
    private fun scheduleSync(binding: ClientEventBinding?, result: SliceMutationResult) {
        if (!result.journaled || binding == null) return
        syncScheduler?.enqueue(DeferredWorkRequest(DeferredWorkKind.SYNC, DeferredWorkSubject.Device(binding.deviceId), binding.serverProfileId))
    }
    private fun q(value: String): String = kotlinx.serialization.json.JsonPrimitive(value).toString()
    /** Manual playlist metadata is normalized at the application boundary before journaling. */
    private fun validatedPlaylistMetadata(name: String, description: String?): PlaylistMetadata {
        val normalizedName = name.trim()
        require(normalizedName.length in 1..120) { "PLAYLIST_NAME_INVALID" }
        val normalizedDescription = description?.trim()?.takeIf(String::isNotEmpty)
        require(normalizedDescription == null || normalizedDescription.length <= 500) {
            "PLAYLIST_DESCRIPTION_INVALID"
        }
        return PlaylistMetadata(normalizedName, normalizedDescription)
    }
    private data class PlaylistMetadata(val name: String, val description: String?)
    private fun missing(): Nothing = throw LocalSliceException(LocalSliceErrorCode.NOT_FOUND)
    private fun removed(): Nothing = throw LocalSliceException(LocalSliceErrorCode.REMOVED)
    private fun tooMany(): Nothing = throw LocalSliceException(LocalSliceErrorCode.TOO_MANY_ENTRIES)
    private companion object {
        const val MAX_PLAYLIST_ENTRIES = 10_000
        const val LEGACY_PROFILE_ID = "legacy-unscoped"
    }
}
