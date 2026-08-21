package app.autplay.application.profilepairing

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.ServerProfileId
import java.util.UUID
import kotlinx.coroutines.runBlocking
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

    private fun port() = OkHttpProfilePairingPort({ _: ServerProfileId -> null }, EmptyCredentials, EmptyKeys, allowUnsafeDevelopmentHttp = true)
    private fun assertFails(block: () -> Unit) { try { block(); throw AssertionError("expected failure") } catch (_: IllegalArgumentException) {} }
    private object EmptyCredentials : CredentialStore { override suspend fun read(profileId: ServerProfileId): ByteArray? = null; override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit; override suspend fun clear(profileId: ServerProfileId) = Unit }
    private object EmptyKeys : M5DeviceKeyStore { override fun publicKeySpki(alias: String) = error("unused"); override fun publicKeyThumbprintSha256(alias: String) = error("unused"); override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = error("unused"); override fun ensure(alias: String) = Unit; override fun delete(alias: String) = Unit }
}
