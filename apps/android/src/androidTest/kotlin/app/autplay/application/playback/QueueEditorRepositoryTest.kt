package app.autplay.application.playback

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.LocalId
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class QueueEditorRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-l1-queue-editor.db"
    private lateinit var database: AutPlayDatabase
    private val commands = mutableListOf<PlaybackCommand>()
    private val owner = object : PlaybackSessionOwner { override suspend fun dispatch(command: PlaybackCommand) { commands += command } }

    @Before fun setUp() { context.deleteDatabase(name); database = AutPlayDatabase.open(context, name) }
    @After fun tearDown() { database.close(); context.deleteDatabase(name) }

    @Test fun createsQueueAndPreservesDuplicateStableEntriesAcrossPromotionAndReopen() = runBlocking {
        val profile = uuid(1); val track = id(2); val duplicate = id(3); val first = id(4)
        database.libraryDao().upsertTrackRef(track(track, profile))
        val editor = QueueEditorRepository(database, owner)
        val created = editor.addToEnd(NewPlaybackQueueEntry(first, track, "ORGANIC", "LOCAL_THEN_VAULT"), profile, null)
        assertTrue(created.created)
        val snapshot = created.snapshotId
        editor.addToEnd(NewPlaybackQueueEntry(duplicate, track, "ORGANIC", "LOCAL_THEN_VAULT"), profile, snapshot)
        val projection = requireNotNull(editor.loadActive(profile))
        assertEquals(listOf(first, duplicate).map { it.value }, projection.entries.map { it.queueEntryId.value })
        assertEquals("USER", projection.queueType)
        database.close(); database = AutPlayDatabase.open(context, name)
        assertEquals(listOf(first, duplicate).map { it.value }, requireNotNull(QueueEditorRepository(database, owner).loadActive(profile)).entries.map { it.queueEntryId.value })
        assertTrue(commands.last() is PlaybackCommand.RefreshQueue)
    }

    @Test fun addNextPromotesSearchAndRejectsCurrentPastWaveProfileStaleAndLimit() = runBlocking {
        val profile = uuid(10); val other = uuid(11); val track = id(12); val e1 = id(13); val e2 = id(14); val added = id(15)
        database.libraryDao().upsertTrackRef(track(track, profile))
        val persistence = PlaybackPersistenceRepository(database)
        val snapshot = id(16)
        persistence.activateQueue(snapshot, listOf(NewPlaybackQueueEntry(e1, track, "SEARCH", "LOCAL_THEN_VAULT"), NewPlaybackQueueEntry(e2, track, "SEARCH", "LOCAL_THEN_VAULT")), "SEARCH", "source", profile, "GENERAL", 1, startEntryId = e2)
        val editor = QueueEditorRepository(database, owner)
        editor.addNext(NewPlaybackQueueEntry(added, track, "SEARCH", "LOCAL_THEN_VAULT"), profile, snapshot)
        assertEquals("USER", database.queueDao().activeSnapshotOnce()?.queueType)
        assertEquals(listOf(e1, e2, added).map { it.value }, requireNotNull(editor.loadActive(profile)).entries.map { it.queueEntryId.value })
        assertFailure("QUEUE_ENTRY_NOT_UPCOMING") { editor.removeUpcoming(e1, profile, snapshot) }
        assertFailure("QUEUE_ENTRY_NOT_UPCOMING") { editor.removeUpcoming(e2, profile, snapshot) }
        assertFailure("QUEUE_PROFILE_MISMATCH") { editor.clearUpcoming(other, snapshot) }
        assertFailure("QUEUE_SNAPSHOT_STALE") { editor.clearUpcoming(profile, id(99)) }
        persistence.activateQueue(id(17), listOf(NewPlaybackQueueEntry(id(18), track, "WAVE", "PINNED")), "WAVE", null, profile, "GENERAL", 2)
        assertFailure("QUEUE_TYPE_NOT_EDITABLE") { editor.clearUpcoming(profile, id(17)) }
    }

    @Test fun duplicateAttributedEntriesSurviveMoveRemoveClearAndReopenInEditedOrder() = runBlocking {
        val profile = uuid(40); val track = id(41); val first = id(42); val duplicateA = id(43)
        val duplicateB = id(44); val tail = id(45); val request = uuid(46); val recording = uuid(47)
        database.libraryDao().upsertTrackRef(track(track, profile))
        val attribution = """{"recommendation_request_id":"$request","recording_id":"$recording","source_rank":1,"source":"home","surface":"for_you","impression_event_local_id":"${uuid(48)}"}"""
        val snapshot = id(49)
        PlaybackPersistenceRepository(database).activateQueue(
            snapshot,
            listOf(
                NewPlaybackQueueEntry(first, track, "ORGANIC", "LOCAL_THEN_VAULT"),
                NewPlaybackQueueEntry(duplicateA, track, "RECOMMENDED", "LOCAL_THEN_VAULT", attribution),
                NewPlaybackQueueEntry(duplicateB, track, "RECOMMENDED", "LOCAL_THEN_VAULT", attribution),
                NewPlaybackQueueEntry(tail, track, "ORGANIC", "LOCAL_THEN_VAULT"),
            ), "PLAYLIST", uuid(50), profile, "GENERAL", 1,
        )
        val editor = QueueEditorRepository(database, owner)
        editor.moveUpcoming(tail, duplicateA, profile, snapshot)
        editor.removeUpcoming(duplicateB, profile, snapshot)
        val afterRemove = requireNotNull(editor.loadActive(profile))
        assertEquals(listOf(first, tail, duplicateA).map { it.value }, afterRemove.entries.map { it.queueEntryId.value })
        assertEquals("USER", afterRemove.queueType)
        assertEquals(request, database.queueDao().entry(duplicateA.value)?.recommendationRequestId)
        assertTrue(requireNotNull(database.queueDao().entry(duplicateA.value)?.recommendationAttributionJson).contains(request))
        editor.clearUpcoming(profile, snapshot)
        assertEquals(listOf(first.value), requireNotNull(editor.loadActive(profile)).entries.map { it.queueEntryId.value })
        database.close(); database = AutPlayDatabase.open(context, name)
        val reopened = requireNotNull(QueueEditorRepository(database, owner).loadActive(profile))
        assertEquals(listOf(first.value), reopened.entries.map { it.queueEntryId.value })
        assertFalse(reopened.canNext)
        assertTrue(commands.filterIsInstance<PlaybackCommand.RefreshQueue>().size >= 3)
    }

    private suspend fun assertFailure(code: String, block: suspend () -> Unit) {
        try { block(); throw AssertionError("expected $code") } catch (failure: QueueEditFailure) { assertEquals(code, failure.code) }
    }

    private fun track(id: LocalId, profile: String) = UserTrackRefEntity(id.value, uuid(30), null, uuid(31), "RESOLVED", "track", "artist", null, 1_000, 1.0, "CLEAN", 1, 0, 1, 1, null, profile)
    private fun id(seed: Int) = LocalId(uuid(seed))
    private fun uuid(seed: Int) = UUID(0, seed.toLong()).toString()
}
