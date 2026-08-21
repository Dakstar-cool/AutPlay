package app.autplay.application.playback

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import app.autplay.playback.PlaybackSessionOwnerBinding
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PlaybackPersistenceRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val databaseName = "autplay-p08-playback.db"
    private lateinit var database: AutPlayDatabase

    @Before fun setUp() {
        context.deleteDatabase(databaseName)
        database = AutPlayDatabase.open(context, databaseName)
    }

    @After fun tearDown() {
        database.close()
        context.deleteDatabase(databaseName)
    }

    @Test
    fun repeatedTrackQueueAndAttributionSessionRestoreFinalizeExactlyOnce() = runBlocking {
        val trackId = id(1)
        val recordingId = uuid(2)
        database.libraryDao().upsertTrackRef(track(trackId, recordingId, uuid(8)))
        val snapshotId = id(3)
        val firstEntry = id(4)
        val secondEntry = id(5)
        val attribution = """{
            "recommendation_request_id":"${uuid(6)}",
            "recording_id":"$recordingId",
            "source_rank":2,
            "source":"home",
            "surface":"for_you",
            "impression_event_local_id":"${uuid(7)}",
            "impression_event_server_id":null,
            "section_key":"fresh_mix"
        }""".trimIndent()
        val repository = PlaybackPersistenceRepository(database)
        repository.activateQueue(
            snapshotId,
            listOf(
                NewPlaybackQueueEntry(firstEntry, trackId, "RECOMMENDED", "LOCAL_THEN_VAULT", attribution),
                NewPlaybackQueueEntry(secondEntry, trackId, "RECOMMENDED", "LOCAL_THEN_VAULT", attribution),
            ),
            "USER",
            null,
            uuid(8),
            "GENERAL",
            100,
        )
        val binding = ClientEventBinding(UserId(uuid(9)), DeviceId(uuid(10)), ServerProfileId(uuid(8)))
        val owner = PlaybackSessionOwnerBinding(
            binding.userId.value,
            binding.deviceId.value,
            binding.serverProfileId.value,
        )
        val started = repository.startSession(secondEntry, 20, 110, owner)
        val checkpointed = repository.checkpoint(started, 800, 90, "OFF", "OFF", null, 120)
        val eventId = checkpointed.listeningEventId

        database.close()
        database = AutPlayDatabase.open(context, databaseName)
        val restoredRepository = PlaybackPersistenceRepository(database)
        val restoredQueue = requireNotNull(restoredRepository.restoreActive())
        assertEquals(listOf(firstEntry.value, secondEntry.value), restoredQueue.media.items.map { it.mediaId })
        assertEquals(1, restoredQueue.media.currentIndex)
        val rawAttribution = requireNotNull(restoredQueue.entries[1].recommendationAttributionJson)
        assertEquals(true, rawAttribution.contains("\"section_key\":\"fresh_mix\""))
        val restoredSession = requireNotNull(restoredRepository.recoverSession())
        assertEquals(eventId, restoredSession.listeningEventId)
        assertEquals(id(7), restoredSession.attribution?.localImpressionId)
        assertEquals(owner, restoredSession.ownerBinding)

        restoredRepository.finalizeSession(restoredSession, 850, 1_000, 10, 130)
        val firstEvent = requireNotNull(database.journalDao().event(eventId.value))
        restoredRepository.finalizeSession(restoredSession, 850, 1_000, 10, 131)
        val retried = requireNotNull(database.journalDao().event(eventId.value))

        assertEquals(1, database.journalDao().eventCount())
        assertEquals(100L, database.historyDao().event(eventId.value)?.playedMs)
        assertArrayEquals(firstEvent.requestHash, retried.requestHash)
        assertNull(database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
        assertNotNull(database.historyDao().event(eventId.value)?.recommendationAttributionJson)
        Unit
    }

    @Test
    fun queueCanStartFromSelectedDuplicateOccurrenceWithoutChangingOrder() = runBlocking {
        val trackId = id(71)
        database.libraryDao().upsertTrackRef(track(trackId, uuid(72)))
        val firstEntry = id(73)
        val selectedDuplicate = id(74)
        val repository = PlaybackPersistenceRepository(database)

        repository.activateQueue(
            snapshotId = id(75),
            entries = listOf(
                NewPlaybackQueueEntry(firstEntry, trackId, "PLAYLIST", "LOCAL_THEN_VAULT"),
                NewPlaybackQueueEntry(selectedDuplicate, trackId, "PLAYLIST", "LOCAL_THEN_VAULT"),
            ),
            queueType = "PLAYLIST",
            sourceContextId = id(76).value,
            serverProfileId = null,
            listeningContext = "GENERAL",
            nowMs = 100,
            startEntryId = selectedDuplicate,
        )

        val restored = requireNotNull(repository.restoreActive())
        assertEquals(listOf(firstEntry.value, selectedDuplicate.value), restored.entries.map { it.queueEntryId })
        assertEquals(1, restored.media.currentIndex)
        Unit
    }

    @Test
    fun replacedQueueFinalizesAgainstCapturedOwnerAndOriginalSnapshot() = runBlocking {
        val trackId = id(31)
        database.libraryDao().upsertTrackRef(track(trackId, uuid(32), uuid(37)))
        val repository = PlaybackPersistenceRepository(database)
        val firstSnapshot = id(33)
        val firstEntry = id(34)
        val owner = PlaybackSessionOwnerBinding(uuid(35), uuid(36), uuid(37))
        repository.activateQueue(
            firstSnapshot,
            listOf(NewPlaybackQueueEntry(firstEntry, trackId, "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, owner.serverProfileId, "FIRST_CONTEXT", 10,
        )
        val session = repository.startSession(firstEntry, 0, 11, owner)
        repository.activateQueue(
            id(38),
            listOf(NewPlaybackQueueEntry(id(39), trackId, "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, uuid(40), "SECOND_CONTEXT", 12,
        )

        repository.finalizeSession(session, 100, 1_000, 100, 13)

        val journal = requireNotNull(database.journalDao().event(session.listeningEventId.value))
        assertEquals(owner.userId, journal.userId)
        assertEquals(owner.deviceId, journal.deviceId)
        assertEquals(owner.serverProfileId, journal.serverProfileId)
        assertEquals("FIRST_CONTEXT", database.historyDao().event(session.listeningEventId.value)?.context)
        assertNull(database.queueDao().snapshot(firstSnapshot.value)?.activeListeningEventId)
        assertEquals(id(38).value, database.queueDao().activeSnapshotOnce()?.queueSnapshotId)
        Unit
    }

    @Test
    fun startupRecoveryFinalizesInactiveSessionAfterQueueReplacementCrashWindow() = runBlocking {
        val trackId = id(51)
        database.libraryDao().upsertTrackRef(track(trackId, uuid(52), uuid(57)))
        val repository = PlaybackPersistenceRepository(database)
        val oldSnapshot = id(53)
        val oldEntry = id(54)
        val owner = PlaybackSessionOwnerBinding(uuid(55), uuid(56), uuid(57))
        repository.activateQueue(
            oldSnapshot,
            listOf(NewPlaybackQueueEntry(oldEntry, trackId, "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, owner.serverProfileId, "CAPTURED_CONTEXT", 10,
        )
        val session = repository.startSession(oldEntry, 10, 11, owner)
        repository.checkpoint(session, 90, 80, "OFF", "OFF", null, 12)
        repository.activateQueue(
            id(58),
            listOf(NewPlaybackQueueEntry(id(59), trackId, "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, uuid(60), "NEW_CONTEXT", 13,
        )

        database.close()
        database = AutPlayDatabase.open(context, databaseName)
        val recovered = PlaybackPersistenceRepository(database)
        requireNotNull(recovered.restoreActive(14))

        val history = requireNotNull(database.historyDao().event(session.listeningEventId.value))
        val journal = requireNotNull(database.journalDao().event(session.listeningEventId.value))
        assertEquals(80L, history.playedMs)
        assertEquals("CAPTURED_CONTEXT", history.context)
        assertEquals(owner.userId, journal.userId)
        assertEquals(owner.deviceId, journal.deviceId)
        assertEquals(owner.serverProfileId, journal.serverProfileId)
        assertNull(database.queueDao().snapshot(oldSnapshot.value)?.activeListeningEventId)
        assertEquals(1, database.journalDao().eventCount())
        recovered.restoreActive(15)
        assertEquals(1, database.journalDao().eventCount())
        Unit
    }

    private fun track(
        id: LocalId,
        recordingId: String,
        serverProfileId: String = "legacy-unscoped",
    ) = UserTrackRefEntity(
        localUserTrackRefId = id.value,
        serverUserTrackRefId = uuid(20),
        localRecordingId = null,
        serverRecordingId = recordingId,
        resolutionStatus = "RESOLVED",
        rawTitle = "P08 track",
        rawArtist = "AutPlay",
        rawAlbum = null,
        rawDurationMs = 1_000,
        resolutionConfidence = 1.0,
        syncState = "CLEAN",
        serverRowVersion = 1,
        lastLocalSequence = 0,
        createdAtMs = 1,
        updatedAtMs = 1,
        deletedAtMs = null,
        serverProfileId = serverProfileId,
    )

    private fun id(seed: Int) = LocalId(uuid(seed))
    private fun uuid(seed: Int): String = UUID(0, seed.toLong()).toString()
}
