package app.autplay.application.settings

import app.autplay.data.settings.NonSecretSettings
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/** Bounded, secret-free transfer format for device-local UI preferences. */
object SettingsTransferCodec {
    private const val SCHEMA_VERSION = 1
    private const val MAX_BYTES = 64 * 1024
    private val json = Json { ignoreUnknownKeys = true }

    fun encode(settings: NonSecretSettings): ByteArray = buildJsonObject {
        put("schema_version", SCHEMA_VERSION)
        put("app_language", settings.appLanguage)
        put("appearance_mode", settings.appearanceMode)
        put("accent_palette", settings.accentPalette)
        put("sync_on_metered_network", settings.syncOnMeteredNetwork)
        put("wave_prefetch_mode", settings.wavePrefetchMode)
    }.toString().encodeToByteArray()

    fun decode(bytes: ByteArray, current: NonSecretSettings): NonSecretSettings {
        require(bytes.size <= MAX_BYTES) { "SETTINGS_IMPORT_TOO_LARGE" }
        val root = json.parseToJsonElement(bytes.decodeToString()).jsonObject
        require(root.getValue("schema_version").jsonPrimitive.content.toInt() == SCHEMA_VERSION) {
            "SETTINGS_SCHEMA_UNSUPPORTED"
        }
        val appearance = root.getValue("appearance_mode").jsonPrimitive.content
        val appLanguage = root["app_language"]?.jsonPrimitive?.content ?: current.appLanguage
        val palette = root.getValue("accent_palette").jsonPrimitive.content
        val prefetch = root.getValue("wave_prefetch_mode").jsonPrimitive.content
        require(appearance in setOf("SYSTEM", "LIGHT", "DARK")) { "SETTINGS_APPEARANCE_INVALID" }
        require(palette in setOf("CORAL", "VIOLET", "GREEN", "BLUE")) { "SETTINGS_PALETTE_INVALID" }
        require(prefetch in setOf("OFF", "NEXT", "NEXT_3", "AGGRESSIVE_WIFI")) {
            "SETTINGS_WAVE_PREFETCH_INVALID"
        }
        return current.copy(
            appLanguage = appLanguage,
            appearanceMode = appearance,
            accentPalette = palette,
            syncOnMeteredNetwork = root.getValue("sync_on_metered_network").jsonPrimitive.boolean,
            wavePrefetchMode = prefetch,
        )
    }
}
