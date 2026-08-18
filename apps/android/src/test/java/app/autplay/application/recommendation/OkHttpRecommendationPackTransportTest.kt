package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpRecommendationPackTransportTest {
    @Test
    fun authenticatedPostUsesExactBoundedContractAndZerosCredential() = runBlocking {
        val material = "secret-device-token".toByteArray()
        val server = MockWebServer()
        server.enqueue(
            MockResponse()
                .setHeader("Cache-Control", "no-store")
                .setBody(
                    """{"offline_pack_id":"$PACK","recommendation_request_id":"$REQUEST","payload_version":1,"payload_encoding":"RAW_JSON","payload_base64":"e30=","payload_sha256":"${"a".repeat(64)}","created_at_ms":1,"expires_at_ms":2}""",
                ),
        )
        server.start()
        val credentials = object : CredentialStore {
            override suspend fun read(profileId: ServerProfileId): ByteArray = material
            override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
            override suspend fun clear(profileId: ServerProfileId) = Unit
        }
        try {
            val downloaded = OkHttpRecommendationPackTransport(server.url("/api/v1").toString(), credentials)
                .fetch(BINDING, RecommendationPackFetchRequest())
            val observed = checkNotNull(server.takeRequest())

            assertEquals("/api/v1/recommendation-packs", observed.requestUrl?.encodedPath)
            assertEquals("Bearer secret-device-token", observed.getHeader("Authorization"))
            val body = Json.parseToJsonElement(observed.body.readUtf8()).jsonObject
            assertEquals(setOf("context", "limit", "exploration", "seed", "pipeline_key", "pipeline_version", "shadow", "ttl_days"), body.keys)
            assertFalse(body.containsKey("user_id"))
            assertFalse(body.containsKey("device_id"))
            assertEquals(PACK, downloaded.offlinePackId)
            assertEquals(1, downloaded.payloadVersion)
            assertTrue(material.all { it == 0.toByte() })
        } finally {
            server.shutdown()
        }
    }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val DEVICE = "33333333-3333-4333-8333-333333333333"
        const val PACK = "44444444-4444-4444-8444-444444444444"
        const val REQUEST = "55555555-5555-4555-8555-555555555555"
        val BINDING = ClientEventBinding(UserId(USER), DeviceId(DEVICE), ServerProfileId(PROFILE))
    }
}
