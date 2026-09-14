package app.autplay.playback

import android.app.ActivityManager
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.room3.useWriterConnection
import androidx.room3.withWriteTransaction
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class PlaybackServiceLifecycleTest {
    @Test fun nextDuringSlowInitialProbeBecomesPlayableAndDestroyCancelsBinder() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("slow", "readable"))
            fixture.await { fixture.providerCount("started") > 0 }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true && fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value } }
            fixture.stop()
            fixture.await { fixture.providerCount("cancelled") > 0 }
            assertEquals("AVAILABLE", fixture.database.localAudioDao().state(fixture.audioIds[0])?.status)
        } finally { fixture.close() }
    }

    @Test fun missingNextHasExplicitTerminalStateWithoutBufferingForever() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "missing"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.await { fixture.main { fixture.controller?.getMediaItemAt(1)?.mediaMetadata?.extras?.getString("unavailable_reason") != null } }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { PlaybackRuntimeState.state.value.unavailableReason != null }
            assertEquals(Player.STATE_IDLE, fixture.main { fixture.controller?.playbackState })
            assertFalse(PlaybackRuntimeState.state.value.isPlaying)
        } finally { fixture.close() }
    }

    @Test fun blockedRemoteNextNeverDelaysCurrentAndCannotEnterReplacementQueue() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.remoteServer()
            fixture.start(listOf("readable", "remote"))
            fixture.await { fixture.remoteEntered.count == 0L }
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            val replacement = fixture.activate(listOf(0))
            fixture.owner.dispatch(PlaybackCommand.StartQueue(replacement))
            fixture.await { PlaybackRuntimeState.state.value.queueSnapshotId == replacement.value && fixture.main { fixture.controller?.isPlaying == true } }
            fixture.remoteRelease.countDown()
            delay(300)
            assertEquals(1, fixture.main { fixture.controller?.mediaItemCount })
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
        } finally { fixture.close() }
    }

    @Test fun destroyWithHeldRoomWriterKeepsMainResponsiveAndOrdersSuccessorRestore() = runBlocking {
        val fixture = Fixture()
        val releaseWriter = CompletableDeferred<Unit>()
        val writerScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            fixture.start(listOf("readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.await { fixture.main { (fixture.controller?.currentPosition ?: 0) >= 1_000 } }
            val event = requireNotNull(fixture.database.queueDao().activeSnapshotOnce()).activeListeningEventId
            val enteredWriter = CompletableDeferred<Unit>()
            writerScope.launch {
                fixture.database.withWriteTransaction { enteredWriter.complete(Unit); releaseWriter.await() }
            }
            enteredWriter.await()
            fixture.main { fixture.controller?.pause(); fixture.controller?.seekTo(12_000) }
            delay(100)
            fixture.stop()
            withTimeout(1_000) { repeat(5) { fixture.main { assertEquals(android.os.Looper.getMainLooper(), android.os.Looper.myLooper()) }; delay(10) } }
            assertFalse(PlaybackRuntimeState.state.value.isPlaying)
            fixture.connect()
            assertEquals(0, fixture.main { fixture.controller?.mediaItemCount })
            releaseWriter.complete(Unit)
            fixture.await {
                fixture.main {
                    fixture.controller?.playbackState == Player.STATE_READY &&
                        (fixture.controller?.currentPosition ?: 0) >= 12_000
                }
            }
            val persisted = requireNotNull(fixture.database.queueDao().activeSnapshotOnce())
            assertEquals(event, persisted.activeListeningEventId)
            assertTrue((persisted.activeSessionObservedPlayedMs ?: 0) >= 800)
            fixture.main { fixture.controller?.seekTo(18_000) }
            fixture.await { (fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs ?: 0) >= 18_000 }
            delay(150)
            assertTrue((fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs ?: 0) >= 18_000)
        } finally { releaseWriter.complete(Unit); writerScope.cancel(); fixture.close() }
    }

    private class Fixture {
        private val instrumentation = InstrumentationRegistry.getInstrumentation()
        private val context = instrumentation.targetContext
        private val testPackage = instrumentation.context.packageName
        val database = AutPlayRuntime.database(context)
        val owner = ServicePlaybackSessionOwner(context)
        private val settings = applicationNonSecretSettingsStore(context)
        private var original: NonSecretSettings? = null
        private val ids = List(15) { UUID.randomUUID().toString() }
        val entries = (0..2).map { LocalId(ids[it]) }.toMutableList()
        val audioIds = ids.slice(3..5)
        private val tracks = ids.slice(6..8)
        private val snapshots = mutableListOf<String>()
        private val profile = ServerProfileId(ids[10])
        private val slowUri = Uri.parse("content://$testPackage.slow/audio/${ids[11]}")
        private var server: MockWebServer? = null
        val remoteEntered = CountDownLatch(1)
        val remoteRelease = CountDownLatch(1)
        var controller: MediaController? = null

        suspend fun remoteServer() {
            original = settings.settings.first()
            val mock = MockWebServer()
            mock.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    remoteEntered.countDown()
                    check(remoteRelease.await(20, TimeUnit.SECONDS))
                    return MockResponse().setBody("""{"audio_variant_id":"${ids[12]}"}""")
                }
            }
            mock.start()
            server = mock
            val origin = mock.url("/").toString().trimEnd('/')
            settings.update(NonSecretSettings(activeServerProfileId = profile,
                activeUserId = app.autplay.domain.UserId(ids[13]), deviceId = app.autplay.domain.DeviceId(ids[14]),
                serverBaseUrl = origin, streamBaseUrl = origin))
            AndroidKeystoreCredentialStore(context).write(profile, "a".repeat(64).toByteArray())
        }

        suspend fun start(sources: List<String>) {
            if (original == null) { original = settings.settings.first(); settings.update(NonSecretSettings()) }
            context.stopService(Intent(context, AutPlayPlaybackService::class.java))
            PlaybackShutdownPersistence.awaitPending()
            context.contentResolver.call(slowUri, "reset", null, null)
            sources.forEachIndexed { index, source ->
                database.libraryDao().upsertTrackRef(UserTrackRefEntity(tracks[index], if (source == "remote") ids[9] else null,
                    null, null, "UNRESOLVED", "Lifecycle fixture", "Fixture", null, 40_000, null,
                    "LOCAL_ONLY", null, 0, 1, 1, null))
                if (source in listOf("slow", "readable")) {
                    val uri = if (source == "slow") slowUri.toString() else "content://$testPackage.readable/audio/${audioIds[index]}"
                    database.localAudioDao().upsertState(LocalAudioStateEntity(audioIds[index], tracks[index], null, null,
                        uri, false, null, null, null, null, "pcm_s16le", "wav", 128_000, 8_000, 1, 40_000,
                        "AVAILABLE", "USER_IMPORT", 640_044, null, null, 1, 1))
                }
            }
            val snapshot = activate(sources.indices.toList())
            owner.dispatch(PlaybackCommand.StartQueue(snapshot))
            connect()
        }

        suspend fun activate(indices: List<Int>): LocalId {
            val snapshot = LocalId.random()
            if (snapshots.isNotEmpty()) indices.forEach { entries[it] = LocalId.random() }
            snapshots += snapshot.value
            PlaybackPersistenceRepository(database).activateQueue(snapshot,
                indices.map { NewPlaybackQueueEntry(entries[it], LocalId(tracks[it]), "ORGANIC", "LOCAL_THEN_VAULT") },
                "USER", null, null, "GENERAL", System.currentTimeMillis())
            return snapshot
        }

        fun connect() {
            controller = MediaController.Builder(context, SessionToken(context,
                ComponentName(context, AutPlayPlaybackService::class.java))).buildAsync().get(10, TimeUnit.SECONDS)
        }
        suspend fun stop() {
            main { controller?.release(); controller = null }
            context.stopService(Intent(context, AutPlayPlaybackService::class.java))
            await {
                context.getSystemService(ActivityManager::class.java).getRunningServices(100)
                    .none { it.service.className == AutPlayPlaybackService::class.java.name }
            }
        }
        fun providerCount(key: String): Int = context.contentResolver.call(slowUri, "status", null, null)?.getInt(key) ?: 0
        suspend fun <T> main(block: () -> T): T = withContext(Dispatchers.Main) { block() }
        suspend fun await(condition: suspend () -> Boolean) = withTimeout(10_000) { while (!condition()) delay(20) }

        suspend fun close() {
            remoteRelease.countDown()
            stop()
            PlaybackShutdownPersistence.awaitPending()
            original?.let { settings.update(it) }
            if (server != null) AndroidKeystoreCredentialStore(context).clear(profile)
            database.useWriterConnection { connection ->
                for (snapshot in snapshots) connection.usePrepared("DELETE FROM queue_snapshot WHERE queue_snapshot_id = ?") { it.bindText(1, snapshot); it.step() }
                for (track in tracks) {
                    for (table in listOf("listening_event", "local_audio_state", "user_track_ref")) {
                        connection.usePrepared("DELETE FROM $table WHERE local_user_track_ref_id = ?") { it.bindText(1, track); it.step() }
                    }
                }
            }
            server?.close()
        }
    }
}
