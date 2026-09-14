package app.autplay.data.network

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CancellableCallTest {
    @Test fun cancellationAbortsWaitingHeaders() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            server.start()
            val call = OkHttpClient().newCall(Request.Builder().url(server.url("/")).build())
            val request = async(Dispatchers.IO) { call.readCancellable { it.body.string() } }
            assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
            withTimeout(2_000) { request.cancelAndJoin() }
            assertTrue(call.isCanceled())
        }
    }

    @Test fun cancellationAbortsBodyAndClosesResponse() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("payload").setBodyDelay(2, TimeUnit.SECONDS))
            server.start()
            val reading = CompletableDeferred<Unit>()
            val closed = CompletableDeferred<Unit>()
            val call = OkHttpClient().newCall(Request.Builder().url(server.url("/")).build())
            val request = async(Dispatchers.IO) {
                call.readCancellable {
                    reading.complete(Unit)
                    try { it.body.string() } finally { closed.complete(Unit) }
                }
            }
            withTimeout(5_000) { reading.await() }
            withTimeout(1_000) { request.cancelAndJoin(); closed.await() }
            assertTrue(call.isCanceled())
        }
    }
}
