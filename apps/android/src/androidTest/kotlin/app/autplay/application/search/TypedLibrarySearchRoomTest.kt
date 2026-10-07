package app.autplay.application.search

import androidx.room3.withWriteTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.library.projectMetadata
import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class TypedLibrarySearchRoomTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "typed-library-search-room-proof.db"
    private lateinit var db: AutPlayDatabase
    @Before fun open() { context.deleteDatabase(name); db = AutPlayDatabase.open(context, name) }
    @After fun close() { db.close(); context.deleteDatabase(name) }

    @Test fun allAndEverySelectedFieldMatchEffectiveMetadataInOnlyTheCurrentProfile() = runBlocking {
        seed("title", title = "Signal title", artist = "Other", album = "Other")
        seed("artist", title = "Other", artist = "Signal performer", album = "Other")
        seed("album", title = "Other", artist = "Other", album = "Signal edition")
        seed("foreign", owner = "foreign", title = "Signal title", artist = "Signal performer", album = "Signal edition")
        val repository = LocalTrackSearchRepository(db)
        fun expected(kind: LibrarySearchKind): Set<String> = when (kind) {
            LibrarySearchKind.All -> setOf("title", "artist", "album")
            LibrarySearchKind.Track -> setOf("title")
            LibrarySearchKind.Artist -> setOf("artist")
            LibrarySearchKind.Album -> setOf("album")
        }
        LibrarySearchKind.entries.forEach { kind ->
            val results = repository.search("signal", "owner", kind = kind)
            assertEquals(expected(kind), results.map { it.localUserTrackRefId }.toSet())
            assertTrue(results.none { it.localUserTrackRefId == "foreign" })
            assertTrue(results.none { it.rawTitle == "Raw title" || it.rawArtist == "Raw artist" })
        }
        db.close(); db = AutPlayDatabase.open(context, name)
        assertEquals(setOf("album"), LocalTrackSearchRepository(db).search("signal", "owner", kind = LibrarySearchKind.Album)
            .map { it.localUserTrackRefId }.toSet())
    }

    @Test fun explicitNullDoesNotFallBackAndCandidateDescriptionsAreNeverSearchable() = runBlocking {
        seed("one", title = "Effective", artist = null, album = null, rawAlbum = "Signal raw edition")
        db.withWriteTransaction {
            val document = buildJsonObject {
                put("revision", 2); put("state", "REVIEW")
                put("fields", buildJsonObject { put("title", JsonNull); put("artist", JsonNull); put("album", JsonNull) })
                put("candidates", buildJsonArray { add(buildJsonObject {
                    put("fields", buildJsonObject { put("title", "Signal candidate"); put("album", "Signal candidate edition") })
                }) })
            }
            assertTrue(db.projectMetadata("owner", "one", document)); db.refreshTrackSearch("one")
        }
        val repository = LocalTrackSearchRepository(db)
        LibrarySearchKind.entries.forEach { assertTrue(repository.search("signal", "owner", kind = it).isEmpty()) }
        assertTrue(repository.search("effective", "owner", kind = LibrarySearchKind.Track).isEmpty())
        val content = db.searchDao().contentForTrack("one")!!
        assertNull(content.title); assertNull(content.artist); assertNull(content.album)
    }

    @Test fun rankOrderSurvivesProjectionAndAllDoesNotSearchUnselectedAliasColumns() = runBlocking {
        seed("z-short", title = "Signal", artist = "Other", album = "Other")
        seed("a-long", title = "Signal extra extra extra extra extra", artist = "Other", album = "Other")
        val content = db.searchDao().contentForTrack("z-short")!!
        db.searchDao().updateContent(content.copy(aliases = "AliasExclusive"))
        val expression = SafeFtsQueryBuilder().build("signal", LibrarySearchKind.All)!!
        val order = db.searchDao().searchForProfile(expression, "owner", 50)
        assertEquals(2, order.size)
        assertEquals(order, LocalTrackSearchRepository(db).search("signal", "owner").map { it.localUserTrackRefId })
        assertTrue(LocalTrackSearchRepository(db).search("AliasExclusive", "owner").isEmpty())
        val ref = db.libraryDao().trackRef("z-short")!!
        db.libraryDao().upsertTrackRef(ref.copy(deletedAtMs = 10))
        assertEquals(listOf("a-long"), LocalTrackSearchRepository(db).search("signal", "owner").map { it.localUserTrackRefId })
    }

    @Test fun vaultRowsUseOnlyCurrentProfileEffectiveLabelsIncludingManualClearAndReopen() = runBlocking {
        seed("owned", title = "Effective title", artist = "Effective artist", album = "Edition")
        seed("foreign", owner = "foreign", title = "Private foreign title", artist = "Private foreign artist", album = "Edition")
        val owned = db.libraryDao().trackRef("owned")!!
        db.libraryDao().upsertTrackRef(owned.copy(serverUserTrackRefId = "server-owned"))
        db.libraryDao().upsertTrackRef(db.libraryDao().trackRef("foreign")!!.copy(serverUserTrackRefId = "server-foreign"))
        val remote = listOf(
            RemoteLibraryEntry("entry-owned", "server-owned", "VAULT", "VAULT", 1),
            RemoteLibraryEntry("entry-foreign", "server-foreign", "VAULT", "VAULT", 1),
        )
        var rows = VaultSearchProjector(db).project("owner", remote)
        assertEquals("Effective title", rows[0].title); assertEquals("Effective artist", rows[0].artist)
        assertEquals("owned", rows[0].localUserTrackRefId)
        assertNull(rows[1].title); assertNull(rows[1].artist); assertNull(rows[1].localUserTrackRefId)
        db.withWriteTransaction {
            assertTrue(db.projectMetadata("owner", "owned", buildJsonObject {
                put("revision", 2); put("state", "READY")
                put("fields", buildJsonObject { put("title", JsonNull); put("artist", JsonNull) })
            }))
        }
        db.close(); db = AutPlayDatabase.open(context, name)
        rows = VaultSearchProjector(db).project("owner", remote)
        assertNull(rows[0].title); assertNull(rows[0].artist)
        assertEquals("owned", rows[0].localUserTrackRefId)
        assertNull(rows[1].localUserTrackRefId)
    }

    private suspend fun seed(id: String, owner: String = "owner", title: String, artist: String?, album: String?,
        rawAlbum: String? = "Raw album") {
        db.withWriteTransaction {
            db.libraryDao().upsertTrackRef(UserTrackRefEntity(id, null, null, null, "UNRESOLVED", "Raw title",
                "Raw artist", rawAlbum, null, null, "DIRTY", 1, 5, 1, 1, null, owner))
            assertTrue(db.projectMetadata(owner, id, buildJsonObject {
                put("revision", 1); put("state", "READY"); put("fields", buildJsonObject {
                    put("title", title); put("artist", artist?.let(::JsonPrimitive) ?: JsonNull)
                    put("album", album?.let(::JsonPrimitive) ?: JsonNull)
                })
            }))
            db.refreshTrackSearch(id)
        }
    }
}
