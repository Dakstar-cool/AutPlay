package app.autplay.application.library

import app.autplay.data.local.dao.MetadataAlbumSourceRow
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AlbumGroupMetadataTest {
    @Test fun nativeAlbumEvidenceRemainsUsableWhileCatalogueLookupIsReviewingOrMissing() {
        listOf("READY", "REVIEW", "NOT_FOUND", "RETRY", "FAILED").forEach { state ->
            assertNotNull(TrackMetadata.decode(document(group()).edit("state" to JsonPrimitive(state))).albumGroup())
        }
    }

    @Test fun hitmoNativeAlbumUsesItsOwnExplicitNamespace() {
        val raw = group().edit("provider" to JsonPrimitive("HITMO"), "key" to JsonPrimitive("native:hitmo:album:album-1"))
        assertEquals("native:hitmo:album:album-1", TrackMetadata.decode(document(raw)).albumGroup()!!.key)
    }

    @Test fun nativeEvidenceCanHaveUnknownArtistAndPositions() {
        val root = document(group(artist = null, disc = null, track = null))
        val value = TrackMetadata.decode(root).albumGroup()!!
        assertEquals("metadata-album:native:bandcamp:album:album-1", value.stableId)
        assertNull(value.albumArtist)
        assertNull(value.discNumber)
        assertNull(value.trackNumber)
    }

    @Test fun unknownJsonSurvivesWithoutAuthorizingUnknownGroups() {
        val raw = group().edit("future" to buildJsonObject { put("nested", "preserve") })
        val root = document(raw).edit("unknown_root" to JsonPrimitive("keep"),
            "provenance" to buildJsonObject { put("title", buildJsonObject { put("source", "FUTURE_PROVIDER") }) })
        val parsed = TrackMetadata.decode(Json.parseToJsonElement(root.toString()).jsonObject)
        assertEquals(root, parsed.payload)
        assertEquals(raw, parsed.albumGroup()!!.payload)
        val unknown = root.edit("album_group_v1" to raw.edit("provider" to JsonPrimitive("FUTURE_PROVIDER")))
        val unsupported = TrackMetadata.decode(unknown)
        assertNull(unsupported.albumGroup())
        assertEquals(unknown, unsupported.payload)
        assertNull(TrackMetadata.decode(root.edit("album_group_v1" to raw.edit("schema_version" to JsonPrimitive(2)))).albumGroup())
    }

    @Test fun albumClearAndChangedEditionCannotRetainMembership() {
        val raw = group()
        for (value in listOf(JsonNull, JsonPrimitive("Manual edition"))) {
            assertNull(TrackMetadata.decode(document(raw, buildJsonObject { put("album", value) })).albumGroup())
        }
        assertNull(TrackMetadata.decode(document(raw, buildJsonObject { put("album_artist", JsonNull) })).albumGroup())
        assertNull(TrackMetadata.decode(document(raw, buildJsonObject { put("release_date", "2025") })).albumGroup())
        assertNotNull(TrackMetadata.decode(document(raw, buildJsonObject { put("title", "Manual title") })).albumGroup())
    }

    @Test fun coherentNormalizedEditionLabelsAndIndependentEmbeddedCreditKeepMembership() {
        val raw = group().edit("title" to JsonPrimitive("THE ALBUM - Deluxe"))
        assertNotNull(TrackMetadata.decode(document(raw, buildJsonObject {
            put("album", "The Album / Deluxe"); put("album_artist", "ALBUM ARTIST")
        })).albumGroup())
        assertNull(TrackMetadata.decode(document(raw, buildJsonObject { put("album", JsonNull) })).albumGroup())
        val unknownCredit = group(artist = null)
        val native = TrackMetadata.decode(document(unknownCredit, buildJsonObject {
            put("album", "ALBUM"); put("album_artist", "Independent embedded credit")
        })).albumGroup()!!
        assertNull(native.albumArtist)
        assertNotNull(TrackMetadata.decode(document(group().edit("title" to JsonPrimitive("Stra\u00dfe")),
            buildJsonObject { put("album", "STRASSE") })).albumGroup())
    }

    @Test fun invalidTypedValuesNeverBecomeConfirmedEvidence() {
        val raw = group()
        val invalid = listOf(
            raw.edit("schema_version" to JsonPrimitive("1")),
            raw.edit("disc_number" to JsonPrimitive("1")),
            raw.edit("track_number" to JsonPrimitive(0)),
            raw.edit("track_number" to JsonPrimitive(10_000)),
            raw.edit("release_date" to JsonPrimitive("2024-02-30")),
            raw.edit("key" to JsonPrimitive("native:bandcamp:album:wrong")),
            raw.edit("title" to JsonPrimitive(" Album")),
            raw.edit("title" to JsonPrimitive("A\u0085B")),
            raw.edit("evidence" to evidence().edit("locked" to JsonPrimitive("false"))),
            raw.edit("evidence" to evidence().edit("source" to JsonPrimitive("MUSICBRAINZ"))),
            raw.edit("evidence" to evidence().edit("source_id" to JsonPrimitive("https://private.invalid/x"))),
            raw.edit("evidence" to evidence().edit("observed_at" to JsonPrimitive("tomorrow"))),
        )
        invalid.forEach { assertNull(it.toString(), TrackMetadata.decode(document(it)).albumGroup()) }
        assertNull(TrackMetadata.decode(document(group(disc = null), buildJsonObject {
            put("disc_number", "malformed")
        })).albumGroup())
        assertNull(TrackMetadata.decode(document(raw.edit("title" to JsonPrimitive("true")), buildJsonObject {
            put("album", true)
        })).albumGroup())
    }

    @Test fun musicBrainzIdentityAndProofMustNameTheSameExactRelease() {
        val identity = "00000000-0000-4000-8000-000000000001"
        val key = "musicbrainz:release:$identity"
        val raw = group().edit("provider" to JsonPrimitive("MUSICBRAINZ"), "release_id" to JsonPrimitive(identity),
            "key" to JsonPrimitive(key), "evidence" to evidence().edit("source" to JsonPrimitive("MUSICBRAINZ"),
                "source_id" to JsonPrimitive(key)))
        assertEquals(key, TrackMetadata.decode(document(raw)).albumGroup()!!.key)
        assertNull(TrackMetadata.decode(document(raw.edit("release_id" to JsonPrimitive("1-1-1-1-1")))).albumGroup())
        assertNull(TrackMetadata.decode(document(raw.edit("evidence" to evidence()))).albumGroup())
        assertNull(TrackMetadata.decode(document(raw, buildJsonObject {
            put("mb_release_id", "00000000-0000-4000-8000-000000000002")
        })).albumGroup())
        assertNull(TrackMetadata.decode(document(group(), buildJsonObject { put("mb_release_id", identity) })).albumGroup())
    }

    @Test fun repeatedReferencesDeduplicateButDifferentEditionsRemainSeparate() {
        val projector = MetadataAlbumProjector()
        val first = row("one", group(track = 1))
        projector.add(first); projector.add(first)
        assertEquals(1, projector.albums().single().members.size)
        projector.add(row("two", group(track = 2)))
        assertEquals(listOf("one", "two"), projector.albums().single().members.map { it.localUserTrackRefId })
        projector.add(row("three", group(identity = "album-2", artist = "Other artist")))
        assertEquals(2, projector.albums().size)
        assertEquals(setOf("Album artist", "Other artist"), projector.albums().map { it.group.albumArtist }.toSet())
    }

    @Test fun newestUnknownMemberDoesNotEraseObservedGroupCreditOrDate() {
        val projector = MetadataAlbumProjector()
        projector.add(row("one", group(artist = "Observed artist", date = "2024")))
        projector.add(row("two", group(artist = null, date = null)).copy(metadataUpdatedAtMs = 3))
        var album = projector.albums().single()
        assertEquals("Observed artist", album.group.albumArtist)
        assertEquals("2024", album.group.releaseDate)
        assertEquals(2, album.members.size)
        projector.add(row("one", group(artist = null, date = null)).copy(metadataUpdatedAtMs = 4))
        album = projector.albums().single()
        assertNull(album.group.albumArtist); assertNull(album.group.releaseDate)
    }

    @Test fun unknownPositionsAndMissingRecordingStayUnknownAndEffectiveNullDoesNotFallBack() {
        val projector = MetadataAlbumProjector()
        projector.add(row("unknown", group(disc = null, track = null), buildJsonObject {
            put("title", JsonNull); put("artist", "Effective artist")
        }))
        val member = projector.albums().single().members.single()
        assertNull(member.localRecordingId)
        assertNull(member.discNumber)
        assertNull(member.trackNumber)
        assertNull(member.title)
        assertEquals("Effective artist", member.artistName)
    }

    @Test fun candidatesAloneNeverAuthorizeAlbumMembership() {
        val root = document(null).edit("candidates" to buildJsonArray { add(buildJsonObject {
            put("album_group_v1", group()); put("fields", buildJsonObject { put("album", "Album") })
        }) })
        assertNull(TrackMetadata.decode(root).albumGroup())
        val projector = MetadataAlbumProjector()
        projector.add(row("one", null).copy(payloadJson = root.toString()))
        assertTrue(projector.albums().isEmpty())
    }

    private fun row(id: String, group: JsonObject?, fields: JsonObject = buildJsonObject { }) =
        MetadataAlbumSourceRow(id, document(group, fields).toString(), null, "Raw title", "Raw artist", 10_000, 1, 2)

    private fun document(group: JsonObject?, fields: JsonObject = buildJsonObject { }) = buildJsonObject {
        put("revision", 1); put("state", "READY"); put("fields", fields)
        put("provenance", buildJsonObject { }); put("genres_future", buildJsonArray { add("keep") })
        if (group != null) put("album_group_v1", group)
    }

    private fun group(identity: String = "album-1", artist: String? = "Album artist", disc: Int? = 1,
        track: Int? = 1, date: String? = "2024-02") = buildJsonObject {
        put("schema_version", 1); put("provider", "BANDCAMP"); put("release_id", identity)
        put("key", "native:bandcamp:album:$identity"); put("title", "Album")
        put("album_artist", artist?.let(::JsonPrimitive) ?: JsonNull); put("release_date", date?.let(::JsonPrimitive) ?: JsonNull)
        put("disc_number", disc?.let(::JsonPrimitive) ?: JsonNull)
        put("track_number", track?.let(::JsonPrimitive) ?: JsonNull); put("evidence", evidence())
    }

    private fun evidence() = buildJsonObject {
        put("source", "SOURCE_NATIVE"); put("source_id", "source/album-1")
        put("normalization_version", "source-native-v1"); put("observed_at", "2026-10-06T10:00:00Z"); put("locked", false)
    }

    private fun JsonObject.edit(vararg values: Pair<String, JsonElement>): JsonObject = JsonObject(this + values)
}
