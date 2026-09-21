package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
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

class AccountRecoveryTransportTest {
    @Test fun codeStaysInPrivateHeaderWithoutCookiesRedirectsOrResponseLeakage() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val origin = server.url("/").toString().trimEnd('/')
            val cookieJar = object : CookieJar {
                override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = Unit
                override fun loadForRequest(url: HttpUrl) = listOf(Cookie.Builder().name("session").value("ambient").domain(url.host).build())
            }
            val identity = SelfPairingIdentity("10000000-0000-4000-8000-000000000001", 1, "a".repeat(64), origin, origin)
            val client = OkHttpClient.Builder().cookieJar(cookieJar).callTimeout(Duration.ofSeconds(3)).build()
            val port = OkHttpAccountRecoveryTransport({ _, _ -> error("no ambient session") }, client, true)
            val code = AccountRecoveryProof.ALPHABET.toByteArray()
            server.enqueue(MockResponse().setResponseCode(307).addHeader("Location", "/leak"))
            val redirect = runCatching { port.recipient(identity, "preview", code, "{}".toByteArray()) }.exceptionOrNull()
            assertTrue(redirect is AccountRecoveryRemoteFailure)
            val request = server.takeRequest()
            assertEquals("/api/v1/recovery/preview", request.path)
            assertEquals("{}", request.body.readUtf8())
            assertEquals(AccountRecoveryProof.ALPHABET, request.getHeader("X-AutPlay-Recovery-Code"))
            assertNull(request.getHeader("Cookie"))
            assertNull(request.getHeader("Authorization"))
            assertEquals(1, server.requestCount)
            server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("x".repeat(20000)))
            assertTrue(runCatching { port.recipient(identity, "recover", code, "{}".toByteArray()) }.isFailure)
            server.enqueue(MockResponse().setResponseCode(409).setBody("private diagnostic with ${AccountRecoveryProof.ALPHABET}"))
            val conflict = runCatching { port.recipient(identity, "recover", code, "{}".toByteArray()) }.exceptionOrNull()
            assertEquals("recovery_conflict", conflict?.message)
            val refresh = "result-refresh-proof-with-32-bytes-minimum".toByteArray()
            server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("{}"))
            port.outcome(identity, refresh, "{}".toByteArray())
            assertEquals("/api/v1/recovery/commit", server.takeRequest().path)
            assertEquals("/api/v1/recovery/commit", server.takeRequest().path)
            val outcome = server.takeRequest()
            assertEquals("/api/v1/recovery/outcome", outcome.path)
            assertEquals(refresh.toString(Charsets.US_ASCII), outcome.getHeader("X-AutPlay-Recovery-Refresh"))
            assertNull(outcome.getHeader("X-AutPlay-Recovery-Code"))
            assertNull(outcome.getHeader("Cookie"))
        } finally { server.shutdown() }
    }
}
