package app.autplay.application.desktop.account

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer

/** Existing portable-v1 metadata contract, independent of Android/Room. Never a media grant. */
data class AccountPortableRecord(val recordId: AccountUuid, val kind: String, val document: JsonObject) {
    fun wire(): JsonObject = buildJsonObject {
        put("record_id", recordId.value); put("kind", kind); put("document", document)
    }
    override fun toString(): String = "AccountPortableRecord"
}
data class AccountPortableCursor(val time: String?, val id: AccountUuid?, val revision: Long,
    val complete: Boolean, val passId: AccountUuid?, val uploadedRevision: Long = 0)
data class AccountPortablePage(val accountId: AccountUuid, val records: List<AccountPortableRecord>,
    val cursorTime: String?, val cursorId: AccountUuid?, val nextTime: String?, val nextId: AccountUuid?)
data class AccountPortableStatistics(val revision: Long, val checkedThroughMs: Long?,
    val complete: Boolean, val available: Boolean, val listens: Long?, val playedMs: Long?)
data class AccountPortableUploadRecord(val record:AccountPortableRecord,val revision:Long)

interface DesktopAccountPortableData {
    suspend fun ownerAccountId(lease:AccountLease):AccountUuid
    suspend fun beginPass(lease: AccountLease, operationId: AccountUuid, passId: AccountUuid): Unit
    suspend fun cursor(lease: AccountLease, source: String): AccountPortableCursor
    /** Exact response bytes are retained separately from normalized records. */
    suspend fun applyPortablePage(lease: AccountLease, operationId: AccountUuid, source: String,
        expectedCursor: AccountPortableCursor, exactResponse: ByteArray): AccountPortableCursor
    suspend fun mergeRecords(lease: AccountLease, operationId: AccountUuid, records: List<AccountPortableRecord>): Unit
    suspend fun recordUpload(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long, uploadedRevision: Long): Unit
    suspend fun readUploadPage(lease:AccountLease,afterRevision:Long,limit:Int=32):List<AccountPortableUploadRecord>
    suspend fun readPage(lease: AccountLease, kind: String, after: AccountUuid?, limit: Int = 32): List<AccountPortableRecord>
    suspend fun statistics(lease: AccountLease): AccountPortableStatistics
}

object AccountPortableV1 {
    val sources: List<String> = listOf("records", "history", "preferences", "playlists", "playlist_entries", "tastes")
    val kinds: Set<String> = setOf("LISTENING", "PREFERENCE", "PLAYLIST", "PLAYLIST_ENTRY", "TASTE")
    private val common = setOf("version", "updated_at_ms")
    fun canonical(value: JsonObject): ByteArray {
        var remaining=DesktopAccountLimits.PAGE_BYTES
        fun bounded(e:JsonElement,depth:Int):Unit {
            require(depth<=34)
            fun text(v:String):Unit {
                remaining-=2;var i=0
                while(i<v.length){val c=v[i++];val cost=when{
                    c.isHighSurrogate()->{require(i<v.length&&v[i++].isLowSurrogate());4}
                    c.isLowSurrogate()->throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
                    c.code<32->6;c=='"'||c=='\\'->2;c.code<128->1;c.code<2_048->2;else->3}
                    remaining-=cost;require(remaining>=0)
                }
            }
            when(e){
                is JsonObject->{remaining-=2;for((k,v)in e){text(k);bounded(v,depth+1);remaining-=2;require(remaining>=0)}}
                is JsonArray->{remaining-=2;for(v in e){bounded(v,depth+1);remaining--;require(remaining>=0)}}
                is JsonPrimitive->text(e.content)
            }
        }
        bounded(value,0)
        return JsonCanonicalizer(value.toString()).encodedUTF8.also{require(it.size<=DesktopAccountLimits.PAGE_BYTES)}
    }
    fun hash(value: JsonObject): String = MessageDigest.getInstance("SHA-256").digest(canonical(value))
        .joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }

    /** Same account-namespace UUIDv5 recipe as existing Android and server legacy exports. */
    fun stableId(accountId: AccountUuid, name: String): AccountUuid {
        require(name.toByteArray(Charsets.UTF_8).size <= 1_024)
        val namespace = UUID.fromString(accountId.value)
        val bytes = ByteBuffer.allocate(16).putLong(namespace.mostSignificantBits).putLong(namespace.leastSignificantBits).array()
        val digest = MessageDigest.getInstance("SHA-1").digest(bytes + ("autplay:portable-" + name).toByteArray(Charsets.UTF_8))
        digest[6] = ((digest[6].toInt() and 15) or 80).toByte(); digest[8] = ((digest[8].toInt() and 63) or 128).toByte()
        val buffer = ByteBuffer.wrap(digest)
        return AccountUuid(UUID(buffer.long, buffer.long).toString())
    }

    fun decode(value: JsonObject): AccountPortableRecord {
        require(value.keys == setOf("record_id", "kind", "document"))
        val id = AccountUuid(text(value, "record_id", 36)); val kind = text(value, "kind", 40)
        val d = value["document"] as? JsonObject ?: invalid()
        require(canonical(d).size <= 16_384)
        integer(d, "version", 1, 1); integer(d, "updated_at_ms", 0, 8_640_000_000_000_000)
        val keys = when (kind) {
            "LISTENING" -> {
                val aliases = (d["event_ids"] as? JsonArray)?.map {
                    val p = it as? JsonPrimitive ?: invalid(); require(p.isString); AccountUuid(p.content)
                } ?: invalid()
                require(aliases.size in 1..32 && aliases.distinct().size == aliases.size && id in aliases)
                track(d); metadata(d)
                val genres = d["genres"] as? JsonArray ?: invalid(); require(genres.size <= 12)
                genres.forEach { checkedText(it, 100, false) }
                integer(d, "started_at_ms", 0, 8_640_000_000_000_000); integer(d, "played_ms", 0, 604_800_000)
                if (d["duration_ms"] != JsonNull) integer(d, "duration_ms", 1, 604_800_000)
                flag(d, "excluded_from_taste")
                require(Regex("[A-Z][A-Z0-9_]{0,99}").matches(text(d, "event_origin", 100)))
                require(Regex("[A-Z][A-Z0-9_]{0,49}").matches(text(d, "context", 50)))
                setOf("event_ids", "track_key", "title", "artist", "genres", "started_at_ms", "played_ms", "duration_ms", "excluded_from_taste", "event_origin", "context")
            }
            "PREFERENCE" -> {
                track(d); metadata(d); flag(d, "excluded_from_taste")
                require(text(d, "preference", 20) in setOf("NEUTRAL", "LIKED", "DISLIKED"))
                if (d["rating"] != JsonNull) integer(d, "rating", 1, 5)
                setOf("track_key", "title", "artist", "preference", "rating", "excluded_from_taste")
            }
            "PLAYLIST" -> {
                text(d, "name", 500); checkedText(d["description"], 2_000, true); flag(d, "deleted")
                setOf("name", "description", "deleted")
            }
            "PLAYLIST_ENTRY" -> {
                AccountUuid(text(d, "playlist_id", 36)); track(d); metadata(d); text(d, "position_key", 128); flag(d, "deleted")
                setOf("playlist_id", "track_key", "title", "artist", "position_key", "deleted")
            }
            "TASTE" -> {
                track(d); require(text(d, "interaction", 20) in setOf("LIKE", "DISLIKE", "SKIP", "COMPLETE", "EXCLUDE"))
                integer(d, "weight", -1_000, 1_000); setOf("track_key", "interaction", "weight")
            }
            else -> invalid()
        }
        require(d.keys == keys + common)
        val normalized = if (kind == "LISTENING") JsonObject(d + mapOf(
            "event_ids" to JsonArray(d.getValue("event_ids").jsonArray.sortedBy { it.jsonPrimitive.content }),
            "genres" to JsonArray(d.getValue("genres").jsonArray.distinct().sortedWith { a, b -> compareText(a.jsonPrimitive.content, b.jsonPrimitive.content) }),
        )) else d
        return AccountPortableRecord(id, kind, normalized)
    }

    fun merge(old: AccountPortableRecord, incoming: AccountPortableRecord): AccountPortableRecord {
        require(old.kind == incoming.kind) { "ACCOUNT_RECORD_IDENTITY_CONFLICT" }
        if (old.kind != "LISTENING") {
            val first = old.document.getValue("updated_at_ms").jsonPrimitive.long
            val second = incoming.document.getValue("updated_at_ms").jsonPrimitive.long
            return if (second > first || second == first && hash(incoming.document) > hash(old.document)) incoming else old
        }
        require(old.document["played_ms"] == incoming.document["played_ms"]) { "ACCOUNT_RECORD_IDENTITY_CONFLICT" }
        val aliases = (old.document.getValue("event_ids").jsonArray + incoming.document.getValue("event_ids").jsonArray)
            .map { it.jsonPrimitive.content }.distinct().sorted()
        require(aliases.size <= 32)
        val result = old.document.toMutableMap()
        result["event_ids"] = JsonArray(aliases.map(::JsonPrimitive))
        result["started_at_ms"] = JsonPrimitive(minOf(old.document.getValue("started_at_ms").jsonPrimitive.long, incoming.document.getValue("started_at_ms").jsonPrimitive.long))
        result["updated_at_ms"] = JsonPrimitive(maxOf(old.document.getValue("updated_at_ms").jsonPrimitive.long, incoming.document.getValue("updated_at_ms").jsonPrimitive.long))
        result["genres"] = JsonArray((old.document.getValue("genres").jsonArray + incoming.document.getValue("genres").jsonArray)
            .map { it.jsonPrimitive.content }.distinct().sortedWith(::compareText).take(12).map(::JsonPrimitive))
        result["track_key"] = JsonPrimitive(minOf(old.document.getValue("track_key").jsonPrimitive.content, incoming.document.getValue("track_key").jsonPrimitive.content))
        result["excluded_from_taste"] = JsonPrimitive(old.document.getValue("excluded_from_taste").jsonPrimitive.boolean || incoming.document.getValue("excluded_from_taste").jsonPrimitive.boolean)
        listOf("event_origin", "context").forEach { result[it] = JsonPrimitive(minOf(old.document.getValue(it).jsonPrimitive.content, incoming.document.getValue(it).jsonPrimitive.content)) }
        listOf("title", "artist", "duration_ms").forEach { name ->
            result[name] = listOf(old.document.getValue(name), incoming.document.getValue(name)).filter { it != JsonNull }
                .minWithOrNull { a, b -> if (name == "duration_ms") a.jsonPrimitive.long.compareTo(b.jsonPrimitive.long)
                    else compareText(a.jsonPrimitive.content, b.jsonPrimitive.content) } ?: JsonNull
        }
        return decode(AccountPortableRecord(AccountUuid(aliases.first()), "LISTENING", JsonObject(result)).wire())
    }
    private fun compareText(first: String, second: String): Int {
        val a = first.toByteArray(Charsets.UTF_8); val b = second.toByteArray(Charsets.UTF_8)
        for (index in 0 until minOf(a.size, b.size)) {
            val difference = (a[index].toInt() and 255) - (b[index].toInt() and 255)
            if (difference != 0) return difference
        }
        return a.size.compareTo(b.size)
    }
    private fun track(d: JsonObject): Unit { AccountUuid(text(d, "track_key", 36)) }
    private fun metadata(d: JsonObject): Unit { checkedText(d["title"], 2_000, true); checkedText(d["artist"], 2_000, true) }
    private fun text(d: JsonObject, key: String, max: Int): String = checkedText(d[key], max, false)!!
    private fun checkedText(value: JsonElement?, max: Int, nullable: Boolean): String? {
        if (nullable && value == JsonNull) return null
        val p = value as? JsonPrimitive ?: invalid()
        require(p.isString && p.content.codePointCount(0, p.content.length) in 1..max && p.content.none { it.code < 32 })
        return p.content
    }
    private fun integer(d: JsonObject, key: String, min: Long, max: Long): Long {
        val p = d[key] as? JsonPrimitive ?: invalid(); require(!p.isString)
        return (p.longOrNull ?: invalid()).also { require(it in min..max) }
    }
    private fun flag(d: JsonObject, key: String): Unit {
        val p = d[key] as? JsonPrimitive ?: invalid(); require(!p.isString && p.booleanOrNull != null)
    }
    private fun invalid(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
}
