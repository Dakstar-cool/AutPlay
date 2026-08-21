package app.autplay.application.profilepairing

/** Pure complete-match state reducer; a delayed result can never partially update a newer flow. */
object PairingReducer {
    fun applyDiscovery(current: PairingState, snapshot: PairingFlowSnapshot, discovered: TrustedServerIdentity, supportedApiMajors: Set<Int>, clientApiMajor: Int): PairingState {
        if (!matches(current, snapshot)) return PairingState.Blocked(PairingFailure.STALE_FLOW_GENERATION)
        if (discovered != TrustedServerIdentity(snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch, snapshot.expectedIdentityThumbprintSha256)) return PairingState.Blocked(PairingFailure.SERVER_IDENTITY_CHANGED)
        if (clientApiMajor !in supportedApiMajors) return PairingState.Blocked(PairingFailure.INCOMPATIBLE_API_MAJOR)
        return PairingState.AwaitingTrust(snapshot)
    }

    fun applyCapabilities(current: PairingState, snapshot: PairingFlowSnapshot, capabilities: CapabilityState, priorHighWater: Long?, nowMs: Long): PairingState {
        if (!matches(current, snapshot)) return PairingState.Blocked(PairingFailure.STALE_FLOW_GENERATION)
        if (capabilities.identity != TrustedServerIdentity(snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch, snapshot.expectedIdentityThumbprintSha256)) return PairingState.Blocked(PairingFailure.SERVER_IDENTITY_CHANGED)
        if (capabilities.userId != snapshot.expectedUserId || capabilities.deviceId != snapshot.expectedDeviceId) return PairingState.Blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
        if (capabilities.expiresAtEpochMs <= nowMs) return PairingState.Blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
        if (priorHighWater != null && capabilities.revision < priorHighWater) return PairingState.Blocked(PairingFailure.CAPABILITY_ROLLBACK_DETECTED)
        return PairingState.Connected(snapshot, capabilities)
    }

    private fun matches(state: PairingState, snapshot: PairingFlowSnapshot): Boolean = when (state) {
        is PairingState.CheckingDiscovery -> state.snapshot == snapshot
        is PairingState.AwaitingTrust -> state.snapshot == snapshot
        is PairingState.AwaitingConfirmation -> state.snapshot == snapshot
        is PairingState.ExchangingInvitation -> state.snapshot == snapshot
        is PairingState.Connected -> state.snapshot == snapshot
        else -> false
    }
}
