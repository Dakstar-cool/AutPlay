package app.autplay.application.selfpairing

import java.time.Duration
import kotlinx.coroutines.runBlocking
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class SelfPairingTransportTest {
    @Test fun publicProofHasNoAmbientSessionAndNeverFollowsRedirects() = runBlocking {
        val server = MockWebServer()
        server.start()
        val cookieJar = object : CookieJar {
            override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = Unit
            override fun loadForRequest(url: HttpUrl): List<Cookie> = listOf(Cookie.Builder().name("session").value("ambient").domain(url.host).build())
        }
        val client = OkHttpClient.Builder().cookieJar(cookieJar).callTimeout(Duration.ofSeconds(3)).build()
        try {
            val origin = server.url("/").toString().trimEnd('/')
            val identity = SelfPairingIdentity(ID, 1, "a".repeat(64), origin, origin)
            val port = OkHttpSelfPairingTransport({ _, _ -> error("no source credentials") }, client, true)
            server.enqueue(MockResponse().setResponseCode(307).addHeader("Location", "/leaked"))
            val secret = SelfPairingProof.secret()
            try {
                val failure = runCatching { port.recipient(identity, "claim", ID, secret, "{}".toByteArray()) }.exceptionOrNull()
                assertTrue(failure is SelfPairingRemoteFailure)
                val request = server.takeRequest()
                assertEquals("/api/v1/pairing/self-service/$ID/claim", request.path)
                assertNull(request.getHeader("Cookie"))
                assertNull(request.getHeader("Authorization"))
                assertEquals(secret.toString(Charsets.US_ASCII), request.getHeader("X-AutPlay-Pairing-Secret"))
                assertEquals(1, server.requestCount)
            } finally { secret.fill(0) }
        } finally { server.shutdown() }
    }

    @Test fun oversizedResponseStopsAtBoundAndSanitizedConflictIsUsable() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val origin = server.url("/").toString().trimEnd('/')
            val identity = SelfPairingIdentity(ID, 1, "a".repeat(64), origin, origin)
            val port = OkHttpSelfPairingTransport({ _, _ -> error("unexpected") }, allowDevelopmentHttp = true)
            val secret = SelfPairingProof.secret()
            try {
                server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("x".repeat(20000)))
                val large = runCatching { port.recipient(identity, "poll", ID, secret, "{}".toByteArray()) }.exceptionOrNull()
                assertEquals("SELF_PAIRING_RESPONSE_TOO_LARGE", large?.message)
                server.enqueue(MockResponse().setResponseCode(409).setBody("""{"error_code":"self_pairing_revision_conflict"}"""))
                val conflict = runCatching { port.recipient(identity, "poll", ID, secret, "{}".toByteArray()) }.exceptionOrNull()
                assertEquals("self_pairing_revision_conflict", (conflict as SelfPairingRemoteFailure).code)
            } finally { secret.fill(0) }
        } finally { server.shutdown() }
    }
    private companion object { const val ID = "10000000-0000-4000-8000-000000000001" }
}
