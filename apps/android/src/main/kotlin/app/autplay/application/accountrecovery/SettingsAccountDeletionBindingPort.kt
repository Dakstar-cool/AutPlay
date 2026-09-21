package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.text
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.JsonObject

class SettingsAccountDeletionBindingPort(
    private val settings: NonSecretSettingsStore, private val credentials: CredentialStore,
    private val purgeRecommendationContext: suspend (ServerProfileId) -> Unit = {},
    private val onDetached: (String) -> Unit = {},
) : AccountDeletionBindingPort {
    override suspend fun prepare(accountId: String, identity: SelfPairingIdentity,
        persist: suspend (AccountDeletionBinding) -> Unit) = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        val checkpoint = requireNotNull(current.m5Binding)
        val profile = requireNotNull(current.activeServerProfileId)
        require(current.activeUserId?.value == accountId && current.deviceId != null && profile.value == identity.serverInstanceId)
        require(checkpoint.serverInstanceId == identity.serverInstanceId && checkpoint.identityEpoch == identity.epoch &&
            checkpoint.identityThumbprintSha256 == identity.thumbprint && current.serverBaseUrl == identity.apiOrigin && current.streamBaseUrl == identity.streamOrigin)
        val material = requireNotNull(credentials.read(profile))
        try {
            val secret = SessionCredentialEnvelopeCodec.decode(material)
            require(!secret.refreshPending && secret.refreshToken != null && secret.bindingCommitId == checkpoint.bindingCommitId &&
                secret.sessionId == checkpoint.sessionId && secret.sessionFamilyId == checkpoint.sessionFamilyId && secret.sessionGeneration == checkpoint.sessionGeneration)
            AccountDeletionBinding(identity, accountId, requireNotNull(current.deviceId).value,
                checkpoint.bindingCommitId, checkpoint.deviceKeyAlias, secret.accessToken.toByteArray(Charsets.US_ASCII)).use { persist(it) }
            detachLocked(current, checkpoint.bindingCommitId)
        } finally { material.fill(0) }
    }

    override suspend fun detach(pending: JsonObject) = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        val commit = pending.text("binding_commit_id")
        if (current.activeServerProfileId?.value == pending.text("expected_server_instance_id") &&
            current.activeUserId?.value == pending.text("account_id") && current.deviceId?.value == pending.text("device_id") &&
            current.m5Binding?.bindingCommitId == commit && current.m5Binding.deviceKeyAlias == pending.text("key_alias")) {
            detachLocked(current, commit)
        } else onDetached(commit)
    }

    private suspend fun detachLocked(expected: NonSecretSettings, commit: String) {
        val profile = requireNotNull(expected.activeServerProfileId)
        purgeRecommendationContext(profile)
        credentials.clear(profile)
        settings.mutate { current ->
            require(current.m5Binding?.bindingCommitId == commit && current.activeServerProfileId == profile &&
                current.activeUserId == expected.activeUserId && current.deviceId == expected.deviceId) { "deletion_binding_changed" }
            current.copy(activeServerProfileId = null, activeUserId = null, deviceId = null, m5Binding = null,
                m5TrustEvidence = null, m5LocalDataDecision = null, m5PendingExchangeCheckpoint = null,
                m5AdmissionCheckpoint = null, accountRecoverySetup = null)
        }
        onDetached(commit)
    }
}
