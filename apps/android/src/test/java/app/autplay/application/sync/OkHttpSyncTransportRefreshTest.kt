package app.autplay.application.sync

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Test

class OkHttpSyncTransportRefreshTest {
    @Test fun pullRefreshesRejectedAccessAndRetriesSameRequestOnce() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(401))
        server.enqueue(MockResponse().setBody("{\"access_token\":\"fresh-access\",\"refresh_token\":\"fresh-refresh\"}"))
        server.enqueue(MockResponse().setBody("{\"next_cursor\":\"next\",\"has_more\":false,\"events\":[]}"))
        server.start()
        val credentials = MutableCredentialStore(
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope("stale-access", "valid-refresh", 0),
            ),
        )
        try {
            val page = OkHttpSyncTransport(server.url("/api/v1").toString(), credentials)
                .pull(BINDING, "before")
            assertEquals("next", page.nextCursor)
            val rejected = server.takeRequest()
            val refresh = server.takeRequest()
            val retried = server.takeRequest()
            assertEquals("Bearer stale-access", rejected.getHeader("Authorization"))
            assertEquals("/api/v1/auth/refresh", refresh.path)
            assertEquals("Bearer fresh-access", retried.getHeader("Authorization"))
            assertEquals(rejected.path, retried.path)
        } finally {
            server.shutdown()
        }
    }

    private class MutableCredentialStore(initial: ByteArray) : CredentialStore {
        private var value = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId): ByteArray = value.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { value.fill(0) }
    }

    private companion object {
        val BINDING = ClientEventBinding(
            UserId("20000000-0000-4000-8000-000000000001"),
            DeviceId("30000000-0000-4000-8000-000000000001"),
            ServerProfileId("40000000-0000-4000-8000-000000000001"),
            LocalId("50000000-0000-4000-8000-000000000001"),
        )
    }
}
