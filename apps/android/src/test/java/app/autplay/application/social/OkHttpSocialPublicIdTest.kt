package app.autplay.application.social

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpSocialPublicIdTest {
    @Test fun `public ID endpoints preserve the existing API origin and authenticated signed card boundary`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"public_id":null,"status":"unregistered"}"""))
            server.enqueue(MockResponse().setBody("""{"public_id":"ptica_1","status":"confirmed"}"""))
            server.enqueue(MockResponse().setBody(card.asJson().toString()))
            server.start()
            val port = OkHttpSocialPort(server.url("/api/v1").toString(), Store())
            assertEquals(SocialResult.Success<String?>(null), port.ownPublicId(PROFILE))
            assertEquals(SocialResult.Success(PublicIdRegistrationReceipt("ptica_1")), port.registerPublicId(PROFILE, OPERATION, "ptica_1"))
            assertEquals(SocialResult.Success(card), port.lookupPublicId(PROFILE, "alice"))
            val own = server.takeRequest()
            val registration = server.takeRequest()
            val lookup = server.takeRequest()
            assertEquals("/api/v1/social/public-id", own.path)
            assertEquals("PUT", registration.method)
            assertEquals("/api/v1/social/public-id", registration.path)
            val body = Json.parseToJsonElement(registration.body.readUtf8()).jsonObject
            assertEquals(setOf("operation_id", "public_id"), body.keys)
            assertEquals(OPERATION, body.getValue("operation_id").jsonPrimitive.content)
            assertEquals("ptica_1", body.getValue("public_id").jsonPrimitive.content)
            assertEquals("/api/v1/social/accounts/by-public-id/alice", lookup.path)
            listOf(own, registration, lookup).forEach {
                assertEquals("Bearer access", it.getHeader("Authorization"))
                assertEquals("no-store", it.getHeader("Cache-Control"))
            }
        }
    }

    @Test fun `missing blocked and inactive lookup results have one unavailable code`() = runBlocking {
        MockWebServer().use { server ->
            repeat(3) { server.enqueue(MockResponse().setResponseCode(404).setBody("""{"error":{"code":"public_id_not_found","message":"Unavailable","retryable":false,"request_id":"test"}}""")) }
            server.start()
            val port = OkHttpSocialPort(server.url("/").toString(), Store())
            listOf("missing", "blocked", "inactive").forEach {
                assertEquals(SocialResult.Failure("public_id_not_found"), port.lookupPublicId(PROFILE, it))
            }
        }
    }

    @Test fun `crossed requests decode as both pending directions`() = runBlocking {
        MockWebServer().use { server ->
            val person = """{"account_id":"$ACCOUNT","display_name_hint":"Alice","presence":"OFFLINE"}"""
            server.enqueue(MockResponse().setBody("""{"friends":[],"incoming_requests":[$person],"outgoing_requests":[$person],"blocked":[],"sent_room_invitations":[],"received_room_invitations":[],"presence_settings":{}}"""))
            server.start()
            val result = OkHttpSocialPort(server.url("/").toString(), Store()).snapshot(PROFILE)
            assertTrue(result is SocialResult.Success)
            val snapshot = (result as SocialResult.Success).value
            assertEquals(listOf(FriendshipStatus.PENDING_INBOUND, FriendshipStatus.PENDING_OUTBOUND), snapshot.friends.map { it.status })
        }
    }

    @Test fun `credentials and network leave the caller thread even when it has no IO dispatcher`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"public_id":"alice","status":"confirmed"}"""))
            server.start()
            val callerThread = Thread.currentThread().id
            val store = Store()
            val networkThread = AtomicReference<Long?>()
            val client = OkHttpClient.Builder().addInterceptor {
                networkThread.set(Thread.currentThread().id)
                it.proceed(it.request())
            }.build()
            val result = OkHttpSocialPort(server.url("/").toString(), store, client).ownPublicId(PROFILE)
            assertEquals(SocialResult.Success("alice"), result)
            assertNotNull(store.readThread.get())
            assertNotEquals(callerThread, store.readThread.get())
            assertNotEquals(callerThread, networkThread.get())
        }
    }

    @Test fun `cancelled lookup aborts the HTTP call and propagates cancellation`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            server.start()
            val activeCall = AtomicReference<Call?>()
            val client = OkHttpClient.Builder().eventListener(object : EventListener() {
                override fun callStart(call: Call) { activeCall.set(call) }
            }).build()
            val port = OkHttpSocialPort(server.url("/").toString(), Store(), client)
            val request = async { port.lookupPublicId(PROFILE, "alice") }
            yield()
            assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
            withTimeout(2_000) { request.cancelAndJoin() }
            assertTrue(activeCall.get()!!.isCanceled())
            assertTrue(runCatching { request.await() }.exceptionOrNull() is CancellationException)
        }
    }

    @Test fun `oversized and contradictory owner replies fail closed`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("x".repeat(4_097)))
            server.enqueue(MockResponse().setBody("""{"public_id":"alice","status":"unregistered"}"""))
            server.enqueue(MockResponse().setBody("""{"public_id":"other_name","status":"confirmed"}"""))
            server.start()
            val port = OkHttpSocialPort(server.url("/").toString(), Store())
            assertEquals(SocialResult.Failure("server_unavailable"), port.ownPublicId(PROFILE))
            assertEquals(SocialResult.Failure("server_unavailable"), port.ownPublicId(PROFILE))
            assertEquals(SocialResult.Failure("server_unavailable"), port.registerPublicId(PROFILE, OPERATION, "my_name"))
        }
    }

    private class Store : CredentialStore {
        val readThread = AtomicReference<Long?>()
        private var value = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("access", "refresh", 0))
        override suspend fun read(profileId: ServerProfileId): ByteArray? {
            readThread.compareAndSet(null, Thread.currentThread().id)
            return if (profileId in app.autplay.data.security.CredentialJournalSlots.all) null else value.copyOf()
        }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { value.fill(0) }
    }

    private companion object {
        val PROFILE = ServerProfileId("11111111-1111-4111-8111-111111111111")
        const val OPERATION = "22222222-2222-4222-8222-222222222222"
        const val ACCOUNT = "33333333-3333-4333-8333-333333333333"
        val card = ContactCard("44444444-4444-4444-8444-444444444444", ACCOUNT, "Alice", "2026-10-06T12:00:00Z", "2026-11-05T12:00:00Z", "a".repeat(86))
    }
}
