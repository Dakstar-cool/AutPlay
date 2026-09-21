package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.SelfPairingProof
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.instant
import app.autplay.application.selfpairing.text
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/** Secret bytes never participate in generated toString/equals methods. */
class AccountRecoveryDocument(
    val identity: SelfPairingIdentity, val accountId: String, val label: String, val code: ByteArray,
) : AutoCloseable {
    init {
        requireCanonicalUuid(accountId)
        require(label.length in 1..200 && label.none { it.code < 32 || it.code == 127 })
        require(code.size == 32 && code.all { it.toInt().toChar() in AccountRecoveryProof.ALPHABET })
    }
    override fun close() = code.fill(0)
    fun encode(): ByteArray = SelfPairingJson.canonical(JsonObject(mapOf(
        "format" to JsonPrimitive("autplay-account-recovery"), "version" to JsonPrimitive(1),
        "server_instance_id" to JsonPrimitive(identity.serverInstanceId),
        "identity_epoch" to JsonPrimitive(identity.epoch),
        "identity_thumbprint_sha256" to JsonPrimitive(identity.thumbprint),
        "api_origin" to JsonPrimitive(identity.apiOrigin), "stream_origin" to JsonPrimitive(identity.streamOrigin),
        "account_id" to JsonPrimitive(accountId), "account_label" to JsonPrimitive(label),
        "code" to JsonPrimitive(code.toString(Charsets.US_ASCII)),
    ))).also { require(it.size <= 4096) }

    companion object {
        fun parse(bytes: ByteArray, allowDevelopmentHttp: Boolean = false): AccountRecoveryDocument {
            val value = SelfPairingJson.parse(bytes, 4096)
            require(value.keys == setOf("format", "version", "server_instance_id", "identity_epoch",
                "identity_thumbprint_sha256", "api_origin", "stream_origin", "account_id", "account_label", "code"))
            require(value.text("format") == "autplay-account-recovery" && value.integer("version") == 1L)
            val identity = SelfPairingIdentity.parse(JsonObject(mapOf(
                "expected_server_instance_id" to value.getValue("server_instance_id"),
                "expected_identity_epoch" to value.getValue("identity_epoch"),
                "expected_identity_thumbprint_sha256" to value.getValue("identity_thumbprint_sha256"),
                "expected_api_origin" to value.getValue("api_origin"),
                "expected_stream_origin" to value.getValue("stream_origin"),
            )), allowDevelopmentHttp)
            val code = AccountRecoveryProof.normalize(value.text("code"))
            return try { AccountRecoveryDocument(identity, value.text("account_id"), value.text("account_label"), code.copyOf()) }
            finally { code.fill(0) }
        }
    }
}

object AccountRecoveryProof {
    const val ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    private val random = SecureRandom()
    fun code(): ByteArray = ByteArray(32).also(random::nextBytes).also { bytes ->
        bytes.indices.forEach { bytes[it] = ALPHABET[bytes[it].toInt() and 31].code.toByte() }
    }
    fun normalize(value: String): ByteArray {
        require(value.length <= 96 && value.all { it.code < 128 }) { "recovery_code_invalid" }
        val text = value.filterNot { it in " \t\r\n-" }.uppercase()
        require(text.length == 32 && text.all { it in ALPHABET }) { "recovery_code_invalid" }
        return text.toByteArray(Charsets.US_ASCII)
    }
    fun verifier(identity: SelfPairingIdentity, accountId: String, code: ByteArray): String {
        requireCanonicalUuid(accountId)
        require(code.size == 32 && code.all { it.toInt().toChar() in ALPHABET })
        val server = UUID.fromString(identity.serverInstanceId)
        val account = UUID.fromString(accountId)
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update("autplay:account-recovery-code:v1\u0000".toByteArray(Charsets.US_ASCII))
        digest.update(ByteBuffer.allocate(32).putLong(server.mostSignificantBits).putLong(server.leastSignificantBits)
            .putLong(account.mostSignificantBits).putLong(account.leastSignificantBits).array())
        return digest.digest(code).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    fun device(keys: M5DeviceKeyStore, alias: String, name: String, version: String): Map<String, JsonElement> {
        require(name.length in 1..120 && version.length in 1..32)
        require((name + version).none { it.code < 32 || it.code == 127 })
        return mapOf(
            "device_public_key_spki_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(keys.publicKeySpki(alias))),
            "device_key_thumbprint_sha256" to JsonPrimitive(keys.publicKeyThumbprintSha256(alias)),
            "device_name" to JsonPrimitive(name), "platform" to JsonPrimitive("ANDROID"), "app_version" to JsonPrimitive(version),
        )
    }
    fun request(kind: String, identity: SelfPairingIdentity, accountId: String, fields: Map<String, JsonElement>,
                now: Instant, keys: M5DeviceKeyStore? = null, alias: String? = null,
                operationId: String = UUID.randomUUID().toString()): ByteArray {
        require(kind in setOf("configure", "preview", "recover"))
        requireCanonicalUuid(accountId)
        requireCanonicalUuid(operationId)
        val common = identity.fields() + mapOf("contract_version" to JsonPrimitive("v1"),
            "schema_version" to JsonPrimitive(1), "account_id" to JsonPrimitive(accountId),
            "operation_id" to JsonPrimitive(operationId), "requested_at" to JsonPrimitive(now.toString()))
        require(fields.keys.intersect(common.keys + setOf("request_sha256", "device_signature_b64url")).isEmpty())
        val unsigned = SelfPairingJson.canonical(JsonObject(common + fields))
        return try {
            val proof = if (kind == "configure") mapOf("request_sha256" to JsonPrimitive(SelfPairingProof.hash(unsigned)))
            else M5RequestSigner.sign(requireNotNull(keys), requireNotNull(alias), "autplay:account-recovery:$kind:v1\n", unsigned).let {
                mapOf("request_sha256" to JsonPrimitive(it.requestSha256), "device_signature_b64url" to JsonPrimitive(it.signatureB64Url))
            }
            SelfPairingJson.canonical(JsonObject(common + fields + proof)).also { require(it.size <= 8192) }
        } finally { unsigned.fill(0) }
    }
}

internal fun JsonObject.recoveryBoolean(name: String): Boolean {
    val value = this[name] as? JsonPrimitive ?: error("recovery_response_invalid")
    require(!value.isString && value.content in setOf("true", "false"))
    return value.content == "true"
}

data class AccountRecoveryPreview(val accountId: String, val label: String, val role: String, val generation: Long,
    val deletion: AccountDeletionReceipt? = null) {
    companion object {
        internal fun parse(value: JsonObject, request: JsonObject): AccountRecoveryPreview {
            require(value.keys == setOf("contract_version", "schema_version", "operation_id", "server_instance_id",
                "identity_epoch", "account_id", "account_label", "role", "code_generation", "confirmation_required"))
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L)
            require(value.text("operation_id") == request.text("operation_id") && value.text("account_id") == request.text("account_id"))
            require(value.text("server_instance_id") == request.text("expected_server_instance_id") && value.integer("identity_epoch") == request.integer("expected_identity_epoch"))
            require(value.recoveryBoolean("confirmation_required") && value.integer("code_generation") > 0)
            require(value.text("role") in setOf("OWNER", "ADMIN", "USER"))
            val label = value.text("account_label")
            require(label.length in 1..200 && label.none { it.code < 32 || it.code == 127 })
            return AccountRecoveryPreview(value.text("account_id"), label, value.text("role"), value.integer("code_generation"))
        }
    }
}

class AccountRecoveryResult(val bindingCommitId: String, val serverId: String, val userId: String,
    val deviceId: String, val sessionId: String, val access: ByteArray, val accessExpiresAt: Instant,
    val refreshExpiresAt: Instant) : AutoCloseable {
    override fun close() = access.fill(0)
    companion object {
        internal fun parse(value: JsonObject, request: JsonObject, now: Instant): AccountRecoveryResult {
            val baseKeys = setOf("contract_version", "schema_version", "operation_id", "binding_commit_id",
                "server_instance_id", "user_id", "account_label", "device_id", "session_id", "refresh_generation",
                "refresh_absolute_expires_at", "receipt_expires_at", "code_generation", "access_token", "access_expires_at", "replayed")
            val recoveredOutcome = value["outcome_recovered"]?.let {
                require(it is JsonPrimitive && it.booleanOrNull == true)
                true
            } ?: false
            require(value.keys == if (recoveredOutcome) baseKeys + "outcome_recovered" else baseKeys)
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L)
            require(value.text("operation_id") == request.text("operation_id") && value.text("binding_commit_id") == request.text("binding_commit_id"))
            require(value.text("server_instance_id") == request.text("expected_server_instance_id") && value.text("user_id") == request.text("confirmed_account_id"))
            require(value.integer("code_generation") == request.integer("expected_code_generation") + 1 && value.integer("refresh_generation") == 0L)
            val replayed = value.recoveryBoolean("replayed")
            require(!recoveredOutcome || replayed)
            listOf("device_id", "session_id", "user_id", "server_instance_id", "binding_commit_id").forEach { requireCanonicalUuid(value.text(it)) }
            val accessExpires = value.instant("access_expires_at")
            val refreshExpires = value.instant("refresh_absolute_expires_at")
            require(accessExpires.isAfter(now) && !accessExpires.isAfter(now.plusSeconds(1020)) && refreshExpires.isAfter(accessExpires))
            require(recoveredOutcome || value.instant("receipt_expires_at").isAfter(now))
            val token = value.text("access_token")
            require(token.length in 1..4096 && token.all { it.code in 33..126 })
            return AccountRecoveryResult(value.text("binding_commit_id"), value.text("server_instance_id"), value.text("user_id"),
                value.text("device_id"), value.text("session_id"), token.toByteArray(Charsets.US_ASCII), accessExpires, refreshExpires)
        }
    }
}
