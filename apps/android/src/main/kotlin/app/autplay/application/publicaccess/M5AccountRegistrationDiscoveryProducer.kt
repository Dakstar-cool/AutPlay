package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.PairingNetworkResult
import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.domain.ServerProfileId
import java.util.UUID

/** Signed discovery only: it never treats discovery as user approval. */
class M5AccountRegistrationDiscoveryProducer(private val port: ProfilePairingPort) {
    suspend fun discoverAndPropose(invitation: AccountInvitation, gate: ApprovedRegistrationContextGate): String? = when (val result = port.discovery(invitation.apiOrigin)) {
        is PairingNetworkResult.Failure -> null
        is PairingNetworkResult.Success -> result.value.let { doc ->
            try {
                if (doc.identity.serverInstanceId != invitation.serverInstanceId || doc.identity.identityEpoch != invitation.identityEpoch || doc.identity.identityThumbprintSha256 != invitation.identityThumbprintSha256 || doc.apiOrigin != invitation.apiOrigin || doc.streamOrigin != invitation.streamOrigin) null
                else {
                    port.seedTrustedIdentity(doc.identity, doc.identityPublicKeySpki.copyOf())
                    gate.propose(VerifiedAccountRegistrationContext(PairingFlowSnapshot(UUID.randomUUID().toString(), doc.apiOrigin, doc.streamOrigin, ServerProfileId(doc.identity.serverInstanceId), doc.identity.serverInstanceId, doc.identity.identityEpoch, doc.identity.identityThumbprintSha256, null, null, null, null, null), doc.identityPublicKeySpki.copyOf()))
                    doc.labelHint
                }
            } finally { doc.identityPublicKeySpki.fill(0) }
        }
    }
}
