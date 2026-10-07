package app.autplay.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import app.autplay.R
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.server.*
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.io.IOException
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Presentation proofs with injected private transport and worker boundaries; no server or acquisition runs. */
class InternetMetadataDiscoveryUiTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun pickerSelectsEveryKindWithoutStartingInternetWork() {
        val chosen = CopyOnWriteArrayList<LibrarySearchKind>()
        val kind = mutableStateOf(LibrarySearchKind.All)
        compose.setContent { AutPlayTheme { LibrarySearchKindPicker(kind.value) { chosen += it; kind.value = it } } }
        LibrarySearchKind.entries.forEach { option ->
            compose.onNodeWithTag("search-kind-picker").performClick()
            waitTag("search-kind-${option.wireValue}")
            compose.onNodeWithTag("search-kind-${option.wireValue}").performClick()
            compose.runOnIdle { assertEquals(option, kind.value) }
        }
        assertEquals(LibrarySearchKind.entries, chosen.toList())
        compose.onNodeWithTag("internet-music-results").assertDoesNotExist()
        compose.onNodeWithTag("internet-metadata-discovery").assertDoesNotExist()
    }

    @Test fun repeatedReleaseOccurrencesRemainSelectableAndExpiredReceiptRehydratesSameSelection() {
        val port = FakePort()
        val work = FakeWork()
        port.discovery = { _, kind, offset -> assertEquals(InternetMetadataDiscoveryKind.ALBUM, kind); page(listOf(release()), offset) }
        port.releaseTracks = { id, offset -> assertEquals(RELEASE, id); page(listOf(track(TRACK_ONE, 1), track(TRACK_TWO, 2)), offset) }
        port.hydrate = { request -> receipt(request, if (port.selections.size == 1) CONTEXT_ONE else CONTEXT_TWO) }
        port.source = { _, _, _ ->
            if (port.searches.size == 1) throw expired()
            InternetMusicSearch("source-search", listOf(InternetMusicCandidate("source-candidate", "Actual audio", "Performer", "YouTube", 180000)))
        }
        mount(port, work, LibrarySearchKind.Album)
        click("catalogue-browse-musicbrainz:release:$RELEASE")
        waitTag("catalogue-source-musicbrainz:release_track:$TRACK_TWO")
        compose.onNodeWithTag("catalogue-source-musicbrainz:release_track:$TRACK_ONE").assertExists()
        assertTrue(port.selections.isEmpty()); assertTrue(port.searches.isEmpty()); assertTrue(work.requests.isEmpty())
        click("catalogue-source-musicbrainz:release_track:$TRACK_TWO")
        waitTag("catalogue-context-rehydrate")
        assertEquals(1, port.selections.size); assertEquals(1, port.searches.size)
        click("catalogue-context-rehydrate")
        compose.waitUntil(5_000) { port.searches.size == 2 }
        compose.onNodeWithText("Actual audio").performScrollTo().assertIsDisplayed()
        assertEquals(2, port.selections.size)
        assertEquals(port.selections[0], port.selections[1])
        assertEquals(buildJsonObject { put("entity_type", "release_track"); put("entity_id", TRACK_TWO); put("release_id", RELEASE) }, port.selections[0])
        assertEquals(CONTEXT_ONE, port.searches[0].contextId); assertEquals(CONTEXT_TWO, port.searches[1].contextId)
        assertNotEquals(port.searches[0].operationId, port.searches[1].operationId)
        assertTrue(port.searches.all { it.query == "Performer Song (Live)" })
        assertTrue(work.requests.isEmpty())
        compose.onNodeWithText(context.getString(R.string.music_add_vault)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.music_add_download)).performScrollTo().performClick()
        assertEquals(listOf(WorkRequest(BINDING.serverProfileId.value, "source-search", "source-candidate", false),
            WorkRequest(BINDING.serverProfileId.value, "source-search", "source-candidate", true)), work.requests.toList())
    }

    @Test fun lostHttpResponseRetainsOperationAndFailedAcquisitionRetryCreatesFreshOperation() {
        val port = FakePort()
        val work = FakeWork()
        val terminalResponse = CompletableDeferred<InternetMusicSearch>()
        work.values["first-source"] = MutableStateFlow(listOf(WorkInfo.State.FAILED))
        port.source = { _, _, _ -> when (port.searches.size) {
            1 -> throw IOException("lost response")
            2 -> InternetMusicSearch("first-source", listOf(InternetMusicCandidate("candidate", "Failed source", "Performer", "YouTube", 180000)))
            else -> terminalResponse.await()
        } }
        compose.setContent { AutPlayTheme { Column(Modifier.verticalScroll(rememberScrollState())) {
            InternetMusicSearchSection("Performer Song", 1, LibrarySearchKind.Track, CONTEXT_ONE, InternetMusicUiSession(BINDING, port, work))
        } } }
        click("source-http-retry")
        waitTag("source-acquisition-retry")
        compose.onNodeWithText(context.getString(R.string.music_add_vault)).assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.music_add_download)).assertIsNotEnabled()
        assertEquals(port.searches[0], port.searches[1])
        click("source-acquisition-retry")
        compose.waitUntil(5_000) { port.searches.size == 3 }
        compose.onNodeWithText("Failed source").assertDoesNotExist()
        assertNotEquals(port.searches[1].operationId, port.searches[2].operationId)
        assertTrue(port.searches.all { it.contextId == CONTEXT_ONE && it.query == "Performer Song" })
        terminalResponse.complete(InternetMusicSearch("fresh-source", listOf(InternetMusicCandidate("fresh-candidate", "Fresh source", "Performer", "YouTube", 180000))))
        compose.waitUntil(5_000) { compose.onAllNodesWithText("Fresh source").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Failed source").assertDoesNotExist()
        assertTrue(work.requests.isEmpty())
    }

    @Test fun artistNavigationAdmitsRecordingWithoutInventingReleaseMembership() {
        val port = FakePort()
        val work = FakeWork()
        port.discovery = { _, kind, offset -> assertEquals(InternetMetadataDiscoveryKind.ARTIST, kind); page(listOf(artist("Performer")), offset) }
        port.artistTracks = { id, offset -> assertEquals(ARTIST, id); page(listOf(card("recording", RECORDING, "Song (Live)", null, RECORDING, null)), offset) }
        port.hydrate = { receipt(it, CONTEXT_ONE) }
        port.source = { _, _, _ -> InternetMusicSearch("artist-source", listOf(InternetMusicCandidate("actual-candidate", "Actual recording audio", "Performer", "YouTube", 180000))) }
        mount(port, work, LibrarySearchKind.Artist)
        click("catalogue-browse-musicbrainz:artist:$ARTIST")
        waitTag("catalogue-source-musicbrainz:recording:$RECORDING")
        assertTrue(port.selections.isEmpty()); assertTrue(port.searches.isEmpty())
        click("catalogue-source-musicbrainz:recording:$RECORDING")
        compose.waitUntil(5_000) { port.searches.size == 1 }
        compose.onNodeWithText("Actual recording audio").performScrollTo().assertIsDisplayed()
        assertEquals(buildJsonObject { put("entity_type", "recording"); put("entity_id", RECORDING); put("release_id", JsonNull) }, port.selections.single())
        assertEquals(CONTEXT_ONE, port.searches.single().contextId)
        assertEquals("Performer Song (Live)", port.searches.single().query)
        compose.onNodeWithText(context.getString(R.string.music_add_download)).performScrollTo().performClick()
        assertEquals(listOf(WorkRequest(BINDING.serverProfileId.value, "artist-source", "actual-candidate", true)), work.requests.toList())
    }

    @Test fun queryKindRequestAndSameProfileOwnerChangesFenceLatePrivatePages() {
        val port = FakePort()
        val stale = CompletableDeferred<InternetMetadataDiscoveryPage>()
        port.discovery = { query, kind, offset ->
            if (query == "old") withContext(NonCancellable) { withTimeout(10_000) { stale.await() } }
            else { assertEquals(InternetMetadataDiscoveryKind.ARTIST, kind); page(listOf(artist("Current artist")), offset) }
        }
        val query = mutableStateOf("old")
        val kind = mutableStateOf(LibrarySearchKind.Album)
        val request = mutableStateOf(1)
        val owner = mutableStateOf(BINDING)
        compose.setContent { AutPlayTheme { Column(Modifier.verticalScroll(rememberScrollState())) {
            InternetMetadataDiscoverySection(query.value, kind.value, request.value, InternetMusicUiSession(owner.value, port, FakeWork()))
        } } }
        compose.waitUntil(5_000) { port.pages.size == 1 }
        compose.runOnIdle {
            query.value = "new"; kind.value = LibrarySearchKind.Artist; request.value = 2
            owner.value = BINDING.copy(userId = UserId(OTHER_USER), deviceId = DeviceId(OTHER_DEVICE))
        }
        waitTag("catalogue-browse-musicbrainz:artist:$ARTIST")
        stale.complete(page(listOf(release("Late private edition"))))
        compose.waitForIdle()
        compose.onNodeWithText("Late private edition").assertDoesNotExist()
        compose.onNodeWithText("Current artist").assertExists()
        assertTrue(port.selections.isEmpty()); assertTrue(port.searches.isEmpty())
    }

    @Test fun ownerSwitchWhileContextHydratesCannotStartSourceFromOldSelection() {
        val port = FakePort()
        val stale = CompletableDeferred<MusicCatalogueContext>()
        port.discovery = { _, _, offset -> page(listOf(release()), offset) }
        port.releaseTracks = { _, offset -> page(listOf(track(TRACK_ONE, 1)), offset) }
        port.hydrate = { withContext(NonCancellable) { withTimeout(10_000) { stale.await() } } }
        val owner = mutableStateOf(BINDING)
        compose.setContent { AutPlayTheme { Column(Modifier.verticalScroll(rememberScrollState())) {
            InternetMetadataDiscoverySection("edition", LibrarySearchKind.Album, 1, InternetMusicUiSession(owner.value, port, FakeWork()))
        } } }
        click("catalogue-browse-musicbrainz:release:$RELEASE")
        click("catalogue-source-musicbrainz:release_track:$TRACK_ONE")
        compose.waitUntil(5_000) { port.selections.size == 1 }
        compose.runOnIdle { owner.value = BINDING.copy(deviceId = DeviceId(OTHER_DEVICE)) }
        waitTag("catalogue-browse-musicbrainz:release:$RELEASE")
        stale.complete(receipt(MusicCatalogueContextRequest.releaseTrack(TRACK_ONE, RELEASE), CONTEXT_ONE))
        compose.waitForIdle()
        assertTrue(port.searches.isEmpty())
        compose.onNodeWithTag("internet-music-results").assertDoesNotExist()
    }

    @Test fun mismatchedReceiptRequiresSameSelectionRetryBeforeAnySourceSearch() {
        val port = FakePort()
        port.discovery = { _, _, offset -> page(listOf(release()), offset) }
        port.releaseTracks = { _, offset -> page(listOf(track(TRACK_ONE, 1)), offset) }
        port.hydrate = { request -> if (port.selections.size == 1)
            receipt(MusicCatalogueContextRequest.releaseTrack(TRACK_TWO, RELEASE), CONTEXT_ONE) else receipt(request, CONTEXT_TWO) }
        port.source = { _, _, _ -> InternetMusicSearch("source-search", emptyList()) }
        mount(port, FakeWork(), LibrarySearchKind.Album)
        click("catalogue-browse-musicbrainz:release:$RELEASE")
        click("catalogue-source-musicbrainz:release_track:$TRACK_ONE")
        waitTag("catalogue-context-retry")
        assertTrue(port.searches.isEmpty())
        click("catalogue-context-retry")
        compose.waitUntil(5_000) { port.searches.size == 1 }
        assertEquals(port.selections[0], port.selections[1])
        assertEquals(CONTEXT_TWO, port.searches.single().contextId)
    }

    @Test fun rawPaginationOffsetAndOccurrenceIdsSurviveDuplicateCardsAndTruncation() {
        val port = FakePort()
        port.discovery = { _, _, offset -> page(listOf(release()), offset) }
        port.releaseTracks = { _, offset -> if (offset == 0)
            page(listOf(track(TRACK_ONE, 1)), 0, total = 26, next = 25)
            else page(listOf(track(TRACK_ONE, 1), track(TRACK_TWO, 2)), 25, total = 27, truncated = true) }
        mount(port, FakeWork(), LibrarySearchKind.Album)
        click("catalogue-browse-musicbrainz:release:$RELEASE")
        click("catalogue-more")
        waitTag("catalogue-source-musicbrainz:release_track:$TRACK_TWO")
        assertEquals(listOf(0, 25), port.releaseOffsets.toList())
        assertEquals(1, compose.onAllNodesWithTag("catalogue-card-musicbrainz:release_track:$TRACK_ONE").fetchSemanticsNodes().size)
        compose.onNodeWithText(context.getString(R.string.music_catalogue_truncated)).performScrollTo().assertIsDisplayed()
        assertTrue(port.selections.isEmpty()); assertTrue(port.searches.isEmpty())
    }

    private fun mount(port: FakePort, work: FakeWork, kind: LibrarySearchKind) {
        compose.setContent { AutPlayTheme { Column(Modifier.verticalScroll(rememberScrollState())) {
            InternetMetadataDiscoverySection("edition", kind, 1, InternetMusicUiSession(BINDING, port, work))
        } } }
    }
    private fun waitTag(tag: String) { compose.waitUntil(5_000) { compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty() } }
    private fun click(tag: String) { waitTag(tag); compose.onNodeWithTag(tag).performScrollTo().performClick() }

    private data class SourceRequest(val query: String, val operationId: String, val contextId: String?)
    private data class WorkRequest(val profile: String, val search: String, val candidate: String, val download: Boolean)
    private class FakeWork : InternetMusicUiWork {
        val requests = CopyOnWriteArrayList<WorkRequest>()
        val values = mutableMapOf<String, MutableStateFlow<List<WorkInfo.State>>>()
        override fun states(searchId: String, candidateId: String): Flow<List<WorkInfo.State>> = values.getOrPut(searchId) { MutableStateFlow(emptyList()) }
        override fun enqueue(profileId: String, searchId: String, candidateId: String, download: Boolean) { requests += WorkRequest(profileId, searchId, candidateId, download) }
    }
    private class FakePort : InternetMetadataDiscoveryPort {
        val selections = CopyOnWriteArrayList<JsonObject>()
        val searches = CopyOnWriteArrayList<SourceRequest>()
        val pages = CopyOnWriteArrayList<String>()
        val releaseOffsets = CopyOnWriteArrayList<Int>()
        var discovery: suspend (String, InternetMetadataDiscoveryKind, Int) -> InternetMetadataDiscoveryPage = { _, _, _ -> error("Unexpected discovery") }
        var releaseTracks: suspend (String, Int) -> InternetMetadataDiscoveryPage = { _, _ -> error("Unexpected release navigation") }
        var artistTracks: suspend (String, Int) -> InternetMetadataDiscoveryPage = { _, _ -> error("Unexpected artist navigation") }
        var hydrate: suspend (MusicCatalogueContextRequest) -> MusicCatalogueContext = { error("Unexpected context admission") }
        var source: suspend (String, String, String?) -> InternetMusicSearch = { _, _, _ -> error("Unexpected source search") }
        override suspend fun discoverMusic(query: String, kind: InternetMetadataDiscoveryKind, limit: Int, offset: Int): InternetMetadataDiscoveryPage {
            assertEquals(25, limit); pages += query; return discovery(query, kind, offset)
        }
        override suspend fun discoveryArtistTracks(id: String, limit: Int, offset: Int): InternetMetadataDiscoveryPage {
            assertEquals(25, limit); return artistTracks(id, offset)
        }
        override suspend fun discoveryReleaseTracks(id: String, limit: Int, offset: Int): InternetMetadataDiscoveryPage {
            assertEquals(25, limit); releaseOffsets += offset; return releaseTracks(id, offset)
        }
        override suspend fun createMusicCatalogueContext(selection: MusicCatalogueContextRequest): MusicCatalogueContext {
            selections += selection.document(); return hydrate(selection)
        }
        override suspend fun searchInternetMusic(query: String, operationId: String, catalogueContextId: String?): InternetMusicSearch {
            searches += SourceRequest(query, operationId, catalogueContextId); return source(query, operationId, catalogueContextId)
        }
    }

    private fun release(title: String = "Selected edition"): JsonObject = card("release", RELEASE, title, RELEASE, null, null)
    private fun artist(title: String): JsonObject = card("artist", ARTIST, title, null, null, null)
    private fun track(id: String, position: Int): JsonObject = card("release_track", id, "Song", RELEASE, RECORDING, position)
    private fun card(entity: String, id: String, title: String, releaseId: String?, recordingId: String?, position: Int?): JsonObject = buildJsonObject {
        put("id", "musicbrainz:$entity:$id"); put("entity_type", entity); put("entity_id", id); put("title", title)
        put("artist", if (entity == "artist") JsonNull else JsonPrimitive("Performer"))
        put("release_id", releaseId?.let(::JsonPrimitive) ?: JsonNull); put("recording_id", recordingId?.let(::JsonPrimitive) ?: JsonNull)
        put("release_date", if (entity == "artist") JsonNull else JsonPrimitive("2001")); put("country", JsonNull); put("disambiguation", JsonNull)
        put("duration_ms", JsonNull); put("disc_number", position?.let { JsonPrimitive(1) } ?: JsonNull)
        put("track_number", position?.let(::JsonPrimitive) ?: JsonNull)
        put("recording_title", if (entity == "release_track") JsonPrimitive("Song (Live)") else JsonNull)
        put("source", "MusicBrainz"); put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
        put("can_browse_tracks", entity in setOf("release", "artist"))
        put("download_search_query", if (entity in setOf("release_track", "recording")) JsonPrimitive("Performer Song (Live)") else JsonNull)
    }
    private fun page(cards: List<JsonObject>, offset: Int = 0, total: Int = cards.size, next: Int? = null,
        truncated: Boolean = false): InternetMetadataDiscoveryPage = InternetMetadataDiscoveryCodec.decode(buildJsonObject {
        put("contract_version", "music-discovery-v1"); put("source", "MusicBrainz"); put("source_scope", "INTERNET")
        put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
        put("capabilities", buildJsonObject { put("direct_acquisition", false); put("track_source_search", true) })
        put("items", JsonArray(cards)); put("limit", 25); put("offset", offset); put("total_count", total)
        put("next_offset", next?.let(::JsonPrimitive) ?: JsonNull); put("truncated", truncated)
    })
    private fun receipt(request: MusicCatalogueContextRequest, id: String): MusicCatalogueContext = MusicCatalogueContextCodec.decode(buildJsonObject {
        put("contract_version", "music-catalogue-context-v1"); put("catalogue_context_id", id)
        put("expires_at", Instant.now().plusSeconds(3600).toString()); put("source", "MusicBrainz")
        put("availability", "METADATA_ONLY"); put("acquisition_allowed", false)
        put("lookup_metadata", buildJsonObject {
            put("schema_version", 1); put("entity_type", request.entity.wireValue); put("entity_id", request.entityId)
            put("recording_mbid", RECORDING); put("release_mbid", request.releaseId?.let(::JsonPrimitive) ?: JsonNull)
            put("title", "Song (Live)"); put("artist", "Performer")
            put("album", if (request.releaseId == null) JsonNull else JsonPrimitive("Selected edition"))
            put("release_date", if (request.releaseId == null) JsonNull else JsonPrimitive("2001"))
            put("duration_ms", JsonNull); put("disc_number", if (request.releaseId == null) JsonNull else JsonPrimitive(1))
            put("track_number", if (request.releaseId == null) JsonNull else JsonPrimitive(if (request.entityId == TRACK_TWO) 2 else 1))
            put("recording_title", "Song (Live)"); put("disambiguation", JsonNull)
        })
    })
    private fun expired(): ServerFeatureHttpException = ServerFeatureHttpException.from(409,
        """{"error":{"code":"music_catalogue_context_expired","retryable":false}}""")
    private companion object {
        const val ARTIST = "00000000-0000-0000-0000-000000000001"
        const val RELEASE = "00000000-0000-0000-0000-000000000002"
        const val RECORDING = "00000000-0000-0000-0000-000000000003"
        const val TRACK_ONE = "00000000-0000-0000-0000-000000000004"
        const val TRACK_TWO = "00000000-0000-0000-0000-000000000005"
        const val CONTEXT_ONE = "00000000-0000-0000-0000-000000000011"
        const val CONTEXT_TWO = "00000000-0000-0000-0000-000000000012"
        const val OTHER_USER = "00000000-0000-0000-0000-000000000024"
        const val OTHER_DEVICE = "00000000-0000-0000-0000-000000000025"
        val BINDING = ClientEventBinding(UserId("00000000-0000-0000-0000-000000000021"),
            DeviceId("00000000-0000-0000-0000-000000000022"), ServerProfileId("00000000-0000-0000-0000-000000000023"))
    }
}
