package app.autplay.application.social

/** A same-server public name, distinct from account/device UUIDs and local profile keys. */
fun normalizeSocialPublicId(value: String): String? {
    if (value.length > 64) return null
    val input = value.trim().removePrefix("@")
    if (!Regex("[A-Za-z0-9_]{3,24}").matches(input)) return null
    return input.lowercase(java.util.Locale.ROOT)
}
