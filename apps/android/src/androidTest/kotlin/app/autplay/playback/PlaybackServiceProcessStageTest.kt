package app.autplay.playback

import android.content.ComponentName
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.LocalId
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runners.MethodSorters
import org.junit.runner.RunWith

/**
 * Run stage1, force-stop app.autplay with adb, then run stage2 for the true process-death gate.
 * The fixed order also keeps the complete connected suite independently green.
 */
@UnstableApi
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
@RunWith(AndroidJUnit4::class)
class PlaybackServiceProcessStageTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext

    @Test fun stage1_seedServiceAndWaitForPeriodicCheckpoint() = runBlocking {
        context.stopService(android.content.Intent(context, AutPlayPlaybackService::class.java))
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase(app.autplay.data.local.AutPlayDatabase.DATABASE_NAME)
        val database = AutPlayRuntime.database(context)
        val trackId = id(71)
        val entryId = id(72)
        val snapshotId = id(73)
        database.libraryDao().upsertTrackRef(track(trackId))
        database.localAudioDao().upsertState(audio(trackId))
        PlaybackPersistenceRepository(database).activateQueue(
            snapshotId,
            listOf(NewPlaybackQueueEntry(entryId, trackId, "ORGANIC", "LOCAL_THEN_VAULT")),
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
            await("periodic checkpoint", 25_000) {
                runBlocking { database.queueDao().activeSnapshotOnce()?.currentPositionMs ?: 0 } >= 10_000
            }
            assertTrue(requireNotNull(database.queueDao().activeSnapshotOnce()).currentPositionMs >= 10_000)
        } finally {
            onMain { controller.release() }
        }
    }

    @Test fun stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection() = runBlocking {
        val database = AutPlayRuntime.database(context)
        val controller = connect()
        try {
            await("restored queue") { onMain { controller.currentMediaItem?.mediaId } == id(72).value }
            assertEquals(id(72).value, onMain { controller.currentMediaItem?.mediaId })
            assertTrue(onMain { controller.currentPosition } >= 10_000)
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

    private fun audio(trackId: LocalId) = LocalAudioStateEntity(
        localAudioStateId = id(74).value,
        localUserTrackRefId = trackId.value,
        localRecordingId = null,
        serverAudioVariantId = null,
        contentUri = "content://app.autplay.test.readable/audio/process",
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
