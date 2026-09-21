package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.requireCanonicalUuid
import java.time.Instant
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Personal label appears only after approval on the recipient. No bearer material. */
data class SelfPairingStatus(
    val ceremonyId: String, val state: String, val revision: Long, val expiresAt: Instant,
    val claimId: String?, val claimHash: String?, val deviceName: String?, val keyThumbprint: String?,
    val comparisonCode: String?, val accountId: String?, val accountLabel: String?,
    val approvalOperationId: String?, val deviceId: String?,
) {
    val terminal: Boolean get() = state in setOf("EXCHANGED", "CANCELLED", "REJECTED", "EXPIRED")

    companion object {
        fun parse(value: JsonObject, identity: SelfPairingIdentity, ceremonyId: String, source: Boolean = false): SelfPairingStatus {
            require(value.keys.containsAll(REQUIRED) && ALLOWED.containsAll(value.keys))
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L)
            require(value.text("ceremony_id") == ceremonyId)
            require(value.integer("revision") in 1..9_007_199_254_740_991L && value.integer("retry_after_seconds") == 2L)
            val state = value.text("state")
            require(state in setOf("OPEN", "CLAIMED", "APPROVED", "EXCHANGED", "CANCELLED", "REJECTED", "EXPIRED"))
            val claimed = CLAIM_MEMBERS.any { it in value }
            if (claimed) {
                require(value.keys.containsAll(CLAIM_MEMBERS))
                requireCanonicalUuid(value.text("claim_id"))
                val name = value.text("device_name")
                require(name.length in 1..120 && name.none { it.code < 32 })
                require(value.text("comparison_code") == SelfPairingProof.comparisonCode(identity, ceremonyId, value.text("claim_request_sha256"), value.text("device_key_thumbprint_sha256")))
            }
            require(state != "OPEN" || !claimed)
            if (state in setOf("CLAIMED", "APPROVED", "EXCHANGED", "REJECTED")) require(claimed)
            require(("account_id" in value) == ("account_label" in value))
            if ("account_id" in value) {
                require(source || state in setOf("APPROVED", "EXCHANGED"))
                requireCanonicalUuid(value.text("account_id"))
                require(value.text("account_label").length in 1..240)
            }
            if ("approval_operation_id" in value) requireCanonicalUuid(value.text("approval_operation_id"))
            if (state in setOf("APPROVED", "EXCHANGED")) require(value.keys.containsAll(setOf("approval_operation_id", "account_id")))
            if ("device_id" in value) { require(state == "EXCHANGED"); requireCanonicalUuid(value.text("device_id")) }
            if (state == "EXCHANGED") require("device_id" in value)
            fun optional(name: String): String? = value[name]?.let { value.text(name) }
            return SelfPairingStatus(ceremonyId, state, value.integer("revision"), value.instant("expires_at"), optional("claim_id"), optional("claim_request_sha256"), optional("device_name"), optional("device_key_thumbprint_sha256"), optional("comparison_code"), optional("account_id"), optional("account_label"), optional("approval_operation_id"), optional("device_id"))
        }

        private val REQUIRED = setOf("contract_version", "schema_version", "ceremony_id", "state", "revision", "expires_at", "retry_after_seconds")
        private val CLAIM_MEMBERS = setOf("claim_id", "claim_request_sha256", "device_name", "device_key_thumbprint_sha256", "comparison_code")
        private val ALLOWED = REQUIRED + CLAIM_MEMBERS + setOf("account_id", "account_label", "approval_operation_id", "device_id")
    }
}

/** Tokens are wiped after the normal credential-first commit consumes a copy. */
class SelfPairingExchangeResult(
    val exchangeId: String, val bindingCommitId: String, val serverInstanceId: String,
    val userId: String, val deviceId: String, val sessionId: String,
    val accessExpiresAt: Instant, val refreshExpiresAt: Instant, val accessToken: ByteArray,
) : AutoCloseable {
    override fun close() = accessToken.fill(0)

    companion object {
        fun parse(value: JsonObject, request: JsonObject, now: Instant = Instant.now()): SelfPairingExchangeResult {
            require(value.keys == MEMBERS)
            require(value.text("contract_version") == "v1" && value.integer("schema_version") == 1L)
            for (name in listOf("exchange_id", "binding_commit_id")) require(value.text(name) == request.text(name))
            require(value.text("server_instance_id") == request.text("expected_server_instance_id"))
            require(value.text("user_id") == request.text("confirmed_account_id"))
            for (name in listOf("server_instance_id", "user_id", "device_id", "session_id")) requireCanonicalUuid(value.text(name))
            require(value.integer("refresh_generation") == 0L)
            val replay = value["replayed"] as? JsonPrimitive ?: error("SELF_PAIRING_RESPONSE_INVALID")
            require(!replay.isString && replay.content in setOf("true", "false"))
            val accessExpires = value.instant("access_expires_at")
            val refreshExpires = value.instant("refresh_absolute_expires_at")
            require(accessExpires.isAfter(now) && !accessExpires.isAfter(now.plusSeconds(1020)))
            require(refreshExpires.isAfter(now) && !accessExpires.isAfter(refreshExpires))
            require(value.instant("receipt_expires_at") == refreshExpires.plusSeconds(300))
            val access = value.text("access_token")
            require(access.length in 1..4096 && access.all { it.code in 33..126 })
            return SelfPairingExchangeResult(value.text("exchange_id"), value.text("binding_commit_id"), value.text("server_instance_id"), value.text("user_id"), value.text("device_id"), value.text("session_id"), accessExpires, refreshExpires, access.toByteArray(Charsets.US_ASCII))
        }
        private val MEMBERS = setOf("contract_version", "schema_version", "exchange_id", "binding_commit_id", "server_instance_id", "user_id", "device_id", "session_id", "refresh_generation", "refresh_absolute_expires_at", "receipt_expires_at", "access_token", "access_expires_at", "replayed")
    }
}
