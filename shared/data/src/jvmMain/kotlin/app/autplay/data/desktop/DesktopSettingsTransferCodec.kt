package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopPreferences
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Stable codes only: never attach parser/platform exceptions containing paths or input. */
class DesktopSettingsException(val code: String) : IllegalArgumentException(code)

/** Android-compatible versions 1..3, with an explicit six-field non-secret allowlist. */
object DesktopSettingsTransferCodec {
    const val MAX_BYTES: Int = 65_536

    fun encode(value: DesktopPreferences): ByteArray {
        DesktopSettingsJson.validatePortable(value)
        return DesktopSettingsJson.document(value, 3).toString().encodeToByteArray().also {
            DesktopSettingsJson.requireCode(it.size <= MAX_BYTES, "desktop_settings_too_large")
        }
    }

    fun decode(bytes: ByteArray, current: DesktopPreferences): DesktopPreferences {
        val root = DesktopSettingsJson.parse(bytes)
        val version = DesktopSettingsJson.version(root)
        DesktopSettingsJson.requireCode(version in 1..3, "desktop_settings_schema_unsupported")
        val value = DesktopSettingsJson.preferences(root, current, version, portable = true)
        DesktopSettingsJson.validatePortable(value)
        return value
    }
}

internal object DesktopSettingsJson {
    private val appearance: Set<String> = setOf("SYSTEM", "LIGHT", "DARK")
    private val palette: Set<String> = setOf("CORAL", "VIOLET", "GREEN", "BLUE")
    private val prefetch: Set<String> = setOf("OFF", "NEXT", "NEXT_3", "AGGRESSIVE_WIFI")

    fun requireCode(valid: Boolean, code: String): Unit {
        if (!valid) throw DesktopSettingsException(code)
    }

    fun validText(value: String): Boolean {
        if (value.isEmpty() || value.length > 128 || '\u0000' in value) return false
        var index = 0
        while (index < value.length) {
            val char = value[index++]
            if (char.isHighSurrogate()) {
                if (index == value.length || !value[index++].isLowSurrogate()) return false
            } else if (char.isLowSurrogate()) return false
        }
        return true
    }

    fun validatePortable(value: DesktopPreferences): Unit {
        requireCode(validText(value.appLanguage), "desktop_settings_language_invalid")
        requireCode(value.appearanceMode in appearance, "desktop_settings_appearance_invalid")
        requireCode(value.accentPalette in palette, "desktop_settings_palette_invalid")
        requireCode(value.wavePrefetchMode in prefetch, "desktop_settings_prefetch_invalid")
    }

    fun document(value: DesktopPreferences, version: Int): JsonObject = buildJsonObject {
        listOf(value.appLanguage, value.appearanceMode, value.accentPalette, value.wavePrefetchMode).forEach {
            requireCode(validText(it), "desktop_settings_value_invalid")
        }
        put("schema_version", version)
        put("app_language", value.appLanguage)
        put("appearance_mode", value.appearanceMode)
        put("accent_palette", value.accentPalette)
        put("download_on_metered_network", value.downloadOnMeteredNetwork)
        put("wave_prefetch_mode", value.wavePrefetchMode)
        put("smooth_track_transitions", value.smoothTrackTransitions)
    }

    fun version(root: JsonObject): Int {
        val primitive = root["schema_version"] as? JsonPrimitive
        requireCode(primitive != null && !primitive.isString && primitive != JsonNull,
            "desktop_settings_schema_invalid")
        return primitive!!.content.toIntOrNull() ?: throw DesktopSettingsException("desktop_settings_schema_invalid")
    }

    fun preferences(root: JsonObject, current: DesktopPreferences, version: Int, portable: Boolean): DesktopPreferences {
        fun text(key: String, fallback: String? = null): String {
            if (portable && key !in root && fallback != null) return fallback
            val primitive = root[key] as? JsonPrimitive
            requireCode(primitive != null && primitive.isString && validText(primitive.content),
                "desktop_settings_value_invalid")
            return primitive!!.content
        }
        fun boolean(key: String, fallback: Boolean? = null): Boolean {
            if (portable && key !in root && fallback != null) return fallback
            val primitive = root[key] as? JsonPrimitive
            requireCode(primitive != null && !primitive.isString && primitive.content in setOf("true", "false"),
                "desktop_settings_value_invalid")
            return primitive!!.content == "true"
        }
        return DesktopPreferences(
            appLanguage = text("app_language", current.appLanguage),
            appearanceMode = text("appearance_mode"),
            accentPalette = text("accent_palette"),
            downloadOnMeteredNetwork = boolean(if (portable && version == 1) "sync_on_metered_network"
                else "download_on_metered_network"),
            wavePrefetchMode = text("wave_prefetch_mode"),
            smoothTrackTransitions = boolean("smooth_track_transitions", current.smoothTrackTransitions),
        )
    }

    fun parse(bytes: ByteArray): JsonObject {
        requireCode(bytes.size <= DesktopSettingsTransferCodec.MAX_BYTES, "desktop_settings_too_large")
        try {
            val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
            return Parser(text).parse() as? JsonObject ?: throw DesktopSettingsException("desktop_settings_root_invalid")
        } catch (error: DesktopSettingsException) {
            throw error
        } catch (_: Exception) {
            throw DesktopSettingsException("desktop_settings_malformed")
        }
    }

    /** Strict grammar before allocation; duplicate keys, deep nesting and excess nodes fail closed. */
    private class Parser(private val text: String) {
        private var position: Int = 0
        private var nodes: Int = 0

        fun parse(): JsonElement {
            val result = value(0)
            space()
            requireCode(position == text.length, "desktop_settings_malformed")
            return result
        }

        private fun space(): Unit {
            while (position < text.length && text[position] in " \t\r\n") position++
        }

        private fun take(char: Char): Boolean {
            space()
            if (position >= text.length || text[position] != char) return false
            position++
            return true
        }

        private fun value(depth: Int): JsonElement {
            requireCode(depth <= 16 && ++nodes <= 4_096, "desktop_settings_structure_limit")
            space()
            requireCode(position < text.length, "desktop_settings_malformed")
            return when (text[position]) {
                '{' -> {
                    position++
                    val fields = linkedMapOf<String, JsonElement>()
                    if (!take('}')) {
                        do {
                            space()
                            requireCode(++nodes <= 4_096, "desktop_settings_structure_limit")
                            val key = string()
                            requireCode(key !in fields && take(':'), "desktop_settings_malformed")
                            fields[key] = value(depth + 1)
                        } while (take(','))
                        requireCode(take('}'), "desktop_settings_malformed")
                    }
                    JsonObject(fields)
                }
                '[' -> {
                    position++
                    val items = mutableListOf<JsonElement>()
                    if (!take(']')) {
                        do { items.add(value(depth + 1)) } while (take(','))
                        requireCode(take(']'), "desktop_settings_malformed")
                    }
                    JsonArray(items)
                }
                '"' -> JsonPrimitive(string())
                else -> {
                    val start = position
                    while (position < text.length && text[position] !in " \t\r\n,]}") position++
                    val token = text.substring(start, position)
                    requireCode(token in setOf("true", "false", "null") || NUMBER.matches(token),
                        "desktop_settings_malformed")
                    Json.parseToJsonElement(token)
                }
            }
        }

        private fun string(): String {
            requireCode(position < text.length && text[position] == '"', "desktop_settings_malformed")
            val start = position++
            var ended = false
            while (position < text.length) {
                val char = text[position++]
                requireCode(char >= ' ', "desktop_settings_malformed")
                if (char == '"') { ended = true; break }
                if (char == '\\') {
                    requireCode(position < text.length, "desktop_settings_malformed")
                    val escape = text[position++]
                    requireCode(escape in "\"\\/bfnrtu", "desktop_settings_malformed")
                    if (escape == 'u') {
                        requireCode(position + 4 <= text.length &&
                            text.substring(position, position + 4).all { it in "0123456789abcdefABCDEF" },
                            "desktop_settings_malformed")
                        position += 4
                    }
                }
            }
            requireCode(ended, "desktop_settings_malformed")
            val decoded = (Json.parseToJsonElement(text.substring(start, position)) as JsonPrimitive).content
            // Unknown local strings can contain NUL, but never malformed Unicode.
            var index = 0
            while (index < decoded.length) {
                val char = decoded[index++]
                if (char.isHighSurrogate()) {
                    requireCode(index < decoded.length && decoded[index++].isLowSurrogate(), "desktop_settings_malformed")
                } else requireCode(!char.isLowSurrogate(), "desktop_settings_malformed")
            }
            return decoded
        }

        companion object {
            private val NUMBER: Regex = Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?")
        }
    }
}
