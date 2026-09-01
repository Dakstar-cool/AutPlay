package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.ProfilePairingPort
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.ServerProfileId
import java.lang.reflect.Proxy
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Assert.assertEquals
import org.junit.Test

class PublicAccountRegistrationCoordinatorTest {
    @Test
    fun activeProfileRejectsEveryImportBeforeParsingOrDiscovery() {
        var discoveryCalls = 0
        val pairingPort = Proxy.newProxyInstance(
            ProfilePairingPort::class.java.classLoader,
            arrayOf(ProfilePairingPort::class.java),
        ) { _, method, _ ->
            if (method.name == "discovery") discoveryCalls += 1
            error("network must not be called")
        } as ProfilePairingPort
        val keys = object : M5DeviceKeyStore {
            override fun ensure(alias: String) = Unit
            override fun publicKeySpki(alias: String) = error("must not be called")
            override fun publicKeyThumbprintSha256(alias: String) = error("must not be called")
            override fun signP1363(
                alias: String,
                domainSeparator: String,
                payloadSha256: ByteArray,
            ) = error("must not be called")
            override fun delete(alias: String) = Unit
        }
        val active = ActiveProfileGate { true }
        val runtime = AccountRegistrationRuntime(
            credentials = object : CredentialStore {
                override suspend fun read(profileId: ServerProfileId) = null
                override suspend fun write(profileId: ServerProfileId, material: ByteArray) =
                    error("must not be called")
                override suspend fun clear(profileId: ServerProfileId) = Unit
            },
            port = AccountRegistrationPort { error("must not be called") },
            binding = AccountRegistrationBindingCommitter { _, _, _, _ -> false },
            activeProfileGate = active,
            discoveryGate = ApprovedRegistrationContextGate(),
        )
        val coordinator = PublicAccountRegistrationCoordinator(
            CoroutineScope(Dispatchers.Unconfined),
            ApprovedRegistrationContextGate(),
            M5AccountRegistrationDiscoveryProducer(pairingPort),
            runtime,
            active,
            keys,
            "Android",
            "0.3.0",
        )

        // Invalid bytes prove the active-profile branch runs before both parser entry paths; QR,
        // picked documents, and ACTION_SEND all converge on this method.
        coordinator.importDocument(AccountInvitationParser.MIME_TYPE, byteArrayOf(1, 2, 3))

        assertEquals(
            PublicAccountRegistrationState.Blocked("ACCOUNT_REGISTRATION_ACTIVE_PROFILE_FORBIDDEN"),
            coordinator.state.value,
        )
        assertEquals(0, discoveryCalls)
    }
}
