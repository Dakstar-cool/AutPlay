package app.autplay.application.server

import java.time.Instant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class MusicCatalogueContextModelsTest {
    @Test
    fun requestContainsOnlySelectedIdentitiesAndNeverClientMetadataOrOperationId() {
        val recording = MusicCatalogueContextRequest.recording(RECORDING)
        assertEquals(setOf("entity_type", "entity_id", "release_id"), recording.document().keys)
        assertEquals(JsonPrimitive("recording"), recording.document()["entity_type"])
        assertEquals(JsonPrimitive(RECORDING), recording.document()["entity_id"])
        assertEquals(JsonNull, recording.document()["release_id"])
        val track = MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)
        assertEquals(setOf("entity_type", "entity_id", "release_id"), track.document().keys)
        assertEquals(JsonPrimitive("release_track"), track.document()["entity_type"])
        assertEquals(JsonPrimitive(RELEASE), track.document()["release_id"])
    }

    @Test
    fun malformedRequestIdentitiesCannotEnterTypedRequest() {
        listOf("not-a-uuid", "1-1-1-1-1", "", "musicbrainz:recording:$RECORDING").forEach { value ->
            assertEquals("SERVER_RESPONSE_INVALID", assertThrows(IllegalArgumentException::class.java) {
                MusicCatalogueContextRequest.recording(value)
            }.message)
        }
        assertThrows(IllegalArgumentException::class.java) { MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, "bad") }
        assertThrows(IllegalArgumentException::class.java) { MusicCatalogueContextRequest.releaseTrack("bad", RELEASE) }
    }

    @Test
    fun contextResponseKeepsNullCreditPositionsAndRecordingVersion() {
        val raw = releaseTrackContext().withLookup(mapOf("artist" to JsonNull, "disc_number" to JsonNull,
            "track_number" to JsonNull, "duration_ms" to JsonNull))
        val parsed = MusicCatalogueContextCodec.decode(raw)
        assertEquals(raw, parsed.rawPayload)
        assertEquals(CONTEXT, parsed.catalogueContextId)
        assertEquals(Instant.parse("2026-10-07T12:00:00Z"), parsed.expiresAt)
        assertEquals(RELEASE_TRACK, parsed.lookupMetadata.entityId)
        assertEquals(RECORDING, parsed.lookupMetadata.recordingMbid)
        assertEquals(RELEASE, parsed.lookupMetadata.releaseMbid)
        assertEquals("Song", parsed.lookupMetadata.title)
        assertEquals("Song (Live)", parsed.lookupMetadata.recordingTitle)
        assertNull(parsed.lookupMetadata.artist)
        assertNull(parsed.lookupMetadata.discNumber)
        assertNull(parsed.lookupMetadata.trackNumber)
        assertNull(parsed.lookupMetadata.durationMs)
        assertTrue(parsed.knownActionsAllowed)
        assertFalse(parsed.directAcquisitionAllowed)
    }

    @Test
    fun recordingContextHasNoImplicitReleaseAssociation() {
        val parsed = MusicCatalogueContextCodec.decode(recordingContext())
        assertTrue(parsed.matches(MusicCatalogueContextRequest.recording(RECORDING)))
        assertNull(parsed.lookupMetadata.releaseMbid)
        assertNull(parsed.lookupMetadata.album)
        assertFalse(parsed.matches(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)))
    }

    @Test
    fun unknownMetadataAndTopLevelFieldsRoundTripWithoutBeingSentBackInRequest() {
        val extension = buildJsonObject { put("revision", 17); put("payload", JsonArray(listOf(JsonPrimitive("kept"), JsonNull))) }
        val raw = releaseTrackContext().with("future_context", extension).withLookup(mapOf("future_metadata" to extension))
        val parsed = MusicCatalogueContextCodec.decode(raw)
        assertEquals(raw, parsed.rawPayload)
        assertEquals(extension, parsed.lookupMetadata.rawPayload["future_metadata"])
        assertTrue(parsed.matches(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)))
        assertFalse(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE).document().containsKey("future_metadata"))
    }

    @Test
    fun unknownContractSourceOrLookupEntityIsPreservedWithoutAdmissionAction() {
        listOf(
            releaseTrackContext().with("contract_version", JsonPrimitive("music-catalogue-context-v2")),
            releaseTrackContext().with("source", JsonPrimitive("FutureCatalogue")),
            releaseTrackContext().with("availability", JsonPrimitive("DOWNLOADABLE")),
            releaseTrackContext().with("acquisition_allowed", JsonPrimitive(true)),
            releaseTrackContext().withLookup(mapOf("schema_version" to JsonPrimitive(2))),
            releaseTrackContext().withLookup(mapOf("entity_type" to JsonPrimitive("future_track"),
                "entity_id" to JsonPrimitive("opaque-identity"), "recording_mbid" to JsonPrimitive("opaque-recording"))),
        ).forEach { raw ->
            val parsed = MusicCatalogueContextCodec.decode(raw)
            assertEquals(raw, parsed.rawPayload)
            assertFalse(parsed.knownActionsAllowed)
            assertNull(parsed.admissionContextId(NOW))
            assertFalse(parsed.matches(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)))
            assertFalse(parsed.directAcquisitionAllowed)
        }
    }

    @Test
    fun exactSelectedEditionMustMatchHydratedContextBeforeFreshSearch() {
        val parsed = MusicCatalogueContextCodec.decode(releaseTrackContext())
        assertTrue(parsed.matches(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, RELEASE)))
        assertFalse(parsed.matches(MusicCatalogueContextRequest.releaseTrack(RELEASE_TRACK, OTHER_RELEASE)))
        assertFalse(parsed.matches(MusicCatalogueContextRequest.releaseTrack(OTHER_TRACK, RELEASE)))
    }

    @Test
    fun expiryIsFreshAdmissionBoundaryAndUtcOffsetIsAccepted() {
        val parsed = MusicCatalogueContextCodec.decode(releaseTrackContext())
        assertEquals(CONTEXT, parsed.admissionContextId(NOW))
        assertNull(parsed.admissionContextId(parsed.expiresAt))
        assertNull(parsed.admissionContextId(parsed.expiresAt.plusSeconds(1)))
        val utcOffset = MusicCatalogueContextCodec.decode(releaseTrackContext().with("expires_at", JsonPrimitive("2026-10-07T12:00:00+00:00")))
        assertEquals(parsed.expiresAt, utcOffset.expiresAt)
    }

    @Test
    fun newSuccessfulReceiptKeepsNewOpaqueIdentityWithoutMutatingPreviousOne() {
        val first = MusicCatalogueContextCodec.decode(releaseTrackContext())
        val second = MusicCatalogueContextCodec.decode(releaseTrackContext().with("catalogue_context_id", JsonPrimitive(OTHER_CONTEXT)))
        assertNotEquals(first.catalogueContextId, second.catalogueContextId)
        assertEquals(CONTEXT, first.catalogueContextId)
        assertEquals(OTHER_CONTEXT, second.catalogueContextId)
        assertEquals(first.lookupMetadata.rawPayload, second.lookupMetadata.rawPayload)
        assertEquals(CONTEXT, first.admissionContextId(NOW))
        assertEquals(OTHER_CONTEXT, second.admissionContextId(NOW))
    }

    @Test
    fun invalidContextIdentityTimestampOrPrimitiveCannotDecode() {
        listOf(
            releaseTrackContext().with("catalogue_context_id", JsonPrimitive("bad")),
            releaseTrackContext().with("catalogue_context_id", JsonPrimitive("1-1-1-1-1")),
            releaseTrackContext().with("expires_at", JsonPrimitive("2026-10-07T12:00:00+03:00")),
            releaseTrackContext().with("expires_at", JsonPrimitive("2026-10-07T12:00:00")),
            releaseTrackContext().with("expires_at", JsonPrimitive("2026-02-30T12:00:00Z")),
            releaseTrackContext().with("acquisition_allowed", JsonPrimitive("false")),
            releaseTrackContext().with("lookup_metadata", JsonNull),
        ).forEach(::invalid)
    }

    @Test
    fun inconsistentKnownLookupIdentityCannotDecode() {
        listOf(
            releaseTrackContext().withLookup(mapOf("entity_id" to JsonPrimitive("bad"))),
            releaseTrackContext().withLookup(mapOf("recording_mbid" to JsonNull)),
            releaseTrackContext().withLookup(mapOf("release_mbid" to JsonNull)),
            releaseTrackContext().withLookup(mapOf("album" to JsonNull)),
            recordingContext().withLookup(mapOf("recording_mbid" to JsonPrimitive(OTHER_TRACK))),
            recordingContext().withLookup(mapOf("release_mbid" to JsonPrimitive(RELEASE))),
            recordingContext().withLookup(mapOf("album" to JsonPrimitive("Unexpected album"))),
            recordingContext().withLookup(mapOf("disc_number" to JsonPrimitive(1))),
            recordingContext().withLookup(mapOf("track_number" to JsonPrimitive(1))),
        ).forEach(::invalid)
    }

    @Test
    fun lookupBoundsRejectControlTextInvalidDatesAndNumericCoercion() {
        listOf(
            mapOf("title" to JsonPrimitive("a".repeat(501))),
            mapOf("artist" to JsonPrimitive("\tArtist")),
            mapOf("artist" to JsonPrimitive(" Artist ")),
            mapOf("album" to JsonPrimitive(" ")),
            mapOf("disambiguation" to JsonPrimitive("bad\u0000text")),
            mapOf("recording_title" to JsonPrimitive(" Song ")),
            mapOf("release_date" to JsonPrimitive("2026-02-29")),
            mapOf("release_date" to JsonPrimitive("0000")),
            mapOf("duration_ms" to JsonPrimitive(0)),
            mapOf("duration_ms" to JsonPrimitive(86_400_001)),
            mapOf("duration_ms" to JsonPrimitive("180000")),
            mapOf("disc_number" to JsonPrimitive(1001)),
            mapOf("track_number" to JsonPrimitive(10_001)),
            mapOf("schema_version" to JsonPrimitive("1")),
        ).forEach { invalid(releaseTrackContext().withLookup(it)) }
    }

    @Test
    fun lookupMetadataByteLimitIncludesUtf8AndUnknownFields() {
        invalid(releaseTrackContext().withLookup(mapOf("future_blob" to JsonPrimitive("a".repeat(16_384)))))
        invalid(releaseTrackContext().withLookup(mapOf("future_blob" to JsonPrimitive("\u044f".repeat(8192)))))
        invalid(releaseTrackContext().with("future_blob", JsonPrimitive("a".repeat(MusicCatalogueContextCodec.MAX_RESPONSE_BYTES))))
    }

    @Test
    fun responseSnapshotDoesNotFollowCallerMutation() {
        val backing = releaseTrackContext().toMutableMap()
        val parsed = MusicCatalogueContextCodec.decode(JsonObject(backing))
        backing["catalogue_context_id"] = JsonPrimitive(OTHER_CONTEXT)
        assertEquals(CONTEXT, parsed.catalogueContextId)
        assertEquals(JsonPrimitive(CONTEXT), parsed.rawPayload["catalogue_context_id"])
    }

    @Test
    fun malformedJsonUsesStableErrorAndDoesNotLeakPayload() {
        val failure = assertThrows(IllegalArgumentException::class.java) { MusicCatalogueContextCodec.decode("{private-data") }
        assertEquals("SERVER_RESPONSE_INVALID", failure.message)
    }

    private fun invalid(document: JsonObject) {
        val failure = assertThrows(IllegalArgumentException::class.java) { MusicCatalogueContextCodec.decode(document) }
        assertEquals("SERVER_RESPONSE_INVALID", failure.message)
    }

    private fun JsonObject.with(key: String, value: JsonElement): JsonObject = JsonObject(this + (key to value))

    private fun JsonObject.withLookup(changes: Map<String, JsonElement>): JsonObject =
        with("lookup_metadata", JsonObject(getValue("lookup_metadata").jsonObject + changes))

    private fun recordingContext(): JsonObject = releaseTrackContext().withLookup(mapOf(
        "entity_type" to JsonPrimitive("recording"), "entity_id" to JsonPrimitive(RECORDING),
        "title" to JsonPrimitive("Song (Live)"), "release_mbid" to JsonNull, "album" to JsonNull,
        "disc_number" to JsonNull, "track_number" to JsonNull,
    ))

    private fun releaseTrackContext(): JsonObject = buildJsonObject {
        put("contract_version", "music-catalogue-context-v1")
        put("catalogue_context_id", CONTEXT)
        put("expires_at", "2026-10-07T12:00:00Z")
        put("source", "MusicBrainz")
        put("availability", "METADATA_ONLY")
        put("acquisition_allowed", false)
        put("lookup_metadata", buildJsonObject {
            put("schema_version", 1)
            put("entity_type", "release_track")
            put("entity_id", RELEASE_TRACK)
            put("recording_mbid", RECORDING)
            put("release_mbid", RELEASE)
            put("title", "Song")
            put("artist", "Example Artist")
            put("album", "Example Album")
            put("release_date", "2001")
            put("duration_ms", 180000)
            put("disc_number", 1)
            put("track_number", 1)
            put("recording_title", "Song (Live)")
            put("disambiguation", JsonNull)
        })
    }

    companion object {
        private const val CONTEXT = "00000000-0000-0000-0000-000000000011"
        private const val OTHER_CONTEXT = "00000000-0000-0000-0000-000000000012"
        private const val RECORDING = "00000000-0000-0000-0000-000000000003"
        private const val RELEASE = "00000000-0000-0000-0000-000000000002"
        private const val OTHER_RELEASE = "00000000-0000-0000-0000-000000000006"
        private const val RELEASE_TRACK = "00000000-0000-0000-0000-000000000004"
        private const val OTHER_TRACK = "00000000-0000-0000-0000-000000000005"
        private val NOW = Instant.parse("2026-10-06T12:00:00Z")
    }
}
