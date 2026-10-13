package app.autplay.data.desktop

import java.util.UUID
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
    fun trackMetadataUpdated(row: TrackRow): LocalFactRow = fact("TRACK_METADATA_UPDATED", row.id, track(row))
    fun managedCopyRepaired(row: TrackRow): LocalFactRow = fact("MANAGED_COPY_REPAIRED", row.id, obj(
        "track_id" to text(row.id), "sha256" to text(row.sha256), "byte_size" to integer(row.byteSize),
    ))
    fun playlistCreated(row: PlaylistRow): LocalFactRow = fact("PLAYLIST_CREATED", row.id, obj(
        "playlist_id" to text(row.id), "name" to text(row.name),
    ))
    fun playlistEntryAppended(row: PlaylistEntryRow): LocalFactRow = fact("PLAYLIST_ENTRY_APPENDED", row.id, playlistEntry(row))
    fun playlistEntryRemoved(row: PlaylistEntryRow): LocalFactRow = fact("PLAYLIST_ENTRY_REMOVED", row.id, playlistEntry(row))

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
        "entries" to JsonArray(entries.sortedBy { it.ordinal }.map { entry -> obj(
            "entry_id" to text(entry.id), "track_id" to text(entry.trackId),
            "ordinal" to integer(entry.ordinal.toLong()), "shuffle_ordinal" to integer(entry.shuffleOrdinal.toLong()),
        ) }),
    ))

    fun listeningSaved(row: ListeningRow): LocalFactRow = fact("LISTENING_CHECKPOINT_SAVED", row.id, obj(
        "event_id" to text(row.id), "queue_entry_id" to text(row.queueEntryId), "track_id" to text(row.trackId),
        "started_at_ms" to integer(row.startedAtMs), "played_ms" to integer(row.playedMs),
        "position_ms" to integer(row.positionMs), "finalized" to JsonPrimitive(row.finalized),
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
