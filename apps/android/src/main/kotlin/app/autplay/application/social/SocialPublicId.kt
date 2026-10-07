package app.autplay.application.social

/** A same-server public name, distinct from account/device UUIDs and local profile keys. */
fun normalizeSocialPublicId(value: String): String? {
    if (value.length > 64) return null
    val input = value.trim().removePrefix("@")
    if (!Regex("[A-Za-z0-9_]{3,24}").matches(input)) return null
    return input.lowercase(java.util.Locale.ROOT)
}

sealed interface PublicIdRegistrationState {
    data object Missing : PublicIdRegistrationState
    /** A local choice only. It makes no claim of server uniqueness. */
    data class Pending(val publicId: String) : PublicIdRegistrationState {
        init { requireCanonicalPublicId(publicId) }
    }
    data class Confirmed(val publicId: String) : PublicIdRegistrationState {
        init { requireCanonicalPublicId(publicId) }
    }
    data class Conflict(val publicId: String) : PublicIdRegistrationState {
        init { requireCanonicalPublicId(publicId) }
    }
}

fun PublicIdRegistrationState.publicIdOrNull(): String? = when (this) {
    PublicIdRegistrationState.Missing -> null
    is PublicIdRegistrationState.Pending -> publicId
    is PublicIdRegistrationState.Confirmed -> publicId
    is PublicIdRegistrationState.Conflict -> publicId
}

data class PublicIdRegistrationReceipt(val publicId: String) {
    init { requireCanonicalPublicId(publicId) }
}

sealed interface PublicIdLookupState {
    data object Idle : PublicIdLookupState
    data class Loading(val publicId: String) : PublicIdLookupState
    /** Volatile signed-card material; never persist it with the locally selected public name. */
    data class Found(val publicId: String, val contactCard: ContactCard) : PublicIdLookupState
    /** Missing, blocked and inaccessible accounts deliberately share this state. */
    data class Unavailable(val publicId: String, val errorCode: String) : PublicIdLookupState
}

internal fun requireCanonicalPublicId(value: String) {
    require(PUBLIC_ID_PATTERN.matches(value))
}

private val PUBLIC_ID_PATTERN = Regex("[a-z0-9_]{3,24}")
