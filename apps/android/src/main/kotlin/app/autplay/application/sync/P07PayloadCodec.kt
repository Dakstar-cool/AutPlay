package app.autplay.application.sync

import java.nio.charset.StandardCharsets
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * P07's additive payload codec. It accepts only safe JSON values, sorts object names for stable
 * hashing, and deliberately keeps unknown attribution members as opaque data.
 */
object P07PayloadCodec {
    private const val MAX_BYTES = 262_144
    private const val MAX_DEPTH = 32
    private val safeName = Regex("^[a-z][a-z0-9_]{0,99}$")
    private val forbidden = Regex("(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|raw_model_features|model_features|feature_vector|debug_text|personal_debug|raw_audio|audio_bytes)(_|$)")
    private val json = Json { isLenient = false; ignoreUnknownKeys = false; allowSpecialFloatingPointValues = false }

    fun canonicalize(payload: String): String {
        if (payload.toByteArray(StandardCharsets.UTF_8).size > MAX_BYTES) {
            throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.PAYLOAD_TOO_LARGE)
        }
        val canonical = try {
            StrictJsonScanner(payload).scanDocument()
            JsonCanonicalizer(payload).encodedString
        } catch (error: LocalIntentPayloadException) {
            throw error
        } catch (_: Exception) {
            throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.MALFORMED_JSON)
        }
        val value = try { json.parseToJsonElement(canonical) } catch (_: Exception) {
            throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.MALFORMED_JSON)
        }
        if (value !is JsonObject) throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.TOP_LEVEL_NOT_OBJECT)
        validate(value, 0)
        return canonical
    }

    private fun validate(value: JsonElement, depth: Int) {
        if (depth > MAX_DEPTH) throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.MAX_NESTING_EXCEEDED)
        when (value) {
            is JsonObject -> value.forEach { (key, child) ->
                if (!safeName.matches(key) || forbidden.containsMatchIn(key)) {
                    throw LocalIntentPayloadException(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME)
                }
                validate(child, depth + 1)
            }
            is JsonArray -> value.forEach { validate(it, depth + 1) }
            is JsonNull, is JsonPrimitive -> Unit
        }
    }
}
