package app.autplay.application.history

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.ListeningEventEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class HistoryRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val databaseName = "history-parity.db"
    private lateinit var database: AutPlayDatabase

    @Before fun setUp() {
        context.deleteDatabase(databaseName)
        database = AutPlayDatabase.open(context, databaseName)
    }

    @After fun tearDown() {
        database.close()
        context.deleteDatabase(databaseName)
    }

    @Test fun keysetPagesAreBoundedStableAndKeepDuplicateListens() = runBlocking {
        val profile = "legacy-unscoped"
        val trackId = uuid(1)
        database.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = trackId,
                serverUserTrackRefId = null,
                localRecordingId = null,
                serverRecordingId = null,
                resolutionStatus = "UNRESOLVED",
                rawTitle = "Repeated track",
                rawArtist = "Artist",
                rawAlbum = null,
                rawDurationMs = 1_000,
                resolutionConfidence = null,
                syncState = "LOCAL_ONLY",
                serverRowVersion = null,
                lastLocalSequence = 0,
                createdAtMs = 1,
                updatedAtMs = 1,
                deletedAtMs = null,
                serverProfileId = profile,
            ),
        )
        listOf(uuid(11) to 200L, uuid(12) to 200L, uuid(13) to 100L).forEach { (eventId, startedAt) ->
            database.historyDao().insert(event(eventId, trackId, profile, startedAt))
        }
        val repository = HistoryRepository(database)

        val first = repository.loadPage(null, pageSize = 2)
        assertNotNull(first.nextCursor)
        val second = repository.loadPage(null, cursor = requireNotNull(first.nextCursor), pageSize = 2)

        assertEquals(listOf(uuid(12), uuid(11)), first.items.map { it.listeningEventId })
        assertEquals(listOf(uuid(13)), second.items.map { it.listeningEventId })
        assertEquals(3, (first.items + second.items).size)
        assertEquals(listOf(trackId, trackId, trackId), (first.items + second.items).map { it.localUserTrackRefId })
    }

    private fun event(id: String, trackId: String, profile: String, startedAt: Long) = ListeningEventEntity(
        listeningEventId = id,
        localUserTrackRefId = trackId,
        serverRecordingId = null,
        startedAtMs = startedAt,
        playedMs = 50,
        trackDurationMs = 1_000,
        completionRatio = .05,
        eventOrigin = "ORGANIC",
        context = "GENERAL",
        recommendationRequestId = null,
        explicitFeedback = "NONE",
        excludedFromTaste = false,
        syncState = "LOCAL_ONLY",
        createdAtMs = startedAt,
        serverProfileId = profile,
    )

    private fun uuid(seed: Int): String = java.util.UUID(0, seed.toLong()).toString()
}
