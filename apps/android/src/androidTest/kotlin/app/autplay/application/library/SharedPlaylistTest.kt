package app.autplay.application.library

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.security.CredentialStore
import app.autplay.domain.*
import java.net.URL
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SharedPlaylistTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "share-acceptance-${UUID.randomUUID()}.db"
    private lateinit var db: AutPlayDatabase
    private val recipient = binding()

    private fun binding() = ClientEventBinding(UserId(UUID.randomUUID().toString()),
        DeviceId(UUID.randomUUID().toString()), ServerProfileId(UUID.randomUUID().toString()))

    @Before fun open() { db = AutPlayDatabase.open(context, name) }
    @After fun close() { db.close(); context.deleteDatabase(name) }

    @Test fun portableCopyRollsBackAndReopensWithoutAudio() = runBlocking {
        val document = SharedPlaylist.decode("""{"format":"autplay-playlist-v1","token":"${UUID.randomUUID()}",
            "name":"Live","description":null,"tracks":[{"title":"Phone only","artist":"A",
            "duration_ms":42,"recording_id":null}],"entries":[0,0]}""".toByteArray())
        val playlist = LocalId.random()
        var writes = 0
        val interrupted = LibraryVerticalSliceRepository(db, SliceFailureInjector { if (++writes == 4) error("interrupt") })
        assertTrue(runCatching { interrupted.saveSharedPlaylist(recipient, playlist, document, 1) }.isFailure)
        assertNull(db.playlistDao().playlist(playlist.value))
        LibraryVerticalSliceRepository(db).saveSharedPlaylist(recipient, playlist, document, 2)
        db.close()
        db = AutPlayDatabase.open(context, name)
        LibraryVerticalSliceRepository(db).saveSharedPlaylist(recipient, playlist, document, 3)
        val entries = CoreProductRepository(db).playlistDetail(playlist.value, recipient.serverProfileId.value)!!.entries
        assertEquals(listOf("Phone only", "Phone only"), entries.map { it.title })
        assertEquals(2, entries.distinctBy { it.localPlaylistEntryId }.size)
        assertEquals(1, entries.distinctBy { it.localUserTrackRefId }.size)
        assertTrue(entries.all { it.unavailable })
        assertNull(db.libraryDao().entryForTrack(entries.first().localUserTrackRefId))
        assertNull(db.libraryDao().preference(entries.first().localUserTrackRefId))
    }

    @Test fun standaloneIntentsMaterializeParentsAndRepeatedEntriesInOrder() = runBlocking {
        val copy = SharedPlaylist("autplay-playlist-v1", UUID.randomUUID().toString(), "Local", null,
            listOf(SharedPlaylistTrack("First", "Artist", 10L, null),
                SharedPlaylistTrack("Second", "Artist", 11L, null)), listOf(1, 0, 1))
        val id = LocalId.random()
        val repository = LibraryVerticalSliceRepository(db)
        repository.saveSharedPlaylist(null, id, copy, 10)
        val intents = db.journalDao().pendingOutbox(10)
        assertEquals(listOf("PLAYLIST", "USER_TRACK_REF", "USER_TRACK_REF", "PLAYLIST_ENTRY",
            "PLAYLIST_ENTRY", "PLAYLIST_ENTRY"), intents.map { it.aggregateType })
        val rows = db.playlistDao().activeEntryList(id.value, 10)
        assertEquals(rows.map { it.localPlaylistEntryId }, intents.takeLast(3).map { it.aggregateLocalId })
        intents.forEach { repository.materializeStandalone(recipient, LocalId(it.localChangeId), LocalId.random(), 20) }
        val detail = CoreProductRepository(db).playlistDetail(id.value, recipient.serverProfileId.value)!!
        assertEquals(listOf("Second", "First", "Second"), detail.entries.map { it.title })
    }

    /** Run against the disposable API through an explicitly supplied adb reverse port. */
    @Test fun publishEditDeleteAndOpenOnFreshProfile() = runBlocking {
        val port = InstrumentationRegistry.getArguments().getString("playlistShareQaPort")?.toIntOrNull()
        Assume.assumeTrue(context.packageName == "app.autplay.qa" && port != null && port in 1024..65535)
        val sender = binding()
        val origin = "http://127.0.0.1:$port"
        val credentials = object : CredentialStore {
            override suspend fun read(profileId: ServerProfileId): ByteArray? =
                if (profileId == sender.serverProfileId) "qa-playlist-publisher".toByteArray() else null
            override suspend fun write(profileId: ServerProfileId, material: ByteArray) = error("Unexpected rotation")
            override suspend fun clear(profileId: ServerProfileId) = error("Unexpected clear")
        }
        val api = ServerFeatureRepository(origin, origin, sender.serverProfileId, credentials)
        val tracks = listOf("First", "Second").map { title ->
            val id = LocalId.random()
            LocalLibraryCommandRepository(db).add(AddLocalTrackCommand(sender, id, LocalId.random(),
                LocalId.random(), title, "Artist", 1))
            id
        }
        val source = LocalId.random()
        val repository = LibraryVerticalSliceRepository(db)
        repository.createPlaylist(sender, source, LocalId.random(), "Mix", null, 2)
        val items = (tracks + tracks.first()).map { ref ->
            LocalId.random().also { repository.addPlaylistEntry(sender, source, it, ref, LocalId.random(), null, null, 3) }
        }
        suspend fun share() = api.publishPlaylist(playlistPublication(db, source.value, sender.serverProfileId.value))
        fun download(link: String) = SharedPlaylist.decode(URL("$link/file").readBytes())
        val original = share()
        assertEquals(original, share())
        val before = download(original)
        val saved = repository.saveSharedPlaylist(recipient, LocalId.random(), before, 4)
        repository.reorderPlaylistEntry(sender, items[1], items[0], LocalId.random(), 5)
        val edited = share()
        assertNotEquals(original, edited)
        repository.deletePlaylist(sender, source, LocalId.random(), 6)
        assertNull(CoreProductRepository(db).playlistDetail(source.value, sender.serverProfileId.value))
        db.close()
        db = AutPlayDatabase.open(context, name)
        val late = download(original)
        assertEquals(before, late)
        assertTrue(URL(original).readText().contains("Mix"))
        val laterRecipient = binding()
        val other = LibraryVerticalSliceRepository(db).saveSharedPlaylist(laterRecipient, LocalId.random(), late, 7)
        for ((id, owner) in listOf(saved to recipient, other to laterRecipient)) {
            val detail = CoreProductRepository(db).playlistDetail(id.value, owner.serverProfileId.value)!!
            assertEquals(listOf("First", "Second", "First"), detail.entries.map { it.title })
            assertTrue(detail.entries.all { it.unavailable })
            assertEquals(3, detail.entries.distinctBy { it.localPlaylistEntryId }.size)
        }
        val changed = download(edited)
        assertEquals(listOf("Second", "First", "First"), changed.entries.map { changed.tracks[it].title })
    }
}
