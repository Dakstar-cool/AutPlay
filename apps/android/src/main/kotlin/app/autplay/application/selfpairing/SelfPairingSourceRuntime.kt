package app.autplay.application.selfpairing

import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

data class SelfPairingSourceBinding(val profile: ServerProfileId, val userId: String, val deviceId: String, val familyId: String, val identity: SelfPairingIdentity)
fun interface SelfPairingSourceContext { suspend fun current(): SelfPairingSourceBinding? }

class SettingsSelfPairingSourceContext(private val settings: NonSecretSettingsStore, private val credentials: CredentialStore) : SelfPairingSourceContext {
    override suspend fun current(): SelfPairingSourceBinding? = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        val binding = current.m5Binding ?: return@serialized null
        val profile = current.activeServerProfileId ?: return@serialized null
        val user = current.activeUserId ?: return@serialized null
        val device = current.deviceId ?: return@serialized null
        val api = current.serverBaseUrl ?: return@serialized null
        val stream = current.streamBaseUrl ?: return@serialized null
        val encrypted = credentials.read(profile) ?: return@serialized null
        try {
            val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
            if (envelope.bindingCommitId != binding.bindingCommitId || envelope.sessionFamilyId != binding.sessionFamilyId || envelope.refreshToken == null) return@serialized null
            SelfPairingSourceBinding(profile, user.value, device.value, binding.sessionFamilyId,
                SelfPairingIdentity(binding.serverInstanceId, binding.identityEpoch, binding.identityThumbprintSha256, api, stream))
        } finally { encrypted.fill(0) }
    }
}

sealed interface SelfPairingSourceState {
    data object Idle : SelfPairingSourceState
    data object Working : SelfPairingSourceState
    data object ExpiredStart : SelfPairingSourceState
    class Ready(val status: SelfPairingStatus, val qrPayload: String?) : SelfPairingSourceState
    data class Blocked(val code: String) : SelfPairingSourceState
}

/** Source commands retain an exact operation until its response is durably recorded. */
class SelfPairingSourceRuntime(
    credentials: CredentialStore,
    private val contexts: SelfPairingSourceContext,
    private val transport: SelfPairingTransport,
    private val now: () -> Instant = Instant::now,
) {
    private val store = SelfPairingPendingStore(credentials)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow<SelfPairingSourceState>(SelfPairingSourceState.Idle)
    val state: StateFlow<SelfPairingSourceState> = mutable
    private var lastPoll: Instant? = null

    suspend fun start() = operation {
        if (store.read(ROLE) != null) { resumePending(); return@operation }
        val binding = requireNotNull(contexts.current()) { "SELF_PAIRING_SOURCE_CHANGED" }
        val ceremonyId = UUID.randomUUID().toString()
        val secret = SelfPairingProof.secret()
        try {
            val request = SelfPairingProof.request("start", binding.identity, ceremonyId, mapOf(
                "operation_id" to JsonPrimitive(UUID.randomUUID().toString()),
                "rendezvous_secret_sha256" to JsonPrimitive(SelfPairingProof.hash(secret)),
            ), now = now())
            try {
                store.write(ROLE, JsonObject(binding.identity.fields() + mapOf(
                    "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("START_PENDING"),
                    "ceremony_id" to JsonPrimitive(ceremonyId), "profile_id" to JsonPrimitive(binding.profile.value),
                    "user_id" to JsonPrimitive(binding.userId), "device_id" to JsonPrimitive(binding.deviceId),
                    "family_id" to JsonPrimitive(binding.familyId), "rendezvous_secret" to JsonPrimitive(secret.toString(Charsets.US_ASCII)),
                    "start_request_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(request)),
                )))
                resumePending()
            } finally { request.fill(0) }
        } finally { secret.fill(0) }
    }

    suspend fun resume() = operation { resumePending() }

    /** Caller supplies Lifecycle.STARTED and screen visibility; source requests are at most 1/2s. */
    suspend fun poll() = operation {
        if (lastPoll?.plusSeconds(2)?.isAfter(now()) == true) return@operation
        lastPoll = now()
        val pending = store.read(ROLE) ?: return@operation
        if (pending.text("stage") != "READY") return@operation
        val binding = authority(pending)
        val response = transport.source(binding.identity, binding.profile, "status", pending.text("ceremony_id"))
        publish(pending, binding, response)
    }

    suspend fun decide(action: String, comparisonCode: String? = null) = operation {
        require(action in setOf("APPROVE", "REJECT", "CANCEL"))
        val pending = requireNotNull(store.read(ROLE))
        authority(pending)
        if (pending.text("stage") == "DECISION_PENDING") { resumePending(); return@operation }
        require(pending.text("stage") == "READY")
        val visible = mutable.value as? SelfPairingSourceState.Ready ?: error("SELF_PAIRING_REVIEW_REQUIRED")
        val status = visible.status
        require(!status.terminal && status.ceremonyId == pending.text("ceremony_id"))
        if (action == "APPROVE") require(status.state == "CLAIMED" && comparisonCode == status.comparisonCode)
        else require(comparisonCode == null)
        val binding = authority(pending)
        val request = SelfPairingProof.request("decision", binding.identity, status.ceremonyId, mapOf(
            "operation_id" to JsonPrimitive(UUID.randomUUID().toString()), "action" to JsonPrimitive(action),
            "expected_revision" to JsonPrimitive(status.revision), "claim_id" to (status.claimId?.let(::JsonPrimitive) ?: JsonNull),
            "claim_request_sha256" to (status.claimHash?.let(::JsonPrimitive) ?: JsonNull),
            "comparison_code" to (comparisonCode?.let(::JsonPrimitive) ?: JsonNull),
        ), now = now())
        try {
            store.write(ROLE, JsonObject(pending + mapOf("stage" to JsonPrimitive("DECISION_PENDING"),
                "decision_request_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(request)))))
            resumePending()
        } finally { request.fill(0) }
    }

    /** Closes the local display; remote cancellation is a separate explicit server command. */
    suspend fun dismiss() = operation {
        val pending = store.read(ROLE)
        if (pending != null) {
            val visible = mutable.value as? SelfPairingSourceState.Ready
            val sameAuthority = runCatching { authority(pending); true }.getOrDefault(false)
            require(visible?.status?.terminal == true || mutable.value == SelfPairingSourceState.ExpiredStart || !sameAuthority) { "SELF_PAIRING_CANCEL_REQUIRED" }
            store.clear(ROLE)
        }
        mutable.value = SelfPairingSourceState.Idle
    }

    private suspend fun resumePending() {
        val pending = store.read(ROLE) ?: return
        val binding = authority(pending)
        val stage = pending.text("stage")
        val startBytes = Base64.getDecoder().decode(pending.text("start_request_b64"))
        val requestedAt = try { SelfPairingJson.parse(startBytes).instant("requested_at") } finally { startBytes.fill(0) }
        val staleStart = stage == "START_PENDING" && !requestedAt.plusSeconds(120).isAfter(now())
        val staleDecision = if (stage == "DECISION_PENDING") {
            val decision = Base64.getDecoder().decode(pending.text("decision_request_b64"))
            try { !SelfPairingJson.parse(decision).instant("requested_at").plusSeconds(120).isAfter(now()) }
            finally { decision.fill(0) }
        } else false
        // An aged command must not be regenerated or submitted with a fresh timestamp.
        // Read the current state and require another explicit review for any further action.
        val kind = when (stage) { "START_PENDING" -> if (staleStart) "status" else "start"; "DECISION_PENDING" -> if (staleDecision) "status" else "decision"; "READY" -> "status"; else -> error("SELF_PAIRING_PENDING_INVALID") }
        val body = if (kind == "status") null else Base64.getDecoder().decode(pending.text(if (kind == "start") "start_request_b64" else "decision_request_b64"))
        mutable.value = SelfPairingSourceState.Working
        val response = try { transport.source(binding.identity, binding.profile, kind, pending.text("ceremony_id"), body) }
        catch (failure: SelfPairingRemoteFailure) {
            if (staleStart && failure.code == "self_pairing_unavailable" && !requestedAt.plusSeconds(1020).isAfter(now())) {
                // No QR was shown before READY. Even an accepted start has reached its fixed TTL.
                mutable.value = SelfPairingSourceState.ExpiredStart
                return
            }
            if (kind == "decision" && failure.code == "self_pairing_revision_conflict") {
                // Server has positively rejected this CAS without applying it. Require a new review.
                store.write(ROLE, JsonObject((pending - "decision_request_b64") + ("stage" to JsonPrimitive("READY"))))
            }
            throw failure
        }
        finally { body?.fill(0) }
        val status = SelfPairingStatus.parse(response, binding.identity, pending.text("ceremony_id"), source = true)
        require(status.accountId == binding.userId)
        if ("expires_at" in pending) require(status.expiresAt == pending.instant("expires_at"))
        val updated = JsonObject((pending - "decision_request_b64") + mapOf("stage" to JsonPrimitive("READY"), "expires_at" to JsonPrimitive(status.expiresAt.toString())))
        store.write(ROLE, updated)
        publish(updated, binding, response)
    }

    private suspend fun authority(pending: JsonObject): SelfPairingSourceBinding {
        require(pending.integer("schema_version") == 1L)
        val current = requireNotNull(contexts.current()) { "SELF_PAIRING_SOURCE_CHANGED" }
        require(current.profile.value == pending.text("profile_id") && current.userId == pending.text("user_id") &&
            current.deviceId == pending.text("device_id") && current.familyId == pending.text("family_id") &&
            current.identity.fields().all { (key, value) -> pending[key] == value }) { "SELF_PAIRING_SOURCE_CHANGED" }
        return current
    }

    private fun publish(pending: JsonObject, binding: SelfPairingSourceBinding, value: JsonObject) {
        val status = SelfPairingStatus.parse(value, binding.identity, pending.text("ceremony_id"), source = true)
        require(status.accountId == binding.userId && status.expiresAt == pending.instant("expires_at"))
        val payload = if (status.state == "OPEN") {
            val secret = pending.text("rendezvous_secret").toByteArray(Charsets.US_ASCII)
            SelfPairingQr(status.ceremonyId, binding.identity, status.expiresAt, secret).use { qr ->
                val encoded = qr.encode()
                try { encoded.toString(Charsets.UTF_8) } finally { encoded.fill(0) }
            }
        } else null
        mutable.value = SelfPairingSourceState.Ready(status, payload)
    }

    private suspend fun operation(block: suspend () -> Unit) = mutex.withLock {
        try { block() } catch (failure: CancellationException) { throw failure }
        catch (failure: Exception) {
            val code = if (failure is SelfPairingRemoteFailure) failure.code
                else failure.message?.takeIf { it in setOf("SELF_PAIRING_SOURCE_CHANGED", "SELF_PAIRING_CANCEL_REQUIRED") } ?: "self_pairing_unavailable"
            mutable.value = SelfPairingSourceState.Blocked(code)
        }
    }
    private companion object { val ROLE = SelfPairingRole.SOURCE }
}
