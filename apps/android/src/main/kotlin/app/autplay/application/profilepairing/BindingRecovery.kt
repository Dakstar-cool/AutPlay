package app.autplay.application.profilepairing

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.first

/** Cross-store recovery is fail-closed: a partial pairing is removed before it can authorize I/O. */
class BindingRecovery(private val settings: NonSecretSettingsStore, private val credentials: CredentialStore) {
    suspend fun recover(profileId: ServerProfileId): BindingRecoveryResult {
        val checkpoint = settings.settings.first().m5Binding ?: return BindingRecoveryResult.NoM5Binding
        if (settings.settings.first().activeServerProfileId != profileId) return BindingRecoveryResult.NoM5Binding
        val material = credentials.read(profileId) ?: return clearPartial(profileId)
        return try {
            val secret = SessionCredentialEnvelopeCodec.decode(material)
            when {
                matches(checkpoint, secret.bindingCommitId, secret.sessionId, secret.sessionFamilyId, secret.sessionGeneration) ->
                    BindingRecoveryResult.Ready(checkpoint)
                canPromoteSuccessor(checkpoint, secret) -> {
                    val promoted = checkpoint.copy(
                        sessionId = requireNotNull(secret.sessionId),
                        sessionFamilyId = requireNotNull(secret.sessionFamilyId),
                        sessionGeneration = requireNotNull(secret.sessionGeneration),
                    )
                    settings.mutate { current ->
                        if (current.activeServerProfileId == profileId && current.m5Binding == checkpoint) {
                            current.copy(m5Binding = promoted)
                        } else {
                            current
                        }
                    }
                    BindingRecoveryResult.Ready(promoted)
                }
                else -> clearPartial(profileId)
            }
        } finally { material.fill(0) }
    }
    private suspend fun clearPartial(profileId: ServerProfileId): BindingRecoveryResult {
        credentials.clear(profileId)
        // Origins remain a non-active trust bookmark; credentials and the active authority binding do not.
        settings.mutate { current ->
            if (current.activeServerProfileId == profileId) {
                current.copy(
                    activeServerProfileId = null,
                    activeUserId = null,
                    deviceId = null,
                    m5Binding = null,
                    m5TrustEvidence = null,
                    m5LocalDataDecision = null,
                    m5PendingExchangeCheckpoint = null,
                )
            } else {
                current
            }
        }
        return BindingRecoveryResult.ClearedPartialBinding
    }
    private fun matches(checkpoint: M5BindingCheckpoint, commit: String?, session: String?, family: String?, generation: Long?) = checkpoint.bindingCommitId == commit && checkpoint.sessionId == session && checkpoint.sessionFamilyId == family && checkpoint.sessionGeneration == generation
    private fun canPromoteSuccessor(
        checkpoint: M5BindingCheckpoint,
        secret: app.autplay.data.security.SessionCredentialEnvelope,
    ): Boolean =
        !secret.refreshPending &&
            checkpoint.bindingCommitId == secret.bindingCommitId &&
            checkpoint.sessionFamilyId == secret.sessionFamilyId &&
            secret.sessionId != null &&
            secret.sessionGeneration != null &&
            secret.sessionGeneration > checkpoint.sessionGeneration
}
sealed interface BindingRecoveryResult { data object NoM5Binding : BindingRecoveryResult; data class Ready(val checkpoint: M5BindingCheckpoint) : BindingRecoveryResult; data object ClearedPartialBinding : BindingRecoveryResult }
