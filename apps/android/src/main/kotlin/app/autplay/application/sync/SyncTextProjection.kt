package app.autplay.application.sync

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Absence preserves data; JSON null clears it; the string "null" is legitimate metadata. */
internal fun JsonObject.projectNullableText(key: String, current: String?): String? {
    val value = this[key] ?: return current
    if (value === JsonNull) return null
    require(value is JsonPrimitive && value.isString) { "PROJECTION_TEXT_INVALID" }
    return value.content
}

internal fun JsonObject.projectRequiredText(key: String, current: String): String =
    if (!containsKey(key)) current else checkNotNull(projectNullableText(key, current)) { "PROJECTION_TEXT_INVALID" }
