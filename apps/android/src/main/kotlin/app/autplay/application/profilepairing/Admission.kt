package app.autplay.application.profilepairing

import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.security.SecureRandom
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Non-secret crash-recovery evidence.  It deliberately has no locator or poll bearer. */
data class AdmissionCheckpoint(
    val requestId: String,
    val requestSha256: String,
    val serverProfileId: ServerProfileId,
    val serverInstanceId: String,
    val identityEpoch: Long,
    val identityThumbprintSha256: String,
    val deviceKeyThumbprintSha256: String,
    val generationId: String,
    val apiOrigin: String,
    val streamOrigin: String,
) {
    init {
        requireCanonicalUuid(requestId); requireSha256(requestSha256); requireCanonicalUuid(serverInstanceId)
        require(identityEpoch >= 1); requireSha256(identityThumbprintSha256); requireSha256(deviceKeyThumbprintSha256)
        requireCanonicalUuid(generationId)
    }
}

sealed interface AdmissionState {
    data object RequestReady : AdmissionState
    data class AwaitingComparison(val checkpoint: AdmissionCheckpoint, val reviewLocator: String, val sas: String) : AdmissionState
    data class Pending(val checkpoint: AdmissionCheckpoint) : AdmissionState
    data class Approved(val checkpoint: AdmissionCheckpoint, val account: AdmissionAccount) : AdmissionState
    data class Exchanging(val checkpoint: AdmissionCheckpoint) : AdmissionState
    data object Connected : AdmissionState
    data class Rejected(val checkpoint: AdmissionCheckpoint?) : AdmissionState
    data class Blocked(val checkpoint: AdmissionCheckpoint?) : AdmissionState
    data class Expired(val checkpoint: AdmissionCheckpoint?) : AdmissionState
    data object Cancelled : AdmissionState
    data object Unavailable : AdmissionState
    data object IdentityChanged : AdmissionState
}

data class AdmissionAccount(val userId: UserId, val label: String) { init { require(label.length in 1..120) } }
data class AdmissionRequest(val checkpoint: AdmissionCheckpoint, val wireJson: String)
data class AdmissionExchangeCommand(
    val checkpoint: AdmissionCheckpoint,
    val account: AdmissionAccount,
    val pollBearer: ByteArray,
    val nextRefreshToken: ByteArray,
    val bindingCommitId: String,
    val wireJson: String,
)
data class AdmissionCreated(val reviewLocator: String, val pollBearer: ByteArray, val sas: String)
sealed interface AdmissionPoll { data object Pending : AdmissionPoll; data class Approved(val account: AdmissionAccount) : AdmissionPoll; data object Rejected : AdmissionPoll; data object Blocked : AdmissionPoll; data object Expired : AdmissionPoll; data object Unavailable : AdmissionPoll }
data class AdmissionRecovery(val reviewLocator: String, val pollBearer: ByteArray, val sas: String)
data class TrustedReenrollmentChallenge(val challengeId: String, val challenge: ByteArray) { init { requireCanonicalUuid(challengeId); require(challenge.size == 22) } }
data class TrustedReenrollmentCommand(val checkpoint: AdmissionCheckpoint, val account: AdmissionAccount, val challenge: TrustedReenrollmentChallenge, val nextRefreshToken: ByteArray, val bindingCommitId: String, val wireJson: String)
sealed interface TrustedReenrollmentState { data object Idle : TrustedReenrollmentState; data object RequestingChallenge : TrustedReenrollmentState; data object Exchanging : TrustedReenrollmentState; data object Connected : TrustedReenrollmentState; data object Unavailable : TrustedReenrollmentState; data object IdentityChanged : TrustedReenrollmentState }

/** Compact DataStore representation; this intentionally cannot encode any bearer or locator. */
object AdmissionCheckpointCodec {
    fun encode(value: AdmissionCheckpoint): String = listOf("v2", value.requestId, value.requestSha256, value.serverProfileId.value, value.serverInstanceId, value.identityEpoch, value.identityThumbprintSha256, value.deviceKeyThumbprintSha256, value.generationId, value.apiOrigin, value.streamOrigin).joinToString("|")
    fun decode(value: String): AdmissionCheckpoint {
        val parts = value.split('|'); require(parts.size == 11 && parts[0] == "v2")
        return AdmissionCheckpoint(parts[1], parts[2], ServerProfileId(parts[3]), parts[4], parts[5].toLong(), parts[6], parts[7], parts[8], parts[9], parts[10])
    }
}

/** Transport boundary: bearer is supplied out-of-band and must be emitted only as an Authorization header. */
interface AdmissionPort {
    suspend fun request(request: AdmissionRequest): PairingNetworkResult<AdmissionCreated>
    suspend fun recover(request: AdmissionRequest): PairingNetworkResult<AdmissionRecovery>
    suspend fun poll(request: AdmissionRequest, pollBearer: ByteArray): PairingNetworkResult<AdmissionPoll>
    suspend fun exchange(command: AdmissionExchangeCommand): PairingNetworkResult<EnrollmentSession>
    suspend fun trustedReenrollmentChallenge(request: AdmissionRequest): PairingNetworkResult<TrustedReenrollmentChallenge>
    suspend fun trustedReenrollmentExchange(command: TrustedReenrollmentCommand): PairingNetworkResult<EnrollmentSession>
}

/** Exact-key two-step trusted reenrollment. It never falls back to a new key or an invitation. */
class TrustedReenrollmentRuntime(
    private val scope: CoroutineScope,
    private val keys: M5DeviceKeyStore,
    private val port: AdmissionPort,
    private val persistEnrollment: suspend (AdmissionCheckpoint, AdmissionAccount, String, EnrollmentSession) -> Boolean,
    private val keyAlias: (ServerProfileId) -> String = { "autplay.m5.${it.value}" },
) {
    private val stateFlow = MutableStateFlow<TrustedReenrollmentState>(TrustedReenrollmentState.Idle)
    val state: StateFlow<TrustedReenrollmentState> = stateFlow.asStateFlow()
    fun reenroll(checkpoint: AdmissionCheckpoint, account: AdmissionAccount) = scope.launch {
        val alias = keyAlias(checkpoint.serverProfileId); keys.ensure(alias)
        if (keys.publicKeyThumbprintSha256(alias) != checkpoint.deviceKeyThumbprintSha256) { stateFlow.value = TrustedReenrollmentState.Unavailable; return@launch }
        stateFlow.value = TrustedReenrollmentState.RequestingChallenge
        val challengeRequest = AdmissionProof.signedJson(keys, alias, AdmissionProof.REENROLLMENT_DOMAIN, mapOf(
            "challenge_request_id" to JsonPrimitive(UUID.randomUUID().toString()), "account_id" to JsonPrimitive(account.userId.value),
            "expected_server_instance_id" to JsonPrimitive(checkpoint.serverInstanceId), "expected_identity_epoch" to JsonPrimitive(checkpoint.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(checkpoint.identityThumbprintSha256), "device_key_thumbprint_sha256" to JsonPrimitive(checkpoint.deviceKeyThumbprintSha256),
            "device_public_key_jwk" to JsonObject(AdmissionProof.p256Jwk(keys.publicKeySpki(alias))), "client_nonce_b64url" to JsonPrimitive(nonce()),
        ))
        val challenge = when (val result = port.trustedReenrollmentChallenge(AdmissionRequest(checkpoint, challengeRequest.json))) {
            is PairingNetworkResult.Success -> result.value
            is PairingNetworkResult.Failure -> { stateFlow.value = if (result.code == "server_identity_changed") TrustedReenrollmentState.IdentityChanged else TrustedReenrollmentState.Unavailable; return@launch }
        }
        val refresh = ByteArray(32).also(SecureRandom()::nextBytes); val commit = UUID.randomUUID().toString()
        try {
            stateFlow.value = TrustedReenrollmentState.Exchanging
            val exchange = AdmissionProof.signedJson(keys, alias, AdmissionProof.REENROLLMENT_DOMAIN, mapOf(
                "challenge_id" to JsonPrimitive(challenge.challengeId), "challenge" to JsonPrimitive(challenge.challenge.toString(Charsets.US_ASCII)),
                "exchange_id" to JsonPrimitive(UUID.randomUUID().toString()), "binding_commit_id" to JsonPrimitive(commit),
                "expected_server_instance_id" to JsonPrimitive(checkpoint.serverInstanceId), "expected_identity_epoch" to JsonPrimitive(checkpoint.identityEpoch),
                "expected_identity_thumbprint_sha256" to JsonPrimitive(checkpoint.identityThumbprintSha256), "expected_api_origin" to JsonPrimitive(checkpoint.apiOrigin), "expected_stream_origin" to JsonPrimitive(checkpoint.streamOrigin),
                "account_id" to JsonPrimitive(account.userId.value), "device_key_thumbprint_sha256" to JsonPrimitive(checkpoint.deviceKeyThumbprintSha256), "device_public_key_jwk" to JsonObject(AdmissionProof.p256Jwk(keys.publicKeySpki(alias))),
                "next_refresh_token_sha256" to JsonPrimitive(sha256(refresh)), "client_nonce_b64url" to JsonPrimitive(nonce()),
            ))
            when (val result = port.trustedReenrollmentExchange(TrustedReenrollmentCommand(checkpoint, account, challenge, refresh, commit, exchange.json))) {
                is PairingNetworkResult.Success -> stateFlow.value = if (persistEnrollment(checkpoint, account, commit, result.value)) TrustedReenrollmentState.Connected else TrustedReenrollmentState.Unavailable
                is PairingNetworkResult.Failure -> stateFlow.value = if (result.code == "server_identity_changed") TrustedReenrollmentState.IdentityChanged else TrustedReenrollmentState.Unavailable
            }
        } finally { challenge.challenge.fill(0); refresh.fill(0) }
    }
    private fun nonce() = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32).also(SecureRandom()::nextBytes))
    private fun sha256(value: ByteArray) = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
}

/**
 * Volatile S1B state machine. A process death loses [pollBearer], intentionally requiring the
 * exact-key recovery ceremony. Generation equality prevents late request/recovery/poll responses.
 */
class AdmissionRuntime(
    private val scope: CoroutineScope,
    private val keys: M5DeviceKeyStore,
    private val port: AdmissionPort,
    private val persistCheckpoint: suspend (AdmissionCheckpoint?) -> Unit,
    /** Persists ordinary M5 credentials only after the exact exchange succeeds. */
    private val persistEnrollment: suspend (AdmissionCheckpoint, AdmissionAccount, String, EnrollmentSession) -> Boolean = { _, _, _, _ -> true },
    private val persistTrustedReenrollment: suspend (AdmissionCheckpoint, AdmissionAccount, String, EnrollmentSession) -> Boolean = { _, _, _, _ -> false },
    private val keyAlias: (ServerProfileId) -> String = { "autplay.m5.${it.value}" },
) {
    private val stateFlow = MutableStateFlow<AdmissionState>(AdmissionState.RequestReady)
    val state: StateFlow<AdmissionState> = stateFlow.asStateFlow()
    private var pollBearer: ByteArray? = null
    private var activeGenerationId: String? = null
    private var lastForegroundPollAtMs: Long? = null
    private var recoveryCheckpoint: AdmissionCheckpoint? = null
    private val trustedRuntime = TrustedReenrollmentRuntime(scope, keys, port, persistTrustedReenrollment, keyAlias)

    fun request(snapshot: PairingFlowSnapshot) = scope.launch {
        val alias = keyAlias(snapshot.serverProfileId); keys.ensure(alias)
        val checkpoint = AdmissionCheckpoint(
            requestId = UUID.randomUUID().toString(), requestSha256 = "0".repeat(64),
            serverProfileId = snapshot.serverProfileId, serverInstanceId = snapshot.expectedServerInstanceId,
            identityEpoch = snapshot.expectedIdentityEpoch, identityThumbprintSha256 = snapshot.expectedIdentityThumbprintSha256,
            deviceKeyThumbprintSha256 = keys.publicKeyThumbprintSha256(alias), generationId = UUID.randomUUID().toString(),
            apiOrigin = snapshot.apiOrigin, streamOrigin = snapshot.streamOrigin,
        )
        activeGenerationId = checkpoint.generationId
        val nonce = newNonce()
        val signed = AdmissionProof.signedJson(keys, alias, AdmissionProof.REQUEST_DOMAIN, mapOf(
            "request_id" to JsonPrimitive(checkpoint.requestId), "expected_server_instance_id" to JsonPrimitive(checkpoint.serverInstanceId),
            "expected_identity_epoch" to JsonPrimitive(checkpoint.identityEpoch), "expected_identity_thumbprint_sha256" to JsonPrimitive(checkpoint.identityThumbprintSha256),
            "client_nonce_b64url" to JsonPrimitive(nonce), "api_major" to JsonPrimitive(1), "device_key_thumbprint_sha256" to JsonPrimitive(checkpoint.deviceKeyThumbprintSha256),
            "device_public_key_jwk" to JsonObject(AdmissionProof.p256Jwk(keys.publicKeySpki(alias))), "nickname" to JsonPrimitive("Android device"),
            "platform" to JsonPrimitive("ANDROID"), "app_version" to JsonPrimitive("0.1.0"), "requested_at" to JsonPrimitive(java.time.Instant.now().toString()),
        ))
        val exact = checkpoint.copy(requestSha256 = signed.sha256)
        recoveryCheckpoint = exact
        // Store only non-secret recovery evidence before the one-time response can be lost.
        persistCheckpoint(exact)
        stateFlow.value = AdmissionState.RequestReady
        when (val result = port.request(AdmissionRequest(exact, signed.json))) {
            is PairingNetworkResult.Success -> if (activeGenerationId == exact.generationId && stateFlow.value is AdmissionState.RequestReady) {
                pollBearer?.fill(0); pollBearer = result.value.pollBearer.copyOf(); persistCheckpoint(exact)
                stateFlow.value = AdmissionState.AwaitingComparison(exact, result.value.reviewLocator, AdmissionProof.sasDecimal12(exact))
            }
            is PairingNetworkResult.Failure -> if (activeGenerationId == exact.generationId) stateFlow.value = failure(result.code, exact)
        }
    }

    fun confirmComparison() { val current = stateFlow.value as? AdmissionState.AwaitingComparison ?: return; stateFlow.value = AdmissionState.Pending(current.checkpoint) }
    fun poll() = scope.launch {
        val current = stateFlow.value as? AdmissionState.Pending ?: return@launch
        val now = System.currentTimeMillis()
        if (lastForegroundPollAtMs?.let { now - it < 2_000L } == true) return@launch
        lastForegroundPollAtMs = now
        val bearer = pollBearer?.copyOf() ?: run { recover(current.checkpoint); return@launch }
        try {
            val wire = signed(current.checkpoint, AdmissionProof.POLL_DOMAIN, mapOf(
                "request_id" to JsonPrimitive(current.checkpoint.requestId),
                "device_key_thumbprint_sha256" to JsonPrimitive(current.checkpoint.deviceKeyThumbprintSha256),
                "client_nonce_b64url" to JsonPrimitive(newNonce()),
            ))
            when (val result = port.poll(AdmissionRequest(current.checkpoint, wire.json), bearer)) {
                is PairingNetworkResult.Success -> if ((stateFlow.value as? AdmissionState.Pending)?.checkpoint?.generationId == current.checkpoint.generationId) when (val value = result.value) {
                    AdmissionPoll.Pending -> Unit
                    is AdmissionPoll.Approved -> stateFlow.value = AdmissionState.Approved(current.checkpoint, value.account)
                    AdmissionPoll.Rejected -> terminal(AdmissionState.Rejected(current.checkpoint))
                    AdmissionPoll.Blocked -> terminal(AdmissionState.Blocked(current.checkpoint))
                    AdmissionPoll.Expired -> terminal(AdmissionState.Expired(current.checkpoint))
                    AdmissionPoll.Unavailable -> stateFlow.value = AdmissionState.Unavailable
                }
                is PairingNetworkResult.Failure -> if ((stateFlow.value as? AdmissionState.Pending)?.checkpoint?.generationId == current.checkpoint.generationId) {
                    stateFlow.value = failure(result.code, current.checkpoint)
                }
            }
        } finally { bearer.fill(0) }
    }
    fun confirmAccount() = scope.launch {
        val current = stateFlow.value as? AdmissionState.Approved ?: return@launch
        val bearer = pollBearer?.copyOf() ?: run { recover(current.checkpoint); return@launch }
        val alias = keyAlias(current.checkpoint.serverProfileId)
        val refresh = ByteArray(32).also(SecureRandom()::nextBytes)
        val commit = UUID.randomUUID().toString()
        val wire = signed(current.checkpoint, AdmissionProof.EXCHANGE_DOMAIN, mapOf(
            "request_id" to JsonPrimitive(current.checkpoint.requestId),
            "request_sha256" to JsonPrimitive(current.checkpoint.requestSha256),
            "exchange_id" to JsonPrimitive(UUID.randomUUID().toString()),
            "binding_commit_id" to JsonPrimitive(commit),
            "poll_bearer_sha256" to JsonPrimitive(sha256(bearer)),
            "expected_server_instance_id" to JsonPrimitive(current.checkpoint.serverInstanceId),
            "expected_identity_epoch" to JsonPrimitive(current.checkpoint.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(current.checkpoint.identityThumbprintSha256),
            "expected_api_origin" to JsonPrimitive(current.checkpoint.apiOrigin),
            "expected_stream_origin" to JsonPrimitive(current.checkpoint.streamOrigin),
            "approved_account_id" to JsonPrimitive(current.account.userId.value),
            "device_key_thumbprint_sha256" to JsonPrimitive(current.checkpoint.deviceKeyThumbprintSha256),
            "device_public_key_jwk" to JsonObject(AdmissionProof.p256Jwk(keys.publicKeySpki(alias))),
            "next_refresh_token_sha256" to JsonPrimitive(sha256(refresh)),
            "client_nonce_b64url" to JsonPrimitive(newNonce()),
        ))
        stateFlow.value = AdmissionState.Exchanging(current.checkpoint)
        try {
            when (val result = port.exchange(AdmissionExchangeCommand(current.checkpoint, current.account, bearer, refresh, commit, wire.json))) {
                is PairingNetworkResult.Success -> {
                    if ((stateFlow.value as? AdmissionState.Exchanging)?.checkpoint?.generationId != current.checkpoint.generationId) {
                        result.value.accessToken.fill(0); result.value.refreshToken.fill(0)
                    } else if (persistEnrollment(current.checkpoint, current.account, commit, result.value)) terminal(AdmissionState.Connected)
                    else stateFlow.value = AdmissionState.Unavailable
                }
                is PairingNetworkResult.Failure -> if ((stateFlow.value as? AdmissionState.Exchanging)?.checkpoint?.generationId == current.checkpoint.generationId) {
                    stateFlow.value = failure(result.code, current.checkpoint)
                }
            }
        } finally { bearer.fill(0); refresh.fill(0) }
    }
    fun recover(checkpoint: AdmissionCheckpoint) = scope.launch {
        recoveryCheckpoint = checkpoint
        activeGenerationId = checkpoint.generationId
        val request = signed(checkpoint, AdmissionProof.RECOVERY_DOMAIN, mapOf(
            "request_id" to JsonPrimitive(checkpoint.requestId), "request_sha256" to JsonPrimitive(checkpoint.requestSha256),
            "expected_server_instance_id" to JsonPrimitive(checkpoint.serverInstanceId), "expected_identity_epoch" to JsonPrimitive(checkpoint.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(checkpoint.identityThumbprintSha256), "device_key_thumbprint_sha256" to JsonPrimitive(checkpoint.deviceKeyThumbprintSha256),
            "recovery_nonce_b64url" to JsonPrimitive(newNonce()),
        ))
        when (val result = port.recover(AdmissionRequest(checkpoint, request.json))) {
            is PairingNetworkResult.Success -> if (activeGenerationId == checkpoint.generationId) {
                pollBearer?.fill(0); pollBearer = result.value.pollBearer.copyOf()
                stateFlow.value = AdmissionState.AwaitingComparison(checkpoint, result.value.reviewLocator, AdmissionProof.sasDecimal12(checkpoint))
            }
            is PairingNetworkResult.Failure -> if (activeGenerationId == checkpoint.generationId) stateFlow.value = failure(result.code, checkpoint)
        }
    }
    fun cancel() = scope.launch { terminal(AdmissionState.Cancelled) }
    fun retry(snapshot: PairingFlowSnapshot) { recoveryCheckpoint?.let(::recover) ?: request(snapshot) }
    fun reenrollTrusted(snapshot: PairingFlowSnapshot) {
        val user = snapshot.expectedUserId ?: return
        val commit = snapshot.bindingCommitId ?: return
        val checkpoint = AdmissionCheckpoint(commit, "0".repeat(64), snapshot.serverProfileId, snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch, snapshot.expectedIdentityThumbprintSha256, requireNotNull(snapshot.deviceKeyThumbprintSha256), UUID.randomUUID().toString(), snapshot.apiOrigin, snapshot.streamOrigin)
        trustedRuntime.reenroll(checkpoint, AdmissionAccount(user, "Current account"))
    }
    private suspend fun terminal(value: AdmissionState) { activeGenerationId = null; recoveryCheckpoint = null; pollBearer?.fill(0); pollBearer = null; persistCheckpoint(null); stateFlow.value = value }
    private fun signed(checkpoint: AdmissionCheckpoint, domain: String, fields: Map<String, kotlinx.serialization.json.JsonElement>): SignedJson =
        AdmissionProof.signedJson(keys, keyAlias(checkpoint.serverProfileId), domain, fields)
    private fun newNonce(): String = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32).also(SecureRandom()::nextBytes))
    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private fun failure(code: String, checkpoint: AdmissionCheckpoint): AdmissionState = when (code) { "admission_rejected" -> AdmissionState.Rejected(checkpoint); "device_key_blocked" -> AdmissionState.Blocked(checkpoint); "admission_expired" -> AdmissionState.Expired(checkpoint); "server_identity_changed" -> AdmissionState.IdentityChanged; else -> AdmissionState.Unavailable }
}

/**
 * Captures only the checkpoint that existed when the process-bound runtimes were created.
 * Checkpoints persisted by a live request must never be mistaken for cold-start recovery work.
 */
class AdmissionRecoveryBootstrap(
    encodedCheckpoint: String?,
    private val preferExistingBinding: Boolean = false,
) {
    val checkpoint: AdmissionCheckpoint? = encodedCheckpoint?.let {
        runCatching { AdmissionCheckpointCodec.decode(it) }.getOrNull()
    }

    /** A committed M5 binding wins over stale evidence left by a crash after session persistence. */
    suspend fun restoreExistingBindingIfPresent(
        pairingRuntime: ProfilePairingRuntime,
        clearStaleCheckpoint: suspend () -> Unit,
    ): Boolean {
        if (!preferExistingBinding) return false
        if (checkpoint != null) clearStaleCheckpoint()
        pairingRuntime.recoverAndRefresh()
        return true
    }
}
