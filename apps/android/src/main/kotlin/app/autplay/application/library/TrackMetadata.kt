package app.autplay.application.library

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.TrackMetadataEntity
import kotlinx.serialization.json.*

data class TrackMetadata(val revision: Long, val state: String, val fields: JsonObject,
    val provenance: JsonObject, val candidates: List<JsonObject>, val artworkSha256: String?, val payload: JsonObject) {
    /** Explicit null stays empty; absence falls back to the original file description. */
    fun text(name: String, fallback: String? = null): String? = if (fields.containsKey(name))
        (fields[name] as? JsonPrimitive)?.contentOrNull else fallback

    companion object {
        fun decode(root: JsonObject): TrackMetadata {
            require(root.toString().toByteArray().size <= 140_000)
            val revision = root["revision"]?.jsonPrimitive?.longOrNull ?: error("METADATA_INVALID")
            require(revision >= 0)
            val state = root["state"]?.jsonPrimitive?.content ?: error("METADATA_INVALID")
            require(state in setOf("MISSING", "LOCAL", "QUEUED", "READY", "REVIEW", "NOT_FOUND", "RETRY", "FAILED"))
            val fields = root["fields"] as? JsonObject ?: JsonObject(emptyMap())
            require(fields.size <= 24)
            fields.values.forEach { require(it.toString().length <= 2000) }
            val sources = root["provenance"] as? JsonObject ?: JsonObject(emptyMap())
            val candidates = (root["candidates"] as? JsonArray).orEmpty().map { it.jsonObject }
            require(candidates.size <= 5)
            val sha = (root["artwork_sha256"] as? JsonPrimitive)?.contentOrNull
            require(sha == null || sha.matches(Regex("[a-f0-9]{64}")))
            return TrackMetadata(revision, state, fields, sources, candidates, sha, root)
        }
    }
}

internal fun TrackMetadataEntity.decoded(): TrackMetadata = TrackMetadata.decode(Json.parseToJsonElement(payloadJson).jsonObject)

/** The caller owns the Room transaction, including the FTS refresh. */
internal suspend fun AutPlayDatabase.projectMetadata(profile: String, track: String, root: JsonObject): Boolean {
    val parsed = runCatching { TrackMetadata.decode(root) }.getOrNull() ?: return false
    val ref = libraryDao().trackRef(track) ?: return false
    if (ref.serverProfileId != profile) return false
    val previous = trackMetadataDao().get(profile, track)
    if (previous != null && previous.revision > parsed.revision && parsed.state != "LOCAL") return true
    val prior = previous?.decoded()
    val olderLocalFields = prior?.fields.orEmpty().filterKeys { field ->
        (prior?.provenance?.get(field) as? JsonObject)?.get("source_id")?.jsonPrimitive?.contentOrNull == "local-file"
    }
    val localFields = if (parsed.state == "LOCAL") parsed.fields else
        (prior?.payload?.get("local_fields") as? JsonObject) ?: JsonObject(olderLocalFields)
    val localSources = if (parsed.state == "LOCAL") parsed.provenance else
        (prior?.payload?.get("local_provenance") as? JsonObject) ?: JsonObject(prior?.provenance.orEmpty().filterKeys { it in olderLocalFields })
    val server = if (parsed.state == "LOCAL" && prior != null && prior.revision > 0) prior else parsed
    val selectedEdition = server.provenance.values.any { value ->
        val evidence = value as? JsonObject
        evidence?.get("source")?.jsonPrimitive?.contentOrNull == "MUSICBRAINZ" && evidence["locked"]?.jsonPrimitive?.booleanOrNull == true
    }
    val serverFields = server.fields.filterKeys { key -> (server.provenance[key] as? JsonObject)?.get("source_id")?.jsonPrimitive?.contentOrNull != "local-file" }
    val fields = JsonObject((if (selectedEdition) emptyMap() else localFields) + serverFields)
    val provenance = JsonObject((if (selectedEdition) emptyMap() else localSources.filterKeys { it !in serverFields }) + server.provenance.filterKeys { it in serverFields })
    val localArt = if (parsed.state == "LOCAL") parsed.payload["artwork_sha256"] else prior?.payload?.get("local_artwork_sha256")
        ?: if (prior?.state == "LOCAL") prior.payload["artwork_sha256"] else null
    val priorLocalArt = (prior?.payload?.get("local_artwork_sha256") as? JsonPrimitive)?.contentOrNull
    val serverArt = server.artworkSha256?.takeUnless { parsed.state == "LOCAL" && (server.state == "LOCAL" || it == priorLocalArt) }
    val art = serverArt ?: if (selectedEdition) null else (localArt as? JsonPrimitive)?.contentOrNull
    val effective = JsonObject(server.payload + mapOf("fields" to fields, "provenance" to provenance,
        "local_fields" to localFields, "local_provenance" to localSources,
        "local_reader_version" to (if (parsed.state == "LOCAL") parsed.payload["local_reader_version"] ?: JsonNull else prior?.payload?.get("local_reader_version") ?: JsonNull),
        "artwork_sha256" to (art?.let(::JsonPrimitive) ?: JsonNull)) +
        (localArt?.let { mapOf("local_artwork_sha256" to it) } ?: emptyMap()))
    trackMetadataDao().upsert(TrackMetadataEntity(profile, track, server.revision, effective.toString(),
        art, System.currentTimeMillis()))
    return true
}
