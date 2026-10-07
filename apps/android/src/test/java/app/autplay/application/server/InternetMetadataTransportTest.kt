package app.autplay.application.server

import app.autplay.application.search.LibrarySearchKind
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.SessionRequiredException
import app.autplay.domain.ServerProfileId
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Loopback-only HTTP proofs. No provider, production server, Room or Android UI is used. */
class InternetMetadataTransportTest {
    @Test
    fun discoverySearchUsesExplicitKindsPagingAndPrivateHeaders() = runBlocking {
        withRepository { server, repository ->
            server.enqueue(success(page(ARTIST_PAGE, limit = 10, offset = 7)))
            server.enqueue(success(page(ALBUM_PAGE, limit = 15, offset = 12)))
            val query = "Massive Attack / Live (2024)"
            val artists = repository.discoverMusic("  $query  ", InternetMetadataDiscoveryKind.ARTIST, 10, 7)
            val artistRequest = take(server)
            assertGet(artistRequest, "/api/v1/music/discovery/search")
            assertQuery(artistRequest, mapOf("q" to query, "kind" to "artist", "limit" to "10", "offset" to "7"))
            assertEquals(7, artists.offset)
            assertEquals(10, artists.limit)
            assertEquals(ARTIST, artists.items.single().browseTarget?.entityId)

            val albums = repository.discoverMusic(query, InternetMetadataDiscoveryKind.ALBUM, 15, 12)
            val albumRequest = take(server)
            assertGet(albumRequest, "/api/v1/music/discovery/search")
            assertQuery(albumRequest, mapOf("q" to query, "kind" to "album", "limit" to "15", "offset" to "12"))
            assertEquals(RELEASE, albums.items.single().browseTarget?.entityId)
            assertFalse(albums.directAcquisitionAllowed)
            assertEquals(2, server.requestCount)
        }
    }

    @Test
    fun trackNavigationUsesExactIdentitiesAndKeepsRepeatedRecordingPositions() = runBlocking {
        withRepository { server, repository ->
            server.enqueue(success(page(RECORDINGS_PAGE, limit = 9, offset = 4)))
            server.enqueue(success(page(RELEASE_TRACKS_PAGE, limit = 8, offset = 6)))
            val artistTracks = repository.discoveryArtistTracks(ARTIST, 9, 4)
            val artistRequest = take(server)
            assertGet(artistRequest, "/api/v1/music/discovery/artists/$ARTIST/tracks")
            assertQuery(artistRequest, mapOf("limit" to "9", "offset" to "4"))
            assertEquals(RECORDING, artistTracks.items.single().trackLookup?.entityId)

            val releaseTracks = repository.discoveryReleaseTracks(RELEASE, 8, 6)
            val releaseRequest = take(server)
            assertGet(releaseRequest, "/api/v1/music/discovery/releases/$RELEASE/tracks")
            assertQuery(releaseRequest, mapOf("limit" to "8", "offset" to "6"))
            assertEquals(listOf(1, 2), releaseTracks.items.map { it.trackNumber })
            assertEquals(listOf(RELEASE_TRACK, OTHER_TRACK), releaseTracks.items.map { it.entityId })
            assertEquals(setOf(RECORDING), releaseTracks.items.map { it.recordingId }.toSet())
            assertTrue(releaseTracks.items.all { it.trackLookup?.searchQuery == "Example Artist Song (Live)" })
            assertTrue(releaseTracks.items.all { !it.directAcquisitionAllowed })
        }
    }

    @Test
    fun librarySearchSendsEveryTypedKindAndDefaultAllWhileReturningTrackRefs() = runBlocking {
        withRepository { server, repository ->
            val kinds = listOf(LibrarySearchKind.All, LibrarySearchKind.Track, LibrarySearchKind.Artist, LibrarySearchKind.Album)
            for (kind in kinds) {
                server.enqueue(success(LIBRARY_PAGE))
                val rows = repository.searchLibrary("  Artist & Album  ", limit = 17, kind = kind)
                val request = take(server)
                assertGet(request, "/api/v1/library/search")
                assertQuery(request, mapOf("q" to "Artist & Album", "kind" to kind.wireValue, "limit" to "17"))
                assertEquals(TRACK_REF, rows.single().userTrackRefId)
                assertEquals("VAULT", rows.single().source)
            }
            server.enqueue(success(LIBRARY_PAGE))
            repository.searchLibrary("Default", 11)
            val legacy = take(server)
            assertGet(legacy, "/api/v1/library/search")
            assertQuery(legacy, mapOf("q" to "Default", "kind" to "all", "limit" to "11"))
            assertEquals(5, server.requestCount)
        }
    }

    @Test
    fun releaseTrackContextPostsOnlySelectedIdentitiesAndMatchesHydratedReceipt() = runBlocking {
        withRepository { server, repository ->
            val response = contextResponse()
            server.enqueue(success(response.toString()))
            val selection = MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)
            val receipt = repository.createMusicCatalogueContext(selection)
            val request = take(server)
            assertPost(request, "/api/v1/music/discovery/contexts")
            assertEquals(buildJsonObject {
                put("entity_type", "release_track"); put("entity_id", RELEASE_TRACK); put("release_id", RELEASE)
            }, body(request))
            assertEquals(setOf("entity_type", "entity_id", "release_id"), selection.document().keys)
            assertEquals(CONTEXT, receipt.catalogueContextId)
            assertEquals(response, receipt.rawPayload)
            assertTrue(receipt.matches(selection))
            assertFalse(receipt.directAcquisitionAllowed)
            assertEquals(1, server.requestCount)
        }
    }

    @Test
    fun recordingContextCarriesNoImplicitAlbumOrReleaseAssociation() = runBlocking {
        withRepository { server, repository ->
            server.enqueue(success(contextResponse(recording = true).toString()))
            val selection = MusicCatalogueContextRequest.recording(RECORDING)
            val receipt = repository.createMusicCatalogueContext(selection)
            val request = take(server)
            assertPost(request, "/api/v1/music/discovery/contexts")
            assertEquals(buildJsonObject {
                put("entity_type", "recording"); put("entity_id", RECORDING); put("release_id", JsonNull)
            }, body(request))
            assertTrue(receipt.matches(selection))
            assertNull(receipt.lookupMetadata.releaseMbid)
            assertNull(receipt.lookupMetadata.album)
        }
    }

    @Test
    fun mismatchedHydratedReceiptIsRejectedWithoutStartingSourceSearch() = runBlocking {
        withRepository { server, repository ->
            val wrongRelease = contextResponse().withLookup(mapOf("release_mbid" to JsonPrimitive(OTHER_RELEASE)))
            val wrongTrack = contextResponse().withLookup(mapOf("entity_id" to JsonPrimitive(OTHER_TRACK)))
            for (response in listOf(wrongRelease, wrongTrack)) {
                server.enqueue(success(response.toString()))
                val failure = runCatching {
                    repository.createMusicCatalogueContext(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE))
                }.exceptionOrNull()
                assertTrue(failure is IllegalStateException)
                assertEquals("SERVER_RESPONSE_INVALID", failure?.message)
                assertPost(take(server), "/api/v1/music/discovery/contexts")
            }
            assertEquals(2, server.requestCount)
        }
    }

    @Test
    fun selectedContextsBindSeparateExplicitSourceOperationsAndLegacySearchOmitsContext() = runBlocking {
        withRepository { server, repository ->
            server.enqueue(success(contextResponse().toString()))
            server.enqueue(success(internetSearchResponse(SEARCH)))
            server.enqueue(success(contextResponse(contextId = OTHER_CONTEXT).toString()))
            server.enqueue(success(internetSearchResponse(OTHER_SEARCH)))
            server.enqueue(success(internetSearchResponse(LEGACY_SEARCH, candidate = true)))
            val selection = MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)
            val first = repository.createMusicCatalogueContext(selection)
            assertPost(take(server), "/api/v1/music/discovery/contexts")
            val firstResult = repository.searchInternetMusic(LOOKUP_QUERY, OPERATION, first.catalogueContextId)
            val firstRequest = take(server)
            assertPost(firstRequest, "/api/v1/music/internet/search")
            assertEquals(buildJsonObject {
                put("query", LOOKUP_QUERY); put("operation_id", OPERATION); put("catalogue_context_id", CONTEXT)
            }, body(firstRequest))
            assertTrue(firstResult.candidates.isEmpty())

            val second = repository.createMusicCatalogueContext(selection)
            assertPost(take(server), "/api/v1/music/discovery/contexts")
            val secondResult = repository.searchInternetMusic(LOOKUP_QUERY, OTHER_OPERATION, second.catalogueContextId)
            val secondRequest = take(server)
            assertPost(secondRequest, "/api/v1/music/internet/search")
            assertEquals(buildJsonObject {
                put("query", LOOKUP_QUERY); put("operation_id", OTHER_OPERATION); put("catalogue_context_id", OTHER_CONTEXT)
            }, body(secondRequest))
            assertNotEquals(first.catalogueContextId, second.catalogueContextId)
            assertEquals(CONTEXT, first.catalogueContextId)
            assertNotEquals(firstResult.id, secondResult.id)
            assertNotEquals(bodyOperation(firstRequest), bodyOperation(secondRequest))

            val legacyResult = repository.searchInternetMusic(LOOKUP_QUERY, LEGACY_OPERATION)
            val legacyRequest = take(server)
            assertPost(legacyRequest, "/api/v1/music/internet/search")
            assertEquals(buildJsonObject { put("query", LOOKUP_QUERY); put("operation_id", LEGACY_OPERATION) }, body(legacyRequest))
            assertEquals("abcdefghijk", legacyResult.candidates.single().id)
            assertEquals("YouTube", legacyResult.candidates.single().provider)
            assertEquals(5, server.requestCount)
        }
    }

    @Test
    fun expiredDisabledBusyMissingAndFutureErrorsKeepLegacyStatusAndPublicCode() = runBlocking {
        withRepository { server, repository ->
            val cases = listOf(
                HttpFailure(409, "music_catalogue_context_expired", false) {
                    it.searchInternetMusic(LOOKUP_QUERY, OPERATION, CONTEXT)
                },
                HttpFailure(503, "music_discovery_disabled", null) {
                    it.discoverMusic("Artist", InternetMetadataDiscoveryKind.ARTIST)
                },
                HttpFailure(503, "music_catalogue_busy", true) {
                    it.createMusicCatalogueContext(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE))
                },
                HttpFailure(404, "music_catalogue_context_not_found", false) {
                    it.searchInternetMusic(LOOKUP_QUERY, OTHER_OPERATION, OTHER_CONTEXT)
                },
                HttpFailure(404, "music_catalogue_entity_not_found", false) {
                    it.createMusicCatalogueContext(MusicCatalogueContextRequest.recording(RECORDING))
                },
                HttpFailure(503, "future_catalogue_failure", null) {
                    it.discoveryReleaseTracks(RELEASE)
                },
            )
            for (case in cases) {
                server.enqueue(errorResponse(case.status, case.code, case.retryable))
                val failure = runCatching { case.invoke(repository) }.exceptionOrNull()
                assertTrue(failure is ServerFeatureHttpException)
                val error = failure as ServerFeatureHttpException
                assertEquals(case.status, error.status)
                assertEquals("SERVER_HTTP_${case.status}", error.message)
                assertEquals(case.code, error.errorCode)
                assertEquals(case.retryable, error.retryable)
                assertFalse(error.toString().contains("private-server-detail"))
                assertPrivate(take(server))
            }
            assertEquals(cases.size, server.requestCount)
        }
    }

    @Test
    fun malformedPublicErrorPayloadFallsBackToStatusWithoutLeakingServerDescription() = runBlocking {
        withRepository { server, repository ->
            val bodies = listOf("{malformed-private-server-detail", """{"error":{"code":"https://private-server-detail.invalid","retryable":"true"}}""")
            for (response in bodies) {
                server.enqueue(success(response).setResponseCode(503))
                val failure = runCatching { repository.discoveryArtistTracks(ARTIST) }.exceptionOrNull()
                assertTrue(failure is ServerFeatureHttpException)
                val error = failure as ServerFeatureHttpException
                assertEquals("SERVER_HTTP_503", error.message)
                assertNull(error.errorCode)
                assertNull(error.retryable)
                assertFalse(error.toString().contains("private-server-detail"))
                assertGet(take(server), "/api/v1/music/discovery/artists/$ARTIST/tracks")
            }
        }
    }

    @Test
    fun unauthorizedCatalogueRefreshesOnceThenReplaysPrivateRequest() = runBlocking {
        val store = Store(SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("stale-access", "refresh", 0)))
        withRepository(store = store) { server, repository ->
            server.enqueue(success("{}").setResponseCode(401))
            server.enqueue(success("""{"access_token":"fresh-access","refresh_token":"fresh-refresh"}"""))
            server.enqueue(success(ARTIST_PAGE))
            val page = repository.discoverMusic("Artist", InternetMetadataDiscoveryKind.ARTIST)
            assertEquals(ARTIST, page.items.single().entityId)
            val rejected = take(server)
            assertGet(rejected, "/api/v1/music/discovery/search", token = "stale-access")
            val refresh = take(server)
            assertEquals("/api/v1/auth/refresh", refresh.requestUrl?.encodedPath)
            val replay = take(server)
            assertGet(replay, "/api/v1/music/discovery/search", token = "fresh-access")
            assertEquals(rejected.path, replay.path)
            assertEquals(3, server.requestCount)
        }
    }

    @Test
    fun forbiddenCatalogueRetainsSessionRequiredAndNeverReturnsAnEmptySuccess() = runBlocking {
        withRepository { server, repository ->
            server.enqueue(errorResponse(403, "permission_denied", false))
            val failure = runCatching { repository.discoverMusic("Artist", InternetMetadataDiscoveryKind.ARTIST) }.exceptionOrNull()
            assertTrue(failure is SessionRequiredException)
            assertEquals("SESSION_REQUIRED", failure?.message)
            assertGet(take(server), "/api/v1/music/discovery/search")
            assertEquals(1, server.requestCount)
        }
    }

    @Test
    fun invalidBoundedQueriesPagingAndContextIdsAreRejectedBeforeNetwork() = runBlocking {
        withRepository { server, repository ->
            val invalid: List<suspend () -> Unit> = listOf(
                { repository.discoverMusic(" ", InternetMetadataDiscoveryKind.ARTIST) },
                { repository.discoverMusic("a".repeat(201), InternetMetadataDiscoveryKind.ALBUM) },
                { repository.discoverMusic("Artist", InternetMetadataDiscoveryKind.ARTIST, limit = 51) },
                { repository.discoverMusic("Album", InternetMetadataDiscoveryKind.ALBUM, offset = 1001) },
                { repository.discoveryArtistTracks("not-a-uuid") },
                { repository.discoveryReleaseTracks(RELEASE, limit = 0) },
                { repository.discoveryReleaseTracks(RELEASE, offset = -1) },
                { repository.searchLibrary(" ", kind = LibrarySearchKind.Album) },
                { repository.searchLibrary("Artist", limit = 101, kind = LibrarySearchKind.Artist) },
                { repository.searchInternetMusic(LOOKUP_QUERY, OPERATION, "not-a-context-uuid") },
            )
            for (call in invalid) {
                assertTrue(runCatching { call() }.exceptionOrNull() is IllegalArgumentException)
            }
            assertEquals(0, server.requestCount)
        }
    }

    @Test
    fun cancellationAbortsCatalogueCallWaitingForHeaders() = runBlocking {
        val activeCall = AtomicReference<Call?>()
        val client = OkHttpClient.Builder().eventListener(object : EventListener() {
            override fun callStart(call: Call) { activeCall.set(call) }
        }).build()
        withRepository(client = client) { server, repository ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val request = async(Dispatchers.IO) { repository.discoverMusic("Artist", InternetMetadataDiscoveryKind.ARTIST) }
            try {
                assertGet(take(server), "/api/v1/music/discovery/search")
                withTimeout(2_000) { request.cancelAndJoin() }
                assertNotNull(activeCall.get())
                assertTrue(activeCall.get()!!.isCanceled())
                assertTrue(runCatching { request.await() }.exceptionOrNull() is CancellationException)
            } finally {
                request.cancelAndJoin()
            }
        }
    }

    private suspend fun withRepository(
        client: OkHttpClient = OkHttpClient(),
        store: CredentialStore = Store(ACCESS.toByteArray(Charsets.UTF_8)),
        block: suspend (MockWebServer, ServerFeatureRepository) -> Unit,
    ) {
        val server = MockWebServer()
        server.start()
        try {
            val repository = ServerFeatureRepository(server.url("/").toString(), server.url("/").toString(), PROFILE, store, client)
            block(server, repository)
        } finally {
            server.shutdown()
        }
    }

    private fun take(server: MockWebServer): RecordedRequest = checkNotNull(server.takeRequest(5, TimeUnit.SECONDS))

    private fun assertPrivate(request: RecordedRequest, token: String = ACCESS) {
        assertEquals("Bearer $token", request.getHeader("Authorization"))
        assertEquals("no-store", request.getHeader("Cache-Control"))
    }

    private fun assertGet(request: RecordedRequest, path: String, token: String = ACCESS) {
        assertPrivate(request, token)
        assertEquals("GET", request.method)
        assertEquals(path, request.requestUrl?.encodedPath)
        assertEquals(0L, request.bodySize)
    }

    private fun assertPost(request: RecordedRequest, path: String) {
        assertPrivate(request)
        assertEquals("POST", request.method)
        assertEquals(path, request.requestUrl?.encodedPath)
        assertTrue(request.getHeader("Content-Type").orEmpty().startsWith("application/json"))
    }

    private fun assertQuery(request: RecordedRequest, expected: Map<String, String>) {
        val url = checkNotNull(request.requestUrl)
        assertEquals(expected.keys, url.queryParameterNames)
        expected.forEach { (name, value) -> assertEquals(listOf(value), url.queryParameterValues(name)) }
    }

    private fun body(request: RecordedRequest): JsonObject = Json.parseToJsonElement(request.body.clone().readUtf8()).jsonObject

    private fun bodyOperation(request: RecordedRequest): JsonElement? = body(request)["operation_id"]

    private fun success(body: String): MockResponse = MockResponse().setResponseCode(200)
        .setHeader("Content-Type", "application/json").setHeader("Cache-Control", "private, no-store")
        .setHeader("Pragma", "no-cache").setBody(body)

    private fun errorResponse(status: Int, code: String, retryable: Boolean?): MockResponse = success(buildJsonObject {
        put("error", buildJsonObject {
            put("code", code); put("message", "private-server-detail")
            retryable?.let { put("retryable", it) }
        })
    }.toString()).setResponseCode(status)

    private fun page(fixture: String, limit: Int, offset: Int): String {
        val raw = Json.parseToJsonElement(fixture).jsonObject
        return JsonObject(raw + mapOf("limit" to JsonPrimitive(limit), "offset" to JsonPrimitive(offset),
            "total_count" to JsonPrimitive(offset + raw.getValue("items").jsonArray.size))).toString()
    }

    private fun contextResponse(contextId: String = CONTEXT, recording: Boolean = false): JsonObject = buildJsonObject {
        put("contract_version", "music-catalogue-context-v1"); put("catalogue_context_id", contextId)
        put("expires_at", "2026-10-07T12:00:00Z"); put("source", "MusicBrainz")
        put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
        put("lookup_metadata", buildJsonObject {
            put("schema_version", 1); put("entity_type", if (recording) "recording" else "release_track")
            put("entity_id", if (recording) RECORDING else RELEASE_TRACK); put("recording_mbid", RECORDING)
            put("release_mbid", if (recording) JsonNull else JsonPrimitive(RELEASE))
            put("title", if (recording) "Song (Live)" else "Song"); put("artist", "Example Artist")
            put("album", if (recording) JsonNull else JsonPrimitive("Example Album"))
            put("release_date", "2001"); put("duration_ms", 180000)
            put("disc_number", if (recording) JsonNull else JsonPrimitive(1))
            put("track_number", if (recording) JsonNull else JsonPrimitive(1))
            put("recording_title", "Song (Live)"); put("disambiguation", JsonNull)
        })
    }

    private fun JsonObject.withLookup(changes: Map<String, JsonElement>): JsonObject =
        JsonObject(this + ("lookup_metadata" to JsonObject(getValue("lookup_metadata").jsonObject + changes)))

    private fun internetSearchResponse(id: String, candidate: Boolean = false): String = buildJsonObject {
        put("contract_version", "internet-music-v1"); put("search_id", id)
        put("candidates", if (candidate) JsonArray(listOf(buildJsonObject {
            put("candidate_id", "abcdefghijk"); put("title", "Song (Live)"); put("artist", "Example Artist")
            put("provider", "YouTube"); put("duration_ms", 180000)
        })) else JsonArray(emptyList()))
    }.toString()

    private class Store(initial: ByteArray) : CredentialStore {
        private var value: ByteArray? = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId): ByteArray? = if (profileId == PROFILE) value?.copyOf() else null
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { require(profileId == PROFILE); value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { if (profileId == PROFILE) { value?.fill(0); value = null } }
    }

    private data class HttpFailure(
        val status: Int,
        val code: String,
        val retryable: Boolean?,
        val invoke: suspend (ServerFeatureRepository) -> Unit,
    )

    private companion object {
        val PROFILE = ServerProfileId("11111111-1111-4111-8111-111111111111")
        const val ACCESS = "private-test-access"
        const val ARTIST = "00000000-0000-0000-0000-000000000001"
        const val RELEASE = "00000000-0000-0000-0000-000000000002"
        const val RECORDING = "00000000-0000-0000-0000-000000000003"
        const val RELEASE_TRACK = "00000000-0000-0000-0000-000000000004"
        const val OTHER_TRACK = "00000000-0000-0000-0000-000000000005"
        const val OTHER_RELEASE = "00000000-0000-0000-0000-000000000006"
        const val CONTEXT = "00000000-0000-0000-0000-000000000011"
        const val OTHER_CONTEXT = "00000000-0000-0000-0000-000000000012"
        const val OPERATION = "00000000-0000-0000-0000-000000000021"
        const val OTHER_OPERATION = "00000000-0000-0000-0000-000000000022"
        const val LEGACY_OPERATION = "00000000-0000-0000-0000-000000000023"
        const val SEARCH = "00000000-0000-0000-0000-000000000031"
        const val OTHER_SEARCH = "00000000-0000-0000-0000-000000000032"
        const val LEGACY_SEARCH = "00000000-0000-0000-0000-000000000033"
        const val TRACK_REF = "00000000-0000-0000-0000-000000000041"
        const val LOOKUP_QUERY = "Example Artist Song (Live)"
        const val LIBRARY_PAGE = """{"items":[{"library_entry_id":"00000000-0000-0000-0000-000000000042","user_track_ref_id":"00000000-0000-0000-0000-000000000041","source":"VAULT","availability_status":"AVAILABLE","row_version":1}],"next_cursor":null}"""

        // Cards and envelope frozen from contracts/metadata-discovery/v1/page-examples.json.
        const val ARTIST_PAGE = """{"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[{"id":"musicbrainz:artist:00000000-0000-0000-0000-000000000001","entity_type":"artist","entity_id":"00000000-0000-0000-0000-000000000001","title":"Example Artist","artist":null,"release_id":null,"recording_id":null,"release_date":null,"country":"GB","disambiguation":null,"duration_ms":null,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":true,"download_search_query":null}],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false}"""
        const val ALBUM_PAGE = """{"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[{"id":"musicbrainz:release:00000000-0000-0000-0000-000000000002","entity_type":"release","entity_id":"00000000-0000-0000-0000-000000000002","title":"Example Album","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":null,"release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":true,"download_search_query":null}],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false}"""
        const val RECORDINGS_PAGE = """{"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[{"id":"musicbrainz:recording:00000000-0000-0000-0000-000000000003","entity_type":"recording","entity_id":"00000000-0000-0000-0000-000000000003","title":"Song (Live)","artist":"Example Artist","release_id":null,"recording_id":"00000000-0000-0000-0000-000000000003","release_date":null,"country":null,"disambiguation":null,"duration_ms":180000,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"}],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false}"""
        const val RELEASE_TRACKS_PAGE = """{"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[{"id":"musicbrainz:release_track:00000000-0000-0000-0000-000000000004","entity_type":"release_track","entity_id":"00000000-0000-0000-0000-000000000004","title":"Song","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":"00000000-0000-0000-0000-000000000003","release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":1,"track_number":1,"recording_title":"Song (Live)","source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"},{"id":"musicbrainz:release_track:00000000-0000-0000-0000-000000000005","entity_type":"release_track","entity_id":"00000000-0000-0000-0000-000000000005","title":"Song","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":"00000000-0000-0000-0000-000000000003","release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":1,"track_number":2,"recording_title":"Song (Live)","source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"}],"limit":25,"offset":0,"total_count":2,"next_offset":null,"truncated":false}"""
    }
}
