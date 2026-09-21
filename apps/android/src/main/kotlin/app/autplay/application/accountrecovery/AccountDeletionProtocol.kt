package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.instant
import app.autplay.application.selfpairing.text
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import java.time.Duration
import java.time.Instant
import java.util.UUID
import java.util.Base64
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonNull

enum class AccountRestorationPurpose { RECOVERY, DELETE_CANCEL }

/** Deletion proofs cannot authorize ordinary recovery or another deletion operation. */
object AccountDeletionProof {
    fun request(kind: String, identity: SelfPairingIdentity, accountId: String,
        fields: Map<String, JsonElement>, now: Instant, keys: M5DeviceKeyStore, alias: String,
        operationId: String = UUID.randomUUID().toString()): ByteArray {
        require(kind in setOf("request", "preview", "cancel"))
        requireCanonicalUuid(accountId); requireCanonicalUuid(operationId)
        val common = identity.fields() + mapOf("contract_version" to JsonPrimitive("v1"),
            "schema_version" to JsonPrimitive(1), "account_id" to JsonPrimitive(accountId),
            "operation_id" to JsonPrimitive(operationId), "requested_at" to JsonPrimitive(now.toString()))
        require(fields.keys.intersect(common.keys + setOf("request_sha256", "device_signature_b64url")).isEmpty())
        val unsigned = SelfPairingJson.canonical(JsonObject(common + fields))
        return try {
            val proof = M5RequestSigner.sign(keys, alias, "autplay:account-deletion:$kind:v1\n", unsigned)
            SelfPairingJson.canonical(JsonObject(common + fields + mapOf(
                "request_sha256" to JsonPrimitive(proof.requestSha256),
                "device_signature_b64url" to JsonPrimitive(proof.signatureB64Url)))).also { require(it.size <= 8192) }
        } finally { unsigned.fill(0) }
    }
}

data class AccountDeletionStatus(val accountId: String, val authorityGeneration: Long,
    val codeGeneration: Long, val canRequest: Boolean, val reason: String?) {
    companion object {
        internal fun parse(value: JsonObject, accountId: String): AccountDeletionStatus {
            require(value.keys == setOf("contract_version", "schema_version", "account_id",
                "authority_generation", "code_generation", "can_request", "reason"))
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L && value.text("account_id") == accountId)
            require(value.integer("authority_generation") > 0 && value.integer("code_generation") >= 0)
            val allowed = value.recoveryBoolean("can_request")
            val reason = (value["reason"] as? JsonPrimitive)?.let { if (it.isString) it.content else null }
            require(value["reason"] == JsonNull || (value["reason"] as? JsonPrimitive)?.isString == true)
            require(if (allowed) reason == null && value.integer("code_generation") > 0
                else reason in setOf("last_owner_required", "recovery_setup_required", "deletion_initializing"))
            return AccountDeletionStatus(accountId, value.integer("authority_generation"), value.integer("code_generation"), allowed, reason)
        }
    }
}

data class AccountDeletionReceipt(val requestId: String, val accountId: String, val state: String,
    val revision: Long, val requestedAt: Instant, val cancelBefore: Instant) {
    companion object {
        internal val FIELDS = setOf("contract_version", "schema_version", "deletion_request_id",
            "account_id", "state", "revision", "requested_at", "cancel_before", "replayed")
        internal fun fromJournal(pending: JsonObject): JsonObject {
            val raw = Base64.getDecoder().decode(pending.text("receipt_b64"))
            return try { SelfPairingJson.parse(raw, 16_384) } finally { raw.fill(0) }
        }
        internal fun journalValue(value: JsonObject): JsonPrimitive {
            val raw = SelfPairingJson.canonical(value)
            return try { require(raw.size <= 16_384); JsonPrimitive(Base64.getEncoder().encodeToString(raw)) }
            finally { raw.fill(0) }
        }
        internal fun parse(value: JsonObject, accountId: String, requestId: String? = null): AccountDeletionReceipt {
            require(value.keys == FIELDS)
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L && value.text("account_id") == accountId)
            requireCanonicalUuid(value.text("deletion_request_id"))
            require(requestId == null || value.text("deletion_request_id") == requestId)
            val state = value.text("state")
            require(state in setOf("PENDING", "CANCELLED", "PURGING"))
            require(value.integer("revision") == if (state == "PENDING") 1L else 2L)
            val requested = value.instant("requested_at")
            val deadline = value.instant("cancel_before")
            require(Duration.between(requested, deadline) == Duration.ofDays(30))
            value.recoveryBoolean("replayed")
            return AccountDeletionReceipt(value.text("deletion_request_id"), accountId, state, value.integer("revision"), requested, deadline)
        }
        internal fun preview(value: JsonObject, request: JsonObject, now: Instant): AccountRecoveryPreview {
            require(value.keys == FIELDS + setOf("account_label", "code_generation", "confirmation_required"))
            val receipt = parse(JsonObject(value.filterKeys { it in FIELDS }), request.text("account_id"))
            require(receipt.state == "PENDING" && receipt.cancelBefore.isAfter(now))
            require(value.recoveryBoolean("confirmation_required") && value.integer("code_generation") > 0)
            val label = value.text("account_label")
            require(label.length in 1..200 && label.none { it.code < 32 || it.code == 127 })
            return AccountRecoveryPreview(receipt.accountId, label, "", value.integer("code_generation"), receipt)
        }
    }
}

/** A durable server veto of the exact request, never an ACTIVE-status inference. */
data class AccountDeletionNotAccepted(val requestId: String, val accountId: String, val resolvedAt: Instant) {
    companion object {
        internal fun parse(value: JsonObject, request: JsonObject): AccountDeletionNotAccepted {
            require(value.keys == setOf("contract_version", "schema_version", "deletion_request_id",
                "account_id", "request_sha256", "state", "resolved_at", "replayed"))
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L)
            require(value.text("state") == "NOT_ACCEPTED")
            requireCanonicalUuid(value.text("deletion_request_id")); requireCanonicalUuid(value.text("account_id"))
            require(value.text("deletion_request_id") == request.text("operation_id") && value.text("account_id") == request.text("account_id"))
            require(value.text("request_sha256").matches(Regex("[0-9a-f]{64}")) && value.text("request_sha256") == request.text("request_sha256"))
            val resolved = value.instant("resolved_at")
            require(resolved.isAfter(request.instant("requested_at").plusSeconds(120)))
            value.recoveryBoolean("replayed")
            return AccountDeletionNotAccepted(value.text("deletion_request_id"), value.text("account_id"), resolved)
        }
    }
}
