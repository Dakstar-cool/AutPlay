package app.autplay.application.profilepairing

import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Application coordinator for UI. It owns flow generations and translates transport failures safely. */
class ProfilePairingCoordinator(private val port: ProfilePairingPort, private val clientApiMajor: Int) {
    private val mutableState = MutableStateFlow<PairingState>(PairingState.NotConnected)
    val state: StateFlow<PairingState> = mutableState.asStateFlow()

    suspend fun checkDiscovery(snapshot: PairingFlowSnapshot) {
        mutableState.value = PairingState.CheckingDiscovery(snapshot)
        when (val result = port.discovery(snapshot.apiOrigin)) {
            is PairingNetworkResult.Success -> mutableState.value = PairingReducer.applyDiscovery(mutableState.value, snapshot, result.value.identity, result.value.supportedApiMajors, clientApiMajor)
            is PairingNetworkResult.Failure -> mutableState.value = PairingState.Blocked(result.code.toPairingFailure())
        }
    }
    suspend fun refreshCapabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot, highWater: Long?, nowMs: Long) {
        when (val result = port.capabilities(profileId, snapshot)) {
            is PairingNetworkResult.Success -> mutableState.value = PairingReducer.applyCapabilities(mutableState.value, snapshot, result.value.state, highWater, nowMs)
            is PairingNetworkResult.Failure -> mutableState.value = PairingState.Blocked(result.code.toPairingFailure())
        }
    }
    fun cancel() { mutableState.value = PairingState.Cancelled }
    private fun String.toPairingFailure() = when (this) {
        "server_identity_changed" -> PairingFailure.SERVER_IDENTITY_CHANGED
        "incompatible_api_major" -> PairingFailure.INCOMPATIBLE_API_MAJOR
        "capability_rollback_detected" -> PairingFailure.CAPABILITY_ROLLBACK_DETECTED
        "auth_attention_required", "device_revoked", "session_revoked" -> PairingFailure.AUTH_ATTENTION_REQUIRED
        else -> PairingFailure.SERVER_UNAVAILABLE
    }
}
