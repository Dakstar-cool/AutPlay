package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.EnrollmentSession
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId

/** Concrete PA2 bridge into the one existing credential-first binding implementation. */
class ProfilePairingAccountRegistrationBindingCommitter(
    private val pairing: ProfilePairingRuntime,
    private val keys: M5DeviceKeyStore,
) : AccountRegistrationBindingCommitter {
    override suspend fun commitAfterCredential(profileId: ServerProfileId, registration: AccountRegistrationRequest, response: AccountRegistrationResponse, context: VerifiedAccountRegistrationContext): Boolean {
        val snapshot = context.snapshot.copy(
            expectedUserId = UserId(response.userId), expectedDeviceId = DeviceId(response.deviceId),
            deviceKeyThumbprintSha256 = keys.publicKeyThumbprintSha256(registration.keyAlias),
            bindingCommitId = registration.bindingCommitId,
        )
        val access = response.accessToken.copyOf()
        val refresh = registration.successorRefreshToken.copyOf()
        return pairing.completePublicAccountRegistration(
            snapshot, registration.keyAlias,
            EnrollmentSession(DeviceId(response.deviceId), response.sessionId, response.sessionId, 0, access, refresh),
            context.identitySpki.copyOf(),
        )
    }
}
