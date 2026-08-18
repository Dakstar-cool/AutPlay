package app.autplay.application.library

import android.os.SystemClock
import androidx.room3.withWriteTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.importing.ContentUriInspection
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.importing.ContentUriStatus
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.PlaylistEntryEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import app.autplay.domain.library.PlaylistPositionKeys
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LibraryVerticalSliceRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "p07-vertical-slice-test.db"
    private lateinit var database: AutPlayDatabase

    @Before fun setUp() { context.deleteDatabase(name); database = AutPlayDatabase.open(context, name) }
    @After fun tearDown() { database.close(); context.deleteDatabase(name) }

    @Test fun standaloneMutationRollsBackAggregateAndOutboxOnInjectedFailure() = runBlocking {
        val ids = ids(1)
        LocalLibraryCommandRepository(database).add(AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1))
        val failing = LibraryVerticalSliceRepository(database, SliceFailureInjector { throw IllegalStateException("injected") })
        runCatching { failing.removeLibrary(null, ids.entry, id(10), 2) }
        assertNull(database.libraryDao().entry(ids.entry.value)?.removedAtMs)
        assertEquals(1, database.journalDao().outboxCount()) // only the initial add
    }

    @Test fun offlineRemoveAndRestoreCommitAsDistinctAtomicIntents() = runBlocking {
        val ids = ids(10)
        LocalLibraryCommandRepository(database).add(
            AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1),
        )
        val repository = LibraryVerticalSliceRepository(database)
        repository.removeLibrary(null, ids.entry, id(14), 2)
        assertEquals(2L, database.libraryDao().entry(ids.entry.value)?.removedAtMs)
        repository.restoreLibrary(null, ids.entry, id(15), 3)
        assertNull(database.libraryDao().entry(ids.entry.value)?.removedAtMs)
        assertEquals(3, database.journalDao().outboxCount())
    }

    @Test fun everyP07AggregateUsesTheSharedRollbackBoundary() = runBlocking {
        val ids = ids(90)
        val regular = LibraryVerticalSliceRepository(database)
        LocalLibraryCommandRepository(database).add(
            AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1),
        )
        val failing = LibraryVerticalSliceRepository(
            database,
            SliceFailureInjector { throw IllegalStateException("injected") },
        )
        runCatching { failing.setPreference(null, ids.track, id(94), "LIKED", false, null, 2) }
        assertNull(database.libraryDao().preference(ids.track.value))
        val playlist = id(95)
        runCatching { failing.createPlaylist(null, playlist, id(96), "P", null, 3) }
        assertNull(database.playlistDao().playlist(playlist.value))
        regular.createPlaylist(null, playlist, id(97), "P", null, 4)
        val playlistEntry = id(98)
        runCatching {
            failing.addPlaylistEntry(null, playlist, playlistEntry, ids.track, id(99), null, null, 5)
        }
        assertNull(database.playlistDao().entry(playlistEntry.value))
        val listening = id(100)
        runCatching {
            failing.recordListening(null, listening, ids.track, 1, null, false, "ORGANIC", now = 6)
        }
        assertNull(database.historyDao().event(listening.value))
        val imported = id(101)
        runCatching {
            failing.importUri(
                null,
                imported,
                id(102),
                id(103),
                id(104),
                "Import",
                "Artist",
                ContentUriInspection(
                    "content://missing.autplay.test/audio/101",
                    ContentUriStatus.MISSING,
                    null,
                    null,
                ),
                false,
                7,
            )
        }
        assertNull(database.libraryDao().trackRef(imported.value))
        assertEquals(2, database.journalDao().outboxCount())
    }

    @Test fun boundPreferenceProducesOneJournalEventAndNoDuplicateInteractionFact() = runBlocking {
        val ids = ids(20)
        val binding = binding()
        LocalLibraryCommandRepository(database).add(AddLocalTrackCommand(binding, ids.track, ids.entry, ids.change, "Title", "Artist", 1))
        val attribution = "{\"recommendation_request_id\":\"00000000-0000-0000-0000-000000000201\",\"recording_id\":\"00000000-0000-0000-0000-000000000202\",\"source_rank\":1,\"source\":\"p07\",\"surface\":\"library\",\"future\":\"x\"}"
        val result = LibraryVerticalSliceRepository(database).setPreference(binding, ids.track, id(21), "LIKED", false, attribution, 2)
        assertTrue(result.journaled)
        assertEquals(2, database.journalDao().eventCount())
        assertEquals(0, database.journalDao().outboxCount())
        assertEquals("LIKED", database.libraryDao().preference(ids.track.value)?.preference)
    }

    @Test fun listeningUsesTheSameSpecializedAggregateAndEventId() = runBlocking {
        val ids = ids(30)
        LocalLibraryCommandRepository(database).add(AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1))
        val listeningId = id(31)
        val result = LibraryVerticalSliceRepository(database).recordListening(null, listeningId, ids.track, 100, 200, true, "ORGANIC", now = 2)
        assertEquals(listeningId, result.changeId)
        assertEquals(listeningId.value, database.historyDao().recent(1).first().first().listeningEventId)
        assertEquals(listeningId.value, database.journalDao().outbox(listeningId.value)?.aggregateLocalId)
    }

    @Test fun importedUriCreatesUnresolvedIntentAndIsSearchable() = runBlocking {
        val repository = LibraryVerticalSliceRepository(database)
        val track = id(40)
        repository.importUri(null, track, id(41), id(42), id(43), "Cyrillic Импорт", "Artist", ContentUriInspection("content://test.provider/audio/40", ContentUriStatus.MISSING, "track.mp3", 1), false, 1)
        assertEquals("UNRESOLVED", database.libraryDao().trackRef(track.value)?.resolutionStatus)
        assertEquals("NOT_FOUND", database.libraryDao().entry(id(41).value)?.availabilityStatus)
        assertEquals(listOf(track.value), LocalTrackSearchRepository(database).search("Импорт").map { it.localUserTrackRefId })
    }

    @Test fun revokedContentPermissionIsARepairableState() {
        val inspection = ContentUriInspector(context.contentResolver).inspect(
            "content://app.autplay.test.revoked/audio/1",
        )
        assertEquals(ContentUriStatus.PERMISSION_REVOKED, inspection.status)
    }

    @Test fun duplicatePlaylistEntriesRemainDistinctAcrossReorder() = runBlocking {
        val ids = ids(50)
        LocalLibraryCommandRepository(database).add(AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1))
        val repository = LibraryVerticalSliceRepository(database)
        val playlist = id(54)
        repository.createPlaylist(null, playlist, id(55), "P", null, 2)
        repository.addPlaylistEntry(null, playlist, id(56), ids.track, id(57), null, null, 3)
        repository.addPlaylistEntry(null, playlist, id(58), ids.track, id(59), null, null, 4)
        repository.reorderPlaylistEntry(null, id(58), id(56), id(60), 5)
        val entries = database.playlistDao().activeEntryList(playlist.value, 10)
        assertEquals(2, entries.size)
        assertEquals(ids.track.value, entries[0].localUserTrackRefId)
        assertEquals(ids.track.value, entries[1].localUserTrackRefId)
        assertTrue(entries[0].activePositionKey!! < entries[1].activePositionKey!!)
        val addPayload = requireNotNull(database.journalDao().outbox(id(59).value)).payloadJson
        assertTrue(addPayload.contains("\"local_playlist_id\":\"${playlist.value}\""))
        assertTrue(addPayload.contains("\"before_local_playlist_entry_id\":null"))
    }

    @Test fun organicAndAttributedOwningEventsRemainDistinctAfterRestart() = runBlocking {
        val ids = ids(110)
        val repository = LibraryVerticalSliceRepository(database)
        LocalLibraryCommandRepository(database).add(
            AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Title", "Artist", 1),
        )
        repository.setPreference(null, ids.track, id(114), "LIKED", false, null, 2)
        repository.setPreference(null, ids.track, id(115), "LIKED", false, attribution(115), 3)
        val playlist = id(116)
        repository.createPlaylist(null, playlist, id(117), "P", null, 4)
        repository.addPlaylistEntry(null, playlist, id(118), ids.track, id(119), null, null, 5)
        repository.addPlaylistEntry(
            null,
            playlist,
            id(120),
            ids.track,
            id(121),
            null,
            attribution(121),
            6,
        )

        database.close()
        database = AutPlayDatabase.open(context, name)
        val organicPreference = requireNotNull(database.journalDao().outbox(id(114).value))
        val attributedPreference = requireNotNull(database.journalDao().outbox(id(115).value))
        val organicPlaylist = requireNotNull(database.journalDao().outbox(id(119).value))
        val attributedPlaylist = requireNotNull(database.journalDao().outbox(id(121).value))
        assertTrue(!organicPreference.payloadJson.contains("recommendation_request_id"))
        assertTrue(attributedPreference.payloadJson.contains("recommendation_request_id"))
        assertTrue(!organicPlaylist.payloadJson.contains("recommendation_request_id"))
        assertTrue(attributedPlaylist.payloadJson.contains("recommendation_request_id"))
        assertEquals(6, database.journalDao().outboxCount())
    }

    @Test fun largeSearchAndPlaylistQueriesMeetApi26Baseline() = runBlocking {
        val ids = ids(70)
        LocalLibraryCommandRepository(database).add(
            AddLocalTrackCommand(null, ids.track, ids.entry, ids.change, "Benchmark anchor", "Artist", 1),
        )
        val repository = LibraryVerticalSliceRepository(database)
        val playlist = id(74)
        repository.createPlaylist(null, playlist, id(75), "Benchmark", null, 2)
        database.withWriteTransaction {
            database.searchDao().insertContents(
                (0 until 10_000).map { index ->
                    TrackSearchContentEntity(
                        localUserTrackRefId = id(10_000 + index).value,
                        title = "Benchmark track $index",
                        artist = "Artist",
                        album = null,
                        aliases = null,
                        transliterations = null,
                    )
                },
            )
            database.playlistDao().upsertEntries(
                (0 until 1_000).map { index ->
                    val key = PlaylistPositionKeys.initial(index)
                    PlaylistEntryEntity(
                        localPlaylistEntryId = id(30_000 + index).value,
                        serverPlaylistEntryId = null,
                        localPlaylistId = playlist.value,
                        localUserTrackRefId = ids.track.value,
                        positionKey = key,
                        activePositionKey = key,
                        sourcePosition = index.toLong(),
                        addedAtMs = 3,
                        syncState = "LOCAL_ONLY",
                        serverRowVersion = null,
                        lastLocalSequence = 0,
                        removedAtMs = null,
                    )
                },
            )
        }

        database.searchDao().search("\"benchmark\"*", 50)
        database.playlistDao().activeEntryList(playlist.value, 1_000)
        val searchSamples = DoubleArray(30)
        val playlistSamples = DoubleArray(30)
        repeat(30) { sample ->
            val searchStart = SystemClock.elapsedRealtimeNanos()
            val search = database.searchDao().search("\"benchmark\"*", 50)
            searchSamples[sample] = (SystemClock.elapsedRealtimeNanos() - searchStart) / 1_000_000.0
            val playlistStart = SystemClock.elapsedRealtimeNanos()
            val entries = database.playlistDao().activeEntryList(playlist.value, 1_000)
            playlistSamples[sample] = (SystemClock.elapsedRealtimeNanos() - playlistStart) / 1_000_000.0
            assertEquals(50, search.size)
            assertEquals(1_000, entries.size)
        }
        val searchP50 = percentile(searchSamples, 0.50)
        val searchP95 = percentile(searchSamples, 0.95)
        val searchP99 = percentile(searchSamples, 0.99)
        val playlistP50 = percentile(playlistSamples, 0.50)
        val playlistP95 = percentile(playlistSamples, 0.95)
        val playlistP99 = percentile(playlistSamples, 0.99)
        println(
            "P14_ANDROID_PERFORMANCE search_p50_ms=$searchP50 search_p95_ms=$searchP95 " +
                "search_p99_ms=$searchP99 playlist_p50_ms=$playlistP50 " +
                "playlist_p95_ms=$playlistP95 playlist_p99_ms=$playlistP99",
        )
        assertTrue("FTS top-50 p95 took ${searchP95}ms", searchP95 <= 150.0)
        assertTrue("Playlist 1,000 p95 took ${playlistP95}ms", playlistP95 <= 150.0)
    }

    private data class Ids(val track: LocalId, val entry: LocalId, val change: LocalId)
    private fun ids(seed: Int) = Ids(id(seed), id(seed + 1), id(seed + 2))
    private fun id(seed: Int) = LocalId(UUID(0, seed.toLong()).toString())
    private fun binding() = ClientEventBinding(UserId(UUID(0, 100).toString()), DeviceId(UUID(0, 101).toString()), ServerProfileId(UUID(0, 102).toString()))
    private fun attribution(seed: Int): String =
        "{\"recommendation_request_id\":\"${UUID(0, seed.toLong())}\",\"recording_id\":\"${UUID(1, seed.toLong())}\",\"source_rank\":1,\"source\":\"p07\",\"surface\":\"library\"}"
    private fun percentile(samples: DoubleArray, fraction: Double): Double {
        val sorted = samples.sorted()
        val index = kotlin.math.ceil(sorted.size * fraction).toInt().coerceIn(1, sorted.size) - 1
        return sorted[index]
    }
}
