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

class AccountDeletionTransportTest {
    @Test fun historicalReceiptAndCancellationNeverUseBearerCookiesOrRecoveryPaths() = runBlocking {
        val server = MockWebServer(); server.start()
        try {
            val origin = server.url("/").toString().trimEnd('/')
            val identity = SelfPairingIdentity("10000000-0000-4000-8000-000000000001", 1, "a".repeat(64), origin, origin)
            val jar = object : CookieJar {
                override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = Unit
                override fun loadForRequest(url: HttpUrl) = listOf(Cookie.Builder().name("ambient").value("secret").domain(url.host).build())
            }
            val port = OkHttpAccountDeletionTransport({ _, _ -> error("no session provider") },
                OkHttpClient.Builder().cookieJar(jar).callTimeout(Duration.ofSeconds(3)).build(), true)
            val code = AccountRecoveryProof.ALPHABET.toByteArray()
            val request = "{}".toByteArray()
            for (path in listOf("/account/deletion", "/deletion/request-receipt", "/deletion/request-resolve", "/deletion/cancel/preview", "/deletion/cancel/commit")) {
                server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("{}"))
                when (path) {
                    "/account/deletion" -> port.request(identity, "captured-bearer".toByteArray(), code, request)
                    "/deletion/request-receipt" -> port.receipt(identity, code, request)
                    "/deletion/request-resolve" -> port.resolve(identity, code, request)
                    else -> AccountDeletionCancellationTransport(port).recipient(identity, if (path.endsWith("preview")) "preview" else "recover", code, request)
                }
                val sent = server.takeRequest()
                assertEquals("/api/v1$path", sent.path)
                assertEquals("{}", sent.body.readUtf8())
                assertEquals(AccountRecoveryProof.ALPHABET, sent.getHeader("X-AutPlay-Recovery-Code"))
                assertNull(sent.getHeader("Cookie"))
                assertEquals(if (path == "/account/deletion") "Bearer captured-bearer" else null, sent.getHeader("Authorization"))
            }
            val refresh = "result-refresh-proof-with-32-bytes-minimum".toByteArray()
            server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("{}"))
            AccountDeletionCancellationTransport(port).outcome(identity, refresh, request)
            val outcome = server.takeRequest()
            assertEquals("/api/v1/deletion/cancel/outcome", outcome.path)
            assertEquals(refresh.toString(Charsets.US_ASCII), outcome.getHeader("X-AutPlay-Recovery-Refresh"))
            assertNull(outcome.getHeader("X-AutPlay-Recovery-Code"))
            assertNull(outcome.getHeader("Cookie"))
            assertNull(outcome.getHeader("Authorization"))
            server.enqueue(MockResponse().setResponseCode(307).addHeader("Location", "/leak"))
            assertTrue(runCatching { port.receipt(identity, code, request) }.exceptionOrNull() is AccountRecoveryRemoteFailure)
            assertEquals(7, server.requestCount)
            server.takeRequest()
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"error\":{\"code\":\"last_owner_required\",\"message\":\"private detail\"}}"))
            assertEquals("last_owner_required", runCatching { port.request(identity, "bearer".toByteArray(), code, request) }.exceptionOrNull()?.message)
            server.takeRequest()
            server.enqueue(MockResponse().setBody("{}"))
            assertTrue(runCatching { port.receipt(identity, code, request) }.isFailure)
            server.takeRequest()
            server.enqueue(MockResponse().addHeader("Cache-Control", "no-store").setBody("x".repeat(20000)))
            assertTrue(runCatching { port.cancel(identity, "cancel", code, request) }.isFailure)
        } finally { server.shutdown() }
    }
}
