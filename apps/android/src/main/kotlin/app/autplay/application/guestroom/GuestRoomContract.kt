package app.autplay.application.guestroom

import app.autplay.application.profilepairing.OriginNormalizer
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put

const val GUEST_DOCUMENT_MIME_TYPE = "application/vnd.autplay.guest+json"
const val GUEST_DOCUMENT_TYPE = "autplay-guest-v1"

/** Parsed app document with a redacted representation and explicitly erasable bearer bytes. */
class GuestRoomDocument internal constructor(
    val serverOrigin: String,
    val serverInstanceId: String,
    val identityEpoch: Long,
    val roomId: String,
    val invitationId: String,
    val expiresAt: Instant,
    private val documentBearer: ByteArray,
) : AutoCloseable {
    internal fun bearer(): String = Base64.getUrlEncoder().withoutPadding().encodeToString(documentBearer)

    override fun close() {
        documentBearer.fill(0)
    }

    override fun toString(): String =
        "GuestRoomDocument(serverInstanceId=$serverInstanceId, identityEpoch=$identityEpoch, " +
            "roomId=$roomId, invitationId=$invitationId, expiresAt=$expiresAt, bearer=<redacted>)"
}

/** Host-side result owns the raw bearer only until the app document has been shared. */
class GuestInvitationReceipt internal constructor(
    val invitationId: String,
    val roomId: String,
    val expiresAt: Instant,
    private val documentBearer: ByteArray,
) : AutoCloseable {
    internal fun bearer(): String = Base64.getUrlEncoder().withoutPadding().encodeToString(documentBearer)

    override fun close() {
        documentBearer.fill(0)
    }

    override fun toString(): String =
        "GuestInvitationReceipt(invitationId=$invitationId, roomId=$roomId, " +
            "expiresAt=$expiresAt, bearer=<redacted>)"
}

data class RedeemedGuestCapability(
    val guestSessionId: String,
    val invitationId: String,
    val roomId: String,
    val roomEpoch: String,
    val displayName: String,
    val expiresAt: Instant,
)

object GuestRoomDocumentCodec {
    private val requiredKeys = setOf(
        "type",
        "version",
        "server_origin",
        "server_instance_id",
        "identity_epoch",
        "room_id",
        "invitation_id",
        "document_bearer",
        "expires_at",
    )

    fun decode(
        raw: String,
        now: Instant,
        allowUnsafeDevelopmentHttp: Boolean = false,
    ): GuestRoomDocument {
        require(raw.length in 2..4_096) { "GUEST_DOCUMENT_SIZE_INVALID" }
        val value = Json.parseToJsonElement(raw) as? JsonObject
            ?: throw IllegalArgumentException("GUEST_DOCUMENT_INVALID")
        require(value.keys == requiredKeys) { "GUEST_DOCUMENT_FIELDS_INVALID" }
        require(value.string("type") == GUEST_DOCUMENT_TYPE && value.long("version") == 1L) {
            "GUEST_DOCUMENT_VERSION_UNSUPPORTED"
        }
        val origin = OriginNormalizer.normalize(
            value.string("server_origin"),
            allowUnsafeDevelopmentHttp,
        )
        val serverInstanceId = canonicalUuid(value.string("server_instance_id"))
        val roomId = canonicalUuid(value.string("room_id"))
        val invitationId = canonicalUuid(value.string("invitation_id"))
        val identityEpoch = value.long("identity_epoch")
        require(identityEpoch >= 1) { "GUEST_DOCUMENT_IDENTITY_INVALID" }
        val expiresAt = Instant.parse(value.string("expires_at"))
        require(expiresAt.isAfter(now)) { "GUEST_DOCUMENT_EXPIRED" }
        val bearer = decodeBearer(value.string("document_bearer"))
        return GuestRoomDocument(
            origin,
            serverInstanceId,
            identityEpoch,
            roomId,
            invitationId,
            expiresAt,
            bearer,
        )
    }

    fun encode(
        receipt: GuestInvitationReceipt,
        serverOrigin: String,
        serverInstanceId: String,
        identityEpoch: Long,
    ): String = buildJsonObject {
        put("type", GUEST_DOCUMENT_TYPE)
        put("version", 1)
        put("server_origin", OriginNormalizer.normalize(serverOrigin, allowUnsafeDevelopmentHttp = true))
        put("server_instance_id", canonicalUuid(serverInstanceId))
        put("identity_epoch", identityEpoch.also { require(it >= 1) })
        put("room_id", canonicalUuid(receipt.roomId))
        put("invitation_id", canonicalUuid(receipt.invitationId))
        put("document_bearer", receipt.bearer())
        put("expires_at", receipt.expiresAt.toString())
    }.toString()

    internal fun generateBearer(): ByteArray = ByteArray(32).also(SecureRandom()::nextBytes)

    internal fun decodeBearer(value: String): ByteArray {
        require(value.length == 43 && value.matches(Regex("^[A-Za-z0-9_-]{43}$"))) {
            "GUEST_BEARER_INVALID"
        }
        val decoded = runCatching { Base64.getUrlDecoder().decode(value) }
            .getOrElse { throw IllegalArgumentException("GUEST_BEARER_INVALID") }
        require(decoded.size == 32) { "GUEST_BEARER_INVALID" }
        return decoded
    }

    private fun JsonObject.string(key: String): String =
        getValue(key).jsonPrimitive.contentOrNull
            ?: throw IllegalArgumentException("GUEST_DOCUMENT_INVALID")

    private fun JsonObject.long(key: String): Long =
        runCatching { getValue(key).jsonPrimitive.long }
            .getOrElse { throw IllegalArgumentException("GUEST_DOCUMENT_INVALID") }

    private fun canonicalUuid(value: String): String = UUID.fromString(value).toString().also {
        require(it == value.lowercase()) { "GUEST_DOCUMENT_UUID_INVALID" }
    }
}
