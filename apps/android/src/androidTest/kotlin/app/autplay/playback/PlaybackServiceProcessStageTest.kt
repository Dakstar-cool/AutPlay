package app.autplay.playback

import android.content.ComponentName
import androidx.media3.common.util.UnstableApi
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.playback.QueueEditorRepository
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.LocalId
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runners.MethodSorters
import org.junit.runner.RunWith

/**
 * Run stage1, force-stop app.autplay with adb, then run stage2 for the true process-death gate.
 * The wrapper-only argument keeps these externally orchestrated stages out of the normal suite.
 */
@UnstableApi
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
@RunWith(AndroidJUnit4::class)
class PlaybackServiceProcessStageTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val testPackageName = instrumentation.context.packageName

    @Test fun stage1_seedServiceAndWaitForPeriodicCheckpoint() = runBlocking {
        requireExternalProcessStage()
        context.stopService(android.content.Intent(context, AutPlayPlaybackService::class.java))
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase(app.autplay.data.local.AutPlayDatabase.DATABASE_NAME)
        val database = AutPlayRuntime.database(context)
        val trackId = id(71)
        val entryId = id(72)
        val snapshotId = id(73)
        val secondTrackId = id(75)
        val thirdTrackId = id(76)
        val secondEntryId = id(77)
        val thirdEntryId = id(78)
        database.libraryDao().upsertTrackRef(track(trackId))
        database.libraryDao().upsertTrackRef(track(secondTrackId))
        database.libraryDao().upsertTrackRef(track(thirdTrackId))
        database.localAudioDao().upsertState(audio(trackId, 74))
        database.localAudioDao().upsertState(audio(secondTrackId, 79))
        database.localAudioDao().upsertState(audio(thirdTrackId, 80))
        PlaybackPersistenceRepository(database).activateQueue(
            snapshotId,
            listOf(
                NewPlaybackQueueEntry(entryId, trackId, "ORGANIC", "LOCAL_THEN_VAULT"),
                NewPlaybackQueueEntry(secondEntryId, secondTrackId, "ORGANIC", "LOCAL_THEN_VAULT"),
                NewPlaybackQueueEntry(thirdEntryId, thirdTrackId, "ORGANIC", "LOCAL_THEN_VAULT"),
            ),
            "USER",
            null,
            null,
            "GENERAL",
            System.currentTimeMillis(),
        )
        ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.StartQueue(snapshotId))
        val controller = connect()
        try {
            await("service queue") { onMain { controller.currentMediaItem?.mediaId } == entryId.value }
            await("ready playback state") { onMain { controller.playbackState == Player.STATE_READY } }
            onMain { controller.play() }
            await("active playback") { onMain { controller.isPlaying } }
            await("logical session") {
                runBlocking { database.queueDao().activeSnapshotOnce()?.activeListeningEventId } != null
            }
            onMain {
                controller.repeatMode = Player.REPEAT_MODE_ALL
                controller.shuffleModeEnabled = true
            }
            await("initial playback modes") {
                onMain {
                    controller.shuffleModeEnabled && controller.repeatMode == Player.REPEAT_MODE_ALL
                }
            }
            await("persisted playback modes") {
                runBlocking {
                    database.queueDao().activeSnapshotOnce()?.let { snapshot ->
                        snapshot.shuffleMode == "SEEDED" &&
                            snapshot.repeatMode == "ALL" &&
                            snapshot.seed != null
                    } == true
                }
            }
            await("initial playback position") { onMain { controller.currentPosition } >= 1_000 }
            await("play intent before refresh") { onMain { controller.playWhenReady } }
            val beforeRefreshPosition = onMain { controller.currentPosition }
            val listeningEventId = requireNotNull(database.queueDao().activeSnapshotOnce()).activeListeningEventId

            QueueEditorRepository(database, ServicePlaybackSessionOwner(context)).moveUpcoming(
                entryId = thirdEntryId,
                beforeEntryId = secondEntryId,
                expectedProfileId = null,
                expectedSnapshotId = snapshotId,
            )
            await("refreshed edited order") {
                onMain { (0 until controller.mediaItemCount).map(controller::getMediaItemAt).map { it.mediaId } } ==
                    listOf(entryId.value, thirdEntryId.value, secondEntryId.value)
            }
            assertEquals(entryId.value, onMain { controller.currentMediaItem?.mediaId })
            assertTrue(onMain { controller.currentPosition } >= beforeRefreshPosition - 500)
            await("play intent preserved after refresh") { onMain { controller.playWhenReady } }
            await("play state preserved after refresh") { onMain { controller.isPlaying } }
            await("shuffle preserved after refresh") { onMain { controller.shuffleModeEnabled } }
            await("repeat preserved after refresh") { onMain { controller.repeatMode == Player.REPEAT_MODE_ALL } }
            assertEquals(listeningEventId, requireNotNull(database.queueDao().activeSnapshotOnce()).activeListeningEventId)

            onMain { controller.shuffleModeEnabled = false; controller.pause() }
            await("paused before navigation") { !onMain { controller.isPlaying } }
            ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.Next)
            await("next service command") { onMain { controller.currentMediaItem?.mediaId } == thirdEntryId.value }
            await("next Room checkpoint") {
                runBlocking { database.queueDao().activeSnapshotOnce()?.currentEntryId } == thirdEntryId.value
            }
            ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.Previous)
            await("previous service command") { onMain { controller.currentMediaItem?.mediaId } == entryId.value }
            await("previous Room checkpoint") {
                runBlocking { database.queueDao().activeSnapshotOnce()?.currentEntryId } == entryId.value
            }
            onMain { controller.seekTo(12_000) }
            await("paused seek") { onMain { controller.currentPosition } >= 10_000 }
            await("paused seek checkpoint") {
                runBlocking { database.queueDao().activeSnapshotOnce()?.currentPositionMs ?: 0 } >= 10_000
            }
            assertFalse(onMain { controller.isPlaying })
            assertTrue(requireNotNull(database.queueDao().activeSnapshotOnce()).currentPositionMs >= 10_000)
            onMain { controller.play() }
            await("foreground playback before forced process death") { onMain { controller.isPlaying } }
        } finally {
            onMain { controller.release() }
        }
    }

    @Test fun stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection() = runBlocking {
        requireExternalProcessStage()
        val database = AutPlayRuntime.database(context)
        val persistedPositionMs = requireNotNull(database.queueDao().activeSnapshotOnce()).currentPositionMs
        assertTrue("Room position before service restore was $persistedPositionMs", persistedPositionMs >= 10_000)
        val controller = connect()
        try {
            await("restored queue") { onMain { controller.currentMediaItem?.mediaId } == id(72).value }
            assertEquals(id(72).value, onMain { controller.currentMediaItem?.mediaId })
            assertEquals(
                listOf(id(72).value, id(78).value, id(77).value),
                onMain { (0 until controller.mediaItemCount).map(controller::getMediaItemAt).map { it.mediaId } },
            )
            await("restored playback position from Room=$persistedPositionMs") {
                onMain { controller.currentPosition } >= 10_000
            }
            await("restored repeat mode") { onMain { controller.repeatMode == Player.REPEAT_MODE_ALL } }
            assertFalse(onMain { controller.shuffleModeEnabled })
            assertTrue(requireNotNull(database.queueDao().activeSnapshotOnce()).currentPositionMs >= 10_000)
        } finally {
            onMain { controller.release() }
            ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.Stop)
        }
    }

    private fun connect(): MediaController = MediaController.Builder(
        context,
        SessionToken(context, ComponentName(context, AutPlayPlaybackService::class.java)),
    ).buildAsync().get(10, TimeUnit.SECONDS)

    private fun requireExternalProcessStage() {
        assumeTrue(
            "Run through scripts/test-l1-process-death.ps1",
            InstrumentationRegistry.getArguments().getString("l1ProcessStage") == "true",
        )
    }

    private fun await(label: String, timeoutMs: Long = 10_000, condition: () -> Boolean) {
        val deadline = android.os.SystemClock.elapsedRealtime() + timeoutMs
        while (!condition() && android.os.SystemClock.elapsedRealtime() < deadline) {
            android.os.SystemClock.sleep(50)
        }
        assertTrue("Timed out waiting for $label", condition())
    }

    private fun <T> onMain(block: () -> T): T {
        val result = java.util.concurrent.atomic.AtomicReference<Result<T>>()
        instrumentation.runOnMainSync { result.set(runCatching(block)) }
        return requireNotNull(result.get()).getOrThrow()
    }

    private fun track(id: LocalId) = UserTrackRefEntity(
        localUserTrackRefId = id.value,
        serverUserTrackRefId = null,
        localRecordingId = null,
        serverRecordingId = null,
        resolutionStatus = "UNRESOLVED",
        rawTitle = "Process restore",
        rawArtist = "AutPlay",
        rawAlbum = null,
        rawDurationMs = 40_000,
        resolutionConfidence = null,
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = 0,
        createdAtMs = 1,
        updatedAtMs = 1,
        deletedAtMs = null,
    )

    private fun audio(trackId: LocalId, stateSeed: Int) = LocalAudioStateEntity(
        localAudioStateId = id(stateSeed).value,
        localUserTrackRefId = trackId.value,
        localRecordingId = null,
        serverAudioVariantId = null,
        contentUri = "content://$testPackageName.readable/audio/process",
        persistedUriPermission = false,
        localSha256 = null,
        fingerprintAlgorithm = null,
        fingerprintVersion = null,
        fingerprintPayload = null,
        codec = "pcm_s16le",
        container = "wav",
        bitrateBps = 128_000,
        sampleRateHz = 8_000,
        channels = 1,
        durationMs = 40_000,
        status = "AVAILABLE",
        storageClass = "USER_IMPORT",
        byteSize = 640_044,
        lastAccessedAtMs = null,
        lastVerifiedAtMs = null,
        createdAtMs = 1,
        updatedAtMs = 1,
    )

    private fun id(seed: Int) = LocalId(UUID(0, seed.toLong()).toString())
}
