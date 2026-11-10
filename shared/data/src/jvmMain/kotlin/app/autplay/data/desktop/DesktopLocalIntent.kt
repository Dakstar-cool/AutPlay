package app.autplay.data.desktop

import java.util.UUID
import app.autplay.application.desktop.DesktopImportJobLimits
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Typed v1 local intent writers. No locator, platform path or authenticated binding is accepted. */
internal object DesktopLocalIntent {
    private const val MAX_BYTES: Int = 262_144
    private const val MAX_DEPTH: Int = 8
    private val safeName: Regex = Regex("^[a-z][a-z0-9_]{0,99}$")
    private val forbidden: Regex = Regex(
        "(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|" +
            "base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|" +
            "raw_model_features|model_features|feature_vector|debug_text|personal_debug|" +
            "raw_audio|audio_bytes|locator|managed_file_name)(_|$)",
    )

    fun trackImported(row: TrackRow): LocalFactRow = fact("TRACK_IMPORTED", row.id, track(row))
    /** The projection supplies bounded, typed event bodies; shared v1 privacy/size guards still apply. */
    fun importJobFact(
        jobId: String, revision: Long, sequence: Long, previousFactId: String?, event: String, body: JsonObject,
    ): LocalFactRow {
        DesktopImportJobProjection.requireUuid(jobId)
        check(revision >= 0 && sequence in 1..DesktopImportJobLimits.FACTS.toLong() &&
            event in setOf("CREATED", "CANDIDATE", "ATTEMPT", "MAPPING", "SETTLED", "STOPPED")) {
            "desktop_import_job_invalid"
        }
        return fact("IMPORT_JOB_FACT", DesktopImportJobProjection.aggregate(jobId), obj(
            "job_id" to text(jobId), "revision" to integer(revision), "sequence" to integer(sequence),
            "previous_fact_id" to textOrNull(previousFactId), "event" to text(event), "body" to body,
        ))
    }
    fun trackMetadataUpdated(row: TrackRow): LocalFactRow = fact("TRACK_METADATA_UPDATED", row.id, track(row))
    /** Counters are allocated inside the same transaction as the linked metadata fact and row. */
    fun trackTextProvenanceUpdated(
        before: TrackRow, after: TrackRow, metadata: LocalFactRow,
        revision: Long, sequence: Long, operation: String, format: String?,
    ): LocalFactRow {
        check(before.id == after.id && before.sha256 == after.sha256 && before.byteSize == after.byteSize &&
            metadata.kind == "TRACK_METADATA_UPDATED" && metadata.aggregateId == after.id &&
            revision > 0 && sequence > 0) { "desktop_text_provenance_invalid" }
        check(operation in setOf("USER", "EMBEDDED", "COMPATIBILITY", "DURATION") &&
            ((operation == "EMBEDDED") == (format in setOf("ID3V23", "ID3V24", "FLAC", "MP4"))) &&
            (operation == "EMBEDDED" || format == null)) { "desktop_text_provenance_invalid" }
        val result = fact("TRACK_TEXT_PROVENANCE_UPDATED", after.id, obj(
            "track_id" to text(after.id), "revision" to integer(revision), "sequence" to integer(sequence),
            "metadata_change_id" to text(metadata.changeId), "operation" to text(operation),
            "format" to textOrNull(format), "before_title" to text(before.title), "before_artist" to text(before.artist),
            "after_title" to text(after.title), "after_artist" to text(after.artist),
            "sha256" to text(after.sha256), "byte_size" to integer(after.byteSize),
        ))
        if (result.payloadJson.toByteArray(Charsets.UTF_8).size > DesktopTextProvenanceProjection.MAX_PROVENANCE_BYTES) {
            throw DesktopStorageException("desktop_outbox_payload_too_large")
        }
        return result
    }
    fun trackPreferenceUpdated(before: TrackPreferenceRow, after: TrackPreferenceRow): LocalFactRow {
        check(before.trackId == after.trackId) { "desktop_preference_invalid" }
        check(after.preference == "NEUTRAL" || after.preference == "LIKED" || after.preference == "DISLIKED") {
            "desktop_preference_invalid"
        }
        return fact("TRACK_PREFERENCE_UPDATED", after.trackId, obj(
            "track_id" to text(after.trackId), "previous_preference" to text(before.preference),
            "preference" to text(after.preference),
        ))
    }
    fun managedCopyRepaired(row: TrackRow): LocalFactRow = fact("MANAGED_COPY_REPAIRED", row.id, obj(
        "track_id" to text(row.id), "sha256" to text(row.sha256), "byte_size" to integer(row.byteSize),
    ))
    fun trackCollectionUpdated(before: TrackCollectionRow, after: TrackCollectionRow): LocalFactRow {
        check(before.trackId == after.trackId) { "desktop_collection_state_invalid" }
        val kind = when (after.collectionState) {
            "REMOVED" -> "TRACK_REMOVED_FROM_COLLECTION"
            "ACTIVE" -> "TRACK_RESTORED_TO_COLLECTION"
            else -> error("desktop_collection_state_invalid")
        }
        return fact(kind, after.trackId, obj(
            "track_id" to text(after.trackId), "previous_collection_state" to text(before.collectionState),
            "collection_state" to text(after.collectionState),
        ))
    }
    fun playlistCreated(row: PlaylistRow): LocalFactRow = fact("PLAYLIST_CREATED", row.id, obj(
        "playlist_id" to text(row.id), "name" to text(row.name), "description" to textOrNull(row.description),
    ))
    fun playlistRenamed(previous: PlaylistRow, renamed: PlaylistRow): LocalFactRow = fact("PLAYLIST_RENAMED", renamed.id, obj(
        "playlist_id" to text(renamed.id), "previous_name" to text(previous.name), "name" to text(renamed.name),
    ))
    fun playlistDescriptionUpdated(before: PlaylistRow, after: PlaylistRow): LocalFactRow {
        check(before.copy(description = after.description) == after) { "desktop_playlist_description_conflict" }
        requirePlaylistDescription(after.description)
        return fact("PLAYLIST_DESCRIPTION_UPDATED", after.id, obj(
            "playlist_id" to text(after.id), "previous_description" to textOrNull(before.description),
            "description" to textOrNull(after.description),
        ))
    }
    fun playlistDeleted(row: PlaylistRow, entryCount: Long): LocalFactRow {
        check(entryCount >= 0) { "desktop_playlist_delete_conflict" }
        return fact("PLAYLIST_DELETED", row.id, obj(
            "playlist_id" to text(row.id), "name" to text(row.name), "description" to textOrNull(row.description),
            "entry_count" to integer(entryCount),
        ))
    }
    fun playlistEntryAppended(row: PlaylistEntryRow): LocalFactRow = fact("PLAYLIST_ENTRY_APPENDED", row.id, playlistEntry(row))
    fun playlistEntryRemoved(row: PlaylistEntryRow): LocalFactRow = fact("PLAYLIST_ENTRY_REMOVED", row.id, playlistEntry(row))

    fun playlistEntryMoved(moved: PlaylistEntryRow, adjacent: PlaylistEntryRow, direction: Int): LocalFactRow =
        fact("PLAYLIST_ENTRY_MOVED", moved.id, obj(
            "playlist_id" to text(moved.playlistId), "entry_id" to text(moved.id), "direction" to JsonPrimitive(direction),
            "entries" to JsonArray(listOf(
                moved.copy(ordinal = adjacent.ordinal) to moved.ordinal,
                adjacent.copy(ordinal = moved.ordinal) to adjacent.ordinal,
            ).sortedBy { it.first.ordinal }.map { (after, previousOrdinal) -> obj(
                "entry_id" to text(after.id), "track_id" to text(after.trackId),
                "previous_ordinal" to integer(previousOrdinal), "ordinal" to integer(after.ordinal),
            ) }),
        ))

    fun queueSaved(row: QueueRow, entries: List<QueueEntryRow>): LocalFactRow = fact("QUEUE_CHECKPOINT_SAVED", row.id, obj(
        "queue_id" to text(row.id),
        "current_entry_id" to textOrNull(row.currentEntryId),
        "queue_type" to text(row.queueType),
        "position_ms" to integer(row.positionMs),
        "shuffle_enabled" to JsonPrimitive(row.shuffleEnabled),
        "repeat_mode" to text(row.repeatMode),
        "session_id" to textOrNull(row.sessionId),
        "session_started_at_ms" to integerOrNull(row.sessionStartedAtMs),
        "played_ms" to integer(row.playedMs),
        "session_taste_state" to text(row.sessionTasteState),
        "listen_taste_state" to text(row.listenTasteState),
        "entries" to JsonArray(entries.sortedBy { it.ordinal }.map { entry -> obj(
            "entry_id" to text(entry.id), "track_id" to text(entry.trackId),
            "ordinal" to integer(entry.ordinal.toLong()), "shuffle_ordinal" to integer(entry.shuffleOrdinal.toLong()),
        ) }),
    ))

    fun listeningSaved(row: ListeningRow): LocalFactRow = fact("LISTENING_CHECKPOINT_SAVED", row.id, obj(
        "event_id" to text(row.id), "queue_entry_id" to text(row.queueEntryId), "track_id" to text(row.trackId),
        "started_at_ms" to integer(row.startedAtMs), "played_ms" to integer(row.playedMs),
        "position_ms" to integer(row.positionMs), "finalized" to JsonPrimitive(row.finalized),
        "session_taste_state" to text(row.sessionTasteState), "listen_taste_state" to text(row.listenTasteState),
    ))

    fun currentListenTasteUpdated(before: QueueRow, after: QueueRow): LocalFactRow {
        check(before.sessionId != null && before.currentEntryId != null &&
            before.copy(listenTasteState = after.listenTasteState) == after &&
            (after.listenTasteState == "INCLUDED" || after.listenTasteState == "EXCLUDED")) {
            "desktop_taste_context_invalid"
        }
        return tasteUpdated("CURRENT_LISTEN_TASTE_UPDATED", "CURRENT_LISTEN", before, after)
    }

    fun queueSessionTasteUpdated(before: QueueRow, after: QueueRow): LocalFactRow {
        check(before.copy(sessionTasteState = after.sessionTasteState) == after &&
            (after.sessionTasteState == "INCLUDED" || after.sessionTasteState == "EXCLUDED")) {
            "desktop_taste_context_invalid"
        }
        return tasteUpdated("QUEUE_SESSION_TASTE_UPDATED", "QUEUE_SESSION", before, after)
    }

    private fun tasteUpdated(kind: String, scope: String, before: QueueRow, after: QueueRow): LocalFactRow =
        fact(kind, after.id, obj(
            "scope" to text(scope), "queue_id" to text(after.id),
            "current_entry_id" to textOrNull(after.currentEntryId),
            "listening_event_id" to textOrNull(after.sessionId),
            "previous_session_taste_state" to text(before.sessionTasteState),
            "previous_listen_taste_state" to text(before.listenTasteState),
            "session_taste_state" to text(after.sessionTasteState),
            "listen_taste_state" to text(after.listenTasteState),
        ))

    private fun track(row: TrackRow): JsonObject = obj(
        "track_id" to text(row.id), "title" to text(row.title), "artist" to text(row.artist),
        "duration_ms" to integerOrNull(row.durationMs), "sha256" to text(row.sha256), "byte_size" to integer(row.byteSize),
    )
    private fun playlistEntry(row: PlaylistEntryRow): JsonObject = obj(
        "entry_id" to text(row.id), "playlist_id" to text(row.playlistId), "track_id" to text(row.trackId),
        "ordinal" to integer(row.ordinal),
    )

    private fun fact(kind: String, aggregateId: String, snapshot: JsonObject): LocalFactRow {
        val document = obj("version" to JsonPrimitive(1), "mutation" to text(kind), "snapshot" to snapshot)
        validate(document, 0)
        // Sorted object keys, compact JSON and lossless decimal strings for domain Long values.
        // This encoding is local v1 intent; it never reuses or hashes signed server wire bytes.
        val payload = document.toString()
        if (payload.toByteArray(Charsets.UTF_8).size > MAX_BYTES) {
            throw DesktopStorageException("desktop_outbox_payload_too_large")
        }
        return LocalFactRow(UUID.randomUUID().toString(), kind, aggregateId, System.currentTimeMillis(), payload)
    }

    private fun validate(value: JsonElement, depth: Int): Unit {
        if (depth > MAX_DEPTH) throw DesktopStorageException("desktop_outbox_payload_invalid")
        when (value) {
            is JsonObject -> value.forEach { (key, child) ->
                if (!safeName.matches(key) || forbidden.containsMatchIn(key)) {
                    throw DesktopStorageException("desktop_outbox_payload_invalid")
                }
                validate(child, depth + 1)
            }
            is JsonArray -> value.forEach { validate(it, depth + 1) }
            is JsonPrimitive -> if (value.isString) requireUnicode(value.content)
        }
    }

    private fun requireUnicode(value: String): Unit {
        var offset = 0
        while (offset < value.length) {
            val character = value[offset++]
            if (character.isHighSurrogate()) {
                if (offset >= value.length || !value[offset++].isLowSurrogate()) {
                    throw DesktopStorageException("desktop_outbox_payload_invalid")
                }
            } else if (character.isLowSurrogate()) {
                throw DesktopStorageException("desktop_outbox_payload_invalid")
            }
        }
    }

    private fun obj(vararg fields: Pair<String, JsonElement>): JsonObject = JsonObject(fields.toMap().toSortedMap())
    private fun text(value: String): JsonPrimitive = JsonPrimitive(value)
    private fun textOrNull(value: String?): JsonElement = value?.let(::text) ?: JsonNull
    private fun integer(value: Long): JsonPrimitive = text(value.toString())
    private fun integerOrNull(value: Long?): JsonElement = value?.let(::integer) ?: JsonNull
}
