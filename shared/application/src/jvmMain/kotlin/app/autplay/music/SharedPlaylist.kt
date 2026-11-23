package app.autplay.application.library

import java.util.UUID
import kotlinx.serialization.json.*

data class SharedPlaylistTrack(
    val title: String?,
    val artist: String?,
    val durationMs: Long?,
    val recordingId: String?,
)

data class SharedPlaylist(
    val format: String,
    val token: String,
    val name: String,
    val description: String?,
    val tracks: List<SharedPlaylistTrack>,
    val entries: List<Int>,
) {
    init {
        require(format == "autplay-playlist-v1" && UUID.fromString(token).toString() == token)
        require(name.isNotBlank() && name.length <= 120 && (description?.length ?: 0) <= 500)
        require(tracks.size <= MAX_ENTRIES && entries.size <= MAX_ENTRIES)
        require(entries.toSet() == tracks.indices.toSet())
        require(tracks.all { (it.title?.length ?: 0) <= 4096 && (it.artist?.length ?: 0) <= 4096 &&
            (it.durationMs == null || it.durationMs >= 0) &&
            (it.recordingId == null || UUID.fromString(it.recordingId).toString() == it.recordingId) })
    }

    companion object {
        fun decode(bytes: ByteArray): SharedPlaylist {
            require(bytes.size <= MAX_FILE_BYTES)
            val json = Json.parseToJsonElement(bytes.decodeToString(throwOnInvalidSequence = true)).jsonObject
            fun JsonObject.label(key: String): String? {
                val value = getValue(key)
                if (value == JsonNull) return null
                return value.jsonPrimitive.also { require(it.isString) }.content
            }
            val tracks = json.getValue("tracks").jsonArray
            val entries = json.getValue("entries").jsonArray
            require(tracks.size <= MAX_ENTRIES && entries.size <= MAX_ENTRIES)
            return SharedPlaylist(
                checkNotNull(json.label("format")), checkNotNull(json.label("token")),
                checkNotNull(json.label("name")), json.label("description"),
                tracks.map { item ->
                    val row = item.jsonObject
                    val duration = row.getValue("duration_ms")
                    SharedPlaylistTrack(row.label("title"), row.label("artist"),
                        duration.takeUnless { it == JsonNull }?.jsonPrimitive?.long, row.label("recording_id"))
                },
                entries.map { it.jsonPrimitive.int },
            )
        }

        const val MAX_ENTRIES = 10_000
        const val MAX_FILE_BYTES = 8 * 1024 * 1024 + 256
    }
}
