package app.autplay.application.profilepairing

import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import java.time.Instant

/** Network boundary for M5. UI calls this port through a coordinator, never OkHttp directly. */
interface ProfilePairingPort {
    /** Seeds previously verified identity evidence after process recovery; implementations revalidate it. */
    fun seedTrustedIdentity(identity: TrustedServerIdentity, publicKeySpki: ByteArray) {}
    suspend fun discovery(apiOrigin: String): PairingNetworkResult<DiscoveryDocument>
    suspend fun capabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot): PairingNetworkResult<CapabilityDocument>
    suspend fun exchange(request: EnrollmentExchangeCommand): PairingNetworkResult<EnrollmentSession>
    suspend fun createInvitation(profileId: ServerProfileId, operationId: String, expiresInSeconds: Int): PairingNetworkResult<ManagedInvitation>
    suspend fun cancelInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): PairingNetworkResult<Unit>
    suspend fun rotate(request: SessionRotationCommand): PairingNetworkResult<EnrollmentSession>
    suspend fun devices(profileId: ServerProfileId): PairingNetworkResult<List<DeviceSummary>>
    suspend fun sessions(profileId: ServerProfileId): PairingNetworkResult<List<SessionSummary>>
    suspend fun lifecycle(profileId: ServerProfileId, command: LifecycleCommand): PairingNetworkResult<Unit>
}

sealed interface PairingNetworkResult<out T> { data class Success<T>(val value: T) : PairingNetworkResult<T>; data class Failure(val code: String, val retryAfterMs: Long? = null) : PairingNetworkResult<Nothing> }
data class DiscoveryDocument(val identity: TrustedServerIdentity, val labelHint: String, val apiOrigin: String, val streamOrigin: String, val supportedApiMajors: Set<Int>, val expiresAt: Instant, val identityPublicKeySpki: ByteArray)
data class CapabilityDocument(val state: CapabilityState, val signedPayload: ByteArray, val payloadSha256: String)
data class EnrollmentExchangeCommand(val snapshot: PairingFlowSnapshot, val invitationId: String, val invitationSecret: ByteArray, val deviceName: String, val nextRefreshToken: ByteArray, val nextRefreshTokenSha256: String, val clientNonceB64Url: String)
data class SessionRotationCommand(val snapshot: PairingFlowSnapshot, val parentSessionId: String, val parentGeneration: Long, val nextRefreshToken: ByteArray, val nextRefreshTokenSha256: String)
data class EnrollmentSession(val deviceId: DeviceId, val sessionId: String, val sessionFamilyId: String, val sessionGeneration: Long, val accessToken: ByteArray, val refreshToken: ByteArray)
/** Secret-bearing invitation returned only for volatile display. Callers must wipe [secret] after use. */
data class ManagedInvitation(val invitationId: String, val expiresAt: String, val secret: ByteArray, val envelopeJson: String) {
    init { requireCanonicalUuid(invitationId); require(secret.size == 32) }
}
data class DeviceSummary(val deviceId: DeviceId, val label: String, val current: Boolean)
data class SessionSummary(val sessionId: String, val deviceId: DeviceId, val generation: Long, val current: Boolean)
data class LifecycleCommand(val action: LifecycleAction, val operationId: String, val targetDeviceId: DeviceId? = null, val reasonCode: String? = null) {
    init { requireCanonicalUuid(operationId); if (action == LifecycleAction.REVOKE_DEVICE) requireNotNull(targetDeviceId) else require(targetDeviceId == null); reasonCode?.let { require(Regex("^[a-z][a-z0-9_]{0,63}$").matches(it)) } }
}
enum class LifecycleAction { LOGOUT_CURRENT, LOGOUT_ALL, REVOKE_DEVICE }
