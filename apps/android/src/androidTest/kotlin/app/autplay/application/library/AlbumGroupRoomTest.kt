package app.autplay.application.library

import androidx.room3.useWriterConnection
import androidx.room3.withWriteTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.search.refreshTrackSearch
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.TrackMetadataEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** Uses its own disposable file; never attaches to the app's real database or server. */
@RunWith(AndroidJUnit4::class)
class AlbumGroupRoomTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "album-group-room-proof.db"
    private val profile = "album-owner"
    private lateinit var db: AutPlayDatabase
    private val ignoredHash = ByteArray(0)

    @Before fun open(): Unit = runBlocking {
        context.deleteDatabase(name)
        db = AutPlayDatabase.open(context, name)
        val commands = LocalLibraryCommandRepository(db)
        fun id(seed: Int) = LocalId(UUID(0, seed.toLong()).toString())
        commands.add(AddLocalTrackCommand(null, id(1), id(2), id(3), "Outbox guard", "Guard", 1))
        val binding = ClientEventBinding(UserId(id(4).value), DeviceId(id(5).value), ServerProfileId(id(6).value))
        commands.add(AddLocalTrackCommand(binding, id(7), id(8), id(9), "Journal guard", "Guard", 2))
        assertEquals(1, db.journalDao().eventCount())
        assertEquals(1, db.journalDao().outboxCount())
    }

    @After fun close(): Unit { db.close(); context.deleteDatabase(name) }

    @Test fun firstSecondRepeatAndReopenKeepOneAlbumWithoutCanonicalOrJournalWrites(): Unit = runBlocking {
        val untouched = protectedCounts()
        var originalPayload: JsonObject? = null
        val albums = AlbumGroupRepository(db)
        val updates = Channel<List<MetadataAlbum>>(Channel.UNLIMITED)
        val collection = launch(Dispatchers.Default) { albums.observe(profile).collect { updates.send(it) } }
        try {
            seed("one", track = 1)
            originalPayload = db.trackMetadataDao().get(profile, "one")!!.decoded().payload
            val first = await(updates) { it.size == 1 && it.single().members.size == 1 }.single()
            assertEquals("metadata-album:native:bandcamp:album:edition-1", first.group.stableId)
            seed("two", track = 2)
            val second = await(updates) { it.size == 1 && it.single().members.size == 2 }.single()
            assertEquals(first.group.stableId, second.group.stableId)
            assertEquals(listOf("one", "two"), second.members.map { it.localUserTrackRefId })
            seed("two", track = 2)
            assertEquals(2, albums.snapshot(profile).single().members.size)
            assertEquals(untouched, protectedCounts())
            val core = CoreProductRepository(db)
            assertEquals(first.group.stableId, core.releases(profile).first().single().stableId)
            val detail = core.releaseDetail(first.group.stableId, profile)!!
            assertNull(detail.serverReleaseId)
            assertEquals(listOf("one", "two"), detail.tracks.map { it.localUserTrackRefId })
            assertTrue(detail.tracks.all { it.localRecordingId == null })
        } finally { collection.cancelAndJoin(); updates.close() }
        db.close(); db = AutPlayDatabase.open(context, name)
        val restored = AlbumGroupRepository(db).snapshot(profile).single()
        assertEquals(2, restored.members.size)
        assertEquals(untouched, protectedCounts())
        assertEquals("Raw one", db.libraryDao().trackRef("one")!!.rawTitle)
        val restoredPayload = db.trackMetadataDao().get(profile, "one")!!.decoded().payload
        assertEquals(originalPayload, restoredPayload)
        assertEquals(buildJsonArray { add("Rock") }, db.trackMetadataDao().get(profile, "one")!!.decoded().fields["genres"])
        assertEquals(JsonPrimitive("FUTURE_PROVIDER"), db.trackMetadataDao().get(profile, "one")!!.decoded().provenance
            .getValue("title").jsonObject["source"])
    }

    @Test fun membershipReactivelyTracksClearDeletionRestoreAndSameTimestampEditionChange(): Unit = runBlocking {
        seed("one")
        val updates = Channel<List<MetadataAlbum>>(Channel.UNLIMITED)
        val collection = launch(Dispatchers.Default) { AlbumGroupRepository(db).observe(profile).collect { updates.send(it) } }
        try {
            await(updates) { it.singleOrNull()?.group?.releaseId == "edition-1" }
            write("one", payload(identity = "edition-2", revision = 2), timestamp = 7)
            await(updates) { it.singleOrNull()?.group?.releaseId == "edition-2" }
            // The SUM(updated_at_ms) is unchanged; table invalidation must still refresh the view.
            write("one", payload(identity = "edition-3", revision = 3), timestamp = 7)
            await(updates) { it.singleOrNull()?.group?.releaseId == "edition-3" }
            write("one", payload(identity = "edition-3", revision = 4, clearAlbum = true))
            await(updates) { it.isEmpty() }
            write("one", payload(identity = "edition-3", revision = 5))
            await(updates) { it.size == 1 }
            val entry = db.libraryDao().entryForTrack("one")!!
            db.libraryDao().upsertEntry(entry.copy(removedAtMs = 10))
            await(updates) { it.isEmpty() }
            db.libraryDao().upsertEntry(entry.copy(removedAtMs = null))
            await(updates) { it.size == 1 }
            val ref = db.libraryDao().trackRef("one")!!
            db.libraryDao().upsertTrackRef(ref.copy(deletedAtMs = 11))
            await(updates) { it.isEmpty() }
            db.libraryDao().upsertTrackRef(ref.copy(deletedAtMs = null))
            await(updates) { it.size == 1 }
            write("one", payload(group = false, revision = 6))
            await(updates) { it.isEmpty() }
        } finally { collection.cancelAndJoin(); updates.close() }
    }

    @Test fun identicalLabelsDifferentEditionsAndProfilesRemainDistinctIncludingUnknownArtist(): Unit = runBlocking {
        seed("one", identity = "edition-1")
        seed("two", identity = "edition-2", albumArtist = "Other artist")
        seed("unknown", identity = "edition-3", albumArtist = null, track = null)
        seed("foreign", owner = "another-owner", identity = "edition-1")
        db.libraryDao().upsertEntry(entry("entry-one", "one", profile))
        val albums = AlbumGroupRepository(db).snapshot(profile)
        assertEquals(3, albums.size)
        assertEquals(3, albums.sumOf { it.members.size })
        assertNull(albums.single { it.group.releaseId == "edition-3" }.group.albumArtist)
        assertNull(albums.single { it.group.releaseId == "edition-3" }.members.single().trackNumber)
        assertEquals(listOf("foreign"), AlbumGroupRepository(db).snapshot("another-owner").single().members.map { it.localUserTrackRefId })
    }

    @Test fun fullMembershipIncludesTracksBeyondVisibleFiveThousandAndSurvivesReopen(): Unit = runBlocking {
        val untouched = protectedCounts()
        val json = payload(track = null).toString()
        db.withWriteTransaction {
            val refs = (0..5_000).map { ref("bulk-" + it.toString().padStart(5, '0'), profile) }
            db.libraryDao().upsertTrackRefs(refs)
            db.libraryDao().upsertEntries(refs.map { entry("entry-${it.localUserTrackRefId}", it.localUserTrackRefId, profile) })
            refs.forEach { db.trackMetadataDao().upsert(TrackMetadataEntity(profile, it.localUserTrackRefId, 1, json, null, 7)) }
        }
        val album = AlbumGroupRepository(db).snapshot(profile).single()
        assertEquals(5_001, album.members.size)
        assertTrue(album.members.any { it.localUserTrackRefId == "bulk-05000" })
        val detail = CoreProductRepository(db).releaseDetail(album.group.stableId, profile)!!
        assertEquals(5_001, detail.tracks.size)
        assertEquals(untouched, protectedCounts())
        db.close(); db = AutPlayDatabase.open(context, name)
        assertEquals(5_001, AlbumGroupRepository(db).snapshot(profile).single().members.size)
    }

    @Test fun oldRevisionCannotMoveAConfirmedTrackBackToAnEarlierEdition(): Unit = runBlocking {
        seed("one")
        write("one", payload(identity = "edition-2", revision = 2))
        write("one", payload(identity = "edition-1", revision = 1))
        assertEquals("edition-2", AlbumGroupRepository(db).snapshot(profile).single().group.releaseId)
        assertEquals(2L, db.trackMetadataDao().get(profile, "one")!!.revision)
    }

    @Test fun concurrentSameReferenceMetadataWritesKeepOneMemberAndUnknownGroupsRoundTrip(): Unit = runBlocking {
        seed("one")
        val untouched = protectedCounts()
        coroutineScope {
            List(8) { index -> async(Dispatchers.Default) {
                write("one", payload(revision = index + 2))
            } }.awaitAll()
        }
        assertEquals(1, AlbumGroupRepository(db).snapshot(profile).single().members.size)
        assertEquals(9L, db.trackMetadataDao().get(profile, "one")!!.revision)
        val known = payload(revision = 10)
        val unknownGroup = known.getValue("album_group_v1").jsonObject.let { JsonObject(it + mapOf(
            "schema_version" to JsonPrimitive(2), "provider" to JsonPrimitive("FUTURE_PROVIDER"))) }
        val unknownDocument = JsonObject(known + ("album_group_v1" to unknownGroup))
        write("one", unknownDocument)
        assertTrue(AlbumGroupRepository(db).snapshot(profile).isEmpty())
        val projected = db.trackMetadataDao().get(profile, "one")!!.decoded().payload
        db.close(); db = AutPlayDatabase.open(context, name)
        assertEquals(projected, db.trackMetadataDao().get(profile, "one")!!.decoded().payload)
        assertEquals(unknownGroup, db.trackMetadataDao().get(profile, "one")!!.decoded().payload["album_group_v1"])
        assertEquals(untouched, protectedCounts())
    }

    @Test fun removingTheMetadataRowInvalidatesGroupAndRestoresRawSearchDescription(): Unit = runBlocking {
        seed("one")
        val untouched = protectedCounts()
        val updates = Channel<List<MetadataAlbum>>(Channel.UNLIMITED)
        val collection = launch(Dispatchers.Default) { AlbumGroupRepository(db).observe(profile).collect { updates.send(it) } }
        try {
            await(updates) { it.size == 1 }
            db.withWriteTransaction {
                db.useWriterConnection { connection ->
                    connection.usePrepared("DELETE FROM track_metadata_projection WHERE server_profile_id=? AND local_user_track_ref_id=?") {
                        it.bindText(1, profile); it.bindText(2, "one"); it.step()
                    }
                }
                db.refreshTrackSearch("one")
            }
            await(updates) { it.isEmpty() }
            assertNull(db.trackMetadataDao().get(profile, "one"))
            assertEquals("Raw one", db.searchDao().contentForTrack("one")!!.title)
            assertEquals(untouched, protectedCounts())
        } finally { collection.cancelAndJoin(); updates.close() }
    }

    private suspend fun seed(id: String, owner: String = profile, identity: String = "edition-1",
        albumArtist: String? = "Album artist", track: Int? = 1) {
        db.withWriteTransaction {
            db.libraryDao().upsertTrackRef(ref(id, owner))
            db.libraryDao().upsertEntry(entry("entry-$id", id, owner))
            assertTrue(db.projectMetadata(owner, id, payload(identity, albumArtist, track)))
            db.refreshTrackSearch(id)
        }
    }

    private suspend fun write(id: String, document: JsonObject, timestamp: Long? = null) {
        db.withWriteTransaction {
            assertTrue(db.projectMetadata(profile, id, document))
            if (timestamp != null) db.trackMetadataDao().upsert(db.trackMetadataDao().get(profile, id)!!.copy(updatedAtMs = timestamp))
            db.refreshTrackSearch(id)
        }
    }

    private fun ref(id: String, owner: String) = UserTrackRefEntity(id, "server-$id", null, null, "UNRESOLVED",
        "Raw $id", "Raw artist", "Raw album", 20_000, null, "DIRTY", 1, 8, 1, 1, null, owner)
    private fun entry(id: String, track: String, owner: String) = LibraryEntryEntity(id, null, track, 1,
        "VAULT", "AVAILABLE", "DIRTY", 1, 8, null, 1, owner)

    private fun payload(identity: String = "edition-1", albumArtist: String? = "Album artist", track: Int? = 1,
        revision: Int = 1, group: Boolean = true, clearAlbum: Boolean = false) = buildJsonObject {
        put("revision", revision); put("state", "READY"); put("future_root", "preserve")
        put("fields", buildJsonObject {
            put("title", "Effective title"); put("artist", "Track performer")
            put("album", if (clearAlbum) JsonNull else JsonPrimitive("Same album"))
            put("album_artist", albumArtist?.let(::JsonPrimitive) ?: JsonNull)
            put("genres", buildJsonArray { add("Rock") }); put("release_date", "2024")
            put("disc_number", 1); put("track_number", track?.let(::JsonPrimitive) ?: JsonNull)
        })
        put("provenance", buildJsonObject {
            put("album", buildJsonObject { put("source", "SOURCE_NATIVE"); put("source_id", "bandcamp:$identity")
                put("normalization_version", "source-native-v1"); put("observed_at", "2026-10-06T10:00:00Z"); put("locked", false) })
            put("title", buildJsonObject { put("source", "FUTURE_PROVIDER"); put("future_proof", "preserve") })
        })
        if (group) put("album_group_v1", buildJsonObject {
            put("schema_version", 1); put("provider", "BANDCAMP"); put("release_id", identity)
            put("key", "native:bandcamp:album:$identity"); put("title", "Same album")
            put("album_artist", albumArtist?.let(::JsonPrimitive) ?: JsonNull); put("release_date", "2024")
            put("disc_number", 1); put("track_number", track?.let(::JsonPrimitive) ?: JsonNull)
            put("evidence", buildJsonObject { put("source", "SOURCE_NATIVE"); put("source_id", "bandcamp:$identity")
                put("normalization_version", "source-native-v1"); put("observed_at", "2026-10-06T10:00:00Z"); put("locked", false) })
        })
    }

    private suspend fun await(channel: Channel<List<MetadataAlbum>>, predicate: (List<MetadataAlbum>) -> Boolean): List<MetadataAlbum> =
        withTimeout(15_000) { var value = channel.receive(); while (!predicate(value)) value = channel.receive(); value }

    private data class ProtectedSnapshot(val counts: List<Long>, val event: OfflineJournalEventEntity,
        val hash: List<Byte>, val outbox: LocalMutationOutboxEntity)
    private suspend fun protectedCounts(): ProtectedSnapshot {
        val counts = db.useWriterConnection { connection ->
            listOf("recording_projection", "release_projection", "release_track_projection", "offline_journal_event", "local_mutation_outbox")
            .map { table -> connection.usePrepared("SELECT COUNT(*) FROM $table") { statement -> check(statement.step()); statement.getLong(0) } }
        }
        val event = db.journalDao().event(UUID(0, 9).toString())!!
        return ProtectedSnapshot(counts, event.copy(requestHash = ignoredHash), event.requestHash.toList(),
            db.journalDao().outbox(UUID(0, 3).toString())!!)
    }
}
