package app.autplay.application.server

import java.time.LocalDate
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull

enum class InternetMetadataDiscoveryKind(val wireValue: String) {
    ARTIST("artist"),
    ALBUM("album"),
}

enum class InternetMetadataDiscoveryEntity(val wireValue: String) {
    ARTIST("artist"),
    RELEASE("release"),
    RECORDING("recording"),
    RELEASE_TRACK("release_track");

    companion object {
        fun known(value: String): InternetMetadataDiscoveryEntity? = entries.firstOrNull { it.wireValue == value }
    }
}

@ConsistentCopyVisibility
data class InternetMetadataBrowseTarget internal constructor(
    val entity: InternetMetadataDiscoveryEntity,
    val entityId: String,
)

/** This starts a separate, explicit source search; it never identifies acquired audio. */
@ConsistentCopyVisibility
data class InternetMetadataTrackLookup internal constructor(
    val entity: InternetMetadataDiscoveryEntity,
    val entityId: String,
    val releaseId: String?,
    val searchQuery: String,
)

class InternetMetadataDiscoveryCard internal constructor(
    val id: String,
    val entityType: String,
    val entityId: String,
    val title: String,
    val artist: String?,
    val releaseId: String?,
    val recordingId: String?,
    val releaseDate: String?,
    val country: String?,
    val disambiguation: String?,
    val durationMs: Int?,
    val discNumber: Int?,
    val trackNumber: Int?,
    val recordingTitle: String?,
    val source: String,
    val availability: String,
    val acquisitionAllowed: Boolean,
    val canBrowseTracks: Boolean,
    val downloadSearchQuery: String?,
    val rawPayload: JsonObject,
    private val pageAllowsKnownActions: Boolean,
) {
    val knownEntity: InternetMetadataDiscoveryEntity? get() = InternetMetadataDiscoveryEntity.known(entityType)

    /** Catalogue cards have no source candidate ID and cannot authorize a download. */
    val directAcquisitionAllowed: Boolean get() = false

    val browseTarget: InternetMetadataBrowseTarget?
        get() {
            if (!knownEnvelope || !canBrowseTracks || downloadSearchQuery != null) return null
            val entity = knownEntity ?: return null
            val coherent = when (entity) {
                InternetMetadataDiscoveryEntity.ARTIST -> releaseId == null && recordingId == null
                InternetMetadataDiscoveryEntity.RELEASE -> releaseId == entityId && recordingId == null
                else -> false
            }
            return if (coherent && discNumber == null && trackNumber == null) {
                InternetMetadataBrowseTarget(entity, entityId)
            } else null
        }

    val trackLookup: InternetMetadataTrackLookup?
        get() {
            if (!knownEnvelope || canBrowseTracks || artist == null) return null
            val query = downloadSearchQuery ?: return null
            val entity = knownEntity ?: return null
            val coherent = when (entity) {
                InternetMetadataDiscoveryEntity.RECORDING -> recordingId == entityId && releaseId == null &&
                    discNumber == null && trackNumber == null
                InternetMetadataDiscoveryEntity.RELEASE_TRACK -> releaseId != null && recordingId != null
                else -> false
            }
            return if (coherent) InternetMetadataTrackLookup(entity, entityId, releaseId, query) else null
        }

    private val knownEnvelope: Boolean
        get() = pageAllowsKnownActions && source == "MusicBrainz" && availability == "METADATA_ONLY" &&
            !acquisitionAllowed
}

class InternetMetadataDiscoveryPage internal constructor(
    val contractVersion: String,
    val source: String,
    val sourceScope: String,
    val availability: String,
    val acquisitionAllowed: Boolean,
    val capabilities: JsonObject,
    val items: List<InternetMetadataDiscoveryCard>,
    val limit: Int,
    val offset: Int,
    val totalCount: Int,
    val nextOffset: Int?,
    val truncated: Boolean,
    val rawPayload: JsonObject,
    val knownActionsAllowed: Boolean,
) {
    val directAcquisitionAllowed: Boolean get() = false
}

object InternetMetadataDiscoveryCodec {
    const val CONTRACT_VERSION: String = "music-discovery-v1"
    const val MAX_PAGE_BYTES: Int = 1_048_576
    const val MAX_CARD_BYTES: Int = 16_384

    fun decode(document: String): InternetMetadataDiscoveryPage = decode(
        MetadataDiscoveryJson.parseObject(document, MAX_PAGE_BYTES),
    )

    fun decode(document: JsonObject): InternetMetadataDiscoveryPage {
        val root = MetadataDiscoveryJson.snapshot(document, MAX_PAGE_BYTES)
        val contract = MetadataDiscoveryJson.requiredString(root, "contract_version", 100)
        val source = MetadataDiscoveryJson.requiredString(root, "source", 100)
        val scope = MetadataDiscoveryJson.requiredString(root, "source_scope", 100)
        val availability = MetadataDiscoveryJson.requiredString(root, "availability", 100)
        val acquisition = MetadataDiscoveryJson.requiredBoolean(root, "acquisition_allowed")
        val capabilities = MetadataDiscoveryJson.requiredObject(root, "capabilities")
        val knownActions = contract == CONTRACT_VERSION && source == "MusicBrainz" && scope == "INTERNET" &&
            availability == "METADATA_ONLY" && !acquisition &&
            capabilities.keys == setOf("direct_acquisition", "track_source_search") &&
            MetadataDiscoveryJson.optionalBoolean(capabilities, "direct_acquisition") == false &&
            MetadataDiscoveryJson.optionalBoolean(capabilities, "track_source_search") == true
        val limit = MetadataDiscoveryJson.requiredInt(root, "limit", 1, 50)
        val offset = MetadataDiscoveryJson.requiredInt(root, "offset", 0, 1000)
        val total = MetadataDiscoveryJson.requiredInt(root, "total_count", 0, 1_000_000)
        val next = MetadataDiscoveryJson.optionalInt(root, "next_offset", 0, 1000)
        val truncated = MetadataDiscoveryJson.requiredBoolean(root, "truncated")
        val rows = root["items"] as? JsonArray ?: MetadataDiscoveryJson.invalid()
        MetadataDiscoveryJson.check(rows.size <= limit && rows.size <= total)
        MetadataDiscoveryJson.check(rows.isEmpty() || offset + rows.size <= total)
        if (next != null) {
            // next_offset counts raw provider rows before UUID deduplication, not displayed cards.
            MetadataDiscoveryJson.check(!truncated && next > offset && next <= offset + limit &&
                next >= offset + rows.size && next < total)
        } else if (!truncated) {
            MetadataDiscoveryJson.check(total <= offset + limit)
        }
        val items = rows.map { row ->
            decodeCard(row as? JsonObject ?: MetadataDiscoveryJson.invalid(), knownActions)
        }
        MetadataDiscoveryJson.check(items.map { it.id }.toSet().size == items.size)
        return InternetMetadataDiscoveryPage(
            contract, source, scope, availability, acquisition, capabilities, items,
            limit, offset, total, next, truncated, root, knownActions,
        )
    }

    private fun decodeCard(root: JsonObject, pageAllowsKnownActions: Boolean): InternetMetadataDiscoveryCard {
        MetadataDiscoveryJson.checkSize(root, MAX_CARD_BYTES)
        val source = MetadataDiscoveryJson.requiredString(root, "source", 100)
        val entityType = MetadataDiscoveryJson.requiredString(root, "entity_type", 100)
        val entityId = MetadataDiscoveryJson.requiredString(root, "entity_id", 128)
        val id = MetadataDiscoveryJson.requiredString(root, "id", 256)
        val release = MetadataDiscoveryJson.optionalString(root, "release_id", 128)
        val recording = MetadataDiscoveryJson.optionalString(root, "recording_id", 128)
        if (source == "MusicBrainz" && InternetMetadataDiscoveryEntity.known(entityType) != null) {
            MetadataDiscoveryJson.uuid(entityId)
            release?.let(MetadataDiscoveryJson::uuid)
            recording?.let(MetadataDiscoveryJson::uuid)
            MetadataDiscoveryJson.check(id == "musicbrainz:$entityType:$entityId")
        }
        val date = MetadataDiscoveryJson.optionalString(root, "release_date", 10)
        date?.let(MetadataDiscoveryJson::partialDate)
        return InternetMetadataDiscoveryCard(
            id = id,
            entityType = entityType,
            entityId = entityId,
            title = MetadataDiscoveryJson.requiredString(root, "title", 500),
            artist = MetadataDiscoveryJson.optionalString(root, "artist", 500),
            releaseId = release,
            recordingId = recording,
            releaseDate = date,
            country = MetadataDiscoveryJson.optionalString(root, "country", 500),
            disambiguation = MetadataDiscoveryJson.optionalString(root, "disambiguation", 500),
            durationMs = MetadataDiscoveryJson.optionalInt(root, "duration_ms", 1, 86_400_000),
            discNumber = MetadataDiscoveryJson.optionalInt(root, "disc_number", 1, 1000),
            trackNumber = MetadataDiscoveryJson.optionalInt(root, "track_number", 1, 10_000),
            recordingTitle = MetadataDiscoveryJson.optionalString(root, "recording_title", 500),
            source = source,
            availability = MetadataDiscoveryJson.requiredString(root, "availability", 100),
            acquisitionAllowed = MetadataDiscoveryJson.requiredBoolean(root, "acquisition_allowed"),
            canBrowseTracks = MetadataDiscoveryJson.requiredBoolean(root, "can_browse_tracks"),
            downloadSearchQuery = MetadataDiscoveryJson.optionalString(root, "download_search_query", 200),
            rawPayload = root,
            pageAllowsKnownActions = pageAllowsKnownActions,
        )
    }
}

/** Shared strict primitives keep decoder failures stable and never include personal payloads. */
internal object MetadataDiscoveryJson {
    const val RESPONSE_INVALID: String = "SERVER_RESPONSE_INVALID"
    private val uuidPattern = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    private val datePattern = Regex("[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?")

    fun invalid(): Nothing = throw IllegalArgumentException(RESPONSE_INVALID)

    fun check(value: Boolean) {
        if (!value) invalid()
    }

    fun checkSize(root: JsonObject, maximum: Int) {
        check(root.toString().toByteArray(Charsets.UTF_8).size <= maximum)
    }

    fun snapshot(root: JsonObject, maximum: Int): JsonObject = parseObject(root.toString(), maximum)

    fun parseObject(document: String, maximum: Int): JsonObject {
        check(document.toByteArray(Charsets.UTF_8).size <= maximum)
        return try {
            Json.parseToJsonElement(document) as? JsonObject ?: invalid()
        } catch (_: IllegalArgumentException) {
            invalid()
        }
    }

    fun requiredObject(root: JsonObject, name: String): JsonObject = root[name] as? JsonObject ?: invalid()

    fun requiredString(root: JsonObject, name: String, maximum: Int): String =
        optionalString(root, name, maximum) ?: invalid()

    fun optionalString(root: JsonObject, name: String, maximum: Int): String? {
        val element = root[name] ?: return null
        if (element == JsonNull) return null
        val primitive = element as? JsonPrimitive ?: invalid()
        check(primitive.isString)
        val value = primitive.content
        check(value.isNotBlank() && value.length <= maximum && value.none(Char::isISOControl))
        return value
    }

    fun requiredBoolean(root: JsonObject, name: String): Boolean = optionalBoolean(root, name) ?: invalid()

    fun optionalBoolean(root: JsonObject, name: String): Boolean? {
        val element = root[name] ?: return null
        if (element == JsonNull) return null
        val primitive = element as? JsonPrimitive ?: invalid()
        check(!primitive.isString)
        return primitive.booleanOrNull ?: invalid()
    }

    fun requiredInt(root: JsonObject, name: String, minimum: Int, maximum: Int): Int =
        optionalInt(root, name, minimum, maximum) ?: invalid()

    fun optionalInt(root: JsonObject, name: String, minimum: Int, maximum: Int): Int? {
        val element = root[name] ?: return null
        if (element == JsonNull) return null
        val primitive = element as? JsonPrimitive ?: invalid()
        check(!primitive.isString)
        val value = primitive.intOrNull ?: invalid()
        check(value in minimum..maximum)
        return value
    }

    fun uuid(value: String): String {
        check(uuidPattern.matches(value))
        return UUID.fromString(value).toString()
    }

    fun partialDate(value: String) {
        check(datePattern.matches(value))
        val parts = value.split('-').map(String::toInt)
        check(parts[0] in 1..9999)
        try {
            LocalDate.of(parts[0], parts.getOrElse(1) { 1 }, parts.getOrElse(2) { 1 })
        } catch (_: java.time.DateTimeException) {
            invalid()
        }
    }
}
