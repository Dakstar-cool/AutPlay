package app.autplay.application.face

import app.autplay.domain.face.PresentationSegmentV1
import app.autplay.domain.face.SourcePresentationMapException
import app.autplay.domain.face.SourcePresentationMapV1
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

/** Canonical map identity shared with the Python v2 publisher. No v1 timeline path uses it. */
object SourcePresentationMapCodecV1 {
    private val integerToken = Regex("-?(0|[1-9][0-9]*)")
    private val fields = setOf(
        "schema_version", "kind", "encoded_source_sha256", "decoded_sample_rate",
        "decoded_sample_count", "decoder_id", "decoder_version", "probe_id", "probe_version",
        "leading_trim_samples", "trailing_trim_samples", "encoder_delay_samples",
        "encoder_padding_samples", "segments",
    )

    fun decode(data: ByteArray): SourcePresentationMapV1 {
        if (data.isEmpty() || data.size > 65_536) fail()
        try {
            val root = boundedFaceJson(data) as? JsonObject ?: fail()
            if (root.keys != fields || integer(root.getValue("schema_version")) != 1L ||
                string(root.getValue("kind")) != "SOURCE_PRESENTATION_MAP_V1"
            ) fail()
            val segments = root.getValue("segments") as? JsonArray ?: fail()
            if (segments.size !in 1..256) fail()
            val parsed = segments.map { item ->
                val row = item as? JsonObject ?: fail()
                if (row.keys != setOf("presentation_start_us", "presentation_end_us", "source_start_sample")) fail()
                PresentationSegmentV1(
                    integer(row.getValue("presentation_start_us")),
                    integer(row.getValue("presentation_end_us")),
                    integer(row.getValue("source_start_sample")),
                )
            }
            val result = SourcePresentationMapV1(
                string(root.getValue("encoded_source_sha256")),
                integer(root.getValue("decoded_sample_rate")),
                integer(root.getValue("decoded_sample_count")),
                string(root.getValue("decoder_id")),
                string(root.getValue("decoder_version")),
                string(root.getValue("probe_id")),
                string(root.getValue("probe_version")),
                integer(root.getValue("leading_trim_samples")),
                integer(root.getValue("trailing_trim_samples")),
                nullableInteger(root.getValue("encoder_delay_samples")),
                nullableInteger(root.getValue("encoder_padding_samples")),
                parsed,
            )
            if (!encode(result).contentEquals(data)) fail()
            return result
        } catch (_: IllegalArgumentException) {
            fail()
        }
    }

    fun encode(value: SourcePresentationMapV1): ByteArray {
        val document = buildJsonObject {
            put("schema_version", 1)
            put("kind", "SOURCE_PRESENTATION_MAP_V1")
            put("encoded_source_sha256", value.encodedSourceSha256)
            put("decoded_sample_rate", value.decodedSampleRate)
            put("decoded_sample_count", value.decodedSampleCount)
            put("decoder_id", value.decoderId)
            put("decoder_version", value.decoderVersion)
            put("probe_id", value.probeId)
            put("probe_version", value.probeVersion)
            put("leading_trim_samples", value.leadingTrimSamples)
            put("trailing_trim_samples", value.trailingTrimSamples)
            put("encoder_delay_samples", value.encoderDelaySamples?.let(::JsonPrimitive) ?: JsonNull)
            put("encoder_padding_samples", value.encoderPaddingSamples?.let(::JsonPrimitive) ?: JsonNull)
            put("segments", JsonArray(value.segments.map { segment -> buildJsonObject {
                put("presentation_start_us", segment.presentationStartUs)
                put("presentation_end_us", segment.presentationEndUs)
                put("source_start_sample", segment.sourceStartSample)
            } }))
        }
        val encoded = try { JsonCanonicalizer(document.toString()).encodedUTF8 }
        catch (_: IllegalArgumentException) { fail() }
        catch (_: java.io.IOException) { fail() }
        if (encoded.size > 65_536) fail()
        return encoded
    }

    fun sha256(value: SourcePresentationMapV1): String = MessageDigest.getInstance("SHA-256")
        .digest(encode(value)).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun string(value: JsonElement): String = (value as? JsonPrimitive ?: fail()).let {
        if (!it.isString) fail()
        it.content
    }

    private fun integer(value: JsonElement): Long = (value as? JsonPrimitive ?: fail()).let {
        if (it.isString || !it.content.matches(integerToken)) fail()
        it.content.toLongOrNull() ?: fail()
    }

    private fun nullableInteger(value: JsonElement): Long? =
        if (value == JsonNull) null else integer(value)

    private fun fail(): Nothing = throw SourcePresentationMapException()
}
