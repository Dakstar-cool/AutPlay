package app.autplay.application.face

import app.autplay.domain.face.FaceAxis
import app.autplay.domain.face.FaceSemanticState
import app.autplay.domain.face.FaceTimelineV2
import app.autplay.domain.face.FaceV2Event
import app.autplay.domain.face.FaceV2Keyframe
import app.autplay.domain.face.FaceV2TimelineException
import java.security.MessageDigest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

/** Separate canonical decoded-sample result codec; it never attaches a projection. */
object FaceV2TimelineCodec {
    const val CONTENT_TYPE = "application/vnd.autplay.face-timeline.v2+json"
    private val integerToken = Regex("-?(0|[1-9][0-9]*)")

    fun decode(data: ByteArray): FaceTimelineV2 {
        if (data.isEmpty() || data.size > 1_048_576) fail()
        try {
            val root = boundedFaceJson(data) as? JsonObject ?: fail()
            if (root.keys != setOf("schema_version", "identity", "source_presentation_map",
                    "track_character", "keyframes", "events") ||
                integer(root.getValue("schema_version")) != 2L
            ) fail()
            val map = SourcePresentationMapCodecV1.decode(
                canonical(root.getValue("source_presentation_map")))
            val identity = FaceV2IdentityCodec.decode(canonical(root.getValue("identity")), map)
            val frames = root.getValue("keyframes") as? JsonArray ?: fail()
            val events = root.getValue("events") as? JsonArray ?: fail()
            if (frames.size > 4096 || events.size > 4096) fail()
            val result = FaceTimelineV2(
                identity, map, state(root.getValue("track_character")),
                frames.map { item ->
                    val frame = row(item, "sample_index", "state")
                    FaceV2Keyframe(integer(frame.getValue("sample_index")), state(frame.getValue("state")))
                },
                events.map { item ->
                    val event = row(item, "sample_index", "event_type", "strength", "confidence")
                    FaceV2Event(integer(event.getValue("sample_index")), string(event.getValue("event_type")),
                        number(event.getValue("strength")), number(event.getValue("confidence")))
                },
            )
            if (!encode(result).contentEquals(data)) fail()
            return result
        } catch (_: IllegalArgumentException) {
            fail()
        }
    }

    fun encode(value: FaceTimelineV2): ByteArray {
        if (value.identity.sourcePresentationMapSha256 != SourcePresentationMapCodecV1.sha256(value.presentationMap)) fail()
        val document = buildJsonObject {
            put("schema_version", 2)
            put("identity", Json.parseToJsonElement(FaceV2IdentityCodec.encode(value.identity).decodeToString()))
            put("source_presentation_map", Json.parseToJsonElement(
                SourcePresentationMapCodecV1.encode(value.presentationMap).decodeToString()))
            put("track_character", stateDocument(value.trackCharacter))
            put("keyframes", JsonArray(value.keyframes.map { frame -> buildJsonObject {
                put("sample_index", frame.sampleIndex)
                put("state", stateDocument(frame.state))
            } }))
            put("events", JsonArray(value.events.map { event -> buildJsonObject {
                put("sample_index", event.sampleIndex)
                put("event_type", event.eventType)
                put("strength", event.strength)
                put("confidence", event.confidence)
            } }))
        }
        val encoded = canonical(document)
        if (encoded.size > 1_048_576) fail()
        return encoded
    }

    fun resultHash(value: FaceTimelineV2): String = MessageDigest.getInstance("SHA-256")
        .apply { update("autplay.face.timeline-result.v2\u0000".toByteArray(Charsets.UTF_8)) }
        .digest(encode(value)).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun stateDocument(value: FaceSemanticState): JsonObject = buildJsonObject {
        put("axes", buildJsonObject {
            for ((name, axis) in value.axes) put(name, buildJsonObject {
                put("value", axis.value?.let(::JsonPrimitive) ?: JsonNull)
                put("confidence", axis.confidence?.let(::JsonPrimitive) ?: JsonNull)
                put("abstained", axis.abstained)
                put("reason_code", axis.reasonCode?.let(::JsonPrimitive) ?: JsonNull)
            })
        })
    }

    private fun state(value: JsonElement): FaceSemanticState {
        val axes = row(value, "axes").getValue("axes") as? JsonObject ?: fail()
        if (axes.size > 64) fail()
        return FaceSemanticState(axes.mapValues { (_, raw) ->
            val axis = row(raw, "value", "confidence", "abstained", "reason_code")
            FaceAxis(nullableNumber(axis.getValue("value")), nullableNumber(axis.getValue("confidence")),
                boolean(axis.getValue("abstained")), nullableString(axis.getValue("reason_code")))
        })
    }

    private fun canonical(value: JsonElement): ByteArray = try {
        JsonCanonicalizer(value.toString()).encodedUTF8
    } catch (_: IllegalArgumentException) { fail() }
      catch (_: java.io.IOException) { fail() }

    private fun row(value: JsonElement, vararg keys: String): JsonObject =
        (value as? JsonObject ?: fail()).also { if (it.keys != keys.toSet()) fail() }

    private fun string(value: JsonElement): String = (value as? JsonPrimitive ?: fail()).let {
        if (!it.isString) fail()
        it.content
    }

    private fun nullableString(value: JsonElement): String? = if (value == JsonNull) null else string(value)

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

    private fun fail(): Nothing = throw FaceV2TimelineException()
}
