package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.application.profilepairing.PairingNetworkResult
import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

sealed interface SelfPairingRecipientState {
    data object Idle : SelfPairingRecipientState
    data object Working : SelfPairingRecipientState
    data class AwaitingTrust(val serverLabel: String, val identity: SelfPairingIdentity, val expiresAt: Instant) : SelfPairingRecipientState
    data class WaitingForApproval(val status: SelfPairingStatus) : SelfPairingRecipientState
    data class AwaitingAccountConfirmation(val status: SelfPairingStatus) : SelfPairingRecipientState
    data class Finished(val status: String) : SelfPairingRecipientState
    data class Blocked(val code: String, val pending: Boolean) : SelfPairingRecipientState
    data object Connected : SelfPairingRecipientState
}

/** Non-secret facts required to recognize a binding already committed before process death. */
data class SelfPairingBindingIntent(
    val identity: SelfPairingIdentity, val generationId: String, val ceremonyId: String,
    val bindingCommitId: String, val confirmedAccountId: String, val keyAlias: String, val keyThumbprint: String,
)

interface SelfPairingBindingCommitter {
    suspend fun isDurable(intent: SelfPairingBindingIntent): Boolean
    suspend fun commit(intent: SelfPairingBindingIntent, result: SelfPairingExchangeResult, refresh: ByteArray, identitySpki: ByteArray): Boolean
}

/**
 * One recipient journal survives claim, explicit account confirmation, exchange and local binding.
 * All irreversible request bytes and secrets are encrypted before sending. Failures retain them.
 */
class SelfPairingRecipientRuntime(
    private val credentials: CredentialStore,
    private val keys: M5DeviceKeyStore,
    private val transport: SelfPairingTransport,
    private val discovery: ProfilePairingPort,
    private val binding: SelfPairingBindingCommitter,
    private val activeProfile: ActiveProfileGate,
    private val firstBind: FirstBindCeremonyGate,
    private val deviceName: String,
    private val appVersion: String,
    private val now: () -> Instant = Instant::now,
    private val allowDevelopmentHttp: Boolean = false,
) {
    private val store = SelfPairingPendingStore(credentials)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow<SelfPairingRecipientState>(SelfPairingRecipientState.Idle)
    val state: StateFlow<SelfPairingRecipientState> = mutable
    private var proposal: Proposal? = null
    private var lastPoll: Instant? = null

    suspend fun inspectQr(raw: ByteArray) = operation {
        require(firstBind.reserve(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        require(store.read(ROLE) == null) { "SELF_PAIRING_RESUME_REQUIRED" }
        requireUnbound()
        val qr = SelfPairingQr.parse(raw, now(), allowDevelopmentHttp)
        try {
            require(SelfPairingRole.entries.none { it.slot.value == qr.identity.serverInstanceId })
            val discovered = discovery.discovery(qr.identity.apiOrigin)
            require(discovered is PairingNetworkResult.Success) { "SELF_PAIRING_DISCOVERY_REQUIRED" }
            val doc = discovered.value
            try {
                require(doc.identity.serverInstanceId == qr.identity.serverInstanceId &&
                    doc.identity.identityEpoch == qr.identity.epoch &&
                    doc.identity.identityThumbprintSha256 == qr.identity.thumbprint &&
                    doc.apiOrigin == qr.identity.apiOrigin && doc.streamOrigin == qr.identity.streamOrigin &&
                    doc.expiresAt.isAfter(now())) { "SELF_PAIRING_IDENTITY_CHANGED" }
                require(SelfPairingProof.hash(doc.identityPublicKeySpki) == qr.identity.thumbprint)
                proposal?.close()
                proposal = Proposal(qr, doc.identityPublicKeySpki.copyOf())
                mutable.value = SelfPairingRecipientState.AwaitingTrust(doc.labelHint, qr.identity, qr.expiresAt)
            } finally { doc.identityPublicKeySpki.fill(0) }
        } catch (failure: Exception) { qr.close(); throw failure }
    }

    /** Called only by the explicit trust button for the currently displayed discovery. */
    suspend fun confirmTrust() = operation {
        val current = requireNotNull(proposal) { "SELF_PAIRING_TRUST_REQUIRED" }
        require(mutable.value is SelfPairingRecipientState.AwaitingTrust)
        require(firstBind.isReservedBy(OWNER))
        requireUnbound()
        require(current.qr.expiresAt.isAfter(now())) { "SELF_PAIRING_EXPIRED" }
        require(store.read(ROLE) == null) { "SELF_PAIRING_RESUME_REQUIRED" }
        val generation = UUID.randomUUID().toString()
        val alias = "autplay.self.pairing.$generation"
        keys.ensure(alias)
        val secret = SelfPairingProof.secret()
        try {
            val claim = SelfPairingProof.claim(current.qr, secret, deviceName, appVersion, keys, alias)
            try {
                val pending = JsonObject(current.qr.identity.fields() + mapOf(
                    "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("CLAIM_PENDING"),
                    "ceremony_id" to JsonPrimitive(current.qr.ceremonyId), "generation_id" to JsonPrimitive(generation),
                    "key_alias" to JsonPrimitive(alias), "identity_spki_b64" to JsonPrimitive(b64(current.identitySpki)),
                    "expires_at" to JsonPrimitive(current.qr.expiresAt.toString()),
                    "claim_request_b64" to JsonPrimitive(b64(claim)),
                    "poll_secret" to JsonPrimitive(secret.toString(Charsets.US_ASCII)),
                    "rendezvous_secret" to JsonPrimitive(current.qr.rendezvousSecret.toString(Charsets.US_ASCII)),
                ))
                store.write(ROLE, pending)
                proposal = null
                current.close()
                claimPending(pending)
            } finally { claim.fill(0) }
        } finally { secret.fill(0) }
    }

    /** Startup/retry uses the encrypted exact request, including after the QR deadline. */
    suspend fun resume() = operation {
        val pending = store.read(ROLE) ?: return@operation
        require(firstBind.reserve(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        validatePending(pending)
        if (pending.text("stage") == "TERMINAL") {
            mutable.value = SelfPairingRecipientState.Finished(pending.text("terminal_state"))
            return@operation
        }
        if (pending.text("stage") == "EXCHANGE_PENDING" && finishIfDurable(pending)) return@operation
        requireUnbound()
        if (pending.text("stage") != "EXCHANGE_PENDING" && !pending.instant("expires_at").isAfter(now())) {
            mutable.value = SelfPairingRecipientState.Finished("EXPIRED")
            return@operation
        }
        when (pending.text("stage")) {
            "CLAIM_PENDING" -> claimPending(pending)
            "WAITING" -> pollPending(pending)
            "EXCHANGE_PENDING" -> exchangePending(pending)
            else -> error("SELF_PAIRING_PENDING_INVALID")
        }
    }

    /** UI invokes this only while its pairing screen is visible and Lifecycle.STARTED. */
    suspend fun poll() = operation {
        val pending = store.read(ROLE) ?: return@operation
        require(firstBind.reserve(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        validatePending(pending)
        if (pending.text("stage") == "TERMINAL") {
            mutable.value = SelfPairingRecipientState.Finished(pending.text("terminal_state"))
            return@operation
        }
        if (pending.text("stage") == "EXCHANGE_PENDING") {
            reconcileExchange(pending)
            return@operation
        }
        requireUnbound()
        if (!pending.instant("expires_at").isAfter(now())) {
            mutable.value = SelfPairingRecipientState.Finished("EXPIRED")
            return@operation
        }
        pollPending(pending)
    }

    suspend fun confirmAccount(accountId: String) = operation {
        require(firstBind.isReservedBy(OWNER)) { "FIRST_BIND_CEREMONY_BUSY" }
        val visible = mutable.value as? SelfPairingRecipientState.AwaitingAccountConfirmation
            ?: error("SELF_PAIRING_ACCOUNT_CONFIRMATION_REQUIRED")
        require(visible.status.accountId == accountId)
        val pending = requireNotNull(store.read(ROLE))
        require(pending.text("stage") == "WAITING")
        validatePending(pending)
        requireUnbound()
        val claim = claim(pending)
        require(visible.status.claimHash == claim.text("request_sha256"))
        val refresh = SelfPairingProof.secret()
        try {
            val request = SelfPairingProof.request("exchange", identity(pending), pending.text("ceremony_id"), mapOf(
                "exchange_id" to JsonPrimitive(UUID.randomUUID().toString()),
                "binding_commit_id" to JsonPrimitive(UUID.randomUUID().toString()),
                "claim_id" to JsonPrimitive(claim.text("claim_id")), "claim_request_sha256" to JsonPrimitive(claim.text("request_sha256")),
                "approval_operation_id" to JsonPrimitive(requireNotNull(visible.status.approvalOperationId)),
                "confirmed_account_id" to JsonPrimitive(accountId), "next_refresh_token_sha256" to JsonPrimitive(SelfPairingProof.hash(refresh)),
            ), keys, pending.text("key_alias"), now())
            try {
                val updated = JsonObject(pending + mapOf("stage" to JsonPrimitive("EXCHANGE_PENDING"),
                    "exchange_request_b64" to JsonPrimitive(b64(request)), "next_refresh_token" to JsonPrimitive(refresh.toString(Charsets.US_ASCII))))
                store.write(ROLE, updated)
                exchangePending(updated)
            } finally { request.fill(0) }
        } finally { refresh.fill(0) }
    }

    /** A request with an unknown remote result is never discarded as a local cancellation. */
    suspend fun dismiss() = operation {
        val pending = store.read(ROLE)
        if (pending != null) {
            require(pending.text("stage") != "EXCHANGE_PENDING") { "SELF_PAIRING_RESUME_REQUIRED" }
            val visible = mutable.value as? SelfPairingRecipientState.Finished
            require(visible?.status in setOf("CANCELLED", "REJECTED", "EXPIRED")) { "SELF_PAIRING_SOURCE_CANCEL_REQUIRED" }
            store.clear(ROLE)
            runCatching { keys.delete(pending.text("key_alias")) }
        }
        proposal?.close(); proposal = null
        firstBind.release(OWNER)
        mutable.value = SelfPairingRecipientState.Idle
    }

    private suspend fun claimPending(pending: JsonObject) {
        mutable.value = SelfPairingRecipientState.Working
        val result = recipient(pending, "claim", "rendezvous_secret", "claim_request_b64")
        val status = validateStatus(pending, result)
        val updated = JsonObject(pending + ("stage" to JsonPrimitive("WAITING")))
        store.write(ROLE, updated)
        present(status)
    }

    private suspend fun pollPending(pending: JsonObject) {
        if (lastPoll?.plusSeconds(2)?.isAfter(now()) == true) return
        lastPoll = now()
        val claim = claim(pending)
        val request = SelfPairingProof.request("poll", identity(pending), pending.text("ceremony_id"), mapOf(
            "claim_id" to JsonPrimitive(claim.text("claim_id")), "claim_request_sha256" to JsonPrimitive(claim.text("request_sha256")),
        ), keys, pending.text("key_alias"), now())
        val secret = pending.text("poll_secret").toByteArray(Charsets.US_ASCII)
        try { present(validateStatus(pending, transport.recipient(identity(pending), "poll", pending.text("ceremony_id"), secret, request))) }
        finally { request.fill(0); secret.fill(0) }
    }

    private suspend fun exchangePending(pending: JsonObject) {
        if (finishIfDurable(pending)) return
        mutable.value = SelfPairingRecipientState.Working
        val document = try { recipient(pending, "exchange", "poll_secret", "exchange_request_b64") }
        catch (failure: SelfPairingRemoteFailure) {
            if (reconcileExchange(pending)) return
            throw failure
        }
        val request = decoded(pending, "exchange_request_b64")
        val result = SelfPairingExchangeResult.parse(document, request, now())
        val refresh = pending.text("next_refresh_token").toByteArray(Charsets.US_ASCII)
        val spki = Base64.getDecoder().decode(pending.text("identity_spki_b64"))
        try {
            require(binding.commit(intent(pending), result, refresh, spki)) { "SELF_PAIRING_BINDING_PENDING" }
            require(finishIfDurable(pending)) { "SELF_PAIRING_BINDING_PENDING" }
        } finally { result.close(); refresh.fill(0); spki.fill(0) }
    }

    private suspend fun finishIfDurable(pending: JsonObject): Boolean {
        if (!binding.isDurable(intent(pending))) return false
        store.clear(ROLE)
        firstBind.release(OWNER)
        mutable.value = SelfPairingRecipientState.Connected
        return true
    }

    private suspend fun reconcileExchange(pending: JsonObject): Boolean {
        if (finishIfDurable(pending)) return true
        if (lastPoll?.plusSeconds(2)?.isAfter(now()) == true) return false
        lastPoll = now()
        val claim = claim(pending)
        val request = SelfPairingProof.request("poll", identity(pending), pending.text("ceremony_id"), mapOf(
            "claim_id" to JsonPrimitive(claim.text("claim_id")), "claim_request_sha256" to JsonPrimitive(claim.text("request_sha256")),
        ), keys, pending.text("key_alias"), now())
        val secret = pending.text("poll_secret").toByteArray(Charsets.US_ASCII)
        val status = try { validateStatus(pending, transport.recipient(identity(pending), "poll", pending.text("ceremony_id"), secret, request)) }
        finally { request.fill(0); secret.fill(0) }
        if (status.state !in setOf("CANCELLED", "REJECTED", "EXPIRED")) return false
        store.write(ROLE, JsonObject((pending - setOf("exchange_request_b64", "next_refresh_token")) + mapOf(
            "stage" to JsonPrimitive("TERMINAL"), "terminal_state" to JsonPrimitive(status.state),
        )))
        mutable.value = SelfPairingRecipientState.Finished(status.state)
        return true
    }

    private suspend fun recipient(pending: JsonObject, kind: String, secretField: String, requestField: String): JsonObject {
        val secret = pending.text(secretField).toByteArray(Charsets.US_ASCII)
        val request = Base64.getDecoder().decode(pending.text(requestField))
        return try { transport.recipient(identity(pending), kind, pending.text("ceremony_id"), secret, request) }
        finally { request.fill(0); secret.fill(0) }
    }

    private suspend fun requireUnbound() {
        require(!activeProfile.hasActiveProfile() && !credentials.hasPublicAccessPendingRegistration()) { "SELF_PAIRING_ACTIVE_PROFILE_FORBIDDEN" }
    }

    private fun validatePending(pending: JsonObject) {
        require(pending.integer("schema_version") == 1L)
        require(pending.text("stage") in setOf("CLAIM_PENDING", "WAITING", "EXCHANGE_PENDING", "TERMINAL"))
        if (pending.text("stage") == "TERMINAL") {
            require(pending.text("terminal_state") in setOf("CANCELLED", "REJECTED", "EXPIRED"))
            return
        }
        val identity = identity(pending)
        require(SelfPairingRole.entries.none { it.slot.value == identity.serverInstanceId })
        val claim = claim(pending)
        require(SelfPairingIdentity.parse(claim, allowDevelopmentHttp) == identity)
        require(claim.text("ceremony_id") == pending.text("ceremony_id"))
        require(SelfPairingProof.hash(Base64.getDecoder().decode(pending.text("identity_spki_b64"))) == identity.thumbprint)
        require(keys.publicKeyThumbprintSha256(pending.text("key_alias")) == claim.text("device_key_thumbprint_sha256")) { "SELF_PAIRING_KEY_LOST" }
        require(b64(keys.publicKeySpki(pending.text("key_alias"))) == claim.text("device_public_key_spki_b64")) { "SELF_PAIRING_KEY_LOST" }
        val poll = pending.text("poll_secret").toByteArray(Charsets.US_ASCII)
        try { SelfPairingProof.requireSecret(poll); require(SelfPairingProof.hash(poll) == claim.text("poll_secret_sha256")) }
        finally { poll.fill(0) }
        if (pending.text("stage") == "EXCHANGE_PENDING") {
            val exchange = decoded(pending, "exchange_request_b64")
            require(SelfPairingIdentity.parse(exchange, allowDevelopmentHttp) == identity)
            require(exchange.text("ceremony_id") == pending.text("ceremony_id"))
            require(exchange.text("claim_id") == claim.text("claim_id") && exchange.text("claim_request_sha256") == claim.text("request_sha256"))
            val refresh = pending.text("next_refresh_token").toByteArray(Charsets.US_ASCII)
            try { SelfPairingProof.requireSecret(refresh); require(SelfPairingProof.hash(refresh) == exchange.text("next_refresh_token_sha256")) }
            finally { refresh.fill(0) }
        }
    }

    private fun validateStatus(pending: JsonObject, value: JsonObject): SelfPairingStatus {
        val status = SelfPairingStatus.parse(value, identity(pending), pending.text("ceremony_id"))
        val claim = claim(pending)
        require(status.claimId == claim.text("claim_id") && status.claimHash == claim.text("request_sha256") && status.keyThumbprint == claim.text("device_key_thumbprint_sha256"))
        require(status.expiresAt == pending.instant("expires_at"))
        return status
    }

    private fun present(status: SelfPairingStatus) {
        mutable.value = when {
            status.state == "APPROVED" -> SelfPairingRecipientState.AwaitingAccountConfirmation(status)
            status.state == "EXCHANGED" -> SelfPairingRecipientState.Blocked("SELF_PAIRING_RESUME_REQUIRED", pending = true)
            status.terminal -> SelfPairingRecipientState.Finished(status.state)
            else -> SelfPairingRecipientState.WaitingForApproval(status)
        }
    }

    private suspend fun operation(block: suspend () -> Unit) = mutex.withLock {
        try { block() } catch (failure: CancellationException) { throw failure }
        catch (failure: Exception) {
            val pending = runCatching { store.read(ROLE) != null }.getOrDefault(true)
            if (!pending && proposal == null) firstBind.release(OWNER)
            val code = if (failure is SelfPairingRemoteFailure) failure.code else failure.message?.takeIf { it in SAFE_ERRORS } ?: "self_pairing_unavailable"
            mutable.value = SelfPairingRecipientState.Blocked(code, pending)
        }
    }

    private fun identity(pending: JsonObject) = SelfPairingIdentity.parse(pending, allowDevelopmentHttp)
    private fun claim(pending: JsonObject) = decoded(pending, "claim_request_b64")
    private fun decoded(pending: JsonObject, name: String): JsonObject {
        val raw = Base64.getDecoder().decode(pending.text(name))
        return try { SelfPairingJson.parse(raw) } finally { raw.fill(0) }
    }
    private fun intent(pending: JsonObject): SelfPairingBindingIntent {
        val exchange = decoded(pending, "exchange_request_b64")
        return SelfPairingBindingIntent(identity(pending), pending.text("generation_id"), pending.text("ceremony_id"), exchange.text("binding_commit_id"), exchange.text("confirmed_account_id"), pending.text("key_alias"), claim(pending).text("device_key_thumbprint_sha256"))
    }
    private class Proposal(val qr: SelfPairingQr, val identitySpki: ByteArray) : AutoCloseable {
        override fun close() { qr.close(); identitySpki.fill(0) }
    }
    private companion object {
        val OWNER = FirstBindCeremonyOwner.SELF_DEVICE_PAIRING
        val ROLE = SelfPairingRole.RECIPIENT
        val SAFE_ERRORS = setOf("FIRST_BIND_CEREMONY_BUSY", "SELF_PAIRING_RESUME_REQUIRED", "SELF_PAIRING_ACTIVE_PROFILE_FORBIDDEN", "SELF_PAIRING_DISCOVERY_REQUIRED", "SELF_PAIRING_IDENTITY_CHANGED", "SELF_PAIRING_EXPIRED", "SELF_PAIRING_KEY_LOST", "SELF_PAIRING_BINDING_PENDING", "SELF_PAIRING_SOURCE_CANCEL_REQUIRED")
        fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    }
}
