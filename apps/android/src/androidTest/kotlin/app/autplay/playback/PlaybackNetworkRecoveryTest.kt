package app.autplay.playback

import android.net.Uri
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.test.platform.app.InstrumentationRegistry
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.*
import org.junit.Test

@UnstableApi
class PlaybackNetworkRecoveryTest {
    @Test fun currentVariantLookupSurvivesShortServerOutageWithoutAnotherPlayCommand() = runBlocking {
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val requests = AtomicInteger()
        val bytes = fixtureBytes()
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.path.orEmpty().endsWith("/playback-variant")) {
                        if (requests.incrementAndGet() <= 3) return MockResponse().setResponseCode(503)
                        return MockResponse().setBody("""{"audio_variant_id":"${fixture.audioIds.first()}"}""")
                    }
                    return audioResponse(request, bytes)
                }
            })
            fixture.start(listOf("remote"))
            fixture.await("the original entry resumes after the server returns") { fixture.main { fixture.controller!!.isPlaying } }
            assertEquals(fixture.entries.first().value, fixture.main { fixture.controller!!.currentMediaItem?.mediaId })
            assertEquals(4, requests.get())
        } finally { fixture.close() }
    }

    @Test fun preparedPersonalWavePlaysFromCachedHeadAndFetchesSuccessorDuringPlayback() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val bytes = fixtureBytes()
        val audioReads = java.util.concurrent.CopyOnWriteArrayList<Pair<String, String?>>()
        var startedAt = 0L
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.method == "GET") {
                        audioReads.add(request.path.orEmpty() to request.getHeader("Range"))
                    }
                    return audioResponse(request, bytes).setHeadersDelay(350, TimeUnit.MILLISECONDS)
                }
            })
            val wave = app.autplay.application.recommendation.PersonalWaveSession.start(null)
            fixture.start(listOf("vault", "vault"), sourceContextId = wave.queueContext(null)) {
                val settings = app.autplay.data.settings.applicationNonSecretSettingsStore(context).settings.first()
                app.autplay.warmPersonalWaveTracks(context, settings.copy(downloadOnMeteredNetwork = true),
                    requireNotNull(settings.activeServerProfileId), fixture.tracks.take(2).map { app.autplay.domain.LocalId(it) })
                assertEquals("bytes=0-262143", audioReads.single().second)
                startedAt = android.os.SystemClock.elapsedRealtime()
            }
            fixture.await("prepared first track playing") {
                fixture.main { fixture.controller!!.isPlaying && fixture.controller!!.currentPosition > 0 }
            }
            val elapsed = android.os.SystemClock.elapsedRealtime() - startedAt
            android.util.Log.i("AutPlayWaveTiming", "serviceToPlayingMs=$elapsed simulatedHeadersDelayMs=350")
            fixture.await("successor audio requested before the first track ends") {
                audioReads.any { (path, _) -> fixture.audioIds[1] in path }
            }
            assertEquals(0, fixture.main { fixture.controller!!.currentMediaItemIndex })
            assertEquals(1, audioReads.count { (path, range) ->
                fixture.audioIds[0] in path && (range == null || range.startsWith("bytes=0-"))
            })
            fixture.main { fixture.controller!!.seekTo(39_000) }
            fixture.await("playing the tail") { fixture.main {
                fixture.controller!!.isPlaying && fixture.controller!!.currentMediaItemIndex == 0 &&
                    fixture.controller!!.currentPosition >= 39_000
            } }
            val rebuffers = AtomicInteger()
            fixture.main {
                fixture.controller!!.addListener(object : Player.Listener {
                    override fun onPlaybackStateChanged(playbackState: Int) {
                        if (playbackState == Player.STATE_BUFFERING) rebuffers.incrementAndGet()
                    }
                })
            }
            fixture.await("continuous play through the next head") {
                fixture.main { fixture.controller!!.currentMediaItemIndex == 1 && fixture.controller!!.isPlaying }
            }
            assertTrue(fixture.main { fixture.controller!!.currentPosition < 2_000 })
            assertEquals(0, rebuffers.get())
        } finally { fixture.close() }
    }

    @Test fun failedSuccessorDoesNotInterruptTheCurrentTailAndRecoversAtItsOwnHead() = runBlocking {
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val requests = AtomicInteger()
        val failing = AtomicBoolean(true)
        val bytes = fixtureBytes()
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests.incrementAndGet()
                    return if (failing.get()) MockResponse().setResponseCode(503) else audioResponse(request, bytes)
                }
            })
            fixture.start(listOf("readable", "vault"), smooth = true)
            fixture.await("successor preload fails while current plays") { requests.get() >= 3 }
            assertTrue(fixture.main { fixture.controller!!.isPlaying })
            assertNull(fixture.main { fixture.controller!!.playerError })
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller!!.currentMediaItem!!.mediaId })
            fixture.await("first session") { fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId != null }
            val firstListen = requireNotNull(fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            fixture.main { fixture.controller!!.seekTo(39_500) }
            fixture.await("error belongs to successor") {
                fixture.main { fixture.controller!!.currentMediaItem?.mediaId == fixture.entries[1].value && fixture.controller!!.playerError != null }
            }
            failing.set(false)
            fixture.await("successor recovers", timeoutMs = 20_000) { fixture.main { fixture.controller!!.isPlaying } }
            assertEquals(fixture.entries[1].value, fixture.main { fixture.controller!!.currentMediaItem!!.mediaId })
            assertTrue(fixture.main { fixture.controller!!.currentPosition in 0..3_000 })
            fixture.await("complete departing tail") { fixture.database.historyDao().event(firstListen)?.sessionEndPositionMs == 40_000L }
        } finally { fixture.close() }
    }

    @Test fun tricklingStreamIsReopenedAfterStarvationWithoutWaitingForReadTimeout() = runBlocking {
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val requests = AtomicInteger()
        val bytes = fixtureBytes()
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val response = audioResponse(request, bytes)
                    // Bytes keep arriving within the 15s read timeout, but cannot feed the decoder.
                    return if (requests.incrementAndGet() == 1) response.throttleBody(8, 5, TimeUnit.SECONDS) else response
                }
            })
            fixture.start(listOf("vault"))
            fixture.await("starvation recovery", timeoutMs = 40_000) { fixture.main { fixture.controller?.isPlaying == true } }
            assertTrue(requests.get() in 2..3)
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
        } finally { fixture.close() }
    }

    @Test fun repeatedFailuresExhaustTheServiceBudgetAndStopOpeningTheSource() = runBlocking {
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val requests = AtomicInteger()
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests.incrementAndGet()
                    return MockResponse().setResponseCode(503)
                }
            })
            fixture.start(listOf("vault"))
            fixture.await("bounded recovery exhaustion", timeoutMs = 60_000) {
                PlaybackRuntimeState.state.value.unavailableReason == "NETWORK_RECOVERY_EXHAUSTED" &&
                    fixture.main { fixture.controller?.playWhenReady == false }
            }
            assertEquals(3 * (1 + PlaybackRecoveryPolicy.MAX_ATTEMPTS), requests.get())
            delay(2_000)
            assertEquals(15, requests.get())
        } finally { fixture.close() }
    }

    @Test fun terminalLoaderFailureRecoversTheSameEntryThroughFreshAuthorizedOpen() = runBlocking {
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val requests = AtomicInteger()
        val rangedRequests = AtomicInteger()
        val audioAvailable = AtomicBoolean(false)
        val invalidAuthorization = AtomicBoolean(false)
        val bytes = fixtureBytes()
        try {
            fixture.vaultServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.getHeader("Authorization") != "Bearer " + "a".repeat(64)) {
                        invalidAuthorization.set(true)
                        return MockResponse().setResponseCode(401)
                    }
                    if (request.method == "GET") {
                        requests.incrementAndGet()
                        if (request.getHeader("Range") != null) rangedRequests.incrementAndGet()
                    }
                    return if (audioAvailable.get()) audioResponse(request, bytes) else MockResponse().setResponseCode(503)
                }
            })
            fixture.start(listOf("vault"), startPositionMs = 12_345)
            fixture.await("terminal loader failure") { fixture.main { fixture.controller?.playerError != null } }
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            assertTrue("Loader did not exhaust its HTTP attempts", requests.get() >= 3)
            audioAvailable.set(true)
            fixture.await("service recovery") { fixture.main { fixture.controller?.isPlaying == true } }
            assertTrue(requests.get() in 4..6)
            assertFalse("Audio opened without the fixture credential", invalidAuthorization.get())
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            assertNull(fixture.main { fixture.controller?.playerError })
            assertTrue(fixture.main { fixture.controller!!.currentPosition } >= 12_345)
            assertTrue(rangedRequests.get() > 0)
        } finally { fixture.close() }
    }

    @Test fun pauseStopProfileSwitchAndQueueReplacementCancelPendingRecovery() = runBlocking {
        for (cancel in listOf("pause", "stop", "profile", "queue", "replacement")) {
            val fixture = PlaybackServiceLifecycleTest.Fixture()
            val requests = AtomicInteger()
            try {
                fixture.vaultServer(object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse {
                        requests.incrementAndGet()
                        return MockResponse().setResponseCode(503)
                    }
                })
                fixture.start(listOf("vault", "readable"))
                fixture.await("error before $cancel") { fixture.main { fixture.controller?.playerError != null } }
                when (cancel) {
                    "pause" -> fixture.main { fixture.controller?.pause() }
                    "stop" -> fixture.main { fixture.controller?.stop() }
                    "profile" -> fixture.disconnectProfile()
                    "queue" -> fixture.main { fixture.controller?.seekToNextMediaItem() }
                    "replacement" -> fixture.owner.dispatch(PlaybackCommand.StartQueue(fixture.activate(listOf(1))))
                }
                val count = requests.get()
                delay(2_500)
                assertEquals("No stale opens after $cancel", count, requests.get())
                if (cancel == "pause" || cancel == "stop") assertFalse(fixture.main { fixture.controller!!.isPlaying })
                if (cancel == "queue" || cancel == "replacement") {
                    assertEquals(fixture.entries[1].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
                    fixture.await("local successor plays after $cancel") { fixture.main { fixture.controller!!.isPlaying } }
                }
            } finally { fixture.close() }
        }
    }

    @Test fun forbiddenAndMissingAudioFailOnceWithoutServiceRetry() = runBlocking {
        for (status in listOf(401, 403, 404)) {
            val fixture = PlaybackServiceLifecycleTest.Fixture()
            val requests = AtomicInteger()
            try {
                fixture.vaultServer(object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse {
                        if (request.method == "GET") requests.incrementAndGet()
                        return MockResponse().setResponseCode(status)
                    }
                })
                fixture.start(listOf("vault"))
                fixture.await("HTTP $status failure") { fixture.main { fixture.controller?.playerError != null } }
                delay(2_000)
                assertEquals(1, requests.get())
            } finally { fixture.close() }
        }
    }

    private fun fixtureBytes(): ByteArray {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        return instrumentation.targetContext.contentResolver.openInputStream(
            Uri.parse("content://${instrumentation.context.packageName}.readable/audio/tone"),
        )!!.use { it.readBytes() }
    }

    private fun audioResponse(request: RecordedRequest, bytes: ByteArray): MockResponse {
        val response = MockResponse().setHeader("Content-Type", "audio/wav").setHeader("Accept-Ranges", "bytes")
        if (request.method == "HEAD") return response.setHeader("Content-Length", bytes.size)
        val range = request.getHeader("Range")?.removePrefix("bytes=")
        val start = range?.substringBefore('-')?.toIntOrNull() ?: 0
        val end = (range?.substringAfter('-')?.toIntOrNull() ?: bytes.lastIndex).coerceAtMost(bytes.lastIndex)
        if (start !in 0..end) return response.setResponseCode(416).setHeader("Content-Range", "bytes */${bytes.size}")
        if (range != null) {
            response.setResponseCode(206).setHeader("Content-Range", "bytes $start-$end/${bytes.size}")
        }
        return response.setBody(Buffer().write(bytes, start, end - start + 1))
    }
}
