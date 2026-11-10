package app.autplay.application.releasewatch

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpReleaseFeedPortTest {
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private val candidateId = "22222222-2222-4222-8222-222222222222"
    private val artistId = "33333333-3333-4333-8333-333333333333"
    private val ownerRef = "44444444-4444-4444-8444-444444444444"

    @Test fun privateOwnerFeedParsesReadyAndEncodesOpaqueCursor() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setBody(body("READY", "PLAY", "\"$ownerRef\"", "opaque+/=")))
            val port = OkHttpReleaseFeedPort(server.url("/api/v1").toString().trimEnd('/'), FixedCredentials())
            val page = port.page(profile, "opaque+/=")
            assertEquals(candidateId, page.items.single().candidateId)
            assertEquals(ReleaseFeedAction.PLAY, page.items.single().availableAction)
            assertEquals(ownerRef, page.items.single().userTrackRefId)
            val request = server.takeRequest()
            assertTrue(request.path!!.startsWith("/api/v1/release-watch/feed?limit=20&cursor="))
            assertEquals("opaque+/=", request.requestUrl!!.queryParameter("cursor"))
            assertEquals("Bearer owner-token", request.getHeader("Authorization"))
            assertEquals("no-store", request.getHeader("Cache-Control"))
        } finally { server.shutdown() }
    }

    @Test fun disabledRouteIsUnavailableAndNonReadyCannotClaimPlayback() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val port = OkHttpReleaseFeedPort(server.url("/api/v1").toString().trimEnd('/'), FixedCredentials())
            server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
            val missing = runCatching { port.page(profile) }.exceptionOrNull()
            assertTrue(missing is ReleaseFeedException && missing.failure == ReleaseFeedFailure.UNAVAILABLE)
            server.enqueue(MockResponse().setBody(body("DISCOVERED", "PLAY", "null", null)))
            assertTrue(runCatching { port.page(profile) }.isFailure)
        } finally { server.shutdown() }
    }

    private fun body(state: String, action: String, ownerRef: String, next: String?): String =
        """{"contract_version":"release-feed-v1","schema_version":1,"ranking_version":"owner-local-v1","items":[{"candidate_id":"$candidateId","canonical_artist_id":"$artistId","artist_name":"Artist","title":"Release","released_at":"2026-09-24T00:00:00+00:00","reason":"MANUAL_FOLLOW","state":"$state","score_milli":100,"release_id":null,"recording_id":null,"user_track_ref_id":$ownerRef,"provider_id":"55555555-5555-4555-8555-555555555555","evidence_run_id":"66666666-6666-4666-8666-666666666666","evidence_adapter_version":"1.0.0","available_action":"$action"}],"next_cursor":${next?.let { "\"$it\"" } ?: "null"}}"""

    private inner class FixedCredentials : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray? = if (profileId == profile) {
            SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("owner-token", "refresh-token", 0))
        } else null
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
}
