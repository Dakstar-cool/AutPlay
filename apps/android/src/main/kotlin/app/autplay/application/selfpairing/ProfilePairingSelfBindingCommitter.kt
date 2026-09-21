package app.autplay.application.selfpairing

import app.autplay.application.profilepairing.EnrollmentSession
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.profilepairing.TrustedServerIdentity
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.flow.first

/** Reuses ordinary account binding and recognizes its durable result without a network replay. */
class ProfilePairingSelfBindingCommitter(
    private val pairing: ProfilePairingRuntime,
    private val port: ProfilePairingPort,
    private val settings: NonSecretSettingsStore,
    private val credentials: CredentialStore,
    private val keys: M5DeviceKeyStore,
) : SelfPairingBindingCommitter {
    override suspend fun isDurable(intent: SelfPairingBindingIntent): Boolean = BindingAuthorityWriteGate.serialized {
        val current = settings.settings.first()
        val binding = current.m5Binding ?: return@serialized false
        if (current.activeServerProfileId?.value != intent.identity.serverInstanceId ||
            current.activeUserId?.value != intent.confirmedAccountId || current.deviceId == null ||
            binding.bindingCommitId != intent.bindingCommitId || binding.deviceKeyAlias != intent.keyAlias ||
            binding.serverInstanceId != intent.identity.serverInstanceId || binding.identityEpoch != intent.identity.epoch ||
            binding.identityThumbprintSha256 != intent.identity.thumbprint ||
            keys.publicKeyThumbprintSha256(intent.keyAlias) != intent.keyThumbprint) return@serialized false
        val encrypted = credentials.read(requireNotNull(current.activeServerProfileId)) ?: return@serialized false
        try {
            val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
            envelope.bindingCommitId == intent.bindingCommitId && envelope.sessionId == binding.sessionId &&
                envelope.sessionFamilyId == binding.sessionFamilyId && envelope.sessionGeneration == binding.sessionGeneration &&
                envelope.refreshToken != null
        } finally { encrypted.fill(0) }
    }

    override suspend fun commit(intent: SelfPairingBindingIntent, result: SelfPairingExchangeResult, refresh: ByteArray, identitySpki: ByteArray): Boolean {
        require(result.bindingCommitId == intent.bindingCommitId && result.userId == intent.confirmedAccountId && result.serverInstanceId == intent.identity.serverInstanceId)
        require(SelfPairingProof.hash(identitySpki) == intent.identity.thumbprint)
        require(keys.publicKeyThumbprintSha256(intent.keyAlias) == intent.keyThumbprint)
        val identity = intent.identity
        port.seedTrustedIdentity(TrustedServerIdentity(identity.serverInstanceId, identity.epoch, identity.thumbprint), identitySpki.copyOf())
        val snapshot = PairingFlowSnapshot(
            generationId = intent.generationId, apiOrigin = identity.apiOrigin, streamOrigin = identity.streamOrigin,
            serverProfileId = ServerProfileId(identity.serverInstanceId), expectedServerInstanceId = identity.serverInstanceId,
            expectedIdentityEpoch = identity.epoch, expectedIdentityThumbprintSha256 = identity.thumbprint,
            expectedUserId = UserId(result.userId), expectedDeviceId = DeviceId(result.deviceId),
            deviceKeyThumbprintSha256 = intent.keyThumbprint, operationId = null, bindingCommitId = intent.bindingCommitId,
        )
        return pairing.completeSelfDevicePairing(snapshot, intent.keyAlias,
            EnrollmentSession(DeviceId(result.deviceId), result.sessionId, result.sessionId, 0, result.accessToken.copyOf(), refresh.copyOf()),
            identitySpki.copyOf())
    }
}
