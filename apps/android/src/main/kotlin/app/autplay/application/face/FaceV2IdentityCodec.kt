package app.autplay.application.face

import app.autplay.domain.face.FaceTimelineIdentityV2
import app.autplay.domain.face.FaceV2IdentityException
import app.autplay.domain.face.SourcePresentationMapV1
import java.security.MessageDigest
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

/** Face v2 semantic key. It never decodes or aliases a historical v1 timeline. */
object FaceV2IdentityCodec {
    private val integerToken = Regex("-?(0|[1-9][0-9]*)")
    private val fields = setOf(
        "schema_version", "timeline_codec_version", "source_timebase", "recording_id",
        "audio_variant_id", "source_sha256", "decoded_sample_rate", "decoded_sample_count",
        "source_presentation_map_sha256", "embedding_model_id", "embedding_manifest_sha256",
        "semantic_interpreter_id", "interpreter_manifest_sha256", "preprocessing_sha256",
        "calibration_sha256", "execution_profile_sha256",
    )

    fun decode(data: ByteArray, presentationMap: SourcePresentationMapV1): FaceTimelineIdentityV2 {
        if (data.isEmpty() || data.size > 4096) fail()
        try {
            val root = boundedFaceJson(data) as? JsonObject ?: fail()
            if (root.keys != fields || integer(root.getValue("schema_version")) != 2L ||
                integer(root.getValue("timeline_codec_version")) != 2L ||
                string(root.getValue("source_timebase")) != "DECODED_SAMPLE_INDEX_V2"
            ) fail()
            val result = FaceTimelineIdentityV2(
                string(root.getValue("recording_id")),
                string(root.getValue("audio_variant_id")),
                string(root.getValue("source_sha256")),
                integer(root.getValue("decoded_sample_rate")),
                integer(root.getValue("decoded_sample_count")),
                string(root.getValue("source_presentation_map_sha256")),
                string(root.getValue("embedding_model_id")),
                string(root.getValue("embedding_manifest_sha256")),
                string(root.getValue("semantic_interpreter_id")),
                string(root.getValue("interpreter_manifest_sha256")),
                string(root.getValue("preprocessing_sha256")),
                string(root.getValue("calibration_sha256")),
                string(root.getValue("execution_profile_sha256")),
            )
            if (!encode(result).contentEquals(data)) fail()
            if (result.sourceSha256 != presentationMap.encodedSourceSha256 ||
                result.decodedSampleRate != presentationMap.decodedSampleRate ||
                result.decodedSampleCount != presentationMap.decodedSampleCount ||
                result.sourcePresentationMapSha256 != SourcePresentationMapCodecV1.sha256(presentationMap)
            ) fail()
            return result
        } catch (_: IllegalArgumentException) {
            fail()
        }
    }

    fun encode(value: FaceTimelineIdentityV2): ByteArray {
        val document = buildJsonObject {
            put("schema_version", 2)
            put("timeline_codec_version", 2)
            put("source_timebase", "DECODED_SAMPLE_INDEX_V2")
            put("recording_id", value.recordingId)
            put("audio_variant_id", value.audioVariantId)
            put("source_sha256", value.sourceSha256)
            put("decoded_sample_rate", value.decodedSampleRate)
            put("decoded_sample_count", value.decodedSampleCount)
            put("source_presentation_map_sha256", value.sourcePresentationMapSha256)
            put("embedding_model_id", value.embeddingModelId)
            put("embedding_manifest_sha256", value.embeddingManifestSha256)
            put("semantic_interpreter_id", value.semanticInterpreterId)
            put("interpreter_manifest_sha256", value.interpreterManifestSha256)
            put("preprocessing_sha256", value.preprocessingSha256)
            put("calibration_sha256", value.calibrationSha256)
            put("execution_profile_sha256", value.executionProfileSha256)
        }
        val result = try { JsonCanonicalizer(document.toString()).encodedUTF8 }
        catch (_: IllegalArgumentException) { fail() }
        catch (_: java.io.IOException) { fail() }
        if (result.size > 4096) fail()
        return result
    }

    fun semanticKey(value: FaceTimelineIdentityV2): String = MessageDigest.getInstance("SHA-256")
        .apply { update("autplay.face.semantic-key.v2\u0000".toByteArray(Charsets.UTF_8)) }
        .digest(encode(value)).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun string(value: JsonElement): String = (value as? JsonPrimitive ?: fail()).let {
        if (!it.isString) fail()
        it.content
    }

    private fun integer(value: JsonElement): Long = (value as? JsonPrimitive ?: fail()).let {
        if (it.isString || !it.content.matches(integerToken)) fail()
        it.content.toLongOrNull() ?: fail()
    }

    private fun fail(): Nothing = throw FaceV2IdentityException()
}
