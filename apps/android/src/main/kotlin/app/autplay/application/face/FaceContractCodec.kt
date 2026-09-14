package app.autplay.application.face

import app.autplay.domain.face.FaceAxis
import app.autplay.domain.face.FaceContractException
import app.autplay.domain.face.FaceEvent
import app.autplay.domain.face.FaceKeyframe
import app.autplay.domain.face.FaceProjectionBinding
import app.autplay.domain.face.FaceSemanticState
import app.autplay.domain.face.FaceTimelineIdentity
import app.autplay.domain.face.TemporalFaceTimeline
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

/** Pure bounded wire codec. Hash equality is integrity, never authorization. */
object FaceContractCodec {
    private val integerToken = Regex("-?(0|[1-9][0-9]*)")

    fun decodeTimeline(data: ByteArray): TemporalFaceTimeline {
        val root = row(boundedFaceJson(data), "schema_version", "identity", "track_character", "keyframes", "events")
        val frames = array(root.getValue("keyframes")).map {
            val frame = row(it, "time_ms", "state")
            FaceKeyframe(integer(frame.getValue("time_ms")), state(frame.getValue("state")))
        }
        val events = array(root.getValue("events")).map {
            val event = row(it, "time_ms", "event_type", "strength", "confidence")
            FaceEvent(integer(event.getValue("time_ms")), string(event.getValue("event_type")),
                number(event.getValue("strength")), number(event.getValue("confidence")))
        }
        version(root.getValue("schema_version"))
        return TemporalFaceTimeline(identity(root.getValue("identity")),
            state(root.getValue("track_character")), frames, events).also { encodeTimeline(it) }
    }

    fun decodeProjection(data: ByteArray): FaceProjectionBinding {
        val root = row(boundedFaceJson(data), "server_profile_id", "user_id", "identity", "semantic_key", "result_hash", "activation_epoch")
        return FaceProjectionBinding(string(root.getValue("server_profile_id")),
            string(root.getValue("user_id")), identity(root.getValue("identity")),
            string(root.getValue("semantic_key")), string(root.getValue("result_hash")),
            integer(root.getValue("activation_epoch"))).also {
            if (semanticKey(it.identity) != it.semanticKey) fail("ml.face.identity_mismatch")
        }
    }

    fun encodeTimeline(timeline: TemporalFaceTimeline): ByteArray = canonical(timelineDocument(timeline))

    fun semanticKey(identity: FaceTimelineIdentity): String = digest(
        "autplay.face.semantic-key.v1\u0000", canonical(identityDocument(identity)),
    )

    fun resultHash(timeline: TemporalFaceTimeline): String = digest(
        "autplay.face.timeline-result.v1\u0000", encodeTimeline(timeline),
    )

    fun verifyProjection(timeline: TemporalFaceTimeline, binding: FaceProjectionBinding) {
        if (timeline.identity != binding.identity || semanticKey(timeline.identity) != binding.semanticKey) {
            fail("ml.face.identity_mismatch")
        }
        if (resultHash(timeline) != binding.resultHash) fail("ml.face.integrity_mismatch")
    }

    fun requireSameResult(existingHash: String, candidateHash: String) {
        if (!existingHash.matches(Regex("[0-9a-f]{64}")) || !candidateHash.matches(Regex("[0-9a-f]{64}"))) fail()
        if (existingHash != candidateHash) fail("ml.face.result_conflict")
    }

    private fun identity(value: JsonElement): FaceTimelineIdentity {
        val item = row(value, "schema_version", "recording_id", "audio_variant_id", "source_sha256",
            "source_duration_ms", "source_timebase", "embedding_model_id", "embedding_manifest_sha256",
            "semantic_interpreter_id", "interpreter_manifest_sha256", "preprocessing_sha256")
        version(item.getValue("schema_version"))
        return FaceTimelineIdentity(string(item.getValue("recording_id")), string(item.getValue("audio_variant_id")),
            string(item.getValue("source_sha256")), integer(item.getValue("source_duration_ms")),
            string(item.getValue("embedding_model_id")), string(item.getValue("embedding_manifest_sha256")),
            string(item.getValue("semantic_interpreter_id")), string(item.getValue("interpreter_manifest_sha256")),
            string(item.getValue("preprocessing_sha256")), sourceTimebase = string(item.getValue("source_timebase")))
    }

    private fun state(value: JsonElement): FaceSemanticState {
        val axes = row(value, "axes").getValue("axes") as? JsonObject ?: fail()
        if (axes.size > 64) fail()
        return FaceSemanticState(axes.mapValues { (_, axis) ->
            val item = row(axis, "value", "confidence", "abstained", "reason_code")
            FaceAxis(nullableNumber(item.getValue("value")), nullableNumber(item.getValue("confidence")),
                boolean(item.getValue("abstained")), item.getValue("reason_code").let {
                    if (it == JsonNull) null else string(it)
                })
        })
    }

    private fun identityDocument(value: FaceTimelineIdentity) = buildJsonObject {
        put("schema_version", value.schemaVersion)
        put("recording_id", value.recordingId)
        put("audio_variant_id", value.audioVariantId)
        put("source_sha256", value.sourceSha256)
        put("source_duration_ms", value.sourceDurationMs)
        put("source_timebase", value.sourceTimebase)
        put("embedding_model_id", value.embeddingModelId)
        put("embedding_manifest_sha256", value.embeddingManifestSha256)
        put("semantic_interpreter_id", value.semanticInterpreterId)
        put("interpreter_manifest_sha256", value.interpreterManifestSha256)
        put("preprocessing_sha256", value.preprocessingSha256)
    }

    private fun stateDocument(value: FaceSemanticState) = buildJsonObject {
        put("axes", JsonObject(value.axes.mapValues { (_, axis) -> buildJsonObject {
            put("value", axis.value?.let(::JsonPrimitive) ?: JsonNull)
            put("confidence", axis.confidence?.let(::JsonPrimitive) ?: JsonNull)
            put("abstained", axis.abstained)
            put("reason_code", axis.reasonCode?.let(::JsonPrimitive) ?: JsonNull)
        } }))
    }

    private fun timelineDocument(value: TemporalFaceTimeline) = buildJsonObject {
        put("schema_version", value.schemaVersion)
        put("identity", identityDocument(value.identity))
        put("track_character", stateDocument(value.trackCharacter))
        put("keyframes", JsonArray(value.keyframes.map { buildJsonObject {
            put("time_ms", it.timeMs)
            put("state", stateDocument(it.state))
        } }))
        put("events", JsonArray(value.events.map { buildJsonObject {
            put("time_ms", it.timeMs)
            put("event_type", it.eventType)
            put("strength", it.strength)
            put("confidence", it.confidence)
        } }))
    }

    private fun canonical(value: JsonElement): ByteArray {
        val result = try { JsonCanonicalizer(value.toString()).encodedUTF8 }
        catch (_: IllegalArgumentException) { fail() }
        catch (_: java.io.IOException) { fail() }
        if (result.size > MAX_FACE_BYTES) fail("ml.face.timeline_too_large")
        return result
    }

    private fun digest(domain: String, bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .apply { update(domain.toByteArray(Charsets.UTF_8)) }.digest(bytes)
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun row(value: JsonElement, vararg keys: String): JsonObject {
        val objectValue = value as? JsonObject ?: fail()
        if (objectValue.keys != keys.toSet()) fail()
        return objectValue
    }
    private fun array(value: JsonElement): JsonArray = (value as? JsonArray ?: fail()).also {
        if (it.size > 4096) fail()
    }
    private fun string(value: JsonElement): String = (value as? JsonPrimitive ?: fail()).let {
        if (!it.isString) fail()
        it.content
    }
    private fun integer(value: JsonElement): Long = (value as? JsonPrimitive ?: fail()).let {
        if (it.isString || !it.content.matches(integerToken)) fail()
        it.content.toLongOrNull() ?: fail()
    }
    private fun number(value: JsonElement): Double = (value as? JsonPrimitive ?: fail()).let {
        if (it.isString) fail()
        (it.content.toDoubleOrNull() ?: fail()).also { number -> if (!number.isFinite()) fail() }
    }
    private fun nullableNumber(value: JsonElement): Double? = if (value == JsonNull) null else number(value)
    private fun boolean(value: JsonElement): Boolean = (value as? JsonPrimitive ?: fail()).let {
        if (it.isString) fail()
        when (it.content) { "true" -> true; "false" -> false; else -> fail() }
    }
    private fun version(value: JsonElement) { if (integer(value) != 1L) fail() }
    private fun fail(code: String = "ml.face.invalid_timeline"): Nothing = throw FaceContractException(code)
}
