package app.autplay.desktop.acceptance

import app.autplay.desktop.audio.MacAudioEngine
import app.autplay.playback.presentation.PlaybackStatus
import java.nio.file.Files
import java.nio.file.Path
import kotlin.math.abs
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

/** One complete JVM -> process protocol -> AVFoundation -> controller -> Room acceptance case. */
class MacNativePlaybackAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(150)

    @Test fun actualNativeTimelineQueueAndPausedRestartUseManagedNeutralExtension(): Unit = runBlocking {
        val mac = System.getProperty("os.name") == "Mac OS X"
        val required = System.getProperty("autplay.requireNativeTest", "false").toBoolean()
        assertTrue("Required native acceptance needs macOS", !required || mac)
        assumeTrue("AVFoundation acceptance needs macOS", mac)
        val configured = System.getProperty("autplay.nativeTestHelper")
        assertTrue("Supply the packaged helper as autplay.nativeTestHelper", !configured.isNullOrBlank())
        val helper = Path.of(checkNotNull(configured)).toRealPath()
        assertTrue("Packaged native helper must be an executable file", Files.isRegularFile(helper) && Files.isExecutable(helper))

        withTimeout(120_000) {
            val fixture = DesktopAcceptanceFixture()
            try {
                fixture.openStore()
                val trackId = fixture.importWav()
                val source = fixture.sources.resolve("Synthetic tone.wav")
                val original = Files.readAllBytes(source)
                val managed = Path.of(checkNotNull(fixture.store.resolveManagedFile(trackId)))
                assertTrue(managed.fileName.toString().endsWith(".media"))
                assertArrayEquals(original, Files.readAllBytes(managed))
                val engine = MacAudioEngine(helper)
                val controller = fixture.start(engine)
                controller.playTracks(listOf(trackId, trackId))
                suspend fun awaitNative(description: String, predicate: () -> Boolean): Unit = eventually(description) {
                    assertTrue("Native helper failed while $description", engine.state.value.status !in setOf("ERROR", "UNAVAILABLE", "CLOSED"))
                    assertNull("Controller failed while $description", controller.error.value)
                    predicate()
                }
                awaitNative("opening copied WAV") {
                    engine.state.value.status == "READY" && engine.state.value.isPlaying &&
                        controller.state.value.isPlaying && (engine.state.value.durationMs ?: 0) > 0
                }
                val duration = checkNotNull(engine.state.value.durationMs)
                assertTrue("Native WAV duration", abs(duration - 8_000) <= 200)
                assertEquals(duration, controller.state.value.durationMs)
                val entries = checkNotNull(fixture.store.state.value.queue).entries
                assertEquals(2, entries.map { it.id }.distinct().size)
                assertEquals(listOf(trackId, trackId), entries.map { it.trackId })
                val startingPosition = engine.state.value.positionMs
                awaitNative("advancing native timeline") {
                    engine.state.value.isPlaying && engine.state.value.positionMs >= startingPosition + 300 &&
                        controller.state.value.positionMs >= startingPosition + 200
                }
                controller.pause()
                awaitNative("pausing native execution") { !engine.state.value.isPlaying && !controller.state.value.isPlaying }
                val paused = engine.state.value.positionMs
                delay(750)
                assertFalse(engine.state.value.isPlaying)
                assertFalse(controller.state.value.isPlaying)
                assertTrue("Native timeline drifted while paused", abs(engine.state.value.positionMs - paused) <= 120)
                controller.seekTo(1_500)
                awaitNative("seeking paused native execution") {
                    !engine.state.value.isPlaying && abs(engine.state.value.positionMs - 1_500) <= 150 &&
                        abs(controller.state.value.positionMs - 1_500) <= 150
                }

                val firstGeneration = engine.state.value.generation
                val firstSession = fixture.store.state.value.queue?.sessionId
                controller.next()
                awaitNative("next duplicate entry") {
                    engine.state.value.generation > firstGeneration && engine.state.value.status == "READY" &&
                        controller.state.value.mediaId == entries.last().id
                }
                assertFalse(engine.state.value.isPlaying)
                assertNotEquals(firstSession, fixture.store.state.value.queue?.sessionId)
                val secondGeneration = engine.state.value.generation
                controller.previous()
                awaitNative("previous duplicate entry") {
                    engine.state.value.generation > secondGeneration && engine.state.value.status == "READY" &&
                        controller.state.value.mediaId == entries.first().id
                }
                val activeGeneration = engine.state.value.generation
                // An obsolete open that would fail on the native owner cannot replace the live generation.
                engine.open(fixture.data.resolve("missing.media").toString(), firstGeneration, 0, true)
                engine.seek(firstGeneration, 7_999)
                engine.play(firstGeneration)
                delay(300)
                assertEquals(activeGeneration, engine.state.value.generation)
                assertEquals("READY", engine.state.value.status)
                assertFalse(engine.state.value.isPlaying)
                assertNull(controller.error.value)

                controller.setRepeat("ONE")
                eventually("repeat one checkpoint") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                controller.seekTo(duration - 350)
                awaitNative("seeking close to EOF") { abs(engine.state.value.positionMs - (duration - 350)) <= 150 }
                val beforeRepeatSession = fixture.store.state.value.queue?.sessionId
                controller.play()
                awaitNative("real EOF repeat creates new generation") {
                    engine.state.value.generation > activeGeneration && engine.state.value.isPlaying &&
                        controller.state.value.isPlaying && fixture.store.state.value.queue?.sessionId != beforeRepeatSession
                }
                assertEquals(entries.first().id, controller.state.value.mediaId)
                assertTrue(fixture.store.state.value.history.single { it.id == beforeRepeatSession }.finalized)
                controller.pause()
                awaitNative("pausing repeated entry") { !engine.state.value.isPlaying && !controller.state.value.isPlaying }
                controller.seekTo(1_200)
                awaitNative("restart checkpoint seek") {
                    abs(engine.state.value.positionMs - 1_200) <= 150 && abs(controller.state.value.positionMs - 1_200) <= 150
                }
                val closingAt = System.nanoTime()
                fixture.stopController()
                assertTrue("Native close exceeded its bound", (System.nanoTime() - closingAt) / 1_000_000 < 15_000)
                assertEquals("CLOSED", engine.state.value.status)
                val checkpoint = checkNotNull(fixture.store.state.value.queue)
                assertNotNull(checkpoint.sessionId)
                fixture.reopenStore()
                val restartedEngine = MacAudioEngine(helper)
                val restarted = fixture.start(restartedEngine)
                eventually("actual native reopen paused") {
                    assertTrue("Reopened helper failed", restartedEngine.state.value.status !in setOf("ERROR", "UNAVAILABLE"))
                    assertNull(restarted.error.value)
                    restartedEngine.state.value.status == "READY" && restarted.state.value.playbackStatus == PlaybackStatus.Ready
                }
                assertFalse(restartedEngine.state.value.isPlaying)
                assertFalse(restarted.state.value.isPlaying)
                assertEquals(checkpoint.sessionId, fixture.store.state.value.queue?.sessionId)
                assertEquals(checkpoint.currentEntryId, restarted.state.value.mediaId)
                assertTrue(abs(restartedEngine.state.value.positionMs - checkpoint.positionMs) <= 150)
                val reopenedPosition = restartedEngine.state.value.positionMs
                delay(750)
                assertTrue("Restored paused timeline drifted", abs(restartedEngine.state.value.positionMs - reopenedPosition) <= 120)
                assertEquals(1, fixture.store.state.value.history.count { it.id == checkpoint.sessionId })

                Files.delete(managed)
                restarted.play()
                eventually("missing managed source fails closed") { restarted.error.value == "SOURCE_UNAVAILABLE" }
                assertFalse(restarted.state.value.isPlaying)
                assertNull(fixture.store.resolveManagedFile(trackId))
                assertArrayEquals(original, Files.readAllBytes(source))
            } finally { fixture.close() }
        }
    }
}
