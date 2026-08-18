package app.autplay.data.security

import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RefreshingSessionCredentialsTest {
    @Test fun rejectedGenerationRotatesAndPersistsCredential() = runBlocking {
        val server = MockWebServer()
        server.enqueue(tokenResponse("new-access", "new-refresh"))
        server.start()
        val store = MutableStore(envelope("old-access", "old-refresh", 4))
        try {
            val provider = RefreshingSessionCredentials(server.url("/api/v1").toString(), store)
            provider.refreshAfterRejection(PROFILE, 4).use { access ->
                assertEquals("new-access", access.token.toString(StandardCharsets.UTF_8))
                assertEquals(5, access.generation)
            }
            val request = server.takeRequest()
            assertEquals("/api/v1/auth/refresh", request.path)
            assertTrue(request.body.readUtf8().contains("old-refresh"))
            assertEquals(5, store.decoded().generation)
            assertEquals("new-refresh", store.decoded().refreshToken)
        } finally {
            server.shutdown()
        }
    }

    @Test fun concurrentRejectedGenerationUsesRefreshTokenOnce() = runBlocking {
        val server = MockWebServer()
        server.enqueue(tokenResponse("new-access", "new-refresh"))
        server.start()
        val store = MutableStore(envelope("old-access", "old-refresh", 0))
        try {
            val provider = RefreshingSessionCredentials(server.url("/api/v1").toString(), store)
            coroutineScope {
                List(4) {
                    async { provider.refreshAfterRejection(PROFILE, 0).use { it.generation } }
                }.awaitAll().forEach { assertEquals(1, it) }
            }
            assertEquals(1, server.requestCount)
        } finally {
            server.shutdown()
        }
    }

    @Test(expected = SessionRequiredException::class)
    fun rejectedRefreshRequiresSession() {
        runBlocking {
            val server = MockWebServer()
            server.enqueue(MockResponse().setResponseCode(401).setBody("{\"code\":\"invalid_refresh_token\"}"))
            server.start()
            try {
                RefreshingSessionCredentials(
                    server.url("/api/v1").toString(),
                    MutableStore(envelope("old-access", "old-refresh", 0)),
                ).refreshAfterRejection(PROFILE, 0)
            } finally {
                server.shutdown()
            }
        }
    }

    @Test fun ambiguousRefreshOutcomePersistsMarkerAndNeverReplaysToken() = runBlocking {
        val server = MockWebServer()
        server.enqueue(
            MockResponse()
                .setBody("{\"access_token\":\"new-access\",\"refresh_token\":\"new-refresh\"}")
                .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY),
        )
        server.start()
        val store = MutableStore(envelope("old-access", "old-refresh", 0))
        val provider = RefreshingSessionCredentials(server.url("/api/v1").toString(), store)
        try {
            assertTrue(provider.refreshFailure(PROFILE, 0) is SessionRequiredException)
            assertTrue(store.decoded().refreshPending)
            assertTrue(provider.refreshFailure(PROFILE, 0) is SessionRequiredException)
            assertEquals(1, server.requestCount)
        } finally {
            server.shutdown()
        }
    }

    private fun envelope(access: String, refresh: String, generation: Long): ByteArray =
        SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope(access, refresh, generation))

    private fun tokenResponse(access: String, refresh: String): MockResponse = MockResponse()
        .setHeader("Cache-Control", "no-store")
        .setBody("{\"access_token\":\"$access\",\"refresh_token\":\"$refresh\"}")

    private suspend fun RefreshingSessionCredentials.refreshFailure(
        profileId: ServerProfileId,
        generation: Long,
    ): Throwable? = runCatching {
        refreshAfterRejection(profileId, generation).close()
    }.exceptionOrNull()

    private class MutableStore(initial: ByteArray) : CredentialStore {
        private var material = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId): ByteArray = synchronized(this) { material.copyOf() }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = synchronized(this) {
            this.material = material.copyOf()
        }
        override suspend fun clear(profileId: ServerProfileId) = synchronized(this) { material.fill(0) }
        fun decoded(): SessionCredentialEnvelope = synchronized(this) {
            SessionCredentialEnvelopeCodec.decode(material.copyOf())
        }
    }

    private companion object {
        val PROFILE = ServerProfileId("10000000-0000-4000-8000-000000000001")
    }
}
