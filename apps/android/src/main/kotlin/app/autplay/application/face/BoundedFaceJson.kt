package app.autplay.application.face

import app.autplay.domain.face.FaceContractException
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonPrimitive

internal const val MAX_FACE_BYTES = 1_048_576

/** Reject duplicate decoded keys and excessive nesting before allocating the JSON tree. */
internal fun boundedFaceJson(data: ByteArray): JsonElement {
    if (data.isEmpty() || data.size > MAX_FACE_BYTES) {
        throw FaceContractException("ml.face.timeline_too_large")
    }
    try {
        val text = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(data)).toString()
        FaceJsonPreflight(text).check()
        return Json.parseToJsonElement(text)
    } catch (error: FaceContractException) {
        throw error
    } catch (_: IllegalArgumentException) {
        throw FaceContractException()
    } catch (_: java.nio.charset.CharacterCodingException) {
        throw FaceContractException()
    }
}

private class FaceJsonPreflight(private val text: String) {
    private var position = 0
    private val primitiveToken = Regex("(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)")

    fun check() {
        value(0)
        whitespace()
        if (position != text.length) fail()
    }

    private fun value(depth: Int) {
        whitespace()
        when (peek()) {
            '{' -> {
                if (depth >= 12) fail()
                position++
                val keys = HashSet<String>()
                whitespace()
                if (consume('}')) return
                do {
                    whitespace()
                    val key = string()
                    if (!keys.add(key)) fail()
                    whitespace()
                    if (!consume(':')) fail()
                    value(depth + 1)
                    whitespace()
                    if (consume('}')) return
                } while (consume(','))
                fail()
            }
            '[' -> {
                if (depth >= 12) fail()
                position++
                whitespace()
                if (consume(']')) return
                do {
                    value(depth + 1)
                    whitespace()
                    if (consume(']')) return
                } while (consume(','))
                fail()
            }
            '"' -> string()
            else -> {
                val start = position
                while (position < text.length && text[position] !in ",]} \r\n\t") position++
                if (position == start || !text.substring(start, position).matches(primitiveToken)) fail()
            }
        }
    }

    private fun string(): String {
        val start = position
        if (!consume('"')) fail()
        var escaped = false
        while (position < text.length) {
            val next = text[position++]
            if (escaped) escaped = false
            else if (next == '\\') escaped = true
            else if (next == '"') {
                return Json.parseToJsonElement(text.substring(start, position)).jsonPrimitive.content
            }
        }
        fail()
    }

    private fun peek(): Char = text.getOrNull(position) ?: fail()
    private fun consume(character: Char): Boolean {
        if (text.getOrNull(position) != character) return false
        position++
        return true
    }
    private fun whitespace() {
        while (position < text.length && text[position] in " \r\n\t") position++
    }
    private fun fail(): Nothing = throw FaceContractException()
}
