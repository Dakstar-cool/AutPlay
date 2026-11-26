package app.autplay.application.library

import java.net.URI
import java.util.UUID
import kotlinx.serialization.json.*

data class ExternalCollection(
    val operation: String,
    val revision: Int,
    val state: String,
    val name: String,
    val action: String,
    val expected: Int?,
    val read: Int,
    val identified: Int,
    val playable: Int,
    val complete: Boolean,
    val job: String?,
    val tracks: List<String>,
    val sets: Int?,
    val expectedSets: Int?,
    val setLinks: List<Pair<String, String>>,
    val likes: Int?,
    val authorization: String?,
    val selectionKey: String?,
    val offset: Int,
    val nextOffset: Int?,
    val missingOccurrences: Int?,
) {
    companion object {
        fun decode(doc: JsonObject): ExternalCollection {
            fun string(key: String) = doc.getValue(key).jsonPrimitive.content
            fun count(key: String) = doc[key]?.jsonPrimitive?.intOrNull
            val tracks = doc.getValue("entries").jsonArray.map { entry ->
                fun field(key: String) = entry.jsonObject[key]?.jsonPrimitive?.contentOrNull
                val label = listOfNotNull(field("artist"), field("title")).joinToString(" — ")
                val position = listOfNotNull(field("disc"), field("number")).joinToString(".")
                listOf(label, field("version").orEmpty(), field("album").orEmpty(), position)
                    .filter(String::isNotEmpty).joinToString(" · ")
            }
            val sets = doc["sets"]?.jsonArray.orEmpty().mapNotNull { entry ->
                val fields = entry.jsonArray
                val url = fields[0].jsonPrimitive.content
                if (isProviderLink(url)) url to fields[1].jsonPrimitive.content else null
            }
            val authorization = doc["authorization_url"]?.jsonPrimitive?.contentOrNull?.also {
                val uri = URI(it)
                require(uri.scheme == "https" && uri.rawAuthority == "accounts.spotify.com" && uri.path == "/authorize")
            }
            return ExternalCollection(
                operation = UUID.fromString(string("operation_id")).toString(),
                revision = checkNotNull(count("revision")),
                state = string("state"),
                name = string("name"),
                action = string("action"),
                expected = count("expected"),
                read = checkNotNull(count("read")),
                identified = checkNotNull(count("identified")),
                playable = checkNotNull(count("playable")),
                complete = doc.getValue("complete").jsonPrimitive.boolean,
                job = doc["import_job_id"]?.jsonPrimitive?.contentOrNull,
                tracks = tracks,
                sets = count("read_sets"),
                expectedSets = count("expected_sets"),
                setLinks = sets,
                likes = count("likes_count"),
                authorization = authorization,
                selectionKey = doc["selection_key"]?.jsonPrimitive?.contentOrNull,
                offset = count("offset") ?: 0,
                nextOffset = count("next_offset"),
                missingOccurrences = count("missing_occurrences"),
            )
        }

        fun isProviderLink(value: String): Boolean = runCatching {
            val url = URI(value.trim())
            url.scheme == "https" && url.rawAuthority?.lowercase() in setOf(
                "music.yandex.ru", "music.yandex.com", "music.yandex.kz", "music.yandex.by",
                "open.spotify.com", "soundcloud.com", "www.soundcloud.com",
                "spotify.link", "on.soundcloud.com", "music.yandex.link",
            )
        }.getOrDefault(false)

        fun startBody(link: String, favorites: Boolean, operation: String, newCopy: Boolean = false) = buildJsonObject {
            put("operation_id", operation)
            put("url", link)
            put("favorites", favorites)
            put("new_copy", newCopy)
        }
    }
}
