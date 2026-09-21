package app.autplay.data.security

import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.first

/** Shared by foreground profile recovery and background I/O. Caller holds the binding write gate. */
class SettingsM5RotationContextResolver(private val settings: NonSecretSettingsStore) : M5RotationContextResolver {
    override suspend fun resolve(profileId: ServerProfileId): M5RotationContext? {
        val value = settings.settings.first()
        val checkpoint = value.m5Binding ?: return null
        if (value.activeServerProfileId != profileId || value.deviceId == null || value.serverBaseUrl == null) return null
        return M5RotationContext(value.serverBaseUrl, checkpoint.serverInstanceId, checkpoint.identityEpoch, value.deviceId, checkpoint.deviceKeyAlias)
    }

    override suspend fun persistSuccessor(profileId: ServerProfileId, successor: SessionCredentialEnvelope) {
        settings.mutate { current ->
            val checkpoint = current.m5Binding ?: throw SessionRequiredException()
            val generation = successor.sessionGeneration ?: throw SessionRequiredException()
            val sessionId = successor.sessionId ?: throw SessionRequiredException()
            if (current.activeServerProfileId != profileId ||
                checkpoint.bindingCommitId != successor.bindingCommitId ||
                checkpoint.sessionFamilyId != successor.sessionFamilyId ||
                generation < checkpoint.sessionGeneration ||
                (generation == checkpoint.sessionGeneration && sessionId != checkpoint.sessionId)
            ) throw SessionRequiredException()
            current.copy(m5Binding = checkpoint.copy(sessionId = sessionId, sessionGeneration = generation))
        }
    }
}
