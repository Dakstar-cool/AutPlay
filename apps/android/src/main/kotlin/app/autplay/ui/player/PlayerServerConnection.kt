package app.autplay.ui.player

import app.autplay.application.profilepairing.PairingState
import app.autplay.application.sync.SyncStatus

enum class PlayerServerConnection { Offline, Checking, Connected }

internal fun playerServerConnection(
    signedInToServer: Boolean, hasPairingCheckpoint: Boolean, pairing: PairingState, sync: SyncStatus,
): PlayerServerConnection {
    if (!signedInToServer) return PlayerServerConnection.Offline
    if (!hasPairingCheckpoint) return if (sync.serverUnavailable) PlayerServerConnection.Offline else PlayerServerConnection.Connected
    return when (pairing) {
        PairingState.NotConnected, is PairingState.CheckingDiscovery,
        is PairingState.ExchangingInvitation -> PlayerServerConnection.Checking
        is PairingState.Connected -> {
            // A successful authenticated response supersedes an older background sync failure.
            val failedSinceVerification = sync.serverUnavailable && (sync.lastAttemptAtMs ?: 0) > pairing.verifiedAtMs
            if (failedSinceVerification) PlayerServerConnection.Offline else PlayerServerConnection.Connected
        }
        is PairingState.Blocked, PairingState.Cancelled, is PairingState.AwaitingTrust,
        is PairingState.AwaitingConfirmation -> PlayerServerConnection.Offline
    }
}
