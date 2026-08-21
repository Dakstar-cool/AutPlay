package app.autplay.application.profilepairing

import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID

/** Immutable guard carried by every M5 asynchronous operation. */
data class PairingFlowSnapshot(
    val generationId: String,
    val apiOrigin: String,
    val streamOrigin: String,
    val serverProfileId: ServerProfileId,
    val expectedServerInstanceId: String,
    val expectedIdentityEpoch: Long,
    val expectedIdentityThumbprintSha256: String,
    val expectedUserId: UserId?,
    val expectedDeviceId: DeviceId?,
    val deviceKeyThumbprintSha256: String?,
    val operationId: String?,
    val bindingCommitId: String?,
    val expectedDeviceName: String? = null,
    val localDataDecision: String? = null,
    val selectedLocalChangeIds: List<String> = emptyList(),
) {
    init {
        requireCanonicalUuid(generationId)
        requireCanonicalUuid(expectedServerInstanceId)
        require(expectedIdentityEpoch >= 1)
        requireSha256(expectedIdentityThumbprintSha256)
        deviceKeyThumbprintSha256?.let(::requireSha256)
        operationId?.let(::requireCanonicalUuid)
        bindingCommitId?.let(::requireCanonicalUuid)
        require(expectedDeviceName == null || expectedDeviceName.length in 1..120)
        require(localDataDecision == null || localDataDecision in setOf("KEEP_LOCAL", "REVIEW_SELECTED"))
        require(selectedLocalChangeIds.size <= 100 && selectedLocalChangeIds.distinct().size == selectedLocalChangeIds.size)
        selectedLocalChangeIds.forEach(::requireCanonicalUuid)
        require(
            (localDataDecision == null && selectedLocalChangeIds.isEmpty()) ||
                (localDataDecision == "KEEP_LOCAL" && selectedLocalChangeIds.isEmpty()) ||
                (localDataDecision == "REVIEW_SELECTED" && selectedLocalChangeIds.isNotEmpty()),
        )
    }
}

data class TrustedServerIdentity(
    val serverInstanceId: String,
    val identityEpoch: Long,
    val identityThumbprintSha256: String,
) {
    init { requireCanonicalUuid(serverInstanceId); require(identityEpoch >= 1); requireSha256(identityThumbprintSha256) }
}

data class CapabilityState(
    val identity: TrustedServerIdentity,
    val userId: UserId,
    val deviceId: DeviceId,
    val apiMajor: Int,
    val revision: Long,
    val expiresAtEpochMs: Long,
    val supportedOperations: Set<String>,
    val requiredFeatures: Set<String> = emptySet(),
) {
    init { require(apiMajor in 1..255 && revision >= 1 && expiresAtEpochMs >= 0); require(supportedOperations.size <= 64); require(requiredFeatures.size <= 16) }
}

sealed interface PairingState {
    data object NotConnected : PairingState
    data class CheckingDiscovery(val snapshot: PairingFlowSnapshot) : PairingState
    data class AwaitingTrust(val snapshot: PairingFlowSnapshot) : PairingState
    data class AwaitingConfirmation(val snapshot: PairingFlowSnapshot) : PairingState
    data class ExchangingInvitation(val snapshot: PairingFlowSnapshot) : PairingState
    data class Connected(val snapshot: PairingFlowSnapshot, val capabilities: CapabilityState) : PairingState
    data class Blocked(val code: PairingFailure) : PairingState
    data object Cancelled : PairingState
}

enum class PairingFailure { STALE_FLOW_GENERATION, SERVER_IDENTITY_CHANGED, INCOMPATIBLE_API_MAJOR, CAPABILITY_ROLLBACK_DETECTED, AUTH_ATTENTION_REQUIRED, SERVER_UNAVAILABLE }

internal fun requireCanonicalUuid(value: String) { require(runCatching { UUID.fromString(value) }.getOrNull()?.toString() == value) }
internal fun requireSha256(value: String) { require(Regex("^[0-9a-f]{64}$").matches(value)) }
