package app.autplay.application.publicaccess

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PublicAccessHttpPortsTest {
    @Test
    fun registrationUsesSeparateUnauthenticatedNoStoreTransport() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val userId = UUID.randomUUID().toString()
            val deviceId = UUID.randomUUID().toString()
            val sessionId = UUID.randomUUID().toString()
            val invitation = invitation(server.url("/").toString().trimEnd('/'))
            val signed = AccountRegistrationProof.create(invitation, "Pixel", "0.3.0", FakeKeys())
            server.enqueue(
                MockResponse()
                    .setResponseCode(201)
                    .addHeader("Cache-Control", "no-store")
                    .addHeader("Pragma", "no-cache")
                    .setBody(
                        """{"contract_version":"v1","schema_version":1,"registration_id":"${signed.registration.registrationId}","binding_commit_id":"${signed.registration.bindingCommitId}","server_instance_id":"${invitation.serverInstanceId}","user_id":"$userId","account_display_name":"Friend","account_role":"USER","device_id":"$deviceId","session_id":"$sessionId","refresh_generation":0,"refresh_absolute_expires_at":"2999-01-01T00:00:00Z","receipt_expires_at":"2999-01-01T00:05:00Z","access_token":"${"x".repeat(64)}","access_expires_at":"2998-01-01T00:00:00Z","replayed":false}""",
                    ),
            )

            val result = OkHttpAccountRegistrationPort(
                allowUnsafeDevelopmentHttp = true,
            ).redeem(signed)
            assertTrue(result.isSuccess)
            result.getOrThrow().accessToken.fill(0)
            val request = server.takeRequest()
            assertEquals("/api/v1/public-access/account-invitations/redeem", request.path)
            assertNull(request.getHeader("Authorization"))
            assertNull(request.getHeader("Cookie"))
            assertEquals("no-store", request.getHeader("Cache-Control"))
            assertEquals("no-cache", request.getHeader("Pragma"))
            signed.close()
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun ownerListingUsesBearerAndRequiresNoStoreNoCacheResponse() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(
                MockResponse()
                    .addHeader("Cache-Control", "no-store")
                    .addHeader("Pragma", "no-cache")
                    .setBody("""{"contract_version":"v1","schema_version":1,"items":[],"next_cursor":null}"""),
            )
            val profile = ServerProfileId(UUID.randomUUID().toString())
            val port = OkHttpOwnerProvisioningPort(
                server.url("/").toString().trimEnd('/'),
                profile,
                FixedCredentialStore(profile),
                allowUnsafeDevelopmentHttp = true,
            )

            assertEquals(emptyList<AccountInvitationView>(), port.listInvitations().getOrThrow())
            val request = server.takeRequest()
            assertEquals("/api/v1/public-access/account-invitations?limit=50", request.path)
            assertEquals("Bearer owner-token", request.getHeader("Authorization"))
            assertEquals("no-store", request.getHeader("Cache-Control"))
            assertEquals("no-cache", request.getHeader("Pragma"))
        } finally {
            server.shutdown()
        }
    }

    private class FixedCredentialStore(private val expected: ServerProfileId) : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray? {
            assertEquals(expected, profileId)
            return SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("owner-token", "refresh-token", 0))
        }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }

    private class FakeKeys : M5DeviceKeyStore {
        override fun ensure(alias: String) = Unit
        override fun publicKeySpki(alias: String) = ByteArray(91) { 7 }
        override fun publicKeyThumbprintSha256(alias: String) = "b".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = ByteArray(64) { 1 }
        override fun delete(alias: String) = Unit
    }

    private fun invitation(origin: String) = AccountInvitation(
        UUID.randomUUID().toString(),
        UUID.randomUUID().toString(),
        1,
        "a".repeat(64),
        origin,
        origin,
        "Friend",
        Instant.now().minusSeconds(60),
        Instant.now().plusSeconds(3_600),
        ByteArray(32) { 3 },
    )
}
