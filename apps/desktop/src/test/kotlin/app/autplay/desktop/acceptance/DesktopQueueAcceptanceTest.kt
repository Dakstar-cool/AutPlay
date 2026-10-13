package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.playback.presentation.PlaybackStatus
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

class DesktopQueueAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(90)

    @Test fun realQueueKeepsDuplicateEntriesRepeatShuffleAndRestartSessionWhileRejectingStaleEof(): Unit = runBlocking {
        withTimeout(75_000) {
            val fixture = DesktopAcceptanceFixture()
            try {
                fixture.openStore()
                val trackId = fixture.importWav(durationMs = 250)
                val engine = PolicyEngine()
                val controller = fixture.start(engine)
                controller.playTracks(listOf(trackId, trackId))
                eventually("valid queue opens before limit rejection") {
                    controller.state.value.isPlaying && fixture.store.state.value.queue?.entries?.size == 2
                }
                val originalQueue = checkNotNull(fixture.store.state.value.queue)
                val originalGeneration = engine.state.value.generation
                val originalOpenCount = engine.opens.size
                val originalStops = engine.stops.toList()
                // Reject an oversized request before changing or stopping the live queue.
                controller.playTracks(List(1_001) { trackId })
                eventually("oversized selection reports queue limit") { controller.error.value == "QUEUE_LIMIT" }
                assertEquals(originalQueue, fixture.store.state.value.queue)
                assertEquals(originalQueue.currentEntryId, controller.state.value.mediaId)
                assertEquals(originalGeneration, engine.state.value.generation)
                assertEquals(originalOpenCount, engine.opens.size)
                assertEquals(originalStops, engine.stops.toList())
                assertTrue(engine.state.value.isPlaying)
                assertTrue(controller.state.value.isPlaying)
                controller.playTracks(listOf(trackId, trackId, trackId))
                eventually("valid duplicate queue recovers and clears limit error") {
                    controller.error.value == null && controller.state.value.isPlaying &&
                        fixture.store.state.value.queue?.entries?.size == 3 &&
                        fixture.store.state.value.queue?.id != originalQueue.id
                }
                val entries = checkNotNull(fixture.store.state.value.queue).entries
                assertEquals(3, entries.map { it.id }.distinct().size)
                assertEquals(listOf(trackId, trackId, trackId), entries.map { it.trackId })
                val firstSession = checkNotNull(fixture.store.state.value.queue?.sessionId)
                controller.setRepeat("ONE")
                eventually("repeat one persisted") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                val firstGeneration = engine.state.value.generation
                engine.emit(DesktopAudioEvent(firstGeneration, "ENDED", 8_000, 8_000))
                eventually("repeat one starts a fresh generation/session") {
                    engine.state.value.generation > firstGeneration && controller.state.value.isPlaying &&
                        fixture.store.state.value.queue?.sessionId != firstSession
                }
                assertEquals(entries.first().id, controller.state.value.mediaId)
                assertTrue(fixture.store.state.value.history.single { it.id == firstSession }.finalized)
                val repeatedSession = fixture.store.state.value.queue?.sessionId
                val repeatedGeneration = engine.state.value.generation
                engine.emit(DesktopAudioEvent(firstGeneration, "ERROR", errorCode = "NATIVE_OPEN_FAILED"))
                delay(150)
                engine.emit(DesktopAudioEvent(firstGeneration, "ENDED", 8_000, 8_000))
                delay(150)
                assertNull(controller.error.value)
                assertEquals(repeatedSession, fixture.store.state.value.queue?.sessionId)
                assertEquals(repeatedGeneration, engine.opens.last().generation)

                controller.setRepeat("ALL")
                controller.setShuffle(true)
                eventually("shuffle permutation persisted") {
                    fixture.store.state.value.queue?.let { it.shuffleEnabled && it.repeatMode == "ALL" } == true
                }
                val order = checkNotNull(fixture.store.state.value.queue).shuffleOrder
                assertEquals(entries.map { it.id }.toSet(), order.toSet())
                assertEquals(entries.first().id, order.first())
                controller.next()
                eventually("next follows shuffle entry identity") { controller.state.value.mediaId == order[1] && controller.state.value.isPlaying }
                controller.previous()
                eventually("previous follows shuffle entry identity") { controller.state.value.mediaId == order.first() && controller.state.value.isPlaying }
                controller.selectEntry(order.last())
                eventually("last shuffled entry selected") { controller.state.value.mediaId == order.last() && controller.state.value.isPlaying }
                controller.next()
                eventually("repeat all wraps") { controller.state.value.mediaId == order.first() && controller.state.value.isPlaying }

                controller.setShuffle(false)
                controller.setRepeat("OFF")
                eventually("ordinary order restored") {
                    fixture.store.state.value.queue?.let { !it.shuffleEnabled && it.repeatMode == "OFF" } == true
                }
                controller.selectEntry(entries.last().id)
                eventually("last ordinary entry selected") { controller.state.value.mediaId == entries.last().id && controller.state.value.isPlaying }
                val finalSession = checkNotNull(fixture.store.state.value.queue?.sessionId)
                val finalGeneration = engine.state.value.generation
                engine.emit(DesktopAudioEvent(finalGeneration, "ENDED", 8_000, 8_000))
                eventually("repeat off finalizes last entry") {
                    controller.state.value.playbackStatus == PlaybackStatus.Ended && fixture.store.state.value.queue?.sessionId == null
                }
                val finalizedHistory = fixture.store.state.value.history
                engine.emit(DesktopAudioEvent(finalGeneration, "READY", 7_900, 8_000, true))
                delay(150)
                engine.emit(DesktopAudioEvent(finalGeneration, "ENDED", 8_000, 8_000))
                delay(150)
                assertEquals(finalizedHistory, fixture.store.state.value.history)
                assertEquals(1, finalizedHistory.count { it.id == finalSession })
                assertTrue(finalizedHistory.single { it.id == finalSession }.finalized)
                assertFalse(controller.state.value.isPlaying)

                controller.selectEntry(entries.first().id)
                eventually("new active session") { controller.state.value.mediaId == entries.first().id && controller.state.value.isPlaying }
                controller.pause()
                eventually("pause observed") { !controller.state.value.isPlaying }
                controller.seekTo(1_200)
                eventually("seek checkpoint persisted") { fixture.store.state.value.queue?.positionMs == 1_200L }
                fixture.stopController()
                val checkpoint = checkNotNull(fixture.store.state.value.queue)
                assertNotNull(checkpoint.sessionId)
                fixture.reopenStore()
                val restoredEngine = PolicyEngine()
                val restored = fixture.start(restoredEngine)
                eventually("restart opens saved session paused") {
                    restoredEngine.opens.isNotEmpty() && restored.state.value.playbackStatus == PlaybackStatus.Ready
                }
                assertFalse(restoredEngine.opens.single().autoplay)
                assertEquals(1_200L, restoredEngine.opens.single().positionMs)
                assertFalse(restored.state.value.isPlaying)
                assertEquals(checkpoint.sessionId, fixture.store.state.value.queue?.sessionId)
                fixture.stopController()
                assertEquals(1, fixture.store.state.value.history.count { it.id == checkpoint.sessionId })
                assertEquals(fixture.store.state.value.history.size, fixture.store.state.value.history.map { it.id }.distinct().size)
            } finally { fixture.close() }
        }
    }
}
