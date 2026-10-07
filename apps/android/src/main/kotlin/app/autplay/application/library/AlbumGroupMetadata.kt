package app.autplay.application.library

import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.text.Normalizer
import java.util.Locale
import java.util.UUID
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull

/** An edition description. Its identifiers never substitute for canonical Room identities. */
public data class MetadataAlbumGroup(
    public val key: String,
    public val provider: String,
    public val releaseId: String,
    public val title: String,
    public val albumArtist: String?,
    public val releaseDate: String?,
    public val discNumber: Int?,
    public val trackNumber: Int?,
    public val payload: JsonObject,
) {
    public val stableId: String get() = METADATA_ALBUM_PREFIX + key
}

internal const val METADATA_ALBUM_PREFIX = "metadata-album:"
private val nativeAlbumProviders = setOf("JAMENDO", "YANDEX", "BANDCAMP", "SOUNDCLOUD", "YOUTUBE", "HITMO")
private val canonicalUuid = Regex("[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")

/** Unsupported data stays in TrackMetadata.payload; only confirmed, coherent groups are exposed. */
internal fun TrackMetadata.albumGroup(): MetadataAlbumGroup? {
    val raw = payload["album_group_v1"] as? JsonObject ?: return null
    return runCatching {
        val version = raw["schema_version"] as? JsonPrimitive ?: error("ALBUM_GROUP_INVALID")
        require(!version.isString && version.intOrNull == 1)
        val provider = raw.requiredAlbumText("provider", 100)
        val release = raw.requiredAlbumText("release_id", 128)
        val key = raw.requiredAlbumText("key", 300)
        val source = if (provider == "MUSICBRAINZ") {
            require(canonicalUuid.matches(release) && UUID.fromString(release).toString() == release)
            require(key == "musicbrainz:release:$release")
            "MUSICBRAINZ"
        } else {
            require(provider in nativeAlbumProviders)
            require(release.matches(Regex("[A-Za-z0-9._-]{1,128}")))
            require(key == "native:${provider.lowercase(Locale.ROOT)}:album:$release")
            "SOURCE_NATIVE"
        }
        val title = raw.requiredAlbumText("title", 500)
        if (fields.containsKey("mb_release_id")) {
            val appliedRelease = fields.nullableAlbumText("mb_release_id", 128)
            require(if (provider == "MUSICBRAINZ") appliedRelease == release else appliedRelease == null)
        }
        val artist = raw.nullableAlbumText("album_artist", 500)
        val date = raw.nullableAlbumText("release_date", 10)
        require(date == null || validMetadataDate(date))
        val disc = raw.nullableAlbumNumber("disc_number")
        val track = raw.nullableAlbumNumber("track_number")
        val evidence = raw["evidence"] as? JsonObject ?: error("ALBUM_GROUP_INVALID")
        require(evidence.requiredAlbumText("source", 100) == source)
        val sourceId = evidence.requiredAlbumText("source_id", 500)
        require(!sourceId.contains("://") && !sourceId.contains('\\') &&
            !sourceId.startsWith('/') && !sourceId.startsWith("file:") && !sourceId.startsWith("data:") &&
            !sourceId.matches(Regex("^[A-Za-z]:/.*")) && sourceId.none { it in "?&#=@" })
        require(provider != "MUSICBRAINZ" || sourceId == key)
        val locked = evidence["locked"] as? JsonPrimitive ?: error("ALBUM_GROUP_INVALID")
        require(!locked.isString && locked.booleanOrNull != null)
        evidence.requiredAlbumText("normalization_version", 100)
        val observed = evidence.requiredAlbumText("observed_at", 100)
        require(runCatching { OffsetDateTime.parse(observed) }.isSuccess ||
            runCatching { LocalDateTime.parse(observed) }.isSuccess)
        // A manual clear or changed effective edition must not retain stale membership.
        require(!fields.containsKey("album") || normalizedAlbumLabel(fields.nullableAlbumText("album", 500)) == normalizedAlbumLabel(title))
        if (artist != null && fields.containsKey("album_artist")) {
            require(normalizedAlbumLabel(fields.nullableAlbumText("album_artist", 500)) == normalizedAlbumLabel(artist))
        }
        require(!fields.containsKey("release_date") || fields["release_date"] == (date?.let(::JsonPrimitive) ?: JsonNull))
        listOf("disc_number" to disc, "track_number" to track).forEach { (name, number) ->
            if (fields.containsKey(name)) require(fields.nullableAlbumNumber(name) == number)
        }
        MetadataAlbumGroup(key, provider, release, title, artist, date, disc, track, raw)
    }.getOrNull()
}

/** Compare labels only after an exact provider/edition key was validated; this never creates IDs. */
private fun normalizedAlbumLabel(value: String?): String? = value?.let { text ->
    // Full upper/lower expansion handles sharp-s and ligatures; dotless-i keeps its own identity.
    val folded = Normalizer.normalize(text, Normalizer.Form.NFKC).split('\u0131').joinToString("\u0131") {
        it.uppercase(Locale.ROOT).lowercase(Locale.ROOT)
    }.replace("\u00df", "ss").replace('\u03c2', '\u03c3')
    folded.replace(Regex("[^\\p{L}\\p{N}_]+"), " ").trim().replace(Regex("\\s+"), " ")
}

private fun JsonObject.requiredAlbumText(name: String, maximum: Int): String {
    val value = this[name] as? JsonPrimitive ?: error("ALBUM_GROUP_INVALID")
    require(value.isString && value.content.isNotBlank() && value.content.length <= maximum &&
        value.content == value.content.trim())
    require(value.content.none { it.code < 32 || it.code in 127..159 })
    return value.content
}

private fun JsonObject.nullableAlbumText(name: String, maximum: Int): String? {
    require(containsKey(name))
    return if (this[name] == JsonNull) null else requiredAlbumText(name, maximum)
}

private fun JsonObject.nullableAlbumNumber(name: String): Int? {
    require(containsKey(name))
    if (this[name] == JsonNull) return null
    val value = this[name] as? JsonPrimitive ?: error("ALBUM_GROUP_INVALID")
    require(!value.isString)
    return requireNotNull(value.intOrNull).also { require(it in 1..9999) }
}
