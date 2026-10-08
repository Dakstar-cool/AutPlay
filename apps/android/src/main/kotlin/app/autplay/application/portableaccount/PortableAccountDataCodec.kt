package app.autplay.application.portableaccount

import app.autplay.data.local.PortableLocalListeningRow
import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.*

/** Only descriptive metadata crosses servers; arbitrary Journal payloads never enter this codec. */
data class PortableDataRecord(val recordId: String, val kind: String, val document: JsonObject) {
    override fun toString(): String = "PortableDataRecord(kind=$kind, metadata=<redacted>)"
    fun wire(): JsonObject = buildJsonObject {
        put("record_id", recordId); put("kind", kind); put("document", document)
    }
}

object PortableAccountDataCodec {
    const val PAGE_SIZE: Int = 32
    const val MAX_PAYLOAD_BYTES: Int = 524_288
    const val ZERO_ID: String = "00000000-0000-0000-0000-000000000000"
    val sources: List<String> = listOf("records", "history", "preferences", "playlists", "playlist_entries", "tastes")
    private val common = setOf("version", "updated_at_ms")

    fun uuid(value: String): String {
        require(UUID.fromString(value).toString() == value) { "ACCOUNT_DATA_INVALID" }
        return value
    }

    fun decode(value: JsonObject): PortableDataRecord {
        require(value.keys == setOf("record_id", "kind", "document")) { "ACCOUNT_DATA_INVALID" }
        val id = uuid(text(value, "record_id", 36))
        val kind = text(value, "kind", 40)
        val d = value["document"] as? JsonObject ?: invalid()
        require(canonicalAccountRequest(d).size <= 16_384) { "ACCOUNT_DATA_INVALID" }
        integer(d, "version", 1, 1); integer(d, "updated_at_ms", 0, 8_640_000_000_000_000)
        val keys = when (kind) {
            "LISTENING" -> {
                val aliases = (d["event_ids"] as? JsonArray)?.map { uuid(it.jsonPrimitive.content) } ?: invalid()
                require(aliases.size in 1..32 && aliases.distinct().size == aliases.size && id in aliases) { "ACCOUNT_DATA_INVALID" }
                track(d); metadata(d)
                val genres = d["genres"] as? JsonArray ?: invalid()
                require(genres.size <= 12) { "ACCOUNT_DATA_INVALID" }
                genres.forEach { checkedText(it, 100, false) }
                integer(d, "started_at_ms", 0, 8_640_000_000_000_000)
                integer(d, "played_ms", 0, 604_800_000)
                if (d["duration_ms"] != JsonNull) integer(d, "duration_ms", 1, 604_800_000)
                flag(d, "excluded_from_taste")
                require(Regex("[A-Z][A-Z0-9_]{0,99}").matches(text(d, "event_origin", 100))) { "ACCOUNT_DATA_INVALID" }
                require(Regex("[A-Z][A-Z0-9_]{0,49}").matches(text(d, "context", 50))) { "ACCOUNT_DATA_INVALID" }
                setOf("event_ids", "track_key", "title", "artist", "genres", "started_at_ms", "played_ms", "duration_ms", "excluded_from_taste", "event_origin", "context")
            }
            "PREFERENCE" -> {
                track(d); metadata(d); flag(d, "excluded_from_taste")
                require(text(d, "preference", 20) in setOf("NEUTRAL", "LIKED", "DISLIKED")) { "ACCOUNT_DATA_INVALID" }
                if (d["rating"] != JsonNull) integer(d, "rating", 1, 5)
                setOf("track_key", "title", "artist", "preference", "rating", "excluded_from_taste")
            }
            "PLAYLIST" -> {
                text(d, "name", 500); checkedText(d["description"], 2000, true); flag(d, "deleted")
                setOf("name", "description", "deleted")
            }
            "PLAYLIST_ENTRY" -> {
                uuid(text(d, "playlist_id", 36)); track(d); metadata(d); text(d, "position_key", 128); flag(d, "deleted")
                setOf("playlist_id", "track_key", "title", "artist", "position_key", "deleted")
            }
            "TASTE" -> {
                track(d)
                require(text(d, "interaction", 20) in setOf("LIKE", "DISLIKE", "SKIP", "COMPLETE", "EXCLUDE")) { "ACCOUNT_DATA_INVALID" }
                integer(d, "weight", -1000, 1000)
                setOf("track_key", "interaction", "weight")
            }
            else -> invalid()
        }
        require(d.keys == keys + common) { "ACCOUNT_DATA_INVALID" }
        val normalized = if (kind == "LISTENING") JsonObject(d + mapOf(
            "event_ids" to JsonArray(d["event_ids"]!!.jsonArray.sortedBy { it.jsonPrimitive.content }),
            "genres" to JsonArray(d["genres"]!!.jsonArray.distinct().sortedWith { a, b -> compareUnicodeText(a.jsonPrimitive.content, b.jsonPrimitive.content) }),
        )) else d
        return PortableDataRecord(id, kind, normalized)
    }

    fun listening(row: PortableLocalListeningRow): PortableDataRecord {
        val aliases = listOfNotNull(uuid(row.eventId), row.materializedEventId?.let(::uuid)).distinct().sorted()
        val fields = row.metadata?.let { runCatching { Json.parseToJsonElement(it).jsonObject["fields"] as? JsonObject }.getOrNull() }
        val genres = (fields?.get("genres") as? JsonArray).orEmpty().mapNotNull {
            (it as? JsonPrimitive)?.contentOrNull?.takeIf { g -> g.isNotBlank() && g.codePointCount(0, g.length) <= 100 && g.none { c -> c.code < 32 } }
        }.distinct().sortedWith(::compareUnicodeText).take(12)
        return checked(aliases.first(), "LISTENING", buildJsonObject {
            put("version", 1); put("updated_at_ms", row.updatedAtMs); put("event_ids", JsonArray(aliases.map(::JsonPrimitive)))
            put("track_key", row.trackKey); putNullable("title", safeDescription(row.title, 2000)); putNullable("artist", safeDescription(row.artist, 2000))
            put("genres", JsonArray(genres.map(::JsonPrimitive))); put("started_at_ms", row.startedAtMs); put("played_ms", row.playedMs)
            put("duration_ms", row.durationMs?.let(::JsonPrimitive) ?: JsonNull); put("excluded_from_taste", row.excluded)
            put("event_origin", row.origin); put("context", row.context)
        })
    }

    fun checked(id: String, kind: String, document: JsonObject): PortableDataRecord = decode(PortableDataRecord(id, kind, document).wire())
    fun canonical(document: JsonObject): String = canonicalAccountRequest(document).toString(Charsets.UTF_8)
    fun hash(document: JsonObject): String = MessageDigest.getInstance("SHA-256").digest(canonicalAccountRequest(document))
        .joinToString("") { "%02x".format(it.toInt() and 255) }

    /** Same LWW ordering as the server, with a deterministic listening alias union. */
    fun merge(old: PortableDataRecord, incoming: PortableDataRecord): PortableDataRecord {
        require(old.kind == incoming.kind) { "ACCOUNT_RECORD_IDENTITY_CONFLICT" }
        if (old.kind != "LISTENING") {
            val first = old.document["updated_at_ms"]!!.jsonPrimitive.long
            val second = incoming.document["updated_at_ms"]!!.jsonPrimitive.long
            return if (second > first || second == first && hash(incoming.document) > hash(old.document)) incoming else old
        }
        require(old.document["played_ms"] == incoming.document["played_ms"]) { "ACCOUNT_RECORD_IDENTITY_CONFLICT" }
        val aliases = (old.document["event_ids"]!!.jsonArray + incoming.document["event_ids"]!!.jsonArray).map { it.jsonPrimitive.content }.distinct().sorted()
        require(aliases.size <= 32) { "ACCOUNT_DATA_LIMIT_REACHED" }
        val result = old.document.toMutableMap()
        result["event_ids"] = JsonArray(aliases.map(::JsonPrimitive))
        result["started_at_ms"] = JsonPrimitive(minOf(old.document["started_at_ms"]!!.jsonPrimitive.long, incoming.document["started_at_ms"]!!.jsonPrimitive.long))
        result["updated_at_ms"] = JsonPrimitive(maxOf(old.document["updated_at_ms"]!!.jsonPrimitive.long, incoming.document["updated_at_ms"]!!.jsonPrimitive.long))
        result["genres"] = JsonArray((old.document["genres"]!!.jsonArray + incoming.document["genres"]!!.jsonArray).map { it.jsonPrimitive.content }
            .distinct().sortedWith(::compareUnicodeText).take(12).map(::JsonPrimitive))
        result["track_key"] = JsonPrimitive(minOf(old.document["track_key"]!!.jsonPrimitive.content, incoming.document["track_key"]!!.jsonPrimitive.content))
        result["excluded_from_taste"] = JsonPrimitive(old.document["excluded_from_taste"]!!.jsonPrimitive.boolean || incoming.document["excluded_from_taste"]!!.jsonPrimitive.boolean)
        listOf("event_origin", "context").forEach { name ->
            result[name] = JsonPrimitive(minOf(old.document[name]!!.jsonPrimitive.content, incoming.document[name]!!.jsonPrimitive.content))
        }
        listOf("title", "artist", "duration_ms").forEach { name ->
            result[name] = listOf(old.document[name]!!, incoming.document[name]!!).filter { it != JsonNull }
                .minWithOrNull { a, b -> if (name == "duration_ms") a.jsonPrimitive.long.compareTo(b.jsonPrimitive.long)
                    else compareUnicodeText(a.jsonPrimitive.content, b.jsonPrimitive.content) } ?: JsonNull
        }
        return checked(aliases.first(), "LISTENING", JsonObject(result))
    }

    fun safeDescription(value: String?, maximum: Int): String? = value?.filter { it.code >= 32 }?.let {
        if (it.codePointCount(0, it.length) > maximum) it.substring(0, it.offsetByCodePoints(0, maximum)) else it
    }?.takeIf { it.isNotBlank() }

    /** Python compares Unicode code points; unsigned UTF-8 comparison matches it, including emoji. */
    private fun compareUnicodeText(first: String, second: String): Int {
        val a = first.toByteArray(Charsets.UTF_8); val b = second.toByteArray(Charsets.UTF_8)
        for (index in 0 until minOf(a.size, b.size)) {
            val difference = (a[index].toInt() and 255) - (b[index].toInt() and 255)
            if (difference != 0) return difference
        }
        return a.size.compareTo(b.size)
    }
    private fun track(d: JsonObject) { uuid(text(d, "track_key", 36)) }
    private fun metadata(d: JsonObject) { checkedText(d["title"], 2000, true); checkedText(d["artist"], 2000, true) }
    private fun text(d: JsonObject, key: String, maximum: Int): String = checkedText(d[key], maximum, false)!!
    private fun checkedText(value: JsonElement?, maximum: Int, nullable: Boolean): String? {
        if (nullable && value == JsonNull) return null
        val primitive = value as? JsonPrimitive ?: invalid()
        require(primitive.isString && primitive.content.codePointCount(0, primitive.content.length) in 1..maximum && primitive.content.none { it.code < 32 }) { "ACCOUNT_DATA_INVALID" }
        return primitive.content
    }
    private fun integer(d: JsonObject, key: String, min: Long, max: Long): Long {
        val p = d[key] as? JsonPrimitive ?: invalid()
        require(!p.isString) { "ACCOUNT_DATA_INVALID" }
        val value = p.longOrNull ?: invalid()
        require(value in min..max) { "ACCOUNT_DATA_INVALID" }; return value
    }
    private fun flag(d: JsonObject, key: String) {
        val value = d[key] as? JsonPrimitive
        require(value != null && !value.isString && value.booleanOrNull != null) { "ACCOUNT_DATA_INVALID" }
    }
    private fun invalid(): Nothing = throw IllegalArgumentException("ACCOUNT_DATA_INVALID")
}

internal fun JsonObjectBuilder.putNullable(key: String, value: String?) { put(key, value?.let(::JsonPrimitive) ?: JsonNull) }
