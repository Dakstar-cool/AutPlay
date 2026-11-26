package app.autplay.application.server

import kotlinx.serialization.json.*
import java.util.UUID

data class AlbumEditionChoice(val source: String, val id: String, val label: String)
data class AlbumPosition(val position: String, val disc: Int?, val title: String, val artist: String?,
    val durationMs: Long?, val recording: String?, val inVault: Boolean?)
data class AlbumTracklist(val edition: AlbumEditionChoice?, val tracks: List<AlbumPosition>,
    val choices: List<AlbumEditionChoice> = emptyList())

private val discogsIdPattern = Regex("""(?:https://(?:www\.)?discogs\.com/(?:[a-z]{2}/)?release/)?([1-9][0-9]{0,15})(?:-[^/\s?#]*)?/?""")
fun discogsReleaseId(input: String): String? = discogsIdPattern.matchEntire(input.trim())?.groupValues?.get(1)

fun decodeAlbumTracklist(root: JsonObject): AlbumTracklist {
    fun JsonObject.text(key: String) = this[key]?.jsonPrimitive?.contentOrNull
    fun edition(row: JsonObject): AlbumEditionChoice {
        val source = requireNotNull(row.text("source"))
        val id = requireNotNull(row.text("id"))
        require(when (source) {
            "discogs" -> id.matches(Regex("[1-9][0-9]{0,15}"))
            "musicbrainz" -> runCatching { UUID.fromString(id).toString() == id }.getOrDefault(false)
            else -> false
        })
        return AlbumEditionChoice(source, id, row.text("label").orEmpty())
    }
    root["choices"]?.let { choices ->
        require(choices.jsonArray.size <= 25)
        return AlbumTracklist(null, emptyList(), choices.jsonArray.map { edition(it.jsonObject) })
    }
    val rows = root.getValue("tracks").jsonArray
    require(rows.size in 1..5000)
    return AlbumTracklist(edition(root), rows.map { item ->
        val row = item.jsonObject
        AlbumPosition(row.text("position").orEmpty(), row["disc"]?.jsonPrimitive?.intOrNull,
            requireNotNull(row.text("title")), row.text("artist"), row["duration_ms"]?.jsonPrimitive?.longOrNull,
            row.text("recording"), row["in_vault"]?.jsonPrimitive?.booleanOrNull)
    })
}
