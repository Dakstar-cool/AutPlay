package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.OriginNormalizer
import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import java.math.BigInteger
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/** Identity is taken from verified discovery; QR identity alone never authorizes a request. */
data class SelfPairingIdentity(
    val serverInstanceId: String,
    val epoch: Long,
    val thumbprint: String,
    val apiOrigin: String,
    val streamOrigin: String,
) {
    init {
        requireCanonicalUuid(serverInstanceId)
        require(epoch in 1..9_007_199_254_740_991L)
        require(HEX.matches(thumbprint))
    }

    fun fields(): Map<String, JsonElement> = mapOf(
        "expected_server_instance_id" to JsonPrimitive(serverInstanceId),
        "expected_identity_epoch" to JsonPrimitive(epoch),
        "expected_identity_thumbprint_sha256" to JsonPrimitive(thumbprint),
        "expected_api_origin" to JsonPrimitive(apiOrigin),
        "expected_stream_origin" to JsonPrimitive(streamOrigin),
    )

    override fun toString(): String = "ServerIdentity($serverInstanceId, epoch=$epoch, origins=<redacted>)"

    companion object {
        internal val HEX = Regex("[0-9a-f]{64}")
        fun parse(value: JsonObject, allowDevelopmentHttp: Boolean = false): SelfPairingIdentity {
            val api = value.text("expected_api_origin")
            val stream = value.text("expected_stream_origin")
            require(api == OriginNormalizer.normalize(api, allowDevelopmentHttp))
            require(stream == OriginNormalizer.normalize(stream, allowDevelopmentHttp))
            return SelfPairingIdentity(
                value.text("expected_server_instance_id"), value.integer("expected_identity_epoch"),
                value.text("expected_identity_thumbprint_sha256"), api, stream,
            )
        }
    }
}

/** The bearer is deliberately excluded from generated toString/equals/component methods. */
class SelfPairingQr(
    val ceremonyId: String,
    val identity: SelfPairingIdentity,
    val expiresAt: Instant,
    val rendezvousSecret: ByteArray,
) : AutoCloseable {
    init { requireCanonicalUuid(ceremonyId); SelfPairingProof.requireSecret(rendezvousSecret) }
    override fun close() = rendezvousSecret.fill(0)

    fun encode(): ByteArray = SelfPairingJson.canonical(JsonObject(identity.fields() + mapOf(
        "format" to JsonPrimitive("autplay-self-device-pairing"), "version" to JsonPrimitive(1),
        "ceremony_id" to JsonPrimitive(ceremonyId), "expires_at" to JsonPrimitive(expiresAt.toString()),
        "rendezvous_secret" to JsonPrimitive(rendezvousSecret.toString(Charsets.US_ASCII)),
    )))

    companion object {
        fun parse(
            payload: ByteArray,
            now: Instant = Instant.now(),
            allowDevelopmentHttp: Boolean = false,
        ): SelfPairingQr {
            val value = SelfPairingJson.parse(payload, 4096)
            require(value.keys == MEMBERS) { "SELF_PAIRING_QR_INVALID" }
            require(value.text("format") == "autplay-self-device-pairing" && value.integer("version") == 1L)
            val expires = value.instant("expires_at")
            require(expires.isAfter(now) && !expires.isAfter(now.plusSeconds(1020))) { "SELF_PAIRING_QR_EXPIRED" }
            val secret = value.text("rendezvous_secret").toByteArray(Charsets.US_ASCII)
            return try {
                SelfPairingQr(value.text("ceremony_id"), SelfPairingIdentity.parse(value, allowDevelopmentHttp), expires, secret.copyOf())
            } finally { secret.fill(0) }
        }

        private val MEMBERS = setOf(
            "format", "version", "ceremony_id", "expires_at", "rendezvous_secret",
            "expected_server_instance_id", "expected_identity_epoch", "expected_identity_thumbprint_sha256",
            "expected_api_origin", "expected_stream_origin",
        )
    }
}

/** A bounded flat object parser rejects duplicates before kotlinx serialization can discard them. */
internal object SelfPairingJson {
    fun parse(bytes: ByteArray, maxBytes: Int = 8192, maxDepth: Int = 1): JsonObject {
        require(maxDepth in 1..2)
        require(bytes.size in 2..maxBytes) { "SELF_PAIRING_DOCUMENT_INVALID" }
        val raw = Charsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString()
        var quoted = false
        var escaped = false
        var depth = 0
        for (char in raw) {
            if (quoted) {
                if (escaped) escaped = false else if (char == '\\') escaped = true else if (char == '"') quoted = false
            } else when (char) {
                '"' -> quoted = true
                '{' -> { depth++; require(depth <= maxDepth) { "SELF_PAIRING_DOCUMENT_INVALID" } }
                '}' -> { depth--; require(depth >= 0) { "SELF_PAIRING_DOCUMENT_INVALID" } }
                '[', ']' -> throw IllegalArgumentException("SELF_PAIRING_DOCUMENT_INVALID")
            }
        }
        require(!quoted && depth == 0)
        // The JCS decoder rejects duplicate decoded property names and invalid Unicode.
        try { JsonCanonicalizer(raw) } catch (_: Exception) {
            throw IllegalArgumentException("SELF_PAIRING_DOCUMENT_INVALID")
        }
        return Json.parseToJsonElement(raw) as? JsonObject
            ?: error("SELF_PAIRING_DOCUMENT_INVALID")
    }

    fun canonical(value: JsonObject): ByteArray = JsonCanonicalizer(value.toString()).encodedString.toByteArray(Charsets.UTF_8)
}

internal fun JsonObject.text(name: String): String {
    val value = this[name] as? JsonPrimitive ?: error("SELF_PAIRING_DOCUMENT_INVALID")
    require(value.isString) { "SELF_PAIRING_DOCUMENT_INVALID" }
    return value.content
}

internal fun JsonObject.integer(name: String): Long {
    val value = this[name] as? JsonPrimitive ?: error("SELF_PAIRING_DOCUMENT_INVALID")
    require(!value.isString && Regex("0|[1-9][0-9]{0,15}").matches(value.content))
    return value.content.toLong().also { require(it <= 9_007_199_254_740_991L) }
}

internal fun JsonObject.instant(name: String): Instant {
    val value = text(name)
    require(Regex("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?Z").matches(value))
    return Instant.parse(value)
}

/** RFC8785 and ES256-P1363 convention frozen in contracts/self-device-pairing/v1. */
object SelfPairingProof {
    private val random = SecureRandom()
    fun secret(): ByteArray = ByteArray(32).also(random::nextBytes).let { raw ->
        try { Base64.getUrlEncoder().withoutPadding().encode(raw) } finally { raw.fill(0) }
    }
    fun requireSecret(value: ByteArray) {
        require(value.size == 43 && value.all { it.toInt() in 0..127 })
        val text = value.toString(Charsets.US_ASCII)
        require(Regex("[A-Za-z0-9_-]{43}").matches(text))
        val raw = Base64.getUrlDecoder().decode(value)
        try { require(Base64.getUrlEncoder().withoutPadding().encode(raw).contentEquals(value)) }
        finally { raw.fill(0) }
    }
    fun hash(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value)
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    fun request(
        kind: String, identity: SelfPairingIdentity, ceremonyId: String,
        fields: Map<String, JsonElement>, keys: M5DeviceKeyStore? = null, alias: String? = null,
        now: Instant = Instant.now(),
    ): ByteArray {
        require(kind in setOf("start", "claim", "poll", "decision", "exchange"))
        requireCanonicalUuid(ceremonyId)
        val common = identity.fields() + mapOf(
            "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
            "ceremony_id" to JsonPrimitive(ceremonyId), "requested_at" to JsonPrimitive(now.toString()),
        )
        require(fields.keys.intersect(common.keys + setOf("request_sha256", "device_signature_b64url")).isEmpty())
        val unsigned = SelfPairingJson.canonical(JsonObject(common + fields))
        return try {
            val proof = if (kind in setOf("claim", "poll", "exchange")) {
                val signed = M5RequestSigner.sign(requireNotNull(keys), requireNotNull(alias), domain(kind), unsigned)
                mapOf("request_sha256" to JsonPrimitive(signed.requestSha256), "device_signature_b64url" to JsonPrimitive(signed.signatureB64Url))
            } else mapOf("request_sha256" to JsonPrimitive(hash(unsigned)))
            SelfPairingJson.canonical(JsonObject(common + fields + proof)).also { require(it.size <= 8192) }
        } finally { unsigned.fill(0) }
    }

    fun claim(qr: SelfPairingQr, pollSecret: ByteArray, name: String, version: String, keys: M5DeviceKeyStore, alias: String, claimId: String = UUID.randomUUID().toString()): ByteArray {
        requireSecret(pollSecret)
        require(name.length in 1..120 && name.none { it.code < 32 } && version.length in 1..32)
        return request("claim", qr.identity, qr.ceremonyId, mapOf(
            "claim_id" to JsonPrimitive(claimId), "poll_secret_sha256" to JsonPrimitive(hash(pollSecret)),
            "device_public_key_spki_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(keys.publicKeySpki(alias))),
            "device_key_thumbprint_sha256" to JsonPrimitive(keys.publicKeyThumbprintSha256(alias)),
            "device_name" to JsonPrimitive(name), "platform" to JsonPrimitive("ANDROID"), "app_version" to JsonPrimitive(version),
        ), keys, alias)
    }

    fun comparisonCode(identity: SelfPairingIdentity, ceremonyId: String, claimHash: String, keyThumbprint: String): String {
        require(SelfPairingIdentity.HEX.matches(claimHash) && SelfPairingIdentity.HEX.matches(keyThumbprint))
        val payload = SelfPairingJson.canonical(JsonObject(mapOf(
            "server_instance_id" to JsonPrimitive(identity.serverInstanceId), "identity_epoch" to JsonPrimitive(identity.epoch),
            "ceremony_id" to JsonPrimitive(ceremonyId), "claim_request_sha256" to JsonPrimitive(claimHash),
            "key_thumbprint" to JsonPrimitive(keyThumbprint),
        )))
        val digest = MessageDigest.getInstance("SHA-256")
        val value = digest.digest("autplay:self-device-pairing:sas:v1\n".toByteArray(Charsets.US_ASCII) + digest.digest(payload))
        return BigInteger(1, value.copyOfRange(0, 8)).mod(BigInteger.TEN.pow(12)).toString().padStart(12, '0')
    }

    fun domain(kind: String): String = "autplay:self-device-pairing:$kind:v1\n"
}
