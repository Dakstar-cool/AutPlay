package app.autplay.application.trainingconsent

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import app.autplay.application.selfpairing.SelfPairingJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class TrainingConsentTransportTest {
    @Test fun exactBodyBearerPrivacyBoundsAndNoRedirectOrAmbientCookie() = runBlocking {
        val server = MockWebServer(); server.start()
        try {
            val profile = ServerProfileId("10000000-0000-4000-8000-000000000001")
            val credentials = object : CredentialStore {
                override suspend fun read(profileId: ServerProfileId): ByteArray? = if (profileId == profile)
                    SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("opaque-test-access", "refresh", 0)) else null
                override suspend fun write(profileId: ServerProfileId, material: ByteArray) = error("No refresh expected")
                override suspend fun clear(profileId: ServerProfileId) = error("No clear expected")
            }
            val jar = object : CookieJar {
                override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = Unit
                override fun loadForRequest(url: HttpUrl) = listOf(Cookie.Builder().name("ambient").value("secret").domain(url.host).build())
            }
            val port = OkHttpTrainingConsentPort(server.url("/api/v1").toString(), profile, credentials,
                OkHttpClient.Builder().cookieJar(jar).build())
            val body = buildJsonObject { put("decision", "DENIED"); put("expected_revision", 0) }
            server.enqueue(MockResponse().setHeader("Cache-Control", "private, no-store").setBody("{}"))
            port.decide(body)
            val sent = server.takeRequest()
            assertEquals("PUT", sent.method)
            assertEquals("/api/v1/privacy/shared-training", sent.path)
            assertEquals(SelfPairingJson.canonical(body).toString(Charsets.UTF_8), sent.body.readUtf8())
            assertEquals("Bearer opaque-test-access", sent.getHeader("Authorization"))
            assertEquals("no-store", sent.getHeader("Cache-Control"))
            assertNull(sent.getHeader("Cookie"))
            server.enqueue(MockResponse().setResponseCode(307).setHeader("Location", "/leak"))
            assertTrue(runCatching { port.get() }.isFailure)
            assertEquals(2, server.requestCount)
            server.takeRequest()
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"error\":{\"code\":\"consent_revision_conflict\"}}"))
            assertEquals("consent_revision_conflict", runCatching { port.decide(body) }.exceptionOrNull()?.message)
            server.takeRequest()
            server.enqueue(MockResponse().setHeader("Cache-Control", "no-store").setBody("x".repeat(5000)))
            assertTrue(runCatching { port.get() }.isFailure)
            server.takeRequest()
            server.enqueue(MockResponse().setBody("{}"))
            assertTrue(runCatching { port.get() }.isFailure)
        } finally { server.shutdown() }
    }
}
