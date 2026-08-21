package app.autplay.profilepairing

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.profilepairing.DiscoveryDocument
import app.autplay.application.profilepairing.EnrollmentExchangeCommand
import app.autplay.application.profilepairing.LifecycleAction
import app.autplay.application.profilepairing.LifecycleCommand
import app.autplay.application.profilepairing.OkHttpProfilePairingPort
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.PairingNetworkResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.OkHttpSyncTransport
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.M5RotationContext
import app.autplay.data.security.M5RotationContextResolver
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Physical-device proof; its invitation is fetched once from a process-local E2E-only route. */
@RunWith(AndroidJUnit4::class)
class M5bProfilePairingE2eTest {
    @Test fun enrollsCapabilitiesSyncsAndRevokesWithoutSecretInstrumentationArguments() {
        val configuredBase = InstrumentationRegistry.getArguments().getString("m5bE2eBaseUrl")
        assumeTrue(
            "The joined M5B harness supplies m5bE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )
        runBlocking {
        val base = requireNotNull(configuredBase).trimEnd('/')
        val invitation = getJson("$base/m5b-e2e/one-shot-invitation")
        val discovery = OkHttpProfilePairingPort(
            originForProfile = { base },
            credentials = AndroidKeystoreCredentialStore(targetContext()),
            deviceKeys = AndroidM5DeviceKeyStore(),
            allowUnsafeDevelopmentHttp = true,
        )
        val discovered = (discovery.discovery(base) as PairingNetworkResult.Success<DiscoveryDocument>).value
        val profile = ServerProfileId(UUID.randomUUID().toString())
        val credentials = AndroidKeystoreCredentialStore(targetContext())
        val keys = AndroidM5DeviceKeyStore()
        val port = OkHttpProfilePairingPort({ if (it == profile) base else null }, credentials, keys, allowUnsafeDevelopmentHttp = true)
        port.seedTrustedIdentity(discovered.identity, discovered.identityPublicKeySpki.copyOf())
        val userId = UserId(invitation.required("user_id"))
        val snapshot = PairingFlowSnapshot(
            generationId = UUID.randomUUID().toString(), apiOrigin = discovered.apiOrigin,
            streamOrigin = discovered.streamOrigin, serverProfileId = profile,
            expectedServerInstanceId = discovered.identity.serverInstanceId,
            expectedIdentityEpoch = discovered.identity.identityEpoch,
            expectedIdentityThumbprintSha256 = discovered.identity.identityThumbprintSha256,
            expectedUserId = userId, expectedDeviceId = null, deviceKeyThumbprintSha256 = null,
            operationId = UUID.randomUUID().toString(), bindingCommitId = UUID.randomUUID().toString(),
        )
        val successor = Base64.getUrlEncoder().withoutPadding().encode(ByteArray(32) { it.toByte() })
        val session = expectSuccess("EXCHANGE", port.exchange(EnrollmentExchangeCommand(
            snapshot = snapshot, invitationId = invitation.required("invitation_id"),
            invitationSecret = Base64.getUrlDecoder().decode(invitation.required("invitation_secret")),
            deviceName = "M5B physical E2E", nextRefreshToken = successor.copyOf(),
            nextRefreshTokenSha256 = sha256(successor), clientNonceB64Url = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(16) { (it + 1).toByte() }),
        )))
        try {
            credentials.write(profile, SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope(
                accessToken = session.accessToken.toString(StandardCharsets.UTF_8),
                refreshToken = session.refreshToken.toString(StandardCharsets.US_ASCII), generation = session.sessionGeneration,
                bindingCommitId = requireNotNull(snapshot.bindingCommitId), sessionId = session.sessionId,
                sessionFamilyId = session.sessionFamilyId, sessionGeneration = session.sessionGeneration,
            )))
            val bound = snapshot.copy(expectedDeviceId = session.deviceId, deviceKeyThumbprintSha256 = keys.publicKeyThumbprintSha256("autplay.m5.${profile.value}"))
            assertTrue(port.capabilities(profile, bound) is PairingNetworkResult.Success)
            bindDevice(base, session.accessToken, profile, userId, session.deviceId.value)
            val rotation = M5SessionRotationClient(
                object : M5RotationContextResolver {
                    override suspend fun resolve(profileId: ServerProfileId) = M5RotationContext(
                        apiOrigin = base,
                        serverInstanceId = discovered.identity.serverInstanceId,
                        identityEpoch = discovered.identity.identityEpoch,
                        deviceId = session.deviceId,
                        deviceKeyAlias = "autplay.m5.${profile.value}",
                    )

                    override suspend fun persistSuccessor(
                        profileId: ServerProfileId,
                        successor: SessionCredentialEnvelope,
                    ) = Unit
                },
                keys,
            )
            val page = OkHttpSyncTransport("$base/api/v1", credentials, m5Rotation = rotation).bootstrap(
                ClientEventBinding(userId, session.deviceId, profile, LocalId(UUID.randomUUID().toString())), null, null, 0,
            )
            assertTrue(page.snapshotId.isNotBlank())
            assertTrue(port.lifecycle(profile, LifecycleCommand(LifecycleAction.REVOKE_DEVICE, UUID.randomUUID().toString(), session.deviceId, "user_requested")) is PairingNetworkResult.Success)
            assertTrue(port.capabilities(profile, bound) is PairingNetworkResult.Failure)
        } finally {
            session.accessToken.fill(0); session.refreshToken.fill(0); successor.fill(0)
            credentials.clear(profile); keys.delete("autplay.m5.${profile.value}")
        }
        }
    }

    private fun bindDevice(base: String, access: ByteArray, profile: ServerProfileId, user: UserId, device: String) {
        val epoch = UUID.randomUUID().toString()
        val payload = "{\"protocol_version\":1,\"user_id\":\"${user.value}\",\"device_id\":\"$device\",\"server_profile_id\":\"${profile.value}\",\"journal_epoch\":\"$epoch\",\"device_name\":\"M5B physical E2E\",\"platform\":\"ANDROID\",\"app_version\":\"e2e\"}"
        CLIENT.newCall(Request.Builder().url("$base/api/v1/devices/bind").header("Authorization", "Bearer ${access.toString(StandardCharsets.UTF_8)}").post(payload.toRequestBody(JSON)).build()).execute().use { check(it.isSuccessful) { "M5B_E2E_BIND_${it.code}" } }
    }

    private fun getJson(url: String) = CLIENT.newCall(Request.Builder().url(url).header("Cache-Control", "no-store").build()).execute().use {
        check(it.isSuccessful) { "M5B_E2E_HANDOFF_${it.code}" }; Json.parseToJsonElement(requireNotNull(it.body).string()).jsonObject
    }
    private fun kotlinx.serialization.json.JsonObject.required(name: String): String = requireNotNull(this[name]).jsonPrimitive.content
    private fun <T> expectSuccess(stage: String, result: PairingNetworkResult<T>): T = when (result) {
        is PairingNetworkResult.Success -> result.value
        is PairingNetworkResult.Failure -> error("M5B_E2E_${stage}_${result.code.uppercase()}")
    }
    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private fun targetContext() = InstrumentationRegistry.getInstrumentation().targetContext
    private companion object { val JSON = "application/json".toMediaType(); val CLIENT = OkHttpClient() }
}
