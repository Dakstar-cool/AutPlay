package app.autplay.application.profilepairing

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.first

/** Cross-store recovery is fail-closed: a partial pairing is removed before it can authorize I/O. */
class BindingRecovery(
    private val settings: NonSecretSettingsStore,
    private val credentials: CredentialStore,
    private val purgeRecommendationContext: suspend (ServerProfileId) -> Unit = {},
) {
    suspend fun recover(profileId: ServerProfileId): BindingRecoveryResult {
        val initial = settings.settings.first()
        val checkpoint = initial.m5Binding ?: return BindingRecoveryResult.NoM5Binding
        if (initial.activeServerProfileId != profileId) return BindingRecoveryResult.NoM5Binding
        val material = credentials.read(profileId) ?: return clearPartial(profileId, checkpoint)
        return try {
            val secret = runCatching { SessionCredentialEnvelopeCodec.decode(material) }
                .getOrElse { return clearPartial(profileId, checkpoint) }
            when {
                matches(checkpoint, secret.bindingCommitId, secret.sessionId, secret.sessionFamilyId, secret.sessionGeneration) ->
                    BindingRecoveryResult.Ready(checkpoint)
                canPromoteSuccessor(checkpoint, secret) -> {
                    val promoted = checkpoint.copy(
                        sessionId = requireNotNull(secret.sessionId),
                        sessionFamilyId = requireNotNull(secret.sessionFamilyId),
                        sessionGeneration = requireNotNull(secret.sessionGeneration),
                    )
                    BindingAuthorityWriteGate.serialized {
                        var stored = false
                        settings.mutate { current ->
                            if (current.activeServerProfileId == profileId && current.m5Binding == checkpoint) {
                                stored = true
                                current.copy(m5Binding = promoted)
                            } else {
                                current
                            }
                        }
                        if (stored) BindingRecoveryResult.Ready(promoted) else BindingRecoveryResult.NoM5Binding
                    }
                }
                else -> clearPartial(profileId, checkpoint)
            }
        } finally { material.fill(0) }
    }
    private suspend fun clearPartial(
        profileId: ServerProfileId,
        expectedCheckpoint: M5BindingCheckpoint,
    ): BindingRecoveryResult = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        if (current.activeServerProfileId != profileId || current.m5Binding != expectedCheckpoint) {
            return@serialized BindingRecoveryResult.NoM5Binding
        }
        // Purge while the rejected binding is still visible. This also fences a downloaded pack
        // from being committed between recovery cleanup and the settings deactivation below.
        purgeRecommendationContext(profileId)
        credentials.clear(profileId)
        // Origins remain a non-active trust bookmark; credentials and active authority do not.
        settings.mutate { latest ->
            if (latest.activeServerProfileId == profileId && latest.m5Binding == expectedCheckpoint) {
                latest.copy(
                    activeServerProfileId = null,
                    activeUserId = null,
                    deviceId = null,
                    m5Binding = null,
                    m5TrustEvidence = null,
                    m5LocalDataDecision = null,
                    m5PendingExchangeCheckpoint = null,
                )
            } else {
                latest
            }
        }
        BindingRecoveryResult.ClearedPartialBinding
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
