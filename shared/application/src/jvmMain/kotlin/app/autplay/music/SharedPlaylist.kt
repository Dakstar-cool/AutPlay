package app.autplay.application.library

import java.util.UUID
import kotlinx.serialization.json.*

data class SharedPlaylistTrack(
    val title: String?,
    val artist: String?,
    val durationMs: Long?,
    val recordingId: String?,
)

/** A private playlist copy from a share or an artist/title text export. */
data class PlaylistImportFile(
    val name: String, val description: String?, val tracks: List<SharedPlaylistTrack>,
    val entries: List<Int>, val shareToken: String?,
) {
    constructor(share: SharedPlaylist) : this(share.name, share.description, share.tracks, share.entries, share.token)

    companion object {
        fun decode(bytes: ByteArray, fileName: String): PlaylistImportFile {
            require(bytes.size <= SharedPlaylist.MAX_FILE_BYTES)
            val content = bytes.decodeToString(throwOnInvalidSequence = true).removePrefix("\uFEFF").trim()
            if (content.startsWith("{")) return PlaylistImportFile(SharedPlaylist.decode(content.encodeToByteArray()))
            val indices = linkedMapOf<SharedPlaylistTrack, Int>()
            val entries = mutableListOf<Int>()
            val separator = Regex("""\s+[-–—]\s+""")
            content.lineSequence().map(String::trim).filter(String::isNotEmpty).forEachIndexed { index, line ->
                val fields = if ('\t' in line) line.split('\t') else separator.split(line, limit = 2)
                require(fields.size in 2..3)
                if (index == 0 && fields.map { it.trim().lowercase() } in listOf(
                        listOf("artist", "title"), listOf("artist", "title", "album"))) return@forEachIndexed
                val artist = fields[0].trim()
                val title = fields[1].trim()
                require(artist.length in 1..4096 && title.length in 1..4096 && entries.size < SharedPlaylist.MAX_ENTRIES)
                entries += indices.getOrPut(SharedPlaylistTrack(title, artist, null, null)) { indices.size }
            }
            require(entries.isNotEmpty())
            return PlaylistImportFile(fileName.substringBeforeLast('.', fileName).trim().take(120).ifEmpty { "Playlist" },
                null, indices.keys.toList(), entries, null)
        }
    }
}

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

    fun encode(): ByteArray = buildJsonObject {
        put("format", format)
        put("token", token)
        put("name", name)
        put("description", JsonPrimitive(description))
        putJsonArray("tracks") {
            tracks.forEach { track ->
                add(buildJsonObject {
                    put("title", JsonPrimitive(track.title))
                    put("artist", JsonPrimitive(track.artist))
                    put("duration_ms", JsonPrimitive(track.durationMs))
                    put("recording_id", JsonPrimitive(track.recordingId))
                })
            }
        }
        putJsonArray("entries") { entries.forEach { add(it) } }
    }.toString().encodeToByteArray().also { require(it.size <= MAX_FILE_BYTES) }

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
