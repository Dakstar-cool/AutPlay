package app.autplay.application.server

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class InternetMetadataDiscoveryModelsTest {
    @Test
    fun frozenServerExamplesDecodeWithoutLosingEditionOrRepeatedRecordingPositions() {
        val raw = examples()
        val pages = raw.map(InternetMetadataDiscoveryCodec::decode)
        assertEquals(raw, pages.map { it.rawPayload })
        assertEquals(InternetMetadataDiscoveryEntity.ARTIST, pages[0].items.single().browseTarget?.entity)
        assertEquals(InternetMetadataDiscoveryEntity.RELEASE, pages[1].items.single().browseTarget?.entity)
        assertEquals("2001", pages[1].items.single().releaseDate)
        assertEquals("Example Artist Song (Live)", pages[2].items.single().trackLookup?.searchQuery)
        val releaseTracks = pages[3].items
        assertEquals(2, releaseTracks.size)
        assertEquals(listOf(1, 2), releaseTracks.map { it.trackNumber })
        assertEquals(1, releaseTracks.map { it.recordingId }.toSet().size)
        assertEquals(2, releaseTracks.map { it.id }.toSet().size)
        assertEquals("Song", releaseTracks[0].title)
        assertEquals("Song (Live)", releaseTracks[0].recordingTitle)
        assertEquals("Example Artist Song (Live)", releaseTracks[0].trackLookup?.searchQuery)
        val selected = MusicCatalogueContextRequest.fromCard(releaseTracks[0])
        assertEquals(InternetMetadataDiscoveryEntity.RELEASE_TRACK, releaseTracks[0].trackLookup?.entity)
        assertEquals(JsonPrimitive("00000000-0000-0000-0000-000000000004"), selected?.document()?.get("entity_id"))
        assertEquals(JsonPrimitive(RELEASE), selected?.document()?.get("release_id"))
        assertEquals(setOf("entity_type", "entity_id", "release_id"), selected?.document()?.keys)
        assertNull(MusicCatalogueContextRequest.fromCard(pages[0].items.single()))
        assertNull(MusicCatalogueContextRequest.fromCard(pages[1].items.single()))
        assertEquals(JsonNull, MusicCatalogueContextRequest.fromCard(pages[2].items.single())?.document()?.get("release_id"))
        pages.forEach { page ->
            assertTrue(page.knownActionsAllowed)
            assertFalse(page.directAcquisitionAllowed)
            page.items.forEach { assertFalse(it.directAcquisitionAllowed) }
        }
    }

    @Test
    fun missingCreditAndUnknownPositionsStayNullAndDoNotInventDownloadQuery() {
        val unknownCredit = changeCard(examples()[3], 0, mapOf(
            "artist" to JsonNull, "disc_number" to JsonNull, "track_number" to JsonNull,
            "download_search_query" to JsonNull,
        ))
        val card = InternetMetadataDiscoveryCodec.decode(unknownCredit).items.first()
        assertNull(card.artist)
        assertNull(card.discNumber)
        assertNull(card.trackNumber)
        assertNull(card.trackLookup)
        assertNull(MusicCatalogueContextRequest.fromCard(card))
    }

    @Test
    fun unknownTopLevelAndCardFieldsRoundTripWithoutDroppingData() {
        val extension = buildJsonObject { put("revision", 17); put("nested", JsonArray(listOf(JsonNull, JsonPrimitive("kept")))) }
        val raw = changeCard(examples()[2], 0, mapOf("future_card" to extension)).with("future_page", extension)
        val parsed = InternetMetadataDiscoveryCodec.decode(raw)
        assertEquals(raw, parsed.rawPayload)
        assertEquals(extension, parsed.items.single().rawPayload["future_card"])
        assertNotNull(parsed.items.single().trackLookup)
    }

    @Test
    fun unknownProviderOrEntityIsPreservedAndOffersNoActions() {
        val foreignProvider = changeCard(examples()[2].with("source", JsonPrimitive("FutureCatalogue")), 0,
            mapOf("source" to JsonPrimitive("FutureCatalogue"), "entity_id" to JsonPrimitive("opaque-provider-id")))
        val unknownEntity = changeCard(examples()[2], 0, mapOf(
            "entity_type" to JsonPrimitive("future_recording"), "entity_id" to JsonPrimitive("opaque-entity"),
            "id" to JsonPrimitive("future:opaque-entity"),
        ))
        assertNull(InternetMetadataDiscoveryCodec.decode(unknownEntity).items.single().knownEntity)
        listOf(foreignProvider, unknownEntity).forEach { raw ->
            val page = InternetMetadataDiscoveryCodec.decode(raw)
            assertEquals(raw, page.rawPayload)
            assertNull(page.items.single().browseTarget)
            assertNull(page.items.single().trackLookup)
            assertNull(MusicCatalogueContextRequest.fromCard(page.items.single()))
            assertFalse(page.directAcquisitionAllowed)
        }
    }

    @Test
    fun unknownOrContradictoryCapabilitiesDoNotAuthorizeAnyAction() {
        val variations = listOf(
            examples()[2].with("contract_version", JsonPrimitive("music-discovery-v2")),
            examples()[2].with("source_scope", JsonPrimitive("FUTURE_SCOPE")),
            examples()[2].with("availability", JsonPrimitive("DOWNLOADABLE")),
            examples()[2].with("acquisition_allowed", JsonPrimitive(true)),
            examples()[2].with("capabilities", buildJsonObject { put("direct_acquisition", true); put("track_source_search", true) }),
            examples()[2].with("capabilities", buildJsonObject { put("direct_acquisition", false); put("track_source_search", false) }),
            examples()[2].with("capabilities", buildJsonObject { put("direct_acquisition", false); put("track_source_search", true); put("future_action", true) }),
            examples()[2].with("capabilities", JsonObject(emptyMap())),
        )
        variations.forEach { raw ->
            val page = InternetMetadataDiscoveryCodec.decode(raw)
            assertEquals(raw, page.rawPayload)
            assertFalse(page.knownActionsAllowed)
            assertNull(page.items.single().trackLookup)
            assertFalse(page.directAcquisitionAllowed)
        }
        val contradictoryCard = InternetMetadataDiscoveryCodec.decode(changeCard(
            examples()[2], 0, mapOf("acquisition_allowed" to JsonPrimitive(true)),
        )).items.single()
        assertNull(contradictoryCard.trackLookup)
        assertFalse(contradictoryCard.directAcquisitionAllowed)
    }

    @Test
    fun mismatchedKnownIdsAndMalformedUuidUseStableResponseError() {
        val mutations = listOf(
            mapOf("id" to JsonPrimitive("musicbrainz:release:$RECORDING")),
            mapOf("entity_id" to JsonPrimitive("not-a-uuid")),
            mapOf("entity_id" to JsonPrimitive("1-1-1-1-1")),
            mapOf("recording_id" to JsonPrimitive("not-a-uuid")),
            mapOf("release_id" to JsonPrimitive("not-a-uuid")),
        )
        mutations.forEach { invalid(changeCard(examples()[2], 0, it)) }
    }

    @Test
    fun contradictoryKnownIdentityAndBrowseFlagsDisableAction() {
        val recording = changeCard(examples()[2], 0, mapOf("recording_id" to JsonPrimitive(RELEASE)))
        val browseRecording = changeCard(examples()[2], 0, mapOf("can_browse_tracks" to JsonPrimitive(true)))
        val wrongEdition = changeCard(examples()[1], 0, mapOf("release_id" to JsonPrimitive(RECORDING)))
        listOf(recording, browseRecording, wrongEdition).forEach { raw ->
            val card = InternetMetadataDiscoveryCodec.decode(raw).items.single()
            assertNull(card.browseTarget)
            assertNull(card.trackLookup)
        }
    }

    @Test
    fun rawProgressOffsetMayAdvancePastDisplayedDeduplicatedCount() {
        val raw = examples()[0].with("limit", JsonPrimitive(3)).with("total_count", JsonPrimitive(5))
            .with("next_offset", JsonPrimitive(3))
        val page = InternetMetadataDiscoveryCodec.decode(raw)
        assertEquals(1, page.items.size)
        assertEquals(3, page.nextOffset)
        assertFalse(page.truncated)
    }

    @Test
    fun capAndEmptyRawPageCanHonestlyReportTruncatedWithoutNextOffset() {
        val atCap = examples()[0].with("offset", JsonPrimitive(1000)).with("total_count", JsonPrimitive(5000))
            .with("truncated", JsonPrimitive(true))
        assertNull(InternetMetadataDiscoveryCodec.decode(atCap).nextOffset)
        val empty = examples()[0].with("items", JsonArray(emptyList())).with("total_count", JsonPrimitive(5000))
            .with("truncated", JsonPrimitive(true))
        assertTrue(InternetMetadataDiscoveryCodec.decode(empty).truncated)
    }

    @Test
    fun invalidPaginationAndDuplicateCardIdentityAreRejected() {
        val base = examples()[0]
        listOf(
            base.with("limit", JsonPrimitive(0)), base.with("limit", JsonPrimitive(51)),
            base.with("offset", JsonPrimitive(-1)), base.with("offset", JsonPrimitive(1001)),
            base.with("total_count", JsonPrimitive(-1)), base.with("total_count", JsonPrimitive(1_000_001)),
            base.with("total_count", JsonPrimitive(0)), base.with("next_offset", JsonPrimitive(0)),
            base.with("offset", JsonPrimitive(1)),
            base.with("total_count", JsonPrimitive(100)).with("next_offset", JsonPrimitive(26)),
            base.with("total_count", JsonPrimitive(100)).with("next_offset", JsonPrimitive(1)).with("truncated", JsonPrimitive(true)),
            base.with("total_count", JsonPrimitive(100)),
            base.with("items", JsonArray(listOf(base["items"]!!.jsonArray.first(), base["items"]!!.jsonArray.first())))
                .with("total_count", JsonPrimitive(2)),
        ).forEach(::invalid)
        invalid(examples()[3].with("total_count", JsonPrimitive(20)).with("next_offset", JsonPrimitive(1)))
    }

    @Test
    fun payloadTextAndNumericBoundsRejectOverlengthControlsOrCoercion() {
        val base = examples()[2]
        listOf(
            mapOf("title" to JsonPrimitive("a".repeat(501))),
            mapOf("title" to JsonPrimitive(" \t")),
            mapOf("title" to JsonPrimitive("song\u0000name")),
            mapOf("download_search_query" to JsonPrimitive("a".repeat(201))),
            mapOf("duration_ms" to JsonPrimitive(0)),
            mapOf("duration_ms" to JsonPrimitive(86_400_001)),
            mapOf("disc_number" to JsonPrimitive(1001)),
            mapOf("track_number" to JsonPrimitive(10_001)),
            mapOf("duration_ms" to JsonPrimitive("180000")),
            mapOf("release_date" to JsonPrimitive("2001-02-29")),
            mapOf("release_date" to JsonPrimitive("0000")),
        ).forEach { invalid(changeCard(base, 0, it)) }
        invalid(base.with("limit", JsonPrimitive("25")))
        invalid(base.with("acquisition_allowed", JsonPrimitive("false")))
        invalid(base.with("capabilities", buildJsonObject { put("direct_acquisition", "false"); put("track_source_search", true) }))
        invalid(changeCard(base, 0, mapOf("future_blob" to JsonPrimitive("a".repeat(16_384)))))
        invalid(base.with("future_page_blob", JsonPrimitive("a".repeat(InternetMetadataDiscoveryCodec.MAX_PAGE_BYTES))))
    }

    @Test
    fun immutableSnapshotDoesNotFollowMutationOfCallerMap() {
        val backing = examples()[2].toMutableMap()
        val page = InternetMetadataDiscoveryCodec.decode(JsonObject(backing))
        backing["source"] = JsonPrimitive("ChangedProvider")
        assertEquals("MusicBrainz", page.source)
        assertEquals(JsonPrimitive("MusicBrainz"), page.rawPayload["source"])
        assertNotNull(page.items.single().trackLookup)
    }

    @Test
    fun malformedJsonUsesStableResponseError() {
        val failure = assertThrows(IllegalArgumentException::class.java) { InternetMetadataDiscoveryCodec.decode("{broken") }
        assertEquals("SERVER_RESPONSE_INVALID", failure.message)
        val arrayFailure = assertThrows(IllegalArgumentException::class.java) { InternetMetadataDiscoveryCodec.decode("[]") }
        assertEquals("SERVER_RESPONSE_INVALID", arrayFailure.message)
    }

    private fun invalid(document: JsonObject) {
        val failure = assertThrows(IllegalArgumentException::class.java) { InternetMetadataDiscoveryCodec.decode(document) }
        assertEquals("SERVER_RESPONSE_INVALID", failure.message)
    }

    private fun JsonObject.with(key: String, value: kotlinx.serialization.json.JsonElement): JsonObject = JsonObject(this + (key to value))

    private fun changeCard(document: JsonObject, index: Int, changes: Map<String, kotlinx.serialization.json.JsonElement>): JsonObject {
        val rows = document["items"]!!.jsonArray.toMutableList()
        rows[index] = JsonObject(rows[index].jsonObject + changes)
        return document.with("items", JsonArray(rows))
    }

    private fun examples(): List<JsonObject> = Json.parseToJsonElement(FROZEN_SERVER_PAGES).jsonArray.map { it.jsonObject }

    companion object {
        private const val RECORDING = "00000000-0000-0000-0000-000000000003"
        private const val RELEASE = "00000000-0000-0000-0000-000000000002"

        // Frozen from contracts/metadata-discovery/v1/page-examples.json; whitespace is insignificant.
        private val FROZEN_SERVER_PAGES = """
            [
              {"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[
                {"id":"musicbrainz:artist:00000000-0000-0000-0000-000000000001","entity_type":"artist","entity_id":"00000000-0000-0000-0000-000000000001","title":"Example Artist","artist":null,"release_id":null,"recording_id":null,"release_date":null,"country":"GB","disambiguation":null,"duration_ms":null,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":true,"download_search_query":null}
              ],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false},
              {"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[
                {"id":"musicbrainz:release:00000000-0000-0000-0000-000000000002","entity_type":"release","entity_id":"00000000-0000-0000-0000-000000000002","title":"Example Album","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":null,"release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":true,"download_search_query":null}
              ],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false},
              {"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[
                {"id":"musicbrainz:recording:00000000-0000-0000-0000-000000000003","entity_type":"recording","entity_id":"00000000-0000-0000-0000-000000000003","title":"Song (Live)","artist":"Example Artist","release_id":null,"recording_id":"00000000-0000-0000-0000-000000000003","release_date":null,"country":null,"disambiguation":null,"duration_ms":180000,"disc_number":null,"track_number":null,"recording_title":null,"source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"}
              ],"limit":25,"offset":0,"total_count":1,"next_offset":null,"truncated":false},
              {"contract_version":"music-discovery-v1","source":"MusicBrainz","source_scope":"INTERNET","availability":"METADATA_ONLY","acquisition_allowed":false,"capabilities":{"direct_acquisition":false,"track_source_search":true},"items":[
                {"id":"musicbrainz:release_track:00000000-0000-0000-0000-000000000004","entity_type":"release_track","entity_id":"00000000-0000-0000-0000-000000000004","title":"Song","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":"00000000-0000-0000-0000-000000000003","release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":1,"track_number":1,"recording_title":"Song (Live)","source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"},
                {"id":"musicbrainz:release_track:00000000-0000-0000-0000-000000000005","entity_type":"release_track","entity_id":"00000000-0000-0000-0000-000000000005","title":"Song","artist":"Example Artist","release_id":"00000000-0000-0000-0000-000000000002","recording_id":"00000000-0000-0000-0000-000000000003","release_date":"2001","country":null,"disambiguation":null,"duration_ms":null,"disc_number":1,"track_number":2,"recording_title":"Song (Live)","source":"MusicBrainz","availability":"METADATA_ONLY","acquisition_allowed":false,"can_browse_tracks":false,"download_search_query":"Example Artist Song (Live)"}
              ],"limit":25,"offset":0,"total_count":2,"next_offset":null,"truncated":false}
            ]
        """.trimIndent()
    }
}
