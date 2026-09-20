package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.EnrollmentSession
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.profilepairing.TrustedServerIdentity
import app.autplay.application.selfpairing.SelfPairingProof
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.flow.first

class ProfilePairingRecoveryBindingCommitter(
    private val pairing: ProfilePairingRuntime, private val port: ProfilePairingPort,
    private val settings: NonSecretSettingsStore, private val credentials: CredentialStore,
    private val keys: M5DeviceKeyStore,
    private val ceremony: FirstBindCeremonyOwner = FirstBindCeremonyOwner.ACCOUNT_RECOVERY,
) : AccountRecoveryBindingCommitter {
    override suspend fun isDurable(intent: AccountRecoveryBindingIntent): Boolean = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        val binding = current.m5Binding ?: return@serialized false
        if (current.activeServerProfileId?.value != intent.identity.serverInstanceId || current.activeUserId?.value != intent.accountId ||
            current.deviceId == null || binding.bindingCommitId != intent.bindingCommitId || binding.deviceKeyAlias != intent.keyAlias ||
            binding.serverInstanceId != intent.identity.serverInstanceId || binding.identityEpoch != intent.identity.epoch ||
            binding.identityThumbprintSha256 != intent.identity.thumbprint || keys.publicKeyThumbprintSha256(intent.keyAlias) != intent.keyThumbprint) return@serialized false
        val material = credentials.read(requireNotNull(current.activeServerProfileId)) ?: return@serialized false
        try {
            val envelope = SessionCredentialEnvelopeCodec.decode(material)
            envelope.bindingCommitId == intent.bindingCommitId && envelope.sessionId == binding.sessionId &&
                envelope.sessionFamilyId == binding.sessionFamilyId && envelope.sessionGeneration == binding.sessionGeneration && envelope.refreshToken != null
        } finally { material.fill(0) }
    }
    override suspend fun commit(intent: AccountRecoveryBindingIntent, result: AccountRecoveryResult, refresh: ByteArray, identitySpki: ByteArray): Boolean {
        require(result.bindingCommitId == intent.bindingCommitId && result.userId == intent.accountId && result.serverId == intent.identity.serverInstanceId)
        require(SelfPairingProof.hash(identitySpki) == intent.identity.thumbprint && keys.publicKeyThumbprintSha256(intent.keyAlias) == intent.keyThumbprint)
        val identity = intent.identity
        port.seedTrustedIdentity(TrustedServerIdentity(identity.serverInstanceId, identity.epoch, identity.thumbprint), identitySpki.copyOf())
        val snapshot = PairingFlowSnapshot(intent.generationId, identity.apiOrigin, identity.streamOrigin,
            ServerProfileId(identity.serverInstanceId), identity.serverInstanceId, identity.epoch, identity.thumbprint,
            UserId(result.userId), DeviceId(result.deviceId), intent.keyThumbprint, null, intent.bindingCommitId)
        return pairing.completeAccountRecovery(snapshot, intent.keyAlias,
            EnrollmentSession(DeviceId(result.deviceId), result.sessionId, result.sessionId, 0, result.access.copyOf(), refresh.copyOf()), identitySpki.copyOf(), ceremony)
    }
}
