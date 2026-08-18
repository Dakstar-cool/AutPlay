package app.autplay.application.server

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerFeatureRepositoryTest {
    @Test
    fun librarySnapshotUsesExactAuthenticatedPagesAndRefreshesOnce() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(401))
        server.enqueue(MockResponse().setBody("""{"access_token":"fresh","refresh_token":"fresh-r"}"""))
        server.enqueue(MockResponse().setBody(entriesJson()))
        server.enqueue(MockResponse().setBody(playlistsJson()))
        server.enqueue(MockResponse().setBody(historyJson()))
        server.start()
        val store = MutableStore(
            SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("stale", "refresh", 0)),
        )
        try {
            val repository = repository(server, store)
            val snapshot = repository.librarySnapshot(10)
            assertEquals(1, snapshot.entries.size)
            assertEquals("AVAILABLE", snapshot.entries.single().availabilityStatus)
            assertEquals(1, snapshot.playlists.size)
            assertEquals(3L, snapshot.history.single().playedMs)

            assertEquals("/api/v1/library/entries?limit=10", server.takeRequest().path)
            assertEquals("/api/v1/auth/refresh", server.takeRequest().path)
            assertEquals("Bearer fresh", server.takeRequest().getHeader("Authorization"))
            assertEquals("/api/v1/library/playlists?limit=10", server.takeRequest().path)
            assertEquals("/api/v1/library/history?limit=10", server.takeRequest().path)
            assertEquals(1, store.decoded().generation)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun importVaultRecommendationAndLogoutContractsAreBounded() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"import_job_id":"$IMPORT","delivery_job_id":"$DELIVERY","replayed":false}"""))
        server.enqueue(MockResponse().setBody(importReportJson()))
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"upload_id":"$UPLOAD","offset":0,"expected_size":3,"state":"OPEN"}"""))
        server.enqueue(MockResponse().setResponseCode(204).setHeader("Upload-Offset", "3"))
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"upload_id":"$UPLOAD","offset":3,"expected_size":3,"state":"PROCESSING"}"""))
        server.enqueue(MockResponse().setBody(recommendationJson()))
        server.enqueue(MockResponse().setResponseCode(204))
        server.start()
        val store = MutableStore("access".toByteArray())
        try {
            val repository = repository(server, store)
            val started = repository.startImport("{}".toByteArray(), "JSON", materialize = true)
            assertEquals(IMPORT, started.importJobId)
            val report = repository.importReport(IMPORT, "page-1")
            assertEquals("RUNNING", report.state)
            assertNull(report.entries.single().resolverState)

            val upload = repository.createVaultUpload(RECORDING, 3, "a".repeat(64), "stable-key")
            assertEquals(UPLOAD, upload.uploadId)
            assertEquals(3, repository.appendVaultUpload(UPLOAD, 0, 0, byteArrayOf(1, 2, 3)))
            assertEquals("PROCESSING", repository.completeVaultUpload(UPLOAD).state)

            val recommendations = repository.recommendations(5)
            assertEquals(REQUEST, recommendations.requestId)
            assertEquals(RECORDING, recommendations.items.single().recordingId)
            repository.logout()
            assertNull(store.read(PROFILE))

            val importRequest = server.takeRequest()
            assertTrue(importRequest.path!!.contains("format=JSON"))
            assertTrue(importRequest.path!!.contains("mode=MATERIALIZE"))
            val reportRequest = server.takeRequest()
            assertEquals("/api/v1/imports/$IMPORT?limit=200&after=page-1", reportRequest.path)
            val create = server.takeRequest()
            assertEquals("stable-key", create.getHeader("Idempotency-Key"))
            val append = server.takeRequest()
            assertEquals("application/offset+octet-stream", append.getHeader("Content-Type"))
            assertEquals("0", append.getHeader("Upload-Offset"))
            server.takeRequest() // complete
            val recommendation = server.takeRequest()
            assertEquals("/api/v1/recommendations", recommendation.path)
            val logout = server.takeRequest()
            assertEquals("/api/v1/auth/logout", logout.path)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun separateApiAndStreamOriginsAreCheckedIndependently() = runBlocking {
        val api = MockWebServer()
        val stream = MockWebServer()
        api.enqueue(MockResponse().setBody("{\"status\":\"ready\"}"))
        stream.enqueue(MockResponse().setBody("{\"status\":\"live\"}"))
        api.start()
        stream.start()
        try {
            val repository = ServerFeatureRepository(
                api.url("/").toString(),
                stream.url("/").toString(),
                PROFILE,
                MutableStore("access".toByteArray()),
            )
            assertEquals(ServerHealth(apiReady = true, streamLive = true), repository.health())
            assertEquals("/health/ready", api.takeRequest().path)
            assertEquals("/health/live", stream.takeRequest().path)
        } finally {
            api.shutdown()
            stream.shutdown()
        }
    }

    private fun repository(server: MockWebServer, store: CredentialStore) = ServerFeatureRepository(
        server.url("/").toString(),
        server.url("/").toString(),
        PROFILE,
        store,
    )

    private fun entriesJson() = """{"items":[{"library_entry_id":"$ENTRY","user_track_ref_id":"$TRACK","source":"LOCAL","availability_status":"AVAILABLE","added_at":"2026-01-01T00:00:00+00:00","row_version":1}],"next_cursor":null}"""
    private fun playlistsJson() = """{"items":[{"playlist_id":"$PLAYLIST","name":"Saved","description":null,"updated_at":"2026-01-01T00:00:00+00:00","row_version":2}],"next_cursor":null}"""
    private fun historyJson() = """{"items":[{"listening_event_id":"$HISTORY","user_track_ref_id":"$TRACK","recording_id":"$RECORDING","started_at":"2026-01-01T00:00:00+00:00","played_ms":3,"event_origin":"ORGANIC"}],"next_cursor":null}"""
    private fun importReportJson() = """{"import_job_id":"$IMPORT","delivery_job_id":"$DELIVERY","state":"RUNNING","progress_current":1,"progress_total":2,"adapter_id":"json","adapter_version":"1","input_schema_version":"1","counts":{"PENDING":1},"entries":[{"source_row_key":"row-1","import_entry_id":"$IMPORT_ENTRY","status":"PENDING","resolver_state":null,"decision_id":null,"candidate_count":0,"unknown_field_count":0,"error_code":null}],"next_after":null}"""
    private fun recommendationJson() = """{"recommendation_request_id":"$REQUEST","surface":"RECOMMENDATIONS","context":"GENERAL","pipeline":{"key":"cpu-baseline","version":"1","manifest_sha256":"${"b".repeat(64)}"},"request_sha256":"${"c".repeat(64)}","input_snapshot_sha256":"${"d".repeat(64)}","replay":"served","shadow":false,"items":[{"recommendation_request_id":"$REQUEST","recording_id":"$RECORDING","source_rank":1,"score":0.5,"score_kind":"heuristic","reason_code":"RECENT","reason_codes":["RECENT"],"section":"FOR_YOU","contributions":[]}]}"""

    private class MutableStore(initial: ByteArray) : CredentialStore {
        private var value: ByteArray? = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId): ByteArray? = value?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { value?.fill(0); value = null }
        fun decoded() = SessionCredentialEnvelopeCodec.decode(requireNotNull(value).copyOf())
    }

    private companion object {
        val PROFILE = ServerProfileId("11111111-1111-4111-8111-111111111111")
        const val ENTRY = "22222222-2222-4222-8222-222222222222"
        const val TRACK = "33333333-3333-4333-8333-333333333333"
        const val PLAYLIST = "44444444-4444-4444-8444-444444444444"
        const val HISTORY = "55555555-5555-4555-8555-555555555555"
        const val RECORDING = "66666666-6666-4666-8666-666666666666"
        const val IMPORT = "77777777-7777-4777-8777-777777777777"
        const val DELIVERY = "88888888-8888-4888-8888-888888888888"
        const val IMPORT_ENTRY = "99999999-9999-4999-8999-999999999999"
        const val DECISION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        const val UPLOAD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        const val REQUEST = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    }
}
