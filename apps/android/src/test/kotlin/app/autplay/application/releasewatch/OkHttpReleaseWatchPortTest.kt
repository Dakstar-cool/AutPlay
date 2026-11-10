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

class OkHttpReleaseWatchPortTest {
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private val artistId = "22222222-2222-4222-8222-222222222222"

    @Test fun ownerBearerContractAndMutationPaths() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(ok("\"artists\":[{\"artist_id\":\"$artistId\",\"reason\":\"MANUAL_FOLLOW\",\"score_milli\":0,\"favorite_generation\":null}]"))
            server.enqueue(ok("\"artist_ids\":[\"$artistId\"]"))
            server.enqueue(ok("\"artist_id\":\"$artistId\",\"revision\":2"))
            server.enqueue(ok("\"artist_id\":\"$artistId\",\"revision\":3"))
            val port = OkHttpReleaseWatchPort(server.url("/api/v1").toString().trimEnd('/'), FixedCredentials())
            assertEquals(WatchReason.MANUAL_FOLLOW, port.artists(profile).items.single().reason)
            assertEquals(listOf(artistId), port.exclusions(profile).items)
            port.setFollow(profile, artistId, true)
            port.setFollow(profile, artistId, false)
            listOf(
                "GET" to "/api/v1/release-watch/artists?limit=100",
                "GET" to "/api/v1/release-watch/exclusions?limit=100",
                "PUT" to "/api/v1/release-watch/artists/$artistId",
                "DELETE" to "/api/v1/release-watch/artists/$artistId",
            ).forEach { (method, path) ->
                val request = server.takeRequest()
                assertEquals(method, request.method)
                assertEquals(path, request.path)
                assertEquals("Bearer owner-token", request.getHeader("Authorization"))
                assertEquals("no-store", request.getHeader("Cache-Control"))
            }
        } finally {
            server.shutdown()
        }
    }

    @Test fun disabledRouteIsUnavailableWithoutClaimingSuccess() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
            val port = OkHttpReleaseWatchPort(server.url("/api/v1").toString().trimEnd('/'), FixedCredentials())
            val failure = runCatching { port.artists(profile) }.exceptionOrNull()
            assertTrue(failure is ReleaseWatchException && failure.failure == WatchFailure.UNAVAILABLE)
        } finally {
            server.shutdown()
        }
    }

    @Test fun autoDownloadUsesFetchedConfirmationOnlyForEnableAndParsesArtistStatus() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val artistPayload = """[{"artist_id":"$artistId","reason":"MANUAL_FOLLOW","score_milli":0,"favorite_generation":null,"auto_download":{"watch_policy_id":null,"policy_revision":null,"policy_import_mode":"REVIEW_REQUIRED","effective_opt_in":false,"execution_enabled":false,"reason":"STAGED_GATE_OFF"}}]"""
            server.enqueue(ok("\"artists\":$artistPayload"))
            server.enqueue(ok("\"enabled\":false,\"revision\":4,\"execution_enabled\":true,\"consequence_confirmation\":\"exact-token\""))
            server.enqueue(ok("\"revision\":5"))
            server.enqueue(ok("\"revision\":6"))
            val port = OkHttpReleaseWatchPort(server.url("/api/v1").toString().trimEnd('/'), FixedCredentials())
            val artist = port.artists(profile).items.single()
            assertEquals(ArtistAutoDownloadReason.STAGED_GATE_OFF, artist.autoDownload?.reason)
            assertEquals(false, artist.autoDownload?.effectiveOptIn)
            val settings = port.autoDownloadSettings(profile)
            assertEquals("exact-token", settings.consequenceConfirmation)
            port.setAutoDownload(profile, true, settings.revision, settings.consequenceConfirmation)
            port.setAutoDownload(profile, false, settings.revision + 1, null)
            server.takeRequest()
            val read = server.takeRequest()
            assertEquals("/api/v1/release-watch/auto-download", read.path)
            val enable = server.takeRequest()
            assertEquals("PUT", enable.method)
            assertEquals("Bearer owner-token", enable.getHeader("Authorization"))
            assertEquals("no-store", enable.getHeader("Cache-Control"))
            assertTrue(enable.body.readUtf8().contains("\"consequence_confirmation\":\"exact-token\""))
            val disable = server.takeRequest()
            assertEquals("PUT", disable.method)
            assertTrue(!disable.body.readUtf8().contains("consequence_confirmation"))
        } finally { server.shutdown() }
    }

    private fun ok(fields: String) = MockResponse().setBody(
        "{\"contract_version\":\"release-watch-v1\",\"schema_version\":1,$fields}",
    )

    private inner class FixedCredentials : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray? = if (profileId == profile) {
            SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("owner-token", "refresh-token", 0))
        } else null
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
}
