package app.autplay.application.server

import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

enum class MusicCatalogueContextEntity(val wireValue: String) {
    RECORDING("recording"),
    RELEASE_TRACK("release_track");

    companion object {
        fun known(value: String): MusicCatalogueContextEntity? = entries.firstOrNull { it.wireValue == value }
    }
}

/** The request carries selected identities only. All descriptive facts come from server hydration. */
class MusicCatalogueContextRequest private constructor(
    val entity: MusicCatalogueContextEntity,
    val entityId: String,
    val releaseId: String?,
) {
    fun document(): JsonObject = buildJsonObject {
        put("entity_type", entity.wireValue)
        put("entity_id", entityId)
        put("release_id", releaseId?.let(::JsonPrimitive) ?: JsonNull)
    }

    companion object {
        fun recording(entityId: String): MusicCatalogueContextRequest = MusicCatalogueContextRequest(
            MusicCatalogueContextEntity.RECORDING, MetadataDiscoveryJson.uuid(entityId), null,
        )

        fun releaseTrack(entityId: String, releaseId: String): MusicCatalogueContextRequest = MusicCatalogueContextRequest(
            MusicCatalogueContextEntity.RELEASE_TRACK,
            MetadataDiscoveryJson.uuid(entityId), MetadataDiscoveryJson.uuid(releaseId),
        )

        fun fromCard(card: InternetMetadataDiscoveryCard): MusicCatalogueContextRequest? {
            val selected = card.trackLookup ?: return null
            return when (selected.entity) {
                InternetMetadataDiscoveryEntity.RECORDING -> recording(selected.entityId)
                InternetMetadataDiscoveryEntity.RELEASE_TRACK -> releaseTrack(
                    selected.entityId, selected.releaseId ?: return null,
                )
                else -> null
            }
        }
    }
}

class MusicCatalogueLookupMetadata internal constructor(
    val schemaVersion: Int,
    val entityType: String,
    val entityId: String,
    val recordingMbid: String?,
    val releaseMbid: String?,
    val title: String,
    val artist: String?,
    val album: String?,
    val releaseDate: String?,
    val durationMs: Int?,
    val discNumber: Int?,
    val trackNumber: Int?,
    val recordingTitle: String?,
    val disambiguation: String?,
    val rawPayload: JsonObject,
    val knownShape: Boolean,
)

/** A context receipt is lookup-only evidence, not native metadata or a selected acquisition. */
class MusicCatalogueContext internal constructor(
    val contractVersion: String,
    val catalogueContextId: String,
    val expiresAt: Instant,
    val source: String,
    val availability: String,
    val acquisitionAllowed: Boolean,
    val lookupMetadata: MusicCatalogueLookupMetadata,
    val rawPayload: JsonObject,
    val knownActionsAllowed: Boolean,
) {
    val directAcquisitionAllowed: Boolean get() = false

    /** TTL applies to fresh search admission; the server owns operation replay after expiry. */
    fun admissionContextId(now: Instant): String? = catalogueContextId.takeIf {
        knownActionsAllowed && now < expiresAt
    }

    /** Validate the hydrated response against the actual selected identity before a new search. */
    fun matches(request: MusicCatalogueContextRequest): Boolean = knownActionsAllowed &&
        lookupMetadata.entityType == request.entity.wireValue &&
        lookupMetadata.entityId == request.entityId && lookupMetadata.releaseMbid == request.releaseId
}

object MusicCatalogueContextCodec {
    const val CONTRACT_VERSION: String = "music-catalogue-context-v1"
    const val MAX_LOOKUP_METADATA_BYTES: Int = 16_384
    const val MAX_RESPONSE_BYTES: Int = 20_480

    fun decode(document: String): MusicCatalogueContext = decode(
        MetadataDiscoveryJson.parseObject(document, MAX_RESPONSE_BYTES),
    )

    fun decode(document: JsonObject): MusicCatalogueContext {
        val root = MetadataDiscoveryJson.snapshot(document, MAX_RESPONSE_BYTES)
        val contract = MetadataDiscoveryJson.requiredString(root, "contract_version", 100)
        val contextId = MetadataDiscoveryJson.uuid(
            MetadataDiscoveryJson.requiredString(root, "catalogue_context_id", 36),
        )
        val expiresAt = utcInstant(MetadataDiscoveryJson.requiredString(root, "expires_at", 64))
        val source = MetadataDiscoveryJson.requiredString(root, "source", 100)
        val availability = MetadataDiscoveryJson.requiredString(root, "availability", 100)
        val acquisition = MetadataDiscoveryJson.requiredBoolean(root, "acquisition_allowed")
        val lookup = decodeLookup(MetadataDiscoveryJson.requiredObject(root, "lookup_metadata"), source)
        val actions = contract == CONTRACT_VERSION && source == "MusicBrainz" &&
            availability == "METADATA_ONLY" && !acquisition && lookup.knownShape
        return MusicCatalogueContext(
            contract, contextId, expiresAt, source, availability, acquisition, lookup, root, actions,
        )
    }

    private fun decodeLookup(root: JsonObject, source: String): MusicCatalogueLookupMetadata {
        MetadataDiscoveryJson.checkSize(root, MAX_LOOKUP_METADATA_BYTES)
        val schema = MetadataDiscoveryJson.requiredInt(root, "schema_version", 0, Int.MAX_VALUE)
        val entityType = MetadataDiscoveryJson.requiredString(root, "entity_type", 100)
        val entity = MusicCatalogueContextEntity.known(entityType)
        val entityId = MetadataDiscoveryJson.requiredString(root, "entity_id", 128)
        val recording = MetadataDiscoveryJson.optionalString(root, "recording_mbid", 128)
        val release = MetadataDiscoveryJson.optionalString(root, "release_mbid", 128)
        val title = MetadataDiscoveryJson.requiredString(root, "title", 500)
        val artist = MetadataDiscoveryJson.optionalString(root, "artist", 500)
        val album = MetadataDiscoveryJson.optionalString(root, "album", 500)
        val date = MetadataDiscoveryJson.optionalString(root, "release_date", 10)
        val disc = MetadataDiscoveryJson.optionalInt(root, "disc_number", 1, 1000)
        val track = MetadataDiscoveryJson.optionalInt(root, "track_number", 1, 10_000)
        val known = source == "MusicBrainz" && schema == 1 && entity != null
        if (known) {
            MetadataDiscoveryJson.uuid(entityId)
            MetadataDiscoveryJson.uuid(recording ?: MetadataDiscoveryJson.invalid())
            release?.let(MetadataDiscoveryJson::uuid)
            when (entity) {
                MusicCatalogueContextEntity.RECORDING -> MetadataDiscoveryJson.check(
                    entityId == recording && release == null && album == null && disc == null && track == null,
                )
                MusicCatalogueContextEntity.RELEASE_TRACK -> MetadataDiscoveryJson.check(release != null && album != null)
            }
            listOfNotNull(title, artist, album).forEach { MetadataDiscoveryJson.check(it == it.trim()) }
        }
        date?.let(MetadataDiscoveryJson::partialDate)
        val recordingTitle = MetadataDiscoveryJson.optionalString(root, "recording_title", 500)
        val disambiguation = MetadataDiscoveryJson.optionalString(root, "disambiguation", 500)
        if (known) listOfNotNull(recordingTitle, disambiguation).forEach { MetadataDiscoveryJson.check(it == it.trim()) }
        return MusicCatalogueLookupMetadata(
            schema, entityType, entityId, recording, release, title, artist, album, date,
            MetadataDiscoveryJson.optionalInt(root, "duration_ms", 1, 86_400_000),
            disc, track, recordingTitle, disambiguation, root, known,
        )
    }

    private fun utcInstant(value: String): Instant {
        MetadataDiscoveryJson.check(value.endsWith("Z") || value.endsWith("+00:00"))
        return try {
            val timestamp = OffsetDateTime.parse(value)
            MetadataDiscoveryJson.check(timestamp.offset == ZoneOffset.UTC)
            timestamp.toInstant()
        } catch (_: java.time.DateTimeException) {
            MetadataDiscoveryJson.invalid()
        }
    }
}
