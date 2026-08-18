package app.autplay.application.sync

import java.nio.charset.StandardCharsets
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Stable failures for local-only intent insertion and journal materialization. */
enum class LocalIntentPayloadErrorCode {
    UNSUPPORTED_EVENT_TYPE,
    UNSUPPORTED_SCHEMA_VERSION,
    UNSUPPORTED_AGGREGATE_TYPE,
    MALFORMED_JSON,
    TOP_LEVEL_NOT_OBJECT,
    DUPLICATE_PROPERTY,
    MAX_NESTING_EXCEEDED,
    UNSAFE_PROPERTY_NAME,
    UNSUPPORTED_PAYLOAD_SHAPE,
    NON_STRING_VALUE,
    LONE_SURROGATE,
    PAYLOAD_TOO_LARGE,
    NON_CANONICAL_PAYLOAD,
}

/** Does not include untrusted payload text in its message or structured fields. */
class LocalIntentPayloadException(val code: LocalIntentPayloadErrorCode) :
    IllegalArgumentException(code.name)

/**
 * Fail-closed P05 policy for local intents that may later become immutable P04 client events.
 *
 * The initial writer supports exactly one v1 event.  Its object is intentionally string-only so
 * that its canonical form is RFC 8785 compatible without accepting a broader JSON dialect early.
 */
object LocalIntentPayloadPolicy {
    const val USER_TRACK_REF_CREATED = "USER_TRACK_REF_CREATED"
    const val USER_TRACK_REF = "USER_TRACK_REF"
    const val SCHEMA_VERSION = 1
    const val MAX_CANONICAL_BYTES = 262_144

    private val json = Json {
        isLenient = false
        ignoreUnknownKeys = false
        allowSpecialFloatingPointValues = false
    }
    private val requiredKeys = setOf("artist", "library_entry_local_id", "title")
    private val propertyName = Regex("^[a-z][a-z0-9_]{0,99}$")
    private val forbiddenSegment = Regex(
        "(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|" +
            "base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|" +
            "raw_model_features|model_features|feature_vector|debug_text|personal_debug|" +
            "raw_audio|audio_bytes)(_|$)",
    )

    /** Parses, validates, and returns the only accepted canonical representation. */
    fun canonicalize(
        eventType: String,
        schemaVersion: Int,
        aggregateType: String,
        payloadJson: String,
    ): String {
        requireSupportedContract(eventType, schemaVersion, aggregateType)
        val element = parseStrict(payloadJson)
        val payload = element as? JsonObject
            ?: fail(LocalIntentPayloadErrorCode.TOP_LEVEL_NOT_OBJECT)
        validateSafeObject(element)
        if (payload.keys != requiredKeys) fail(LocalIntentPayloadErrorCode.UNSUPPORTED_PAYLOAD_SHAPE)
        val values = requiredKeys.associateWith { key ->
            val primitive = payload.getValue(key) as? JsonPrimitive
                ?: fail(LocalIntentPayloadErrorCode.NON_STRING_VALUE)
            if (!primitive.isString) fail(LocalIntentPayloadErrorCode.NON_STRING_VALUE)
            primitive.content.also(::requireNoLoneSurrogate)
        }
        val canonical = buildString {
            append('{')
            appendJcsProperty("artist", values.getValue("artist"))
            append(',')
            appendJcsProperty("library_entry_local_id", values.getValue("library_entry_local_id"))
            append(',')
            appendJcsProperty("title", values.getValue("title"))
            append('}')
        }
        if (canonical.toByteArray(StandardCharsets.UTF_8).size > MAX_CANONICAL_BYTES) {
            fail(LocalIntentPayloadErrorCode.PAYLOAD_TOO_LARGE)
        }
        return canonical
    }

    /** Revalidates source data and refuses any representation other than [canonicalize]'s output. */
    fun validateStoredCanonical(
        eventType: String,
        schemaVersion: Int,
        aggregateType: String,
        payloadJson: String,
    ) {
        val canonical = canonicalize(eventType, schemaVersion, aggregateType, payloadJson)
        if (payloadJson != canonical) fail(LocalIntentPayloadErrorCode.NON_CANONICAL_PAYLOAD)
    }

    private fun requireSupportedContract(eventType: String, schemaVersion: Int, aggregateType: String) {
        if (eventType != USER_TRACK_REF_CREATED) fail(LocalIntentPayloadErrorCode.UNSUPPORTED_EVENT_TYPE)
        if (schemaVersion != SCHEMA_VERSION) fail(LocalIntentPayloadErrorCode.UNSUPPORTED_SCHEMA_VERSION)
        if (aggregateType != USER_TRACK_REF) fail(LocalIntentPayloadErrorCode.UNSUPPORTED_AGGREGATE_TYPE)
    }

    private fun parseStrict(payloadJson: String): JsonElement {
        try {
            StrictJsonScanner(payloadJson).scanDocument()
            return json.parseToJsonElement(payloadJson)
        } catch (error: LocalIntentPayloadException) {
            throw error
        } catch (_: Exception) {
            fail(LocalIntentPayloadErrorCode.MALFORMED_JSON)
        }
    }

    private fun validateSafeObject(element: JsonElement) {
        when (element) {
            is JsonObject -> element.forEach { (key, value) ->
                if (!propertyName.matches(key) || forbiddenSegment.containsMatchIn(key)) {
                    fail(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME)
                }
                requireNoLoneSurrogate(key)
                validateSafeObject(value)
            }
            is JsonArray -> element.forEach(::validateSafeObject)
            is JsonPrimitive -> if (element.isString) requireNoLoneSurrogate(element.content)
        }
    }

    private fun StringBuilder.appendJcsProperty(name: String, value: String) {
        appendJcsString(name)
        append(':')
        appendJcsString(value)
    }

    private fun StringBuilder.appendJcsString(value: String) {
        requireNoLoneSurrogate(value)
        append('"')
        value.forEach { character ->
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\t' -> append("\\t")
                '\n' -> append("\\n")
                '\u000C' -> append("\\f")
                '\r' -> append("\\r")
                else -> if (character.code < 0x20) {
                    append("\\u").append(character.code.toString(16).padStart(4, '0'))
                } else {
                    append(character)
                }
            }
        }
        append('"')
    }

    private fun requireNoLoneSurrogate(value: String) {
        var index = 0
        while (index < value.length) {
            val character = value[index]
            if (character.isHighSurrogate()) {
                if (index + 1 >= value.length || !value[index + 1].isLowSurrogate()) {
                    fail(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                }
                index += 2
            } else {
                if (character.isLowSurrogate()) fail(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                index++
            }
        }
    }

    private fun fail(code: LocalIntentPayloadErrorCode): Nothing = throw LocalIntentPayloadException(code)
}

/** Bounded JSON syntax scan that preserves the duplicate-property information JSON trees discard. */
internal class StrictJsonScanner(private val source: String) {
    private var offset = 0

    fun scanDocument() {
        skipWhitespace()
        scanValue(0)
        skipWhitespace()
        if (offset != source.length) malformed()
    }

    private fun scanValue(depth: Int) {
        if (depth > MAX_NESTING) throwPayload(LocalIntentPayloadErrorCode.MAX_NESTING_EXCEEDED)
        skipWhitespace()
        when (peek()) {
            '{' -> scanObject(depth + 1)
            '[' -> scanArray(depth + 1)
            '"' -> scanString()
            't' -> scanLiteral("true")
            'f' -> scanLiteral("false")
            'n' -> scanLiteral("null")
            '-', in '0'..'9' -> scanNumber()
            else -> malformed()
        }
    }

    private fun scanObject(depth: Int) {
        consume('{')
        skipWhitespace()
        val names = HashSet<String>()
        if (tryConsume('}')) return
        while (true) {
            skipWhitespace()
            if (peek() != '"') malformed()
            val name = scanString()
            if (!names.add(name)) throwPayload(LocalIntentPayloadErrorCode.DUPLICATE_PROPERTY)
            skipWhitespace()
            consume(':')
            scanValue(depth)
            skipWhitespace()
            if (tryConsume('}')) return
            consume(',')
        }
    }

    private fun scanArray(depth: Int) {
        consume('[')
        skipWhitespace()
        if (tryConsume(']')) return
        while (true) {
            scanValue(depth)
            skipWhitespace()
            if (tryConsume(']')) return
            consume(',')
        }
    }

    private fun scanString(): String {
        consume('"')
        val result = StringBuilder()
        while (offset < source.length) {
            val character = source[offset++]
            when (character) {
                '"' -> return result.toString()
                '\\' -> result.append(scanEscape())
                else -> {
                    if (character.code < 0x20) malformed()
                    if (character.isHighSurrogate()) {
                        if (offset >= source.length || !source[offset].isLowSurrogate()) {
                            throwPayload(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                        }
                        result.append(character).append(source[offset++])
                    } else {
                        if (character.isLowSurrogate()) throwPayload(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                        result.append(character)
                    }
                }
            }
        }
        malformed()
    }

    private fun scanEscape(): String = when (val escape = take()) {
        '"', '\\', '/' -> escape.toString()
        'b' -> "\b"
        'f' -> "\u000C"
        'n' -> "\n"
        'r' -> "\r"
        't' -> "\t"
        'u' -> {
            val first = scanHexCodeUnit()
            when {
                first.isHighSurrogate() -> {
                    if (offset + 1 >= source.length || source[offset] != '\\' || source[offset + 1] != 'u') {
                        throwPayload(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                    }
                    offset += 2
                    val second = scanHexCodeUnit()
                    if (!second.isLowSurrogate()) throwPayload(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                    "${first}${second}"
                }
                first.isLowSurrogate() -> throwPayload(LocalIntentPayloadErrorCode.LONE_SURROGATE)
                else -> first.toString()
            }
        }
        else -> malformed()
    }

    private fun scanHexCodeUnit(): Char {
        if (offset + 4 > source.length) malformed()
        val hex = source.substring(offset, offset + 4)
        offset += 4
        return hex.toIntOrNull(16)?.toChar() ?: malformed()
    }

    private fun scanLiteral(literal: String) {
        if (!source.regionMatches(offset, literal, 0, literal.length)) malformed()
        offset += literal.length
    }

    private fun scanNumber() {
        val remainder = source.substring(offset)
        val match = NUMBER.matchAt(remainder, 0) ?: malformed()
        offset += match.value.length
    }

    private fun skipWhitespace() {
        while (offset < source.length && source[offset] in WHITESPACE) offset++
    }

    private fun consume(expected: Char) {
        if (!tryConsume(expected)) malformed()
    }

    private fun tryConsume(expected: Char): Boolean {
        if (offset < source.length && source[offset] == expected) {
            offset++
            return true
        }
        return false
    }

    private fun take(): Char = if (offset < source.length) source[offset++] else malformed()
    private fun peek(): Char? = source.getOrNull(offset)
    private fun malformed(): Nothing = throwPayload(LocalIntentPayloadErrorCode.MALFORMED_JSON)

    private companion object {
        const val MAX_NESTING = 32
        val WHITESPACE = setOf(' ', '\t', '\n', '\r')
        val NUMBER = Regex("-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
    }
}

private fun throwPayload(code: LocalIntentPayloadErrorCode): Nothing = throw LocalIntentPayloadException(code)
