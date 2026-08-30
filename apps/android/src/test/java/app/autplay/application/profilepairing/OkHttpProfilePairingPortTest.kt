package app.autplay.application.profilepairing

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpProfilePairingPortTest {
    @Test fun discoveryRejectsResponseAboveContractBoundBeforeParsing() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody("x".repeat(16 * 1024 + 1)))
            val port = port()
            val result = port.discovery(server.url("/").toString().trimEnd('/'))
            assertEquals(PairingNetworkResult.Failure("server_unavailable"), result)
            assertEquals("/api/v1/pairing/discovery", server.takeRequest().path)
        } finally { server.close() }
    }

    @Test fun lifecycleCarriesBoundedIdempotencyAndExplicitRevokeTarget() {
        val operation = UUID.randomUUID().toString()
        val logout = LifecycleCommand(LifecycleAction.LOGOUT_CURRENT, operation)
        assertEquals(operation, logout.operationId)
        assertFails { LifecycleCommand(LifecycleAction.REVOKE_DEVICE, operation) }
        assertFails { LifecycleCommand(LifecycleAction.LOGOUT_ALL, operation, app.autplay.domain.DeviceId(UUID.randomUUID().toString())) }
        assertTrue(LifecycleCommand(LifecycleAction.REVOKE_DEVICE, operation, app.autplay.domain.DeviceId(UUID.randomUUID().toString())).targetDeviceId != null)
    }

    @Test fun standardApiErrorEnvelopePreservesMachineReadableCode() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(
                MockResponse().setResponseCode(422).setBody(
                    """{"error":{"code":"request_validation_failed","message":"safe","retryable":false,"request_id":"00000000-0000-4000-8000-000000000000"}}""",
                ),
            )
            val result = port().discovery(server.url("/").toString().trimEnd('/'))
            assertEquals(PairingNetworkResult.Failure("request_validation_failed"), result)
        } finally {
            server.close()
        }
    }

    @Test fun rotationSendsCanonicalRefreshTokenWithoutDoubleEncoding() = runBlocking {
        val server = MockWebServer()
        server.start()
        val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
        val refresh = "r".repeat(43)
        val next = "n".repeat(43).toByteArray(StandardCharsets.US_ASCII)
        val credentials = StoredCredentials(
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = "a".repeat(32),
                    refreshToken = refresh,
                    generation = 0,
                    bindingCommitId = "22222222-2222-4222-8222-222222222222",
                    sessionId = "33333333-3333-4333-8333-333333333333",
                    sessionFamilyId = "44444444-4444-4444-8444-444444444444",
                    sessionGeneration = 0,
                ),
            ),
        )
        try {
            server.enqueue(
                MockResponse()
                    .setHeader("Cache-Control", "no-store")
                    .setHeader("Pragma", "no-cache")
                    .setBody(
                        """{"contract_version":"v1","schema_version":1,"access_token":"${"b".repeat(32)}","parent_session_id":"33333333-3333-4333-8333-333333333333","session_id":"55555555-5555-4555-8555-555555555555","family_id":"44444444-4444-4444-8444-444444444444","generation":1}""",
                    ),
            )
            val port = OkHttpProfilePairingPort(
                { if (it == profile) server.url("/").toString().trimEnd('/') else null },
                credentials,
                SigningKeys,
                allowUnsafeDevelopmentHttp = true,
            )
            val snapshot = PairingFlowSnapshot(
                generationId = "66666666-6666-4666-8666-666666666666",
                apiOrigin = server.url("/").toString().trimEnd('/'),
                streamOrigin = server.url("/").toString().trimEnd('/'),
                serverProfileId = profile,
                expectedServerInstanceId = "77777777-7777-4777-8777-777777777777",
                expectedIdentityEpoch = 1,
                expectedIdentityThumbprintSha256 = "a".repeat(64),
                expectedUserId = UserId("88888888-8888-4888-8888-888888888888"),
                expectedDeviceId = DeviceId("99999999-9999-4999-8999-999999999999"),
                deviceKeyThumbprintSha256 = "b".repeat(64),
                operationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                bindingCommitId = "22222222-2222-4222-8222-222222222222",
            )

            val result = port.rotate(
                SessionRotationCommand(
                    snapshot,
                    "33333333-3333-4333-8333-333333333333",
                    0,
                    next.copyOf(),
                    MessageDigest.getInstance("SHA-256").digest(next).joinToString("") { "%02x".format(it.toInt() and 0xff) },
                ),
            )

            assertTrue(result is PairingNetworkResult.Success)
            val body = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            assertEquals(refresh, body.getValue("current_refresh_token").jsonPrimitive.content)
        } finally {
            next.fill(0)
            credentials.clear(profile)
            server.close()
        }
    }

    private fun port() = OkHttpProfilePairingPort({ _: ServerProfileId -> null }, EmptyCredentials, EmptyKeys, allowUnsafeDevelopmentHttp = true)
    private fun assertFails(block: () -> Unit) { try { block(); throw AssertionError("expected failure") } catch (_: IllegalArgumentException) {} }
    private object EmptyCredentials : CredentialStore { override suspend fun read(profileId: ServerProfileId): ByteArray? = null; override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit; override suspend fun clear(profileId: ServerProfileId) = Unit }
    private object EmptyKeys : M5DeviceKeyStore { override fun publicKeySpki(alias: String) = error("unused"); override fun publicKeyThumbprintSha256(alias: String) = error("unused"); override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = error("unused"); override fun ensure(alias: String) = Unit; override fun delete(alias: String) = Unit }
    private class StoredCredentials(material: ByteArray) : CredentialStore {
        private var value: ByteArray? = material.copyOf()
        override suspend fun read(profileId: ServerProfileId) = value?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { value?.fill(0); value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { value?.fill(0); value = null }
    }
    private object SigningKeys : M5DeviceKeyStore {
        override fun publicKeySpki(alias: String) = ByteArray(64)
        override fun publicKeyThumbprintSha256(alias: String) = "b".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = ByteArray(64)
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) = Unit
    }
}
