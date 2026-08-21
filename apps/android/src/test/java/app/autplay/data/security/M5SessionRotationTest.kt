package app.autplay.data.security

import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class M5SessionRotationTest {
    @Test
    fun ambiguousResponseReplaysExactRequestThenPersistsSuccessorAndCheckpoint() = runBlocking {
        val server = MockWebServer()
        server.enqueue(
            MockResponse()
                .setHeader("Cache-Control", "no-store")
                .setHeader("Pragma", "no-cache")
                .setBody("{")
                .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY),
        )
        server.start()
        val store = MutableStore(SessionCredentialEnvelopeCodec.encode(currentEnvelope()))
        var persistedSuccessor: SessionCredentialEnvelope? = null
        val rotation = M5SessionRotationClient(
            contexts = object : M5RotationContextResolver {
                override suspend fun resolve(profileId: ServerProfileId) = M5RotationContext(
                    apiOrigin = server.url("/").toString(),
                    serverInstanceId = SERVER_INSTANCE_ID,
                    identityEpoch = 1,
                    deviceId = DEVICE_ID,
                    deviceKeyAlias = "test-key",
                )

                override suspend fun persistSuccessor(
                    profileId: ServerProfileId,
                    successor: SessionCredentialEnvelope,
                ) {
                    persistedSuccessor = successor
                }
            },
            keys = FakeKeys(),
        )
        val provider = RefreshingSessionCredentials(
            server.url("/api/v1").toString(),
            store,
            m5Rotation = rotation,
        )
        try {
            assertTrue(
                runCatching { provider.refreshAfterRejection(PROFILE, 0).close() }
                    .exceptionOrNull() is SessionRequiredException,
            )
            val first = server.takeRequest().body.readUtf8()
            val pending = store.decoded()
            assertTrue(pending.refreshPending)
            assertEquals(first, pending.m5PendingRotationRequest)
            val rotationId = Json.parseToJsonElement(first).jsonObject
                .getValue("rotation_id").jsonPrimitive.content
            val requestObject = Json.parseToJsonElement(first).jsonObject
            val unsigned = kotlinx.serialization.json.JsonObject(
                requestObject.filterKeys {
                    it != "request_sha256" && it != "device_signature_b64url"
                },
            )
            val canonical = org.erdtman.jcs.JsonCanonicalizer(unsigned.toString()).encodedString
            val expectedHash = MessageDigest.getInstance("SHA-256")
                .digest(canonical.toByteArray(StandardCharsets.UTF_8))
                .joinToString("") { "%02x".format(it.toInt() and 0xff) }
            assertEquals(expectedHash, requestObject.getValue("request_sha256").jsonPrimitive.content)
            server.enqueue(successResponse(rotationId))

            provider.refreshAfterRejection(PROFILE, 0).use { access ->
                assertEquals(1, access.generation)
                assertEquals(ACCESS_TOKEN, access.token.toString(StandardCharsets.UTF_8))
            }

            val second = server.takeRequest().body.readUtf8()
            assertEquals(first, second)
            val successor = store.decoded()
            assertFalse(successor.refreshPending)
            assertEquals(SUCCESSOR_SESSION_ID, successor.sessionId)
            assertEquals(1L, successor.sessionGeneration)
            assertEquals(43, successor.refreshToken?.length)
            assertNotEquals(CURRENT_REFRESH, successor.refreshToken)
            assertEquals(successor, persistedSuccessor)
        } finally {
            server.shutdown()
        }
    }

    private fun currentEnvelope() = SessionCredentialEnvelope(
        accessToken = "old-access",
        refreshToken = CURRENT_REFRESH,
        generation = 0,
        bindingCommitId = BINDING_COMMIT_ID,
        sessionId = PARENT_SESSION_ID,
        sessionFamilyId = SESSION_FAMILY_ID,
        sessionGeneration = 0,
    )

    private fun successResponse(rotationId: String) = MockResponse()
        .setHeader("Cache-Control", "no-store")
        .setHeader("Pragma", "no-cache")
        .setBody(
            """{"contract_version":"v1","schema_version":1,"rotation_id":"$rotationId","parent_session_id":"$PARENT_SESSION_ID","session_id":"$SUCCESSOR_SESSION_ID","family_id":"$SESSION_FAMILY_ID","generation":1,"access_token":"$ACCESS_TOKEN","expires_at":"2099-01-01T00:00:00Z"}""",
        )

    private class MutableStore(initial: ByteArray) : CredentialStore {
        private var material = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId) = synchronized(this) { material.copyOf() }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = synchronized(this) {
            this.material = material.copyOf()
        }
        override suspend fun clear(profileId: ServerProfileId) = synchronized(this) { material.fill(0) }
        fun decoded() = synchronized(this) { SessionCredentialEnvelopeCodec.decode(material.copyOf()) }
    }

    private class FakeKeys : M5DeviceKeyStore {
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) = Unit
        override fun publicKeySpki(alias: String) = ByteArray(91) { 1 }
        override fun publicKeyThumbprintSha256(alias: String) = "a".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) =
            ByteArray(64) { 2 }
    }

    private companion object {
        const val SERVER_INSTANCE_ID = "10000000-0000-4000-8000-000000000001"
        const val BINDING_COMMIT_ID = "10000000-0000-4000-8000-000000000002"
        const val PARENT_SESSION_ID = "10000000-0000-4000-8000-000000000003"
        const val SESSION_FAMILY_ID = "10000000-0000-4000-8000-000000000004"
        const val SUCCESSOR_SESSION_ID = "10000000-0000-4000-8000-000000000005"
        const val CURRENT_REFRESH = "c2Vzc2lvbi1yZWZyZXNoLXRva2VuLXdpdGgtNDMtY2hhcnM"
        const val ACCESS_TOKEN = "new-access-token-with-at-least-thirty-two-characters"
        val PROFILE = ServerProfileId("10000000-0000-4000-8000-000000000006")
        val DEVICE_ID = DeviceId("10000000-0000-4000-8000-000000000007")
    }
}
