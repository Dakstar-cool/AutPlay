package app.autplay.work

/** Only stable codes may be persisted; exception messages can contain private URLs and payloads. */
internal fun serverWorkErrorCode(error: Exception): String =
    error.message?.takeIf { Regex("[A-Z][A-Z0-9_]{2,99}").matches(it) } ?: "SERVER_REQUEST_UNAVAILABLE"

internal fun terminalServerWorkError(code: String): Boolean = code in setOf(
    "SESSION_REQUIRED", "SERVER_PROFILE_NOT_ACTIVE", "SYNC_PROFILE_NOT_ACTIVE", "DEVICE_REVOKED",
    "SERVER_HTTP_401", "SERVER_HTTP_403", "SYNC_HTTP_401", "SYNC_HTTP_403",
)
