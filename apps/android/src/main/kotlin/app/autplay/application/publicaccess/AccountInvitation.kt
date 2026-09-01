package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.profilepairing.requireSha256
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * A secret-bearing account invitation. It is deliberately distinct from an M5 device invitation:
 * redeeming it creates an account and only its first device.
 */
data class AccountInvitation(
    val invitationId: String,
    val serverInstanceId: String,
    val identityEpoch: Long,
    val identityThumbprintSha256: String,
    val apiOrigin: String,
    val streamOrigin: String,
    val accountDisplayName: String,
    val issuedAt: Instant,
    val expiresAt: Instant,
    val secret: ByteArray,
) : AutoCloseable {
    init {
        requireCanonicalUuid(invitationId); requireCanonicalUuid(serverInstanceId)
        require(identityEpoch >= 1); requireSha256(identityThumbprintSha256)
        require(OriginNormalizer.normalize(apiOrigin, allowUnsafeDevelopmentHttp = true) == apiOrigin)
        require(OriginNormalizer.normalize(streamOrigin, allowUnsafeDevelopmentHttp = true) == streamOrigin)
        require(accountDisplayName.length in 1..120); require(issuedAt.isBefore(expiresAt)); require(secret.size == 32)
    }
    override fun close() = secret.fill(0)

    /** Exact app-document representation for volatile QR/share UI; callers must never persist it. */
    fun toDocumentJson(): String = buildJsonObject {
        put("contract_version", JsonPrimitive("v1"))
        put("schema_version", JsonPrimitive(1))
        put("invitation_id", JsonPrimitive(invitationId))
        put("server_instance_id", JsonPrimitive(serverInstanceId))
        put("identity_epoch", JsonPrimitive(identityEpoch))
        put("identity_thumbprint_sha256", JsonPrimitive(identityThumbprintSha256))
        put("api_origin", JsonPrimitive(apiOrigin))
        put("stream_origin", JsonPrimitive(streamOrigin))
        put("account_display_name", JsonPrimitive(accountDisplayName))
        put("account_role", JsonPrimitive("USER"))
        put("issued_at", JsonPrimitive(issuedAt.toString()))
        put("expires_at", JsonPrimitive(expiresAt.toString()))
        put("invitation_secret", JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(secret)))
        put("secret_handling", JsonPrimitive(AccountInvitationParser.SECRET_HANDLING))
    }.toString()
}

/** Fail-closed parser for QR payloads and `.autplayinvite` document bytes. */
object AccountInvitationParser {
    const val MIME_TYPE = "application/vnd.autplay.account-invitation+json"
    private val members = setOf(
        "contract_version", "schema_version", "invitation_id", "server_instance_id", "identity_epoch",
        "identity_thumbprint_sha256", "api_origin", "stream_origin", "account_display_name", "account_role",
        "issued_at", "expires_at", "invitation_secret", "secret_handling",
    )
    internal const val SECRET_HANDLING = "DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"

    fun parseQr(payload: String): AccountInvitation = parseJson(payload)

    fun parseDocument(mimeType: String, bytes: ByteArray): AccountInvitation {
        require(mimeType.equals(MIME_TYPE, ignoreCase = true)) { "ACCOUNT_INVITATION_MIME_INVALID" }
        require(bytes.size in 2..16_384) { "ACCOUNT_INVITATION_SIZE_INVALID" }
        return parseJson(bytes.toString(Charsets.UTF_8))
    }

    private fun parseJson(raw: String): AccountInvitation {
        // Never accept a URL/deep link: a bearer in a URL leaks through browser, history and logs.
        require(!raw.trimStart().startsWith("http", ignoreCase = true)) { "ACCOUNT_INVITATION_URL_FORBIDDEN" }
        val value = Json { isLenient = false; ignoreUnknownKeys = false }.parseToJsonElement(raw)
        val root = value as? JsonObject ?: error("ACCOUNT_INVITATION_OBJECT_REQUIRED")
        require(root.keys == members) { "ACCOUNT_INVITATION_MEMBERS_INVALID" }
        fun string(name: String) = root[name]?.jsonPrimitive?.content ?: error("ACCOUNT_INVITATION_FIELD_MISSING")
        require(string("contract_version") == "v1" && string("schema_version") == "1") { "ACCOUNT_INVITATION_VERSION_INVALID" }
        require(string("account_role") == "USER") { "ACCOUNT_INVITATION_ROLE_INVALID" }
        require(string("secret_handling") == SECRET_HANDLING) { "ACCOUNT_INVITATION_HANDLING_INVALID" }
        val api = OriginNormalizer.normalize(string("api_origin"))
        val stream = OriginNormalizer.normalize(string("stream_origin"))
        val secretText = string("invitation_secret")
        require(Regex("^[A-Za-z0-9_-]{43}$").matches(secretText)) { "ACCOUNT_INVITATION_SECRET_INVALID" }
        val secret = Base64.getUrlDecoder().decode(secretText)
        try {
            require(secret.size == 32)
            val issued = Instant.parse(string("issued_at"))
            val expires = Instant.parse(string("expires_at"))
            require(issued.isBefore(expires)) { "ACCOUNT_INVITATION_WINDOW_INVALID" }
            require(expires.isAfter(Instant.now())) { "ACCOUNT_INVITATION_EXPIRED" }
            return AccountInvitation(
                string("invitation_id"), string("server_instance_id"), string("identity_epoch").toLong(),
                string("identity_thumbprint_sha256"), api, stream, string("account_display_name"), issued, expires, secret.copyOf(),
            )
        } finally { secret.fill(0) }
    }
}
