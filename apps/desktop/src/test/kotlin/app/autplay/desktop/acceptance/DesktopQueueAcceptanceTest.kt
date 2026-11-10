package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopCopyInspectionLimits
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportReviewPort
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import app.autplay.application.desktop.DesktopEmbeddedTextFormat
import app.autplay.application.desktop.DesktopEmbeddedTrackTextApplication
import app.autplay.application.desktop.DesktopHistoryCursor
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryPage
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.DesktopSleepTimerLimits
import app.autplay.application.desktop.DesktopTastePolicy
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopTrackFieldProvenance
import app.autplay.application.desktop.DesktopTrackTextOrigin
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.desktop.DesktopRuntime
import app.autplay.desktop.history.DesktopHistoryController
import app.autplay.desktop.library.DesktopCopyInspectionController
import app.autplay.desktop.lifecycle.DesktopNativeLifecycle
import app.autplay.desktop.lifecycle.DesktopNativeLifecycleApi
import app.autplay.desktop.lifecycle.DesktopNativeLifecycleFeature
import app.autplay.desktop.lifecycle.DesktopShutdownOwner
import app.autplay.desktop.lifecycle.DesktopWindowLifecycle
import app.autplay.desktop.metadata.DesktopMetadataController
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.settings.DesktopSettingsController
import app.autplay.playback.presentation.PlaybackStatus
import java.awt.desktop.QuitResponse
import java.nio.file.Path
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withContext
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

class DesktopQueueAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(90)

    @Test fun realQueueKeepsDuplicateEntriesRepeatShuffleAndRestartSessionWhileRejectingStaleEof(): Unit = runBlocking {
        withTimeout(75_000) {
            assertDesktopLifecyclePolicy()
            assertDesktopSystemMediaPolicy()
            assertFtsSearchHostPolicy()
            assertDesktopImportJobsHostPolicy()
            assertDesktopLocalStatisticsHostPolicy()
            DesktopAcceptanceFixture().use { fixture ->
                fixture.openStore()
                val trackId = fixture.importWav(durationMs = 250)
                val batchTrackId = fixture.importWav("Batch second tone.wav", durationMs = 750)
                val batchPlaylist = fixture.store.createPlaylist("Full duplicate batch")
                listOf(trackId, batchTrackId, trackId).forEach { fixture.store.appendToPlaylist(batchPlaylist.id, it) }
                val emptyPlaylist = fixture.store.createPlaylist("Empty batch")
                val engine = PolicyEngine().apply { deferOpen = true }
                val queueWrites = AtomicInteger()
                val timerNow = AtomicLong(Long.MAX_VALUE - 30_000_000_000L)
                val deferTimerPause = AtomicBoolean(false)
                val pauseRequests = AtomicInteger()
                val timerEngine = object : DesktopAudioEngine by engine {
                    override fun pause(generation: Long): Unit {
                        pauseRequests.incrementAndGet()
                        if (!deferTimerPause.get()) engine.pause(generation)
                    }
                }
                val realStore = fixture.store
                val unknownCollectionTrack = AtomicReference<String?>(null)
                val removeBeforeQueueSave = AtomicReference<String?>(null)
                val rejectedQueueEvent = AtomicReference<DesktopListeningEvent?>(null)
                val replacementSaveGate = AtomicReference<ReplacementSaveGate?>(null)
                val insertionSaveGate = AtomicReference<MetadataApplyGate?>(null)
                val tasteProbe = TasteProbe()
                val observedStore = object : DesktopLibraryStore by realStore {
                    // Unknown persisted states are projected without normalizing the real store.
                    override val state: StateFlow<DesktopLibrarySnapshot> =
                        object : StateFlow<DesktopLibrarySnapshot> by realStore.state {
                            override val value: DesktopLibrarySnapshot
                                get() {
                                    val snapshot = realStore.state.value
                                    val unknownId = unknownCollectionTrack.get() ?: return snapshot
                                    return snapshot.copy(tracks = snapshot.tracks.map {
                                        if (it.id == unknownId) it.copy(collectionState = "FUTURE_COLLECTION") else it
                                    })
                                }
                        }
                    override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                        queueWrites.incrementAndGet()
                        insertionSaveGate.getAndSet(null)?.let { insertion ->
                            insertion.entered.complete(Unit)
                            insertion.release.await()
                            insertion.failure?.let { throw it }
                        }
                        val gate = replacementSaveGate.get()?.takeIf { queue.id != it.oldQueueId }
                        if (gate != null && !gate.entered.isCompleted) {
                            gate.entered.complete(QueueSaveObservation(queue, event, realStore.state.value,
                                engine.state.value, engine.opens.size, engine.stops.size))
                            // Hold the candidate's first admission before the real transaction.
                            gate.release.await()
                        }
                        val removedId = removeBeforeQueueSave.get()
                        val retained = realStore.state.value.queue?.entries.orEmpty().associate { it.id to it.trackId }
                        if (removedId != null && queue.entries.any { it.trackId == removedId && retained[it.id] != it.trackId } &&
                            removeBeforeQueueSave.compareAndSet(removedId, null)) {
                            // The actor already captured ACTIVE. Exercise real transactional
                            // rejection, rather than simulating a controller-side validation error.
                            rejectedQueueEvent.set(event)
                            realStore.removeTrackFromCollection(removedId)
                        }
                        realStore.saveQueue(queue, event)
                        gate?.writes?.add(QueueSaveObservation(queue, event, realStore.state.value,
                            engine.state.value, engine.opens.size, engine.stops.size))
                    }
                    override suspend fun setCurrentListenTasteExcluded(
                        expectedQueueId: String, expectedEntryId: String, expectedListeningEventId: String, excluded: Boolean,
                    ): DesktopQueue? {
                        tasteProbe.listenCalls.incrementAndGet()
                        tasteProbe.failure.getAndSet(null)?.let { throw it }
                        val result = if (tasteProbe.retireNext.getAndSet(false)) null else
                            realStore.setCurrentListenTasteExcluded(expectedQueueId, expectedEntryId, expectedListeningEventId, excluded)
                        tasteProbe.results.add(result)
                        return result
                    }
                    override suspend fun setQueueSessionTasteExcluded(expectedQueueId: String, excluded: Boolean): DesktopQueue? {
                        tasteProbe.sessionCalls.incrementAndGet()
                        tasteProbe.failure.getAndSet(null)?.let { throw it }
                        val result = if (tasteProbe.retireNext.getAndSet(false)) null else
                            realStore.setQueueSessionTasteExcluded(expectedQueueId, excluded)
                        tasteProbe.results.add(result)
                        return result
                    }
                }
                val controller = fixture.start(timerEngine, observedStore, timerNowNanos = timerNow::get)
                controller.enqueuePlaylist("missing-playlist")
                controller.playPlaylistNext(emptyPlaylist.id)
                controller.enqueuePlaylist(batchPlaylist.id)
                eventually("whole playlist creates one paused checkpoint before native readiness") {
                    engine.opens.size == 1 && controller.state.value.playbackStatus == PlaybackStatus.Buffering &&
                        fixture.store.state.value.queue?.entries?.size == 3 && controller.taste.value.listenCommandsEnabled
                }
                val pausedBatch = checkNotNull(fixture.store.state.value.queue)
                assertEquals(listOf(trackId, batchTrackId, trackId), pausedBatch.entries.map { it.trackId })
                assertEquals(3, pausedBatch.entries.map { it.id }.distinct().size)
                assertEquals(pausedBatch.entries.map { it.id }, pausedBatch.shuffleOrder)
                assertEquals("PLAYLIST", pausedBatch.queueType)
                assertNotNull(pausedBatch.sessionId)
                assertFalse(engine.opens.single().autoplay)
                assertFalse(controller.state.value.isPlaying)
                assertEquals(1, queueWrites.get())
                assertTrue(controller.taste.value.listenCommandsEnabled)
                assertTrue(controller.taste.value.sessionCommandsEnabled)
                assertEquals(pausedBatch.id, controller.taste.value.queueId)
                assertEquals(pausedBatch.currentEntryId, controller.taste.value.currentEntryId)
                assertEquals(pausedBatch.sessionId, controller.taste.value.listeningEventId)
                // Annotation admission depends on local identity, even before native READY.
                controller.setCurrentListenTasteExcluded(pausedBatch.id, checkNotNull(pausedBatch.currentEntryId),
                    checkNotNull(pausedBatch.sessionId), true)
                eventually("buffering listen accepts a confirmed local annotation") { controller.taste.value.listenTasteState == "EXCLUDED" }
                assertEquals(pausedBatch.copy(listenTasteState = "EXCLUDED"), fixture.store.state.value.queue)
                assertEquals(1, engine.opens.size)
                assertFalse(controller.state.value.isPlaying)
                controller.playTracks(listOf(trackId, trackId))
                eventually("header duration appears while native readiness remains pending") {
                    engine.opens.size == 2 && controller.state.value.playbackStatus == PlaybackStatus.Buffering &&
                        controller.state.value.durationMs == 250L
                }
                assertFalse(controller.state.value.isPlaying)
                assertFalse(controller.state.value.isSeekable)
                assertFalse(controller.state.value.seekEnabled)
                val bufferingPauses = pauseRequests.get()
                controller.seekTo(100)
                val bufferingTarget = checkNotNull(controller.playerState.value.target)
                DesktopTransportAction.entries.forEach { controller.transport(bufferingTarget, it, 100) }
                // A subsequent persisted actor action fences the refused transport requests.
                controller.setRepeat("ONE")
                eventually("mode action follows rejected buffering seek") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                assertTrue(engine.seeks.isEmpty())
                assertEquals(2, engine.opens.size)
                assertEquals(bufferingPauses, pauseRequests.get())
                assertFalse(engine.state.value.isPlaying)
                assertEquals(0L, fixture.store.state.value.queue?.positionMs)
                controller.setRepeat("OFF")
                engine.deferOpen = false
                engine.emit(DesktopAudioEvent(engine.opens.last().generation, "READY", 0, 8_000, true))
                eventually("valid queue opens before limit rejection") {
                    controller.state.value.isPlaying && controller.state.value.durationMs == 8_000L &&
                        fixture.store.state.value.queue?.entries?.size == 2 && fixture.store.state.value.queue?.repeatMode == "OFF" &&
                        fixture.store.state.value.tracks.single { it.id == trackId }.durationMs == 8_000L
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

                engine.deferSeek = true
                controller.seekTo(4_000)
                eventually("deferred seek dispatched") { engine.seeks.lastOrNull()?.positionMs == 4_000L }
                assertEquals(1_200L, fixture.store.state.value.queue?.positionMs)
                val seekingGeneration = engine.state.value.generation
                engine.emit(DesktopAudioEvent(seekingGeneration, "BUFFERING", 0, 8_000))
                eventually("seek buffering observed") { controller.state.value.playbackStatus == PlaybackStatus.Buffering }
                assertEquals(1_200L, fixture.store.state.value.queue?.positionMs)
                engine.emit(DesktopAudioEvent(seekingGeneration, "READY", 1_000, 8_000))
                eventually("unrelated READY cannot complete pending seek") {
                    controller.state.value.playbackStatus == PlaybackStatus.Ready && !controller.state.value.seekEnabled
                }
                assertEquals(1_200L, fixture.store.state.value.queue?.positionMs)
                val admittedSeeks = engine.seeks.size
                controller.seekTo(6_000)
                delay(150)
                assertEquals(admittedSeeks, engine.seeks.size)
                engine.emit(DesktopAudioEvent(seekingGeneration, "READY", 4_000, 8_000))
                eventually("native-observed seek position persisted") { fixture.store.state.value.queue?.positionMs == 4_000L }
                engine.deferSeek = false
                controller.seekTo(1_200)
                eventually("restart position confirmed") { fixture.store.state.value.queue?.positionMs == 1_200L }

                // Edit only future entries while a past entry and the current paused session remain.
                controller.selectEntry(entries[1].id)
                eventually("queue edit current selected") { controller.state.value.mediaId == entries[1].id && controller.state.value.isPlaying }
                controller.pause()
                eventually("queue edit current paused") { !controller.state.value.isPlaying }
                controller.seekTo(1_200)
                eventually("queue edit observed position retained") { fixture.store.state.value.queue?.positionMs == 1_200L }
                suspend fun assertQueueEditsAndCapacity(): Unit {
                    val editBase = checkNotNull(fixture.store.state.value.queue)
                    val editGeneration = engine.state.value.generation
                    val editOpens = engine.opens.size
                    val editStops = engine.stops.size
                    fun assertCurrentRetained(): Unit {
                        val edited = checkNotNull(fixture.store.state.value.queue)
                        assertEquals(editBase.currentEntryId, edited.currentEntryId)
                        assertEquals(editBase.sessionId, edited.sessionId)
                        assertEquals(1_200L, edited.positionMs)
                        assertTrue(edited.playedMs >= editBase.playedMs)
                        // Injected late callbacks change the fake event flow, not the executed selection.
                        assertEquals(editGeneration, engine.opens.last().generation)
                        assertEquals(editOpens, engine.opens.size)
                        assertEquals(editStops, engine.stops.size)
                        assertFalse(controller.state.value.isPlaying)
                    }
                    // Track preference changes do not control the selected listen or native execution.
                    val preferenceHistory = fixture.store.state.value.history
                    val preferenceQueueWrites = queueWrites.get()
                    listOf("LIKED", "DISLIKED", "NEUTRAL", "DISLIKED").forEach { preference ->
                        fixture.store.setTrackPreference(trackId, preference)
                        assertEquals(preference, fixture.store.state.value.tracks.single { it.id == trackId }.preference)
                        assertEquals(editBase, fixture.store.state.value.queue)
                        assertEquals(preferenceHistory, fixture.store.state.value.history)
                        assertEquals(preferenceQueueWrites, queueWrites.get())
                        assertCurrentRetained()
                    }
                    fixture.store.updateTrackTextMetadata(trackId, "Manual title", "Manual artist")
                    engine.emit(DesktopAudioEvent(editGeneration, "READY", 1_200, 9_000,
                        title = "Conflicting native title", artist = "Conflicting native artist"))
                    eventually("native readiness updates only duration and keeps manual labels") {
                        fixture.store.state.value.tracks.single { it.id == trackId }.let {
                            it.title == "Manual title" && it.artist == "Manual artist" && it.durationMs == 9_000L &&
                                it.preference == "DISLIKED"
                        } && controller.state.value.durationMs == 9_000L
                    }
                    assertEquals(editBase, fixture.store.state.value.queue)
                    assertEquals(editBase.sessionStartedAtMs, fixture.store.state.value.queue?.sessionStartedAtMs)
                    assertCurrentRetained()
                    controller.playNext(trackId)
                    eventually("play next creates a distinct future entry") { fixture.store.state.value.queue?.entries?.size == 4 }
                    val inserted = checkNotNull(fixture.store.state.value.queue).entries.single { candidate -> editBase.entries.none { it.id == candidate.id } }
                    assertEquals(trackId, inserted.trackId)
                    assertEquals(listOf(entries[0].id, entries[1].id, inserted.id, entries[2].id), fixture.store.state.value.queue?.entries?.map { it.id })
                    controller.moveUpcoming(inserted.id, 1)
                    eventually("future entry moves down in original order") { fixture.store.state.value.queue?.entries?.lastOrNull()?.id == inserted.id }
                    assertEquals(editBase.entries.map { it.id } + inserted.id, fixture.store.state.value.queue?.shuffleOrder)
                    assertCurrentRetained()
                    val writesBeforeNoOps = queueWrites.get()
                    controller.moveUpcoming(entries[0].id, 1)
                    controller.moveUpcoming(entries[1].id, 1)
                    controller.moveUpcoming(inserted.id, 1)
                    controller.moveUpcoming("missing-entry", -1)
                    controller.moveUpcoming(inserted.id, 0)
                    controller.setRepeat("ONE")
                    eventually("mode action follows invalid future moves") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                    assertEquals(writesBeforeNoOps + 1, queueWrites.get())
                    controller.clearUpcoming()
                    eventually("clear retains past and current in ordinary order") { fixture.store.state.value.queue?.entries?.size == 2 }
                    assertEquals(editBase.entries.take(2).map { it.id }, fixture.store.state.value.queue?.entries?.map { it.id })
                    assertEquals("ONE", fixture.store.state.value.queue?.repeatMode)
                    assertCurrentRetained()
                    controller.setShuffle(true)
                    eventually("shuffle order starts with current") { fixture.store.state.value.queue?.shuffleEnabled == true }
                    controller.playNext(trackId)
                    eventually("play next inserts into both shuffle and original order") { fixture.store.state.value.queue?.entries?.size == 3 }
                    val shuffledEdit = checkNotNull(fixture.store.state.value.queue)
                    assertEquals(shuffledEdit.entries.last().id, shuffledEdit.shuffleOrder[1])
                    controller.moveUpcoming(entries[0].id, -1)
                    eventually("shuffle future move changes active order") { fixture.store.state.value.queue?.shuffleOrder?.get(1) == entries[0].id }
                    assertEquals(shuffledEdit.entries, fixture.store.state.value.queue?.entries)

                    // Resolve full persisted batches in the actor, retaining a pending native seek.
                    val batchBase = checkNotNull(fixture.store.state.value.queue)
                    engine.deferSeek = true
                    controller.seekTo(4_000)
                    eventually("batch edit begins with a pending seek") { engine.seeks.lastOrNull()?.positionMs == 4_000L }
                    controller.setRepeat("ALL")
                    eventually("pending seek dispatch checkpoint precedes the batch") { fixture.store.state.value.queue?.repeatMode == "ALL" }
                    val writesBeforeAppend = queueWrites.get()
                    controller.enqueuePlaylist(batchPlaylist.id)
                    eventually("enqueue appends the full duplicate batch") { fixture.store.state.value.queue?.entries?.size == 6 }
                    val appendedBatch = checkNotNull(fixture.store.state.value.queue).entries.takeLast(3)
                    assertEquals(listOf(trackId, batchTrackId, trackId), appendedBatch.map { it.trackId })
                    assertEquals(batchBase.entries + appendedBatch, fixture.store.state.value.queue?.entries)
                    assertEquals(batchBase.shuffleOrder + appendedBatch.map { it.id }, fixture.store.state.value.queue?.shuffleOrder)
                    assertEquals(writesBeforeAppend + 1, queueWrites.get())
                    assertFalse(controller.state.value.seekEnabled)
                    assertCurrentRetained()
                    val persistedBatch = fixture.store.state.value.playlists.single { it.id == batchPlaylist.id }
                    fixture.store.movePlaylistEntry(batchPlaylist.id, persistedBatch.entries.last().id, -1)
                    val writesBeforeNextBatch = queueWrites.get()
                    controller.playPlaylistNext(batchPlaylist.id)
                    eventually("next batch uses latest persisted order in both lists") { fixture.store.state.value.queue?.entries?.size == 9 }
                    val nextBatchQueue = checkNotNull(fixture.store.state.value.queue)
                    val nextBatch = nextBatchQueue.entries.subList(2, 5)
                    assertEquals(listOf(trackId, trackId, batchTrackId), nextBatch.map { it.trackId })
                    assertEquals(batchBase.entries.take(2) + nextBatch + batchBase.entries.drop(2) + appendedBatch, nextBatchQueue.entries)
                    assertEquals(batchBase.shuffleOrder.take(1) + nextBatch.map { it.id } + batchBase.shuffleOrder.drop(1) + appendedBatch.map { it.id }, nextBatchQueue.shuffleOrder)
                    assertEquals(9, nextBatchQueue.entries.map { it.id }.distinct().size)
                    assertEquals(writesBeforeNextBatch + 1, queueWrites.get())
                    assertFalse(controller.state.value.seekEnabled)
                    assertCurrentRetained()

                    // Raw selection batches share the same owner and freeze before actor admission.
                    controller.stopAfterCurrent(nextBatchQueue.id, checkNotNull(nextBatchQueue.currentEntryId),
                        checkNotNull(nextBatchQueue.sessionId))
                    eventually("raw batches begin with an armed current-listen timer") { controller.sleepTimer.value.planId != null }
                    val selectedTimer = controller.sleepTimer.value
                    val selectedTarget = controller.playerState.value.target
                    val selectedHistory = fixture.store.state.value.history
                    val rawGate = MetadataApplyGate()
                    insertionSaveGate.set(rawGate)
                    val writesBeforeRaw = queueWrites.get()
                    controller.enqueueTracks(listOf(batchTrackId, trackId, batchTrackId))
                    rawGate.entered.await()
                    val mutableSelection = mutableListOf(trackId, batchTrackId, trackId)
                    controller.playTracksNext(mutableSelection)
                    mutableSelection.clear()
                    mutableSelection.addAll(List(1_001) { "missing-after-dispatch" })
                    val mutableEnqueue = mutableListOf(batchTrackId, batchTrackId, trackId)
                    controller.enqueueTracks(mutableEnqueue)
                    mutableEnqueue.clear()
                    mutableEnqueue.add("missing-after-dispatch")
                    assertEquals(nextBatchQueue, fixture.store.state.value.queue)
                    assertEquals(selectedHistory, fixture.store.state.value.history)
                    assertEquals(selectedTimer, controller.sleepTimer.value)
                    assertEquals(selectedTarget, controller.playerState.value.target)
                    assertCurrentRetained()
                    rawGate.release.complete(Unit)
                    eventually("raw batches commit their original ordered duplicate occurrences") {
                        fixture.store.state.value.queue?.entries?.size == 18
                    }
                    val rawQueue = checkNotNull(fixture.store.state.value.queue)
                    val rawAppend = rawQueue.entries.takeLast(6).take(3)
                    val frozenAppend = rawQueue.entries.takeLast(3)
                    val rawNext = rawQueue.entries.subList(2, 5)
                    assertEquals(listOf(batchTrackId, trackId, batchTrackId), rawAppend.map { it.trackId })
                    assertEquals(listOf(batchTrackId, batchTrackId, trackId), frozenAppend.map { it.trackId })
                    assertEquals(listOf(trackId, batchTrackId, trackId), rawNext.map { it.trackId })
                    assertEquals(nextBatchQueue.entries.take(2) + rawNext + nextBatchQueue.entries.drop(2) + rawAppend + frozenAppend,
                        rawQueue.entries)
                    assertEquals(nextBatchQueue.shuffleOrder.take(1) + rawNext.map { it.id } +
                        nextBatchQueue.shuffleOrder.drop(1) + rawAppend.map { it.id } + frozenAppend.map { it.id }, rawQueue.shuffleOrder)
                    assertEquals(18, rawQueue.entries.map { it.id }.distinct().size)
                    assertEquals(writesBeforeRaw + 3, queueWrites.get())
                    assertEquals(selectedTarget, controller.playerState.value.target)
                    assertEquals(selectedTimer, controller.sleepTimer.value)
                    assertFalse(controller.state.value.seekEnabled)
                    assertCurrentRetained()

                    for (afterCurrent in listOf(false, true)) {
                        val failureGate = MetadataApplyGate(DesktopStorageException("desktop_outbox_payload_invalid"))
                        insertionSaveGate.set(failureGate)
                        val beforeFailure = fixture.store.state.value
                        val failureWrites = queueWrites.get()
                        if (afterCurrent) controller.playTracksNext(listOf(trackId, batchTrackId))
                        else controller.enqueueTracks(listOf(batchTrackId, trackId))
                        failureGate.entered.await()
                        assertEquals(beforeFailure, fixture.store.state.value)
                        failureGate.release.complete(Unit)
                        val fenceMode = if (afterCurrent) "ALL" else "ONE"
                        controller.setRepeat(fenceMode)
                        eventually("raw batch storage refusal retains its owner $afterCurrent") {
                            fixture.store.state.value.queue?.repeatMode == fenceMode
                        }
                        assertEquals("PLAYBACK_OPERATION_FAILED", controller.error.value)
                        assertEquals(beforeFailure.copy(queue = beforeFailure.queue?.copy(repeatMode = fenceMode)), fixture.store.state.value)
                        assertEquals(failureWrites + 2, queueWrites.get())
                        assertEquals(selectedTarget, controller.playerState.value.target)
                        assertEquals(selectedTimer, controller.sleepTimer.value)
                        assertFalse(controller.state.value.seekEnabled)
                        assertCurrentRetained()
                    }
                    engine.emit(DesktopAudioEvent(editGeneration - 1, "EOF", 8_000, 8_000))
                    controller.clearUpcoming()
                    eventually("shuffle clear retains only effective past and current") { fixture.store.state.value.queue?.entries?.size == 1 }
                    assertEquals(listOf(entries[1].id), fixture.store.state.value.queue?.shuffleOrder)
                    assertCurrentRetained()
                    engine.emit(DesktopAudioEvent(editGeneration, "READY", 4_000, 8_000))
                    eventually("seek retained across both batches completes on native facts") { fixture.store.state.value.queue?.positionMs == 4_000L }
                    engine.deferSeek = false
                    controller.seekTo(1_200)
                    eventually("batch edit position restored before capacity scene") { fixture.store.state.value.queue?.positionMs == 1_200L }
                    val writesBeforeEmptyClear = queueWrites.get()
                    controller.clearUpcoming()
                    controller.setRepeat("OFF")
                    eventually("mode action follows empty clear") { fixture.store.state.value.queue?.repeatMode == "OFF" }
                    assertEquals(writesBeforeEmptyClear + 1, queueWrites.get())

                    controller.playTracks(List(998) { trackId })
                    eventually("batch capacity fixture opens") { fixture.store.state.value.queue?.entries?.size == 998 && controller.state.value.isPlaying }
                    controller.pause()
                    eventually("batch capacity fixture checkpoints paused") { !controller.state.value.isPlaying && !engine.state.value.isPlaying }
                    controller.setRepeat("ALL")
                    eventually("paused capacity fence") { fixture.store.state.value.queue?.repeatMode == "ALL" }
                    val batchCapacity = checkNotNull(fixture.store.state.value.queue)
                    val batchCapacityWrites = queueWrites.get()
                    val batchCapacityOpens = engine.opens.toList()
                    val batchCapacityStops = engine.stops.toList()
                    controller.enqueuePlaylist(batchPlaylist.id)
                    controller.playPlaylistNext(batchPlaylist.id)
                    controller.enqueuePlaylist("missing-playlist")
                    controller.playPlaylistNext("missing-playlist")
                    controller.enqueuePlaylist(emptyPlaylist.id)
                    controller.playPlaylistNext(emptyPlaylist.id)
                    controller.enqueueTracks(listOf(trackId, batchTrackId, trackId))
                    controller.playTracksNext(listOf(trackId, batchTrackId, trackId))
                    controller.enqueueTracks(List(1_001) { trackId })
                    controller.playTracksNext(List(1_001) { trackId })
                    controller.enqueueTracks(emptyList())
                    controller.playTracksNext(emptyList())
                    controller.setRepeat("OFF")
                    eventually("batch overflow and no-ops finish before the fence") { fixture.store.state.value.queue?.repeatMode == "OFF" }
                    assertEquals("QUEUE_LIMIT", controller.error.value)
                    assertEquals(batchCapacity.copy(repeatMode = "OFF"), fixture.store.state.value.queue)
                    assertEquals(batchCapacityWrites + 1, queueWrites.get())
                    assertEquals(batchCapacityOpens, engine.opens.toList())
                    assertEquals(batchCapacityStops, engine.stops.toList())

                    controller.enqueueTracks(listOf(trackId, batchTrackId))
                    eventually("a complete raw batch fills exactly the remaining two slots") {
                        fixture.store.state.value.queue?.entries?.size == 1_000
                    }
                    val filledBatch = checkNotNull(fixture.store.state.value.queue)
                    assertEquals(listOf(trackId, batchTrackId), filledBatch.entries.takeLast(2).map { it.trackId })
                    assertEquals(batchCapacity.copy(repeatMode = "OFF").entries, filledBatch.entries.dropLast(2))
                    assertEquals(batchCapacity.currentEntryId, filledBatch.currentEntryId)
                    assertEquals(batchCapacity.sessionId, filledBatch.sessionId)
                    assertEquals(batchCapacityWrites + 2, queueWrites.get())
                    assertEquals(batchCapacityOpens, engine.opens.toList())
                    assertEquals(batchCapacityStops, engine.stops.toList())
                    assertFalse(controller.state.value.isPlaying)

                    controller.playTracks(List(1_000) { trackId })
                    eventually("queue reaches admitted capacity") { fixture.store.state.value.queue?.entries?.size == 1_000 && controller.state.value.isPlaying }
                    controller.pause()
                    eventually("capacity fixture pauses") { !controller.state.value.isPlaying }
                    val atCapacity = fixture.store.state.value.queue
                    val capacityOpens = engine.opens.size
                    val capacityStops = engine.stops.size
                    val capacityWrites = queueWrites.get()
                    controller.playNext(trackId)
                    eventually("play next rejects before capacity mutation") { controller.error.value == "QUEUE_LIMIT" }
                    assertEquals(atCapacity, fixture.store.state.value.queue)
                    assertEquals(capacityWrites, queueWrites.get())
                    assertEquals(capacityOpens, engine.opens.size)
                    assertEquals(capacityStops, engine.stops.size)
                    controller.clearUpcoming()
                    eventually("capacity can be freed without replacing current") { fixture.store.state.value.queue?.entries?.size == 1 }
                    controller.playNext(trackId)
                    controller.playNext(trackId)
                    eventually("valid play next clears only the limit error") { fixture.store.state.value.queue?.entries?.size == 3 && controller.error.value == null }
                    assertEquals(atCapacity?.sessionId, fixture.store.state.value.queue?.sessionId)
                    assertEquals(capacityOpens, engine.opens.size)
                    assertEquals(capacityStops, engine.stops.size)
                    controller.seekTo(1_200)
                    eventually("edited queue restart position confirmed") { fixture.store.state.value.queue?.positionMs == 1_200L }

                }
                assertQueueEditsAndCapacity()

                suspend fun assertQueueTimers(): Unit {
                    // One process-local timer flow; the injected clock never manufactures played time.
                    val timerQueue = checkNotNull(fixture.store.state.value.queue)
                    controller.scheduleSleepTimer(timerQueue.id, DesktopSleepTimerLimits.MIN_DURATION_MS)
                    eventually("duration timer arms on the paused current session") { controller.sleepTimer.value.remainingMs == 60_000L }
                    val firstPlan = checkNotNull(controller.sleepTimer.value.planId)
                    controller.scheduleSleepTimer(timerQueue.id, DesktopSleepTimerLimits.MAX_DURATION_MS)
                    eventually("rearm replaces the duration plan") { controller.sleepTimer.value.planId != firstPlan }
                    val durationPlan = checkNotNull(controller.sleepTimer.value.planId)
                    controller.cancelSleepTimer(firstPlan)
                    controller.scheduleSleepTimer("obsolete-queue", 60_000)
                    controller.scheduleSleepTimer(timerQueue.id, DesktopSleepTimerLimits.MIN_DURATION_MS - 1)
                    controller.scheduleSleepTimer(timerQueue.id, DesktopSleepTimerLimits.MAX_DURATION_MS + 1)
                    controller.setRepeat("ONE")
                    eventually("timer refusal commands precede the policy fence") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                    assertEquals(durationPlan, controller.sleepTimer.value.planId)
                    assertEquals(DesktopSleepTimerLimits.MAX_DURATION_MS, controller.sleepTimer.value.remainingMs)
                    controller.cancelSleepTimer(durationPlan)
                    eventually("exact plan cancellation clears the armed duration") { controller.sleepTimer.value.planId == null }
                    controller.scheduleSleepTimer(timerQueue.id, 60_000)
                    eventually("duration arms before switching exclusive modes") { controller.sleepTimer.value.remainingMs == 60_000L }
                    controller.stopAfterCurrent(timerQueue.id, checkNotNull(timerQueue.currentEntryId), checkNotNull(timerQueue.sessionId))
                    eventually("stop-after replaces duration rather than combining modes") { controller.sleepTimer.value.stopAfterEntryId == timerQueue.currentEntryId }
                    assertNull(controller.sleepTimer.value.remainingMs)
                    val afterPlan = checkNotNull(controller.sleepTimer.value.planId)
                    controller.stopAfterCurrent(timerQueue.id, checkNotNull(timerQueue.currentEntryId), "obsolete-session")
                    controller.cancelSleepTimer(durationPlan)
                    controller.setRepeat("ALL")
                    eventually("stale stop-after and cancel precede the policy fence") { fixture.store.state.value.queue?.repeatMode == "ALL" }
                    assertEquals(afterPlan, controller.sleepTimer.value.planId)
                    controller.next()
                    eventually("manual session change cancels only the entry-bound plan") {
                        fixture.store.state.value.queue?.sessionId != timerQueue.sessionId && controller.sleepTimer.value.planId == null &&
                            controller.state.value.playbackStatus == PlaybackStatus.Ready
                    }
                    controller.scheduleSleepTimer(timerQueue.id, 60_000)
                    eventually("duration plan arms before navigation within its queue") { controller.sleepTimer.value.remainingMs == 60_000L }
                    val scopedPlan = controller.sleepTimer.value.planId
                    controller.next()
                    eventually("duration plan follows the same queue's new session") { controller.state.value.mediaId == timerQueue.entries.last().id }
                    assertEquals(scopedPlan, controller.sleepTimer.value.planId)
                    controller.playNext(trackId)
                    eventually("future insertion retains the duration plan") { fixture.store.state.value.queue?.entries?.size == 4 }
                    assertEquals(scopedPlan, controller.sleepTimer.value.planId)
                    val timerFuture = checkNotNull(fixture.store.state.value.queue).entries.single { added -> timerQueue.entries.none { it.id == added.id } }
                    controller.removeEntry(timerFuture.id)
                    eventually("future removal retains the duration plan") { fixture.store.state.value.queue?.entries?.size == 3 }
                    assertEquals(scopedPlan, controller.sleepTimer.value.planId)
                    controller.seekTo(1_200)
                    eventually("timer expiry fixture retains observed paused position") { fixture.store.state.value.queue?.positionMs == 1_200L }
                    val beforeExpiry = checkNotNull(fixture.store.state.value.queue)
                    val expiryOpens = engine.opens.size
                    val expiryPauses = pauseRequests.get()
                    timerNow.addAndGet(60_000_000_000L) // Cross the signed nanoTime origin without deadline comparison.
                    controller.setRepeat("OFF")
                    eventually("paused timer expires and checkpoints without replacing the session") {
                        controller.sleepTimer.value.planId == null && fixture.store.state.value.queue?.repeatMode == "OFF"
                    }
                    assertEquals(beforeExpiry.copy(repeatMode = "OFF"), fixture.store.state.value.queue)
                    assertEquals(expiryOpens, engine.opens.size)
                    assertEquals(expiryPauses + 1, pauseRequests.get())
                    assertFalse(controller.state.value.isPlaying)
                    timerNow.addAndGet(60_000_000_000L)
                    controller.setRepeat("ONE")
                    eventually("consumed duration cannot fire twice") { fixture.store.state.value.queue?.repeatMode == "ONE" }
                    assertEquals(expiryPauses + 1, pauseRequests.get())

                    controller.play()
                    eventually("playing session precedes natural duration scope transition") { controller.state.value.isPlaying }
                    controller.scheduleSleepTimer(timerQueue.id, 60_000)
                    eventually("duration arms before natural repeat") { controller.sleepTimer.value.remainingMs == 60_000L }
                    val naturalPlan = checkNotNull(controller.sleepTimer.value.planId)
                    val naturalSession = fixture.store.state.value.queue?.sessionId
                    val naturalGeneration = engine.opens.last().generation
                    engine.emit(DesktopAudioEvent(naturalGeneration, "EOF", 8_000, 8_000))
                    eventually("duration survives natural repeat's fresh session within the queue") {
                        fixture.store.state.value.queue?.sessionId != naturalSession && controller.state.value.isPlaying
                    }
                    assertEquals(naturalPlan, controller.sleepTimer.value.planId)
                    controller.cancelSleepTimer(naturalPlan)
                    eventually("natural-repeat duration cancels by its exact token") { controller.sleepTimer.value.planId == null }

                    for (repeatMode in listOf("ONE", "ALL")) {
                        controller.play()
                        eventually("explicit playback starts before stop-after $repeatMode") { controller.state.value.isPlaying }
                        controller.setRepeat(repeatMode)
                        eventually("stop-after $repeatMode policy is current") { fixture.store.state.value.queue?.repeatMode == repeatMode }
                        val bound = checkNotNull(fixture.store.state.value.queue)
                        val boundGeneration = engine.opens.last().generation
                        val boundOpens = engine.opens.size
                        controller.stopAfterCurrent(bound.id, checkNotNull(bound.currentEntryId), checkNotNull(bound.sessionId))
                        eventually("stop-after $repeatMode arms") { controller.sleepTimer.value.stopAfterEntryId == bound.currentEntryId }
                        engine.emit(DesktopAudioEvent(boundGeneration, "EOF", 8_000, 8_000))
                        eventually("stop-after intercepts $repeatMode EOF and finalizes once") {
                            controller.state.value.playbackStatus == PlaybackStatus.Ended && controller.sleepTimer.value.planId == null &&
                                fixture.store.state.value.queue?.sessionId == null
                        }
                        assertEquals(bound.id, fixture.store.state.value.queue?.id)
                        assertEquals(bound.currentEntryId, fixture.store.state.value.queue?.currentEntryId)
                        assertEquals(boundOpens, engine.opens.size)
                        assertTrue(fixture.store.state.value.history.single { it.id == bound.sessionId }.finalized)
                        val finishedHistory = fixture.store.state.value.history
                        val lateWrites = queueWrites.get()
                        engine.emit(DesktopAudioEvent(boundGeneration, "READY", 7_900, 8_000, true))
                        delay(100)
                        engine.emit(DesktopAudioEvent(boundGeneration, "EOF", 8_001, 8_000))
                        controller.setShuffle(false)
                        eventually("late EOF remains fenced after $repeatMode") { queueWrites.get() > lateWrites && !controller.state.value.isPlaying }
                        assertEquals(finishedHistory, fixture.store.state.value.history)
                        assertEquals(boundOpens, engine.opens.size)
                    }

                    controller.play()
                    eventually("explicit replay restores the session before expired EOF") { controller.state.value.isPlaying }
                    val dueAtEof = checkNotNull(fixture.store.state.value.queue)
                    val dueGeneration = engine.opens.last().generation
                    val dueOpens = engine.opens.size
                    controller.scheduleSleepTimer(dueAtEof.id, 60_000)
                    eventually("duration arms before EOF can enter the actor") { controller.sleepTimer.value.remainingMs == 60_000L }
                    deferTimerPause.set(true)
                    timerNow.addAndGet(60_000_000_000L)
                    engine.emit(DesktopAudioEvent(dueGeneration, "EOF", 8_000, 8_000))
                    eventually("already-due timer blocks EOF advancement without waiting for a tick or pause ACK") {
                        controller.state.value.playbackStatus == PlaybackStatus.Ended && controller.sleepTimer.value.planId == null &&
                            fixture.store.state.value.queue?.positionMs == 8_000L
                    }
                    assertEquals(dueAtEof.sessionId, fixture.store.state.value.queue?.sessionId)
                    assertFalse(fixture.store.state.value.history.single { it.id == dueAtEof.sessionId }.finalized)
                    assertEquals(dueOpens, engine.opens.size)
                    controller.play()
                    eventually("explicit replay can resume after timer EOF") { controller.state.value.isPlaying }
                    controller.scheduleSleepTimer(dueAtEof.id, 60_000)
                    eventually("duration arms before navigation awaiting pause ACK") { controller.sleepTimer.value.remainingMs == 60_000L }
                    val nextOpens = engine.opens.size
                    timerNow.addAndGet(60_000_000_000L)
                    controller.next()
                    eventually("navigation following expiry opens paused despite last observed playing fact") { engine.opens.size == nextOpens + 1 }
                    assertFalse(engine.opens.last().autoplay)
                    deferTimerPause.set(false)
                    eventually("new paused native selection is observed") { controller.state.value.playbackStatus == PlaybackStatus.Ready && !controller.state.value.isPlaying }
                    controller.seekTo(1_200)
                    eventually("timer composite restores restart checkpoint position") { fixture.store.state.value.queue?.positionMs == 1_200L }

                }
                assertQueueTimers()

                exerciseTastePolicy(fixture, controller, engine, trackId, batchTrackId, queueWrites, pauseRequests, tasteProbe,
                    replacementSaveGate)

                val collectionActiveId = exerciseCollectionAdmission(
                    fixture, controller, engine, trackId, batchTrackId, queueWrites,
                    unknownCollectionTrack, removeBeforeQueueSave, rejectedQueueEvent, replacementSaveGate, insertionSaveGate,
                )
                eventually("close cancels an armed process-local plan") { controller.sleepTimer.value.planId != null }
                val targetBeforeClose = checkNotNull(controller.playerState.value.target)
                fixture.stopController()
                assertNull(controller.sleepTimer.value.planId)
                assertFalse(controller.sleepTimer.value.commandsEnabled)
                assertFalse(controller.taste.value.listenCommandsEnabled)
                assertFalse(controller.taste.value.sessionCommandsEnabled)
                val checkpoint = checkNotNull(fixture.store.state.value.queue)
                assertNotNull(checkpoint.sessionId)
                val closedHistory = fixture.store.state.value.history
                val closedNative = engine.state.value
                val closedCalls = tasteProbe.listenCalls.get() + tasteProbe.sessionCalls.get()
                val closedWrites = queueWrites.get()
                val closedSeeks = engine.seeks.toList()
                val closedStops = engine.stops.toList()
                val closedPauses = pauseRequests.get()
                DesktopTransportAction.entries.forEach { controller.transport(targetBeforeClose, it, 7_000) }
                controller.setCurrentListenTasteExcluded(checkpoint.id, checkNotNull(checkpoint.currentEntryId),
                    checkNotNull(checkpoint.sessionId), false)
                controller.setQueueSessionTasteExcluded(checkpoint.id, false)
                assertEquals(closedCalls, tasteProbe.listenCalls.get() + tasteProbe.sessionCalls.get())
                assertEquals(checkpoint, fixture.store.state.value.queue)
                assertEquals(closedHistory, fixture.store.state.value.history)
                assertEquals(closedNative, engine.state.value)
                assertEquals(closedWrites, queueWrites.get())
                assertEquals(closedSeeks, engine.seeks.toList())
                assertEquals(closedStops, engine.stops.toList())
                assertEquals(closedPauses, pauseRequests.get())
                suspend fun assertQueueRestartAndRefusal(): Unit {
                    fixture.reopenStore()
                    assertEquals("DISLIKED", fixture.store.state.value.tracks.single { it.id == trackId }.preference)
                    assertEquals("REMOVED", fixture.store.state.value.tracks.single { it.id == trackId }.collectionState)
                    assertEquals("REMOVED", fixture.store.state.value.tracks.single { it.id == collectionActiveId }.collectionState)
                    val restoredEngine = PolicyEngine()
                    val restored = fixture.start(restoredEngine, timerNowNanos = timerNow::get)
                    eventually("restart opens saved session paused") {
                        restoredEngine.opens.isNotEmpty() && restored.state.value.playbackStatus == PlaybackStatus.Ready &&
                            restored.taste.value.listeningEventId == checkpoint.sessionId &&
                            restored.taste.value.sessionTasteState == checkpoint.sessionTasteState && restored.taste.value.listenTasteState == checkpoint.listenTasteState
                    }
                    assertFalse(restoredEngine.opens.single().autoplay)
                    assertEquals(1_200L, restoredEngine.opens.single().positionMs)
                    assertFalse(restored.state.value.isPlaying)
                    assertEquals(checkpoint.sessionId, fixture.store.state.value.queue?.sessionId)
                    assertEquals("EXCLUDED", checkpoint.sessionTasteState)
                    assertEquals("EXCLUDED", checkpoint.listenTasteState)
                    assertEquals(checkpoint.sessionTasteState, restored.taste.value.sessionTasteState)
                    assertEquals(checkpoint.listenTasteState, restored.taste.value.listenTasteState)
                    assertEquals(checkpoint.sessionId, restored.taste.value.listeningEventId)
                    assertNull(restored.sleepTimer.value.planId)

                    restored.play()
                    eventually("persisted removed current remains playable without reopening") { restored.state.value.isPlaying }
                    restored.pause()
                    eventually("persisted removed current checkpoints paused") { !restored.state.value.isPlaying && !restoredEngine.state.value.isPlaying }
                    restored.moveUpcoming(checkpoint.entries.last().id, -1)
                    eventually("persisted removed upcoming entries can reorder") {
                        fixture.store.state.value.queue?.entries?.get(1)?.id == checkpoint.entries.last().id
                    }
                    restored.moveUpcoming(checkpoint.entries.last().id, 1)
                    eventually("removed queue original order is retained after restoration of order") {
                        fixture.store.state.value.queue?.entries == checkpoint.entries
                    }
                    assertEquals(checkpoint.sessionId, fixture.store.state.value.queue?.sessionId)
                    assertEquals(1_200L, fixture.store.state.value.queue?.positionMs)
                    assertTrue(checkNotNull(fixture.store.state.value.queue).playedMs >= checkpoint.playedMs)
                    assertEquals(1, restoredEngine.opens.size)

                    val pausedGeneration = restoredEngine.state.value.generation
                    restoredEngine.emit(DesktopAudioEvent(pausedGeneration, "ENDED", 8_000, 8_000))
                    eventually("paused EOF checkpoints the retained session") {
                        restored.state.value.playbackStatus == PlaybackStatus.Ended &&
                            fixture.store.state.value.queue?.positionMs == 8_000L
                    }
                    assertEquals(checkpoint.sessionId, fixture.store.state.value.queue?.sessionId)
                    assertFalse(fixture.store.state.value.history.single { it.id == checkpoint.sessionId }.finalized)
                    assertEquals("EXCLUDED", restored.taste.value.listenTasteState)
                    val pausedHistory = fixture.store.state.value.history
                    restoredEngine.emit(DesktopAudioEvent(pausedGeneration, "READY", 7_900, 8_000, true))
                    delay(150)
                    restoredEngine.emit(DesktopAudioEvent(pausedGeneration, "ENDED", 8_000, 8_000))
                    delay(150)
                    assertEquals(pausedHistory, fixture.store.state.value.history)
                    assertFalse(restored.state.value.isPlaying)
                    assertEquals(1, restoredEngine.opens.size)

                    // History replay submits only its track ID; the former finalized event stays immutable.
                    restored.selectEntry(checkpoint.entries[1].id)
                    eventually("existing removed upcoming selection remains playable") {
                        restored.state.value.mediaId == checkpoint.entries[1].id && restored.state.value.isPlaying
                    }
                    restored.pause()
                    eventually("removed upcoming selection pauses before history replay refusal") { !restored.state.value.isPlaying }
                    val replayedEvent = fixture.store.state.value.history.first { it.finalized && it.trackId == trackId }
                    val beforeRemovedReplay = checkNotNull(fixture.store.state.value.queue)
                    val removedReplayHistory = fixture.store.state.value.history
                    val removedReplayNative = restoredEngine.state.value
                    val removedReplayOpens = restoredEngine.opens.toList()
                    val removedReplayStops = restoredEngine.stops.toList()
                    restored.playTracks(listOf(replayedEvent.trackId), "LIBRARY")
                    eventually("history replay is a new selection and refuses removed membership") {
                        restored.error.value == "desktop_track_not_in_collection"
                    }
                    assertEquals(beforeRemovedReplay, fixture.store.state.value.queue)
                    assertEquals(removedReplayHistory, fixture.store.state.value.history)
                    assertEquals(removedReplayNative, restoredEngine.state.value)
                    assertEquals(removedReplayOpens, restoredEngine.opens.toList())
                    assertEquals(removedReplayStops, restoredEngine.stops.toList())
                    fixture.store.restoreTrackToCollection(replayedEvent.trackId)
                    fixture.store.restoreTrackToCollection(collectionActiveId)
                    restored.playTracks(listOf(replayedEvent.trackId), "LIBRARY")
                    eventually("history replay starts a fresh zero-position session") {
                        restored.state.value.isPlaying && fixture.store.state.value.queue?.positionMs == 0L &&
                            fixture.store.state.value.queue?.sessionId != null &&
                            fixture.store.state.value.queue?.sessionId != checkpoint.sessionId &&
                            fixture.store.state.value.queue?.sessionId != replayedEvent.id
                    }
                    assertEquals("LIBRARY", fixture.store.state.value.queue?.queueType)
                    assertEquals(listOf(replayedEvent.trackId), fixture.store.state.value.queue?.entries?.map { it.trackId })
                    assertEquals(replayedEvent, fixture.store.state.value.history.single { it.id == replayedEvent.id })
                    assertEquals(0L, restoredEngine.opens.last().positionMs)
                    restored.pause()
                    eventually("history replay pauses before refusal") { !restored.state.value.isPlaying }

                    // Playlist edits affect the next Play request, never the already selected queue.
                    val otherTrackId = fixture.importWav("Second synthetic tone.wav", durationMs = 500)
                    val playlist = fixture.store.createPlaylist("Duplicate playlist")
                    listOf(trackId, otherTrackId, trackId).forEach { fixture.store.appendToPlaylist(playlist.id, it) }
                    val playlistBefore = fixture.store.state.value.playlists.single { it.id == playlist.id }
                    assertEquals(3, playlistBefore.entries.map { it.id }.distinct().size)
                    val selectedPlaylistEntry = playlistBefore.entries.first()
                    val replayQueueId = checkNotNull(fixture.store.state.value.queue).id
                    restored.scheduleSleepTimer(replayQueueId, 60_000)
                    eventually("timer arms before accepted queue replacement") { restored.sleepTimer.value.planId != null }
                    restored.playTracks(playlistBefore.entries.dropWhile { it.id != selectedPlaylistEntry.id }.map { it.trackId }, "PLAYLIST")
                    eventually("playlist entry starts its own ordered queue") {
                        restored.state.value.isPlaying && fixture.store.state.value.queue?.let {
                            it.queueType == "PLAYLIST" && it.entries.map { entry -> entry.trackId } == listOf(trackId, otherTrackId, trackId)
                        } == true
                    }
                    assertNull(restored.sleepTimer.value.planId)
                    restored.pause()
                    eventually("playlist queue pauses on the native fake") { !restored.state.value.isPlaying && !restoredEngine.state.value.isPlaying }
                    restored.seekTo(1_200)
                    eventually("playlist queue checkpoints observed native position") {
                        fixture.store.state.value.queue?.positionMs == 1_200L && restored.state.value.positionMs == 1_200L
                    }
                    val playlistQueue = checkNotNull(fixture.store.state.value.queue)
                    val playlistSession = checkNotNull(playlistQueue.sessionId)
                    val playlistHistory = fixture.store.state.value.history
                    val playlistNative = restoredEngine.state.value
                    val playlistOpens = restoredEngine.opens.toList()
                    val playlistSeeks = restoredEngine.seeks.toList()
                    val playlistStops = restoredEngine.stops.toList()
                    fixture.store.renamePlaylist(playlist.id, "Renamed duplicate playlist")
                    fixture.store.movePlaylistEntry(playlist.id, playlistBefore.entries.last().id, -1)
                    val playlistAfter = fixture.store.state.value.playlists.single { it.id == playlist.id }
                    assertEquals("Renamed duplicate playlist", playlistAfter.name)
                    assertEquals(listOf(playlistBefore.entries[0], playlistBefore.entries[2], playlistBefore.entries[1]), playlistAfter.entries)
                    assertEquals(playlistQueue, fixture.store.state.value.queue)
                    assertEquals(playlistHistory, fixture.store.state.value.history)
                    assertEquals(playlistQueue.currentEntryId, restored.state.value.mediaId)
                    assertEquals(1_200L, restored.state.value.positionMs)
                    assertFalse(restored.state.value.isPlaying)
                    assertEquals(playlistNative, restoredEngine.state.value)
                    assertEquals(playlistOpens, restoredEngine.opens.toList())
                    assertEquals(playlistSeeks, restoredEngine.seeks.toList())
                    assertEquals(playlistStops, restoredEngine.stops.toList())
                    restored.playTracks(playlistAfter.entries.map { it.trackId }, "PLAYLIST")
                    eventually("next playlist Play takes the edited order and a fresh session") {
                        restored.state.value.isPlaying && fixture.store.state.value.queue?.let {
                            it.id != playlistQueue.id && it.sessionId != null && it.sessionId != playlistSession &&
                                it.entries.map { entry -> entry.trackId } == listOf(trackId, trackId, otherTrackId)
                        } == true
                    }
                    val updatedPlaylistQueue = checkNotNull(fixture.store.state.value.queue)
                    assertEquals("PLAYLIST", updatedPlaylistQueue.queueType)
                    assertEquals(3, updatedPlaylistQueue.entries.map { it.id }.distinct().size)
                    assertTrue(updatedPlaylistQueue.entries.none { entry -> playlistQueue.entries.any { it.id == entry.id } })
                    assertEquals(0L, updatedPlaylistQueue.positionMs)
                    assertTrue(restoredEngine.state.value.generation > playlistNative.generation)
                    assertEquals(0L, restoredEngine.opens.last().positionMs)
                    assertTrue(fixture.store.state.value.history.single { it.id == playlistSession }.finalized)
                    restored.pause()
                    eventually("edited playlist pauses before refusal") { !restored.state.value.isPlaying }
                    val refusalQueue = checkNotNull(fixture.store.state.value.queue)
                    restored.scheduleSleepTimer(refusalQueue.id, 60_000)
                    eventually("timer is armed before snapshot refusal") { restored.sleepTimer.value.planId != null }
                    val historyBeforeRefusal = fixture.store.state.value.history
                    val opensBeforeRefusal = restoredEngine.opens.size
                    val displayOnlyQueue = fixture.store.state.value.queue
                    val targetBeforeRefusal = checkNotNull(restored.playerState.value.target)
                    restored.reportSnapshotUnavailable()
                    eventually("snapshot refusal disables controls") {
                        restored.error.value == "desktop_snapshot_unavailable" && !restored.state.value.shuffleEnabled &&
                            !restored.state.value.repeatEnabled && !restored.state.value.seekEnabled &&
                            restored.sleepTimer.value.planId == null && !restored.sleepTimer.value.commandsEnabled &&
                            !restored.taste.value.listenCommandsEnabled && !restored.taste.value.sessionCommandsEnabled
                    }
                    timerNow.addAndGet(60_000_000_000L)
                    val stopsAfterFault = restoredEngine.stops.size
                    val refusedSeeks = restoredEngine.seeks.toList()
                    val refusedNative = restoredEngine.state.value
                    for (target in listOf(targetBeforeRefusal, checkNotNull(restored.playerState.value.target))) {
                        DesktopTransportAction.entries.forEach { restored.transport(target, it, 7_000) }
                    }
                    restored.reportSnapshotUnavailable()
                    restored.playTracks(listOf(trackId))
                    restored.enqueue(trackId)
                    restored.playNext(trackId)
                    restored.enqueuePlaylist(batchPlaylist.id)
                    restored.playPlaylistNext(batchPlaylist.id)
                    restored.enqueueTracks(listOf(trackId, trackId))
                    restored.playTracksNext(listOf(trackId, trackId))
                    restored.moveUpcoming(checkNotNull(displayOnlyQueue).entries.last().id, -1)
                    restored.clearUpcoming()
                    restored.seekTo(0)
                    restored.setCurrentListenTasteExcluded(refusalQueue.id, checkNotNull(refusalQueue.currentEntryId),
                        checkNotNull(refusalQueue.sessionId), true)
                    restored.setQueueSessionTasteExcluded(refusalQueue.id, true)
                    delay(150)
                    assertEquals("desktop_snapshot_unavailable", restored.error.value)
                    assertEquals(displayOnlyQueue, fixture.store.state.value.queue)
                    assertEquals(historyBeforeRefusal, fixture.store.state.value.history)
                    assertEquals(opensBeforeRefusal, restoredEngine.opens.size)
                    assertEquals(stopsAfterFault, restoredEngine.stops.size)
                    assertEquals(refusedSeeks, restoredEngine.seeks.toList())
                    assertEquals(refusedNative, restoredEngine.state.value)
                    fixture.stopController()
                    assertEquals(1, fixture.store.state.value.history.count { it.id == checkpoint.sessionId })
                    assertEquals(fixture.store.state.value.history.size, fixture.store.state.value.history.map { it.id }.distinct().size)
                }
                assertQueueRestartAndRefusal()

                assertMembershipSnapshotFailure(fixture, trackId, timerNow::get)
                assertOpaqueTasteRestart(fixture, trackId, timerNow::get)
                assertTasteSnapshotFailures(fixture, trackId, timerNow::get)
                assertHomeContinuationFence(fixture, trackId)
                // Paging seeds far-future rows; keep that database out of later live-session scenes.
                DesktopAcceptanceFixture().use { historyFixture ->
                    historyFixture.openStore()
                    assertHistoryReadPolicy(historyFixture, historyFixture.importWav())
                }
                assertInitialTrackBatchPolicy(fixture, trackId)
                assertUpcomingGroupPolicy(fixture, trackId)
                assertUpcomingGroupRefusals(fixture, trackId)
                assertMetadataPolicy(fixture, trackId)
                assertSystemSleepPolicy(fixture, trackId)
            }
        }
    }

    /** Source-only / UNVERIFIED: grouped edits extend the existing real-store composite. */
    private suspend fun assertDesktopLifecyclePolicy(): Unit {
        val requestingThread = Thread.currentThread()
        val admissionCalls = AtomicInteger()
        val closeCalls = AtomicInteger()
        val failureReports = AtomicInteger()
        val closeEntered = CompletableDeferred<Unit>()
        val closeRelease = CompletableDeferred<Unit>()
        val closeThread = AtomicReference<Thread?>(null)
        val sleepCalls = AtomicInteger()
        val shutdown = DesktopShutdownOwner(
            closeAdmission = { admissionCalls.incrementAndGet(); Unit },
            closeRuntime = {
                closeThread.set(Thread.currentThread())
                closeCalls.incrementAndGet()
                closeEntered.complete(Unit)
                closeRelease.await()
            },
            reportFailure = { failureReports.incrementAndGet(); Unit },
        )
        val lifecycle = DesktopWindowLifecycle({ shutdown.request(); Unit })
        val bindings = mutableListOf<DesktopNativeLifecycle>()
        val firstResponse = LifecycleQuitResponse()
        val secondResponse = LifecycleQuitResponse()
        try {
            assertTrue(lifecycle.state.value.visible)
            assertFalse(lifecycle.state.value.canHideWindow)
            assertEquals(0L, lifecycle.state.value.settingsRequest)
            val api = LifecycleApiFixture()
            val binding = DesktopNativeLifecycle.install(api, lifecycle, onSystemSleep = {
                assertSame(requestingThread, Thread.currentThread())
                sleepCalls.incrementAndGet(); Unit
            }).also { bindings.add(it) }
            val settings = checkNotNull(api.settingsCallback)
            val reopen = checkNotNull(api.reopenCallback)
            val quit = checkNotNull(api.quitCallback)
            val sleep = checkNotNull(api.sleepCallback)
            val wake = checkNotNull(api.wakeCallback)
            assertTrue(lifecycle.state.value.canHideWindow)
            lifecycle.closeWindow()
            lifecycle.closeWindow()
            assertFalse(lifecycle.state.value.visible)
            assertFalse(shutdown.requested.value)
            reopen()
            assertTrue(lifecycle.state.value.visible)
            assertEquals(1L, lifecycle.state.value.focusRequest)
            assertEquals(0L, lifecycle.state.value.settingsRequest)
            lifecycle.closeWindow()
            settings()
            assertTrue(lifecycle.state.value.visible)
            assertEquals(1L, lifecycle.state.value.settingsRequest)
            repeat(128) { settings() }
            assertEquals(129L, lifecycle.state.value.settingsRequest)
            assertEquals(0, closeCalls.get())
            assertEquals(0, admissionCalls.get())

            // Removing an installed route restores a hidden window, and old callbacks stay retired.
            lifecycle.closeWindow()
            val hidden = lifecycle.state.value
            sleep(); wake()
            assertEquals(hidden, lifecycle.state.value)
            assertEquals(1, sleepCalls.get())
            binding.retire()
            binding.retire()
            assertTrue(lifecycle.state.value.visible)
            assertFalse(lifecycle.state.value.canHideWindow)
            assertEquals(1, api.removals)
            assertEquals(1, api.sleepRemovals)
            assertEquals(1, api.settingsClears)
            val afterRetirement = lifecycle.state.value
            settings(); reopen(); quit(firstResponse); quit(firstResponse); sleep(); wake()
            assertEquals(afterRetirement, lifecycle.state.value)
            assertEquals(1, sleepCalls.get())
            assertFalse(shutdown.requested.value)

            val replacement = LifecycleApiFixture()
            val replacementBinding = DesktopNativeLifecycle.install(replacement, lifecycle,
                onSystemSleep = { sleepCalls.incrementAndGet(); Unit }).also { bindings.add(it) }
            assertTrue(lifecycle.state.value.canHideWindow)
            settings(); reopen(); quit(secondResponse)
            assertEquals(afterRetirement.settingsRequest, lifecycle.state.value.settingsRequest)
            assertFalse(shutdown.requested.value)
            lifecycle.closeWindow()
            val nativeQuit = checkNotNull(replacement.quitCallback)
            nativeQuit(firstResponse)
            assertTrue(shutdown.requested.value)
            assertTrue(lifecycle.state.value.closing)
            assertEquals(1, admissionCalls.get())
            val attempt = shutdown.request()
            closeEntered.await()
            assertNotSame(requestingThread, closeThread.get())
            assertEquals(1, closeCalls.get())
            assertFalse(attempt.isCompleted)
            val closing = lifecycle.state.value
            repeat(128) {
                nativeQuit(firstResponse); nativeQuit(secondResponse); nativeQuit(firstResponse)
                checkNotNull(replacement.sleepCallback)(); checkNotNull(replacement.wakeCallback)()
                lifecycle.quit(); lifecycle.closeWindow(); lifecycle.showWindow(); lifecycle.showSettings()
                assertSame(attempt, shutdown.request())
            }
            assertEquals(closing, lifecycle.state.value)
            assertEquals(1, admissionCalls.get())
            assertEquals(1, closeCalls.get())
            assertEquals(1, sleepCalls.get())
            assertEquals(0, firstResponse.decisions)
            assertEquals(0, secondResponse.decisions)

            // Cancelling a composition-like waiter cannot cancel the process-owned close attempt.
            val waiterScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            try {
                val waiter = waiterScope.async { attempt.await() }
                waiter.cancel()
                waiter.join()
                assertFalse(attempt.isCancelled)
                closeRelease.complete(Unit)
                assertTrue(attempt.await().isSuccess)
            } finally { waiterScope.cancel() }
            assertSame(attempt, shutdown.request())
            assertEquals(1, closeCalls.get())
            assertEquals(0, failureReports.get())
            lifecycle.dispose()
            replacementBinding.retire()
            val disposed = lifecycle.state.value
            checkNotNull(replacement.quitCallback)(firstResponse)
            settings(); reopen(); lifecycle.showWindow(); lifecycle.showSettings(); lifecycle.closeWindow(); lifecycle.quit()
            assertEquals(disposed, lifecycle.state.value)
            assertTrue(disposed.disposed)
            assertEquals(1, closeCalls.get())
        } finally {
            closeRelease.complete(Unit)
            lifecycle.dispose()
            bindings.forEach { it.retire() }
            withContext(NonCancellable) { shutdown.request().await() }
        }

        // Absent, unsupported and failed native routes retain close-to-quit with a visible window.
        val incomplete = listOf(emptySet(), setOf(DesktopNativeLifecycleFeature.Quit),
            setOf(DesktopNativeLifecycleFeature.Reopen), setOf(DesktopNativeLifecycleFeature.Settings))
        val fallbacks = incomplete.map { LifecycleApiFixture(features = it) } + listOf(
            LifecycleApiFixture(isMacOS = false),
            LifecycleApiFixture(failure = DesktopNativeLifecycleFeature.Quit),
            LifecycleApiFixture(failure = DesktopNativeLifecycleFeature.Reopen),
            LifecycleApiFixture(failPreparation = true),
        )
        for (api in fallbacks + listOf(null)) {
            var requests = 0
            val fallback = DesktopWindowLifecycle({ requests += 1 })
            val binding = DesktopNativeLifecycle.install(api, fallback)
            assertFalse(fallback.state.value.canHideWindow)
            fallback.closeWindow()
            fallback.closeWindow(); fallback.quit()
            assertTrue(fallback.state.value.visible)
            assertTrue(fallback.state.value.closing)
            assertEquals(1, requests)
            fallback.dispose(); binding.retire()
            if (api != null && !api.isMacOS) {
                assertEquals(0, api.supportQueries)
                assertEquals(0, api.installations)
            }
        }
        for (api in listOf(
            LifecycleApiFixture(features = setOf(DesktopNativeLifecycleFeature.Quit, DesktopNativeLifecycleFeature.Reopen)),
            LifecycleApiFixture(failure = DesktopNativeLifecycleFeature.Settings),
        )) {
            var requests = 0
            val menuFallback = DesktopWindowLifecycle({ requests += 1 })
            val binding = DesktopNativeLifecycle.install(api, menuFallback)
            assertTrue(menuFallback.state.value.canHideWindow)
            menuFallback.closeWindow()
            menuFallback.showSettings()
            assertTrue(menuFallback.state.value.visible)
            assertEquals(1L, menuFallback.state.value.settingsRequest)
            assertEquals(0, requests)
            menuFallback.dispose(); binding.retire()
        }
        var smokeRequests = 0
        val smoke = DesktopWindowLifecycle({ smokeRequests += 1 }, allowHide = false)
        val smokeBinding = DesktopNativeLifecycle.install(LifecycleApiFixture(), smoke)
        smoke.closeWindow()
        assertTrue(smoke.state.value.visible)
        assertFalse(smoke.state.value.canHideWindow)
        assertEquals(1, smokeRequests)
        smoke.dispose(); smokeBinding.retire()

        val failedCloses = AtomicInteger()
        val reports = AtomicInteger()
        val failureOwner = DesktopShutdownOwner({}, {
            failedCloses.incrementAndGet()
            error("synthetic_close_failure")
        }, { reports.incrementAndGet(); Unit })
        val failed = DesktopWindowLifecycle({ failureOwner.request(); Unit })
        failed.installed(reopen = true, quit = true)
        failed.closeWindow()
        failed.quit()
        val failedAttempt = failureOwner.request()
        assertTrue(failedAttempt.await().isFailure)
        assertSame(failedAttempt, failureOwner.request())
        failed.showWindow(); failed.showSettings(); failed.quit()
        assertTrue(failed.state.value.closing)
        assertFalse(failed.state.value.visible)
        assertEquals(1, failedCloses.get())
        assertEquals(1, reports.get())
        failed.dispose()

        val cleanupAfterBarrierFailure = AtomicInteger()
        val barrierFailure = DesktopShutdownOwner({ error("synthetic_admission_failure") },
            { cleanupAfterBarrierFailure.incrementAndGet(); Unit }, {})
        assertTrue(barrierFailure.request().await().isFailure)
        assertTrue(barrierFailure.request().await().isFailure)
        assertEquals(1, cleanupAfterBarrierFailure.get())

        // Sleep is optional and independent of the existing hide/reopen/Quit capability pair.
        for (api in listOf(
            LifecycleApiFixture(features = DesktopNativeLifecycleFeature.entries.toSet() - DesktopNativeLifecycleFeature.SystemSleep),
            LifecycleApiFixture(failure = DesktopNativeLifecycleFeature.SystemSleep),
            LifecycleApiFixture(isMacOS = false),
        )) {
            val window = DesktopWindowLifecycle({})
            val binding = DesktopNativeLifecycle.install(api, window, onSystemSleep = { error("uninstalled_sleep_callback") })
            try {
                val before = window.state.value
                api.sleepCallback?.invoke(); api.wakeCallback?.invoke()
                assertEquals(before, window.state.value)
                assertEquals(api.isMacOS, before.canHideWindow)
                assertEquals(0, api.sleepRemovals)
            } finally { window.dispose(); binding.retire() }
        }
    }

    /** Authored only: fake OS events exercise the real host barrier and identified actor. */
    private suspend fun assertSystemSleepPolicy(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        fixture.stopController()
        val realStore = fixture.store
        realStore.restoreTrackToCollection(trackId)
        realStore.saveQueue(DesktopQueue("system-sleep-empty", emptyList(), null))
        val saveGate = AtomicReference<MetadataApplyGate?>(null)
        val gates = mutableListOf<MetadataApplyGate>()
        val writes = AtomicInteger()
        val store = object : DesktopLibraryStore by realStore {
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                saveGate.getAndSet(null)?.let { gate ->
                    gate.entered.complete(Unit)
                    gate.release.await()
                }
                realStore.saveQueue(queue, event)
                writes.incrementAndGet()
            }
            // The outer fixture retains its real store and owns its eventual close/removal.
            override suspend fun close(): Unit = Unit
        }
        val engine = PolicyEngine()
        val pauses = AtomicInteger()
        val plays = AtomicInteger()
        val countedEngine = object : DesktopAudioEngine by engine {
            override fun pause(generation: Long): Unit { pauses.incrementAndGet(); engine.pause(generation) }
            override fun play(generation: Long): Unit { plays.incrementAndGet(); engine.play(generation) }
        }
        val timerNow = AtomicLong(0)
        val controller = fixture.start(countedEngine, store, timerNow::get, periodicObservations = emptyFlow())
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val preferences = object : DesktopPreferencesStore {
            override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState())
            override suspend fun setAppLanguage(value: String): Unit = error("unexpected_sleep_settings_write")
            override suspend fun setAppearanceMode(value: String): Unit = error("unexpected_sleep_settings_write")
            override suspend fun setAccentPalette(value: String): Unit = error("unexpected_sleep_settings_write")
            override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("unexpected_sleep_settings_write")
            override suspend fun applyPortable(value: DesktopPreferences): Unit = error("unexpected_sleep_settings_write")
            override suspend fun close(): Unit = Unit
        }
        val settings = DesktopSettingsController(preferences, scope)
        var runtime: DesktopRuntime? = null
        var window: DesktopWindowLifecycle? = null
        var binding: DesktopNativeLifecycle? = null
        // Alternate read-only refusal codes so each wait fences newly queued actor work.
        suspend fun fence(): Unit {
            val listen = controller.taste.value.errorCode == "TASTE_SESSION_STALE"
            if (listen) controller.setCurrentListenTasteExcluded("sleep-fence", "missing", "missing", false)
            else controller.setQueueSessionTasteExcluded("sleep-fence", false)
            val expected = if (listen) "TASTE_LISTEN_STALE" else "TASTE_SESSION_STALE"
            eventually("sleep actor fence") { controller.taste.value.errorCode == expected }
        }
        try {
            val constructor = DesktopRuntime::class.java.declaredConstructors.single { it.parameterCount == 5 }
                .apply { isAccessible = true }
            val importer = object : DesktopImportPort {
                override suspend fun importSelected(paths: List<String>): DesktopImportResult = error("unexpected_sleep_import")
            }
            val host = constructor.newInstance(store, importer, controller, scope, settings) as DesktopRuntime
            runtime = host
            val lifecycle = DesktopWindowLifecycle(host::beginClose).also { window = it }
            val api = LifecycleApiFixture()
            val native = DesktopNativeLifecycle.install(api, lifecycle, host::pauseForSystemSleep).also { binding = it }
            val sleep = checkNotNull(api.sleepCallback)
            val wake = checkNotNull(api.wakeCallback)
            fence()
            assertNull(host.playerState.value.target)
            val empty = realStore.state.value
            sleep(); wake(); fence()
            assertEquals(empty, realStore.state.value)
            assertEquals(0, pauses.get())
            assertEquals(0, writes.get())
            assertTrue(engine.opens.isEmpty())

            controller.playTracks(listOf(trackId, trackId))
            eventually("sleep scene uses the existing playing owner") { host.playerState.value.playback.isPlaying }
            val original = checkNotNull(host.playerState.value.target)
            val initialQueue = checkNotNull(realStore.state.value.queue)
            val entries = initialQueue.entries.map { it.id }
            controller.scheduleSleepTimer(original.queueId, DesktopSleepTimerLimits.MIN_DURATION_MS)
            eventually("sleep scene has one ordinary timer") { controller.sleepTimer.value.planId != null }
            val planId = controller.sleepTimer.value.planId
            val opening = engine.opens.toList()
            val stops = engine.stops.toList()
            lifecycle.closeWindow()
            val hidden = lifecycle.state.value
            val beforePause = pauses.get()
            sleep()
            eventually("sleep requests an identified pause without replacing the owner") {
                pauses.get() == beforePause + 1 && !host.playerState.value.playback.isPlaying
            }
            fence()
            val checkpoint = checkNotNull(realStore.state.value.queue)
            assertEquals(original, host.playerState.value.target)
            assertEquals(initialQueue.positionMs, checkpoint.positionMs)
            assertTrue(checkpoint.playedMs >= initialQueue.playedMs)
            val listen = realStore.state.value.history.single { it.id == original.sessionId }
            assertFalse(listen.finalized)
            assertEquals(checkpoint.playedMs, listen.playedMs)
            assertEquals(checkpoint.positionMs, listen.positionMs)
            assertEquals(planId, controller.sleepTimer.value.planId)
            assertEquals(opening, engine.opens.toList())
            assertEquals(stops, engine.stops.toList())
            assertTrue(engine.seeks.isEmpty())
            assertEquals(hidden, lifecycle.state.value)
            val afterPause = realStore.state.value
            val playCount = plays.get()
            repeat(128) { wake() }
            fence()
            assertEquals(afterPause, realStore.state.value)
            assertEquals(playCount, plays.get())
            assertEquals(beforePause + 1, pauses.get())
            assertFalse(host.playerState.value.playback.isPlaying)
            assertEquals(hidden, lifecycle.state.value)

            host.transport(original, DesktopTransportAction.PLAY)
            eventually("explicit Play remains the resume authority") { host.playerState.value.playback.isPlaying }
            suspend fun queuedSleep(navigation: List<String>): Pair<DesktopPlaybackTarget, DesktopPlaybackTarget> {
                val old = checkNotNull(host.playerState.value.target)
                val pauseCount = pauses.get()
                val gate = MetadataApplyGate().also { gates.add(it); saveGate.set(it) }
                controller.setRepeat(if (realStore.state.value.queue?.repeatMode == "ALL") "OFF" else "ALL")
                gate.entered.await()
                navigation.forEach(controller::selectEntry)
                sleep()
                // The native callback has returned while the actor is blocked on prior IO.
                assertEquals(old, host.playerState.value.target)
                assertEquals(pauseCount, pauses.get())
                gate.release.complete(Unit)
                eventually("queued navigation retires the sleep target") {
                    host.playerState.value.target != old && host.playerState.value.target?.entryId == navigation.last() &&
                        host.playerState.value.playback.isPlaying
                }
                fence()
                assertEquals(pauseCount, pauses.get())
                assertEquals(planId, controller.sleepTimer.value.planId)
                return old to checkNotNull(host.playerState.value.target)
            }
            val (beforeEntry, afterEntry) = queuedSleep(listOf(entries[1]))
            assertNotEquals(beforeEntry.entryId, afterEntry.entryId)
            val (beforeReturn, afterReturn) = queuedSleep(listOf(entries[0], entries[1]))
            assertEquals(beforeReturn.queueId, afterReturn.queueId)
            assertEquals(beforeReturn.entryId, afterReturn.entryId)
            assertNotEquals(beforeReturn.sessionId, afterReturn.sessionId)
            assertNotEquals(beforeReturn.generation, afterReturn.generation)

            // Sleep must retain the ordinary native-readiness and pending-seek barriers.
            engine.deferSeek = true
            host.transport(afterReturn, DesktopTransportAction.SEEK, 4_000)
            eventually("sleep scene keeps a pending native seek") { engine.seeks.lastOrNull()?.positionMs == 4_000L }
            engine.emit(DesktopAudioEvent(afterReturn.generation, "BUFFERING", 0, 8_000, true))
            eventually("native buffering retains the seek checkpoint") { controller.state.value.playbackStatus == PlaybackStatus.Buffering }
            fence()
            val seeking = realStore.state.value
            val pendingPauses = pauses.get()
            val seekCount = engine.seeks.size
            sleep(); wake(); fence()
            assertEquals(pendingPauses, pauses.get())
            assertEquals(seeking, realStore.state.value)
            assertFalse(controller.state.value.seekEnabled)
            assertEquals(planId, controller.sleepTimer.value.planId)
            assertEquals(afterReturn, host.playerState.value.target)
            host.transport(afterReturn, DesktopTransportAction.SEEK, 6_000)
            fence()
            assertEquals(seekCount, engine.seeks.size)
            engine.emit(DesktopAudioEvent(afterReturn.generation, "READY", 4_000, 8_000, false))
            eventually("only matching native evidence completes the retained seek") {
                controller.state.value.positionMs == 4_000L && controller.state.value.seekEnabled
            }

            // Exercise host admission independently of the lifecycle's own closing guard.
            host.beginClose()
            fence()
            val closing = realStore.state.value
            val closingPauses = pauses.get()
            sleep(); wake(); host.pauseForSystemSleep(); fence()
            assertEquals(closingPauses, pauses.get())
            assertEquals(closing, realStore.state.value)
            lifecycle.quit()
            native.retire(); native.retire()
            sleep(); wake(); fence()
            assertEquals(closingPauses, pauses.get())
            assertEquals(playCount + 1, plays.get())
            assertEquals(closing, realStore.state.value)
            assertEquals(1, api.sleepRemovals)
        } finally {
            gates.forEach { it.release.complete(Unit) }
            window?.dispose()
            binding?.retire()
            withContext(NonCancellable) {
                val host = runtime
                try {
                    if (host != null) host.close() else settings.close()
                } finally { scope.cancel(); fixture.stopController() }
            }
        }
    }

    private class LifecycleQuitResponse : QuitResponse {
        var decisions: Int = 0
        override fun performQuit(): Unit { decisions += 1; error("native_default_quit_bypassed_cleanup") }
        override fun cancelQuit(): Unit { decisions += 1; error("one_shot_quit_response_consumed") }
    }

    private class LifecycleApiFixture(
        override val isMacOS: Boolean = true,
        private val features: Set<DesktopNativeLifecycleFeature> = DesktopNativeLifecycleFeature.entries.toSet(),
        private val failure: DesktopNativeLifecycleFeature? = null,
        private val failPreparation: Boolean = false,
    ) : DesktopNativeLifecycleApi {
        var settingsCallback: (() -> Unit)? = null
        var quitCallback: ((QuitResponse) -> Unit)? = null
        var reopenCallback: (() -> Unit)? = null
        var sleepCallback: (() -> Unit)? = null
        var wakeCallback: (() -> Unit)? = null
        var removals: Int = 0
        var sleepRemovals: Int = 0
        var settingsClears: Int = 0
        var supportQueries: Int = 0
        var installations: Int = 0
        override fun supports(feature: DesktopNativeLifecycleFeature): Boolean {
            supportQueries += 1
            return feature in features
        }
        override fun prepareForOrderlyQuit(): Unit { check(!failPreparation) { "synthetic_quit_preparation_failure" } }
        override fun setSettingsHandler(handler: (() -> Unit)?): Unit {
            installations += 1
            if (handler == null) settingsClears += 1
            settingsCallback = handler
            if (failure == DesktopNativeLifecycleFeature.Settings && handler != null) error("synthetic_settings_install_failure")
        }
        override fun setQuitHandler(handler: (QuitResponse) -> Unit): Unit {
            installations += 1
            quitCallback = handler
            if (failure == DesktopNativeLifecycleFeature.Quit) error("synthetic_quit_install_failure")
        }
        override fun addReopenListener(handler: () -> Unit): () -> Unit {
            installations += 1
            if (failure == DesktopNativeLifecycleFeature.Reopen) error("synthetic_reopen_install_failure")
            reopenCallback = handler
            return { removals += 1; reopenCallback = null }
        }
        override fun addSystemSleepListener(aboutToSleep: () -> Unit, awoke: () -> Unit): () -> Unit {
            installations += 1
            // Simulate a partial registration before failure; its retained callback must be inert.
            sleepCallback = aboutToSleep
            wakeCallback = awoke
            if (failure == DesktopNativeLifecycleFeature.SystemSleep) error("synthetic_sleep_install_failure")
            return { sleepRemovals += 1; sleepCallback = null; wakeCallback = null }
        }
    }

    private suspend fun assertUpcomingGroupPolicy(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        fixture.stopController()
        val realStore = fixture.store
        realStore.restoreTrackToCollection(trackId)
        val entries = List(8) { DesktopQueueEntry("group-entry-$it", trackId) }
        val ids = entries.map { it.id }
        val seed = DesktopQueue("group-queue", entries, ids[1], positionMs = 1_200,
            sessionId = "group-listen", sessionStartedAtMs = 123, playedMs = 42,
            sessionTasteState = "FUTURE_SESSION_TASTE", listenTasteState = "FUTURE_LISTEN_TASTE")
        realStore.saveQueue(seed)
        val nextGate = AtomicReference<MetadataApplyGate?>(null)
        val writes = AtomicInteger()
        val savedEvents = CopyOnWriteArrayList<DesktopListeningEvent?>()
        val observedStore = object : DesktopLibraryStore by realStore {
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                writes.incrementAndGet()
                savedEvents.add(event)
                nextGate.getAndSet(null)?.let { gate ->
                    gate.entered.complete(Unit)
                    gate.release.await()
                    gate.failure?.let { throw it }
                }
                realStore.saveQueue(queue, event)
            }
        }
        val engine = PolicyEngine()
        val pauses = AtomicInteger()
        val adapter = object : DesktopAudioEngine by engine {
            override fun pause(generation: Long): Unit { pauses.incrementAndGet(); engine.pause(generation) }
        }
        val timerNow = AtomicLong(0)
        val timerReads = AtomicInteger()
        val controller = fixture.start(adapter, observedStore,
            timerNowNanos = { timerReads.incrementAndGet(); timerNow.get() }, periodicObservations = emptyFlow())
        suspend fun fence(): Unit {
            val listen = controller.taste.value.errorCode != "TASTE_LISTEN_STALE"
            if (listen) controller.setCurrentListenTasteExcluded("group-fence", "missing", "missing", false)
            else controller.setQueueSessionTasteExcluded("group-fence", false)
            eventually("group actor fence $listen") {
                controller.taste.value.errorCode == if (listen) "TASTE_LISTEN_STALE" else "TASTE_SESSION_STALE"
            }
        }
        suspend fun perform(action: DesktopQueueGroupAction, selected: List<String>, expectedIds: List<String>): Unit {
            val before = realStore.state.value
            val current = checkNotNull(before.queue)
            val editor = controller.playerState.value.queueEditor
            val target = checkNotNull(editor.target)
            val beforeWrites = writes.get()
            val native = engine.state.value
            val opens = engine.opens.toList()
            val stops = engine.stops.toList()
            val seeks = engine.seeks.toList()
            val timer = controller.sleepTimer.value
            val reads = timerReads.get()
            val pauseCount = pauses.get()
            controller.editUpcoming(target, action, selected)
            eventually("group $action publishes its confirmed effective order") {
                controller.playerState.value.queueEditor.let { it.target != target && it.entries.map { row -> row.id } == expectedIds }
            }
            fence()
            val retained = current.entries.filter { it.id in expectedIds }
            val expected = current.copy(entries = if (current.shuffleEnabled) retained else expectedIds.map { id -> retained.single { it.id == id } },
                shuffleOrder = expectedIds)
            assertEquals(before.copy(queue = expected), realStore.state.value)
            assertEquals(beforeWrites + 1, writes.get())
            val event = checkNotNull(savedEvents.last())
            assertEquals(current.sessionId, event.id)
            assertEquals(current.currentEntryId, event.queueEntryId)
            assertEquals(current.playedMs, event.playedMs)
            assertEquals(current.positionMs, event.positionMs)
            assertFalse(event.finalized)
            assertEquals(current.sessionTasteState, event.sessionTasteState)
            assertEquals(current.listenTasteState, event.listenTasteState)
            assertEquals(native, engine.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops, engine.stops.toList())
            assertEquals(seeks, engine.seeks.toList())
            assertEquals(timer, controller.sleepTimer.value)
            assertEquals(reads, timerReads.get())
            assertEquals(pauseCount, pauses.get())
            assertEquals(target, editor.target)
            assertEquals(current.entries.map { it.id }.toSet(), editor.entries.map { it.id }.toSet())
        }
        eventually("group queue restores coherently before edits") {
            controller.state.value.playbackStatus == PlaybackStatus.Ready && controller.playerState.value.queueEditor.target != null
        }
        fence()
        val initialEditor = controller.playerState.value.queueEditor
        assertEquals(entries, initialEditor.entries)
        assertEquals(seed.currentEntryId, initialEditor.currentEntryId)
        assertEquals(8, initialEditor.entries.map { it.id }.distinct().size)
        assertEquals(1, initialEditor.entries.map { it.trackId }.distinct().size)
        assertTrue(runCatching { (initialEditor.entries as MutableList<DesktopQueueEntry>).clear() }.exceptionOrNull() is UnsupportedOperationException)
        val managed = Path.of(checkNotNull(realStore.resolveManagedFile(trackId)))
        val bytesHash = MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(managed)).toList()

        perform(DesktopQueueGroupAction.MOVE_UP, listOf(ids[7], ids[5], ids[2], ids[4]), listOf(0, 1, 2, 4, 5, 3, 7, 6).map { ids[it] })
        perform(DesktopQueueGroupAction.MOVE_DOWN, listOf(ids[7], ids[5], ids[2], ids[4]), listOf(0, 1, 3, 2, 4, 5, 6, 7).map { ids[it] })
        perform(DesktopQueueGroupAction.MOVE_DOWN, listOf(ids[7], ids[5], ids[4]), listOf(0, 1, 3, 2, 6, 4, 5, 7).map { ids[it] })
        perform(DesktopQueueGroupAction.MOVE_NEXT, listOf(ids[7], ids[5], ids[2], ids[3]), listOf(0, 1, 3, 2, 5, 7, 6, 4).map { ids[it] })
        perform(DesktopQueueGroupAction.REMOVE, listOf(ids[7], ids[5]), listOf(0, 1, 3, 2, 6, 4).map { ids[it] })
        assertEquals(entries, initialEditor.entries)

        val stable = checkNotNull(controller.playerState.value.queueEditor.target)
        realStore.updateTrackTextMetadata(trackId, "Group metadata title", "Group metadata artist")
        realStore.setTrackPreference(trackId, "LIKED")
        controller.setQueueSessionTasteExcluded(seed.id, true)
        engine.emit(DesktopAudioEvent(stable.generation, "READY", 1_400, 9_000))
        eventually("position, duration, text and taste publish without retiring editor identity") {
            controller.state.value.positionMs == 1_400L && controller.state.value.title == "Group metadata title" &&
                controller.state.value.durationMs == 9_000L && controller.taste.value.sessionTasteState == "EXCLUDED"
        }
        fence()
        assertSame(stable, controller.playerState.value.queueEditor.target)
        val refused = realStore.state.value
        val refusedWrites = writes.get()
        val malformed = listOf(emptyList(), listOf(ids[3], ids[3]), listOf(ids[3], "missing"),
            listOf(ids[3], ids[0]), listOf(ids[3], ids[1]), listOf(ids[3], ""),
            List(1_000) { "missing-$it" }, List(1_001) { "oversized-$it" })
        for (action in DesktopQueueGroupAction.entries) for (selected in malformed) controller.editUpcoming(stable, action, selected)
        controller.editUpcoming(stable, DesktopQueueGroupAction.MOVE_UP, listOf(ids[3]))
        controller.editUpcoming(stable, DesktopQueueGroupAction.MOVE_DOWN, listOf(ids[4]))
        controller.editUpcoming(stable, DesktopQueueGroupAction.MOVE_NEXT, listOf(ids[2], ids[3]))
        for (forged in listOf(stable.copy(queueId = "retired"), stable.copy(currentEntryId = ids[3]),
            stable.copy(sessionId = "retired"), stable.copy(generation = stable.generation + 1), stable.copy(revision = stable.revision + 1))) {
            controller.editUpcoming(forged, DesktopQueueGroupAction.REMOVE, listOf(ids[3]))
        }
        fence()
        assertEquals(refused, realStore.state.value)
        assertEquals(refusedWrites, writes.get())
        assertSame(stable, controller.playerState.value.queueEditor.target)

        // Ordinary failure leaves the owner authoritative, so a queued frozen selection is valid.
        val failure = MetadataApplyGate(DesktopStorageException("desktop_outbox_payload_invalid"))
        nextGate.set(failure)
        val beforeFailure = realStore.state.value
        val failureNative = engine.state.value
        val failureWrites = writes.get()
        controller.editUpcoming(stable, DesktopQueueGroupAction.MOVE_NEXT, listOf(ids[4]))
        failure.entered.await()
        val mutableIds = mutableListOf(ids[2])
        controller.editUpcoming(stable, DesktopQueueGroupAction.REMOVE, mutableIds)
        mutableIds.clear()
        mutableIds.addAll(List(1_001) { "changed-after-dispatch" })
        assertEquals(beforeFailure, realStore.state.value)
        assertSame(stable, controller.playerState.value.queueEditor.target)
        assertEquals(failureNative, engine.state.value)
        failure.release.complete(Unit)
        eventually("queued group selection uses its frozen IDs after ordinary refusal") {
            realStore.state.value.queue?.entries?.none { it.id == ids[2] } == true && controller.playerState.value.queueEditor.target != stable
        }
        fence()
        assertEquals(failureWrites + 2, writes.get())
        assertEquals("PLAYBACK_OPERATION_FAILED", controller.error.value)
        assertEquals(failureNative, engine.state.value)
        assertEquals(beforeFailure.history, realStore.state.value.history)

        val admitted = checkNotNull(controller.playerState.value.queueEditor.target)
        val admission = MetadataApplyGate()
        nextGate.set(admission)
        val beforeAdmission = realStore.state.value
        val admissionWrites = writes.get()
        controller.editUpcoming(admitted, DesktopQueueGroupAction.MOVE_NEXT, listOf(ids[4]))
        admission.entered.await()
        controller.editUpcoming(admitted, DesktopQueueGroupAction.REMOVE, listOf(ids[3]))
        assertEquals(beforeAdmission, realStore.state.value)
        assertSame(admitted, controller.playerState.value.queueEditor.target)
        admission.release.complete(Unit)
        eventually("successful group admission retires queued callbacks of its old owner") {
            controller.playerState.value.queueEditor.target != admitted
        }
        fence()
        assertEquals(admissionWrites + 1, writes.get())
        assertTrue(checkNotNull(realStore.state.value.queue).entries.any { it.id == ids[3] })

        controller.setShuffle(true)
        fence()
        val shuffled = checkNotNull(realStore.state.value.queue)
        val active = shuffled.shuffleOrder
        val selected = active.takeLast(2)
        perform(DesktopQueueGroupAction.MOVE_NEXT, selected.reversed(), active.take(1) + selected + active.drop(1).filterNot { it in selected })
        assertEquals(shuffled.entries, realStore.state.value.queue?.entries)
        val removeId = checkNotNull(realStore.state.value.queue).shuffleOrder.last()
        val remainingOrder = checkNotNull(realStore.state.value.queue).shuffleOrder.filterNot { it == removeId }
        perform(DesktopQueueGroupAction.REMOVE, listOf(removeId), remainingOrder)
        assertEquals(shuffled.entries.filterNot { it.id == removeId }, realStore.state.value.queue?.entries)
        controller.setShuffle(false)
        fence()

        // Reversible topology/mode changes must still retire A-B-A callbacks.
        for (change in listOf("repeat", "shuffle", "order")) {
            val old = checkNotNull(controller.playerState.value.queueEditor.target)
            val before = checkNotNull(realStore.state.value.queue)
            val last = before.entries.last().id
            when (change) {
                "repeat" -> { controller.setRepeat("ONE"); controller.setRepeat("OFF") }
                "shuffle" -> { controller.setShuffle(true); controller.setShuffle(false) }
                else -> { controller.moveUpcoming(last, -1); controller.moveUpcoming(last, 1) }
            }
            controller.editUpcoming(old, DesktopQueueGroupAction.REMOVE, listOf(last))
            fence()
            assertEquals(before, realStore.state.value.queue)
            assertNotEquals(old, controller.playerState.value.queueEditor.target)
        }
        val oldCurrent = checkNotNull(controller.playerState.value.queueEditor.target)
        val another = checkNotNull(realStore.state.value.queue).entries.first { it.id != oldCurrent.currentEntryId }.id
        controller.selectEntry(another)
        eventually("group current leaves its old entry") { controller.state.value.mediaId == another && controller.state.value.isPlaying }
        controller.selectEntry(oldCurrent.currentEntryId)
        eventually("group current returns with a new listen") {
            controller.state.value.mediaId == oldCurrent.currentEntryId && controller.state.value.isPlaying
        }
        controller.pause()
        eventually("group current A-B-A scene pauses") { !controller.state.value.isPlaying }
        fence()
        assertEquals(oldCurrent.currentEntryId, controller.playerState.value.queueEditor.target?.currentEntryId)
        assertNotEquals(oldCurrent.sessionId, controller.playerState.value.queueEditor.target?.sessionId)
        val afterCurrent = realStore.state.value
        val currentWrites = writes.get()
        controller.editUpcoming(oldCurrent, DesktopQueueGroupAction.REMOVE, listOf(checkNotNull(afterCurrent.queue).entries.last().id))
        fence()
        assertEquals(afterCurrent, realStore.state.value)
        assertEquals(currentWrites, writes.get())

        val oldGeneration = checkNotNull(controller.playerState.value.queueEditor.target)
        engine.emit(DesktopAudioEvent(oldGeneration.generation, "ERROR", errorCode = "NATIVE_OPEN_FAILED"))
        eventually("terminal native observation retires the editor generation") {
            controller.playerState.value.queueEditor.target?.generation != oldGeneration.generation
        }
        controller.selectEntry(oldGeneration.currentEntryId)
        eventually("same logical listen reopens under a fresh native generation") {
            controller.state.value.playbackStatus == PlaybackStatus.Ready && controller.state.value.isPlaying
        }
        controller.pause()
        eventually("reopened group generation pauses") { !controller.state.value.isPlaying }
        fence()
        assertEquals(oldGeneration.sessionId, controller.playerState.value.queueEditor.target?.sessionId)
        val reopened = realStore.state.value
        val reopenedWrites = writes.get()
        controller.editUpcoming(oldGeneration, DesktopQueueGroupAction.REMOVE, listOf(checkNotNull(reopened.queue).entries.last().id))
        fence()
        assertEquals(reopened, realStore.state.value)
        assertEquals(reopenedWrites, writes.get())

        engine.deferSeek = true
        controller.seekTo(4_000)
        eventually("group timer scene retains a pending seek") { engine.seeks.lastOrNull()?.positionMs == 4_000L }
        controller.scheduleSleepTimer(seed.id, 60_000)
        eventually("group scene arms a duration timer") { controller.sleepTimer.value.remainingMs == 60_000L }
        fence()
        timerNow.addAndGet(60_000_000_000L)
        val dueTarget = checkNotNull(controller.playerState.value.queueEditor.target)
        val due = realStore.state.value
        val dueWrites = writes.get()
        val dueReads = timerReads.get()
        val dueTimer = controller.sleepTimer.value
        val duePauses = pauses.get()
        val dueNative = engine.state.value
        val dueOpens = engine.opens.toList()
        val dueStops = engine.stops.toList()
        val dueSeeks = engine.seeks.toList()
        val dueIds = controller.playerState.value.queueEditor.entries.map { it.id }
        val currentIndex = dueIds.indexOf(dueTarget.currentEntryId)
        val last = dueIds.last()
        controller.editUpcoming(dueTarget.copy(revision = dueTarget.revision - 1), DesktopQueueGroupAction.REMOVE,
            listOf(checkNotNull(due.queue).entries.last().id))
        for (action in DesktopQueueGroupAction.entries) for (selection in listOf(emptyList(), listOf(last, last),
            listOf(last, "missing"), listOf(last, dueTarget.currentEntryId), List(1_001) { "oversized-$it" })) {
            controller.editUpcoming(dueTarget, action, selection)
        }
        controller.editUpcoming(dueTarget, DesktopQueueGroupAction.MOVE_UP, listOf(dueIds[currentIndex + 1]))
        controller.editUpcoming(dueTarget, DesktopQueueGroupAction.MOVE_DOWN, listOf(last))
        controller.editUpcoming(dueTarget, DesktopQueueGroupAction.MOVE_NEXT, dueIds.drop(currentIndex + 1).reversed())
        fence()
        assertEquals(due, realStore.state.value)
        assertEquals(dueWrites, writes.get())
        assertSame(dueTarget, controller.playerState.value.queueEditor.target)
        assertEquals(dueNative, engine.state.value)
        assertEquals(dueOpens, engine.opens.toList())
        assertEquals(dueStops, engine.stops.toList())
        assertEquals(dueSeeks, engine.seeks.toList())
        perform(DesktopQueueGroupAction.MOVE_NEXT, listOf(last),
            dueIds.take(currentIndex + 1) + last + dueIds.drop(currentIndex + 1).filterNot { it == last })
        assertFalse(controller.state.value.seekEnabled)
        assertEquals(dueReads, timerReads.get())
        assertEquals(dueTimer, controller.sleepTimer.value)
        assertEquals(duePauses, pauses.get())
        val restartQueue = checkNotNull(realStore.state.value.queue)
        val restartHistory = realStore.state.value.history
        val retired = checkNotNull(controller.playerState.value.queueEditor.target)
        fixture.stopController()
        assertNull(controller.playerState.value.queueEditor.target)
        val closedSnapshot = fixture.store.state.value
        controller.editUpcoming(retired, DesktopQueueGroupAction.REMOVE, listOf(last))
        assertEquals(closedSnapshot, fixture.store.state.value)
        fixture.reopenStore()
        assertEquals(restartQueue, fixture.store.state.value.queue)
        assertEquals(restartHistory, fixture.store.state.value.history)
        assertEquals(bytesHash, MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(managed)).toList())
        val restoredEngine = PolicyEngine()
        val restored = fixture.start(restoredEngine, periodicObservations = emptyFlow())
        eventually("group order restarts in the same paused logical listen") {
            restored.state.value.playbackStatus == PlaybackStatus.Ready && restored.playerState.value.queueEditor.target != null
        }
        assertEquals(restartQueue.entries, restored.playerState.value.queueEditor.entries)
        assertEquals(restartQueue.currentEntryId, restored.playerState.value.queueEditor.target?.currentEntryId)
        assertEquals(restartQueue.sessionId, restored.playerState.value.queueEditor.target?.sessionId)
        assertFalse(restoredEngine.opens.single().autoplay)
        fixture.stopController()
    }

    /** Source-only / UNVERIFIED: bounded malformed queues and refreshed/sticky refusals. */
    private suspend fun assertUpcomingGroupRefusals(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        val realStore = fixture.store
        val track = realStore.state.value.tracks.single { it.id == trackId }.copy(collectionState = "ACTIVE")
        val entries = List(1_001) { DesktopQueueEntry("group-capacity-$it", trackId) }
        for (mode in listOf("capacity", "oversized-queue", "duplicate-queue", "unknown-type", "unknown-repeat",
            "no-current", "missing-current", "invalid-shuffle", "blank-entry")) {
            val base = DesktopQueue("group-$mode", entries.take(1_000), entries.first().id,
                sessionId = "group-$mode-listen", sessionStartedAtMs = 123)
            val seed = when (mode) {
                "oversized-queue" -> base.copy(entries = entries, shuffleOrder = entries.map { it.id })
                "duplicate-queue" -> base.copy(entries = listOf(entries[0], entries[0]), shuffleOrder = listOf(entries[0].id, entries[0].id))
                "unknown-type" -> base.copy(queueType = "FUTURE_QUEUE")
                "unknown-repeat" -> base.copy(repeatMode = "FUTURE_REPEAT")
                "no-current" -> base.copy(currentEntryId = null)
                "missing-current" -> base.copy(currentEntryId = "missing")
                "invalid-shuffle" -> base.copy(shuffleOrder = base.shuffleOrder.dropLast(1))
                "blank-entry" -> base.copy(entries = listOf(DesktopQueueEntry("", trackId)), currentEntryId = "", shuffleOrder = listOf(""))
                else -> base
            }
            val snapshot = MutableStateFlow(DesktopLibrarySnapshot(tracks = listOf(track), queue = seed))
            val writes = AtomicInteger()
            val store = object : DesktopLibraryStore by realStore {
                override val state: StateFlow<DesktopLibrarySnapshot> = snapshot
                override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                    writes.incrementAndGet()
                    snapshot.value = snapshot.value.copy(queue = queue)
                }
                override suspend fun resolveManagedFile(trackId: String): String = "synthetic-managed-capability"
                override suspend fun updateTrackDuration(trackId: String, durationMs: Long): Unit = Unit
            }
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val engine = PolicyEngine()
            val timerReads = AtomicInteger()
            val controller = DesktopPlaybackController(store, engine, scope,
                timerNowNanos = { timerReads.incrementAndGet(); 0L }, periodicObservations = emptyFlow())
            suspend fun fence(listen: Boolean): Unit {
                if (listen) controller.setCurrentListenTasteExcluded("capacity-fence", "missing", "missing", false)
                else controller.setQueueSessionTasteExcluded("capacity-fence", false)
                eventually("capacity actor fence $mode $listen") {
                    controller.taste.value.errorCode == if (listen) "TASTE_LISTEN_STALE" else "TASTE_SESSION_STALE"
                }
            }
            try {
                if (mode in listOf("capacity", "blank-entry")) eventually("supported capacity scene opens $mode") {
                    controller.state.value.playbackStatus == PlaybackStatus.Ready
                }
                fence(true)
                val target = controller.playerState.value.queueEditor.target
                    ?: DesktopQueueEditTarget(seed.id, checkNotNull(base.currentEntryId), seed.sessionId, engine.state.value.generation, 1)
                if (mode == "capacity") {
                    assertEquals(1_000, controller.playerState.value.queueEditor.entries.size)
                    controller.scheduleSleepTimer(seed.id, 60_000)
                    eventually("capacity scene arms its timer") { controller.sleepTimer.value.planId != null }
                } else {
                    assertNull(controller.playerState.value.queueEditor.target)
                    assertTrue(controller.playerState.value.queueEditor.entries.isEmpty())
                }
                val before = snapshot.value
                val beforeWrites = writes.get()
                val native = engine.state.value
                val opens = engine.opens.toList()
                val stops = engine.stops.toList()
                val timer = controller.sleepTimer.value
                val reads = timerReads.get()
                for (action in DesktopQueueGroupAction.entries) {
                    for (selection in listOf(emptyList(), base.entries.map { it.id }, entries.map { it.id },
                        listOf(entries[1].id, entries[1].id), listOf(entries[1].id, "missing"))) {
                        controller.editUpcoming(target, action, selection)
                    }
                }
                fence(false)
                assertEquals(before, snapshot.value)
                assertEquals(beforeWrites, writes.get())
                assertEquals(native, engine.state.value)
                assertEquals(opens, engine.opens.toList())
                assertEquals(stops, engine.stops.toList())
                assertTrue(engine.seeks.isEmpty())
                assertEquals(timer, controller.sleepTimer.value)
                assertEquals(reads, timerReads.get())
                if (mode == "capacity") {
                    // A full 1,000-row queue can remove all 999 actual upcoming occurrences.
                    controller.editUpcoming(target, DesktopQueueGroupAction.REMOVE, base.entries.drop(1).map { it.id })
                    eventually("full queue accepts its complete bounded upcoming selection") {
                        snapshot.value.queue?.entries?.size == 1 && controller.playerState.value.queueEditor.entries.size == 1
                    }
                    fence(true)
                    assertEquals(base.copy(entries = listOf(entries.first()), shuffleOrder = listOf(entries.first().id)), snapshot.value.queue)
                    assertEquals(beforeWrites + 1, writes.get())
                    assertEquals(timer, controller.sleepTimer.value)
                    assertEquals(reads, timerReads.get())
                }
            } finally {
                withContext(NonCancellable) { controller.close() }
                scope.cancel()
            }
        }

        val failures = listOf(
            DesktopStorageException("desktop_outbox_payload_invalid"),
            DesktopStorageException("desktop_snapshot_unavailable"),
            IllegalStateException("private-local-detail", DesktopStorageException("desktop_snapshot_unavailable")),
            DesktopStorageException("desktop_outbox_payload_invalid").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
            CancellationException("fixture_cancelled").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
        )
        for ((index, failure) in failures.withIndex()) {
            val seed = DesktopQueue("group-failure-$index", entries.take(4), entries.first().id,
                sessionId = "group-failure-listen-$index", sessionStartedAtMs = 123, playedMs = 7)
            val snapshot = MutableStateFlow(DesktopLibrarySnapshot(tracks = listOf(track), queue = seed))
            val gate = MetadataApplyGate(failure)
            val nextGate = AtomicReference<MetadataApplyGate?>(null)
            val writes = AtomicInteger()
            val store = object : DesktopLibraryStore by realStore {
                override val state: StateFlow<DesktopLibrarySnapshot> = snapshot
                override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                    writes.incrementAndGet()
                    nextGate.getAndSet(null)?.let { pending ->
                        pending.entered.complete(Unit)
                        pending.release.await()
                        pending.failure?.let { throw it }
                    }
                    snapshot.value = snapshot.value.copy(queue = queue)
                }
                override suspend fun resolveManagedFile(trackId: String): String = "synthetic-managed-capability"
                override suspend fun updateTrackDuration(trackId: String, durationMs: Long): Unit = Unit
            }
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val engine = PolicyEngine()
            val pauses = AtomicInteger()
            val adapter = object : DesktopAudioEngine by engine {
                override fun pause(generation: Long): Unit { pauses.incrementAndGet(); engine.pause(generation) }
            }
            val controller = DesktopPlaybackController(store, adapter, scope, timerNowNanos = { 0L }, periodicObservations = emptyFlow())
            try {
                eventually("group failure scene restores $index") { controller.state.value.playbackStatus == PlaybackStatus.Ready }
                controller.play()
                eventually("group failure scene plays $index") { controller.state.value.isPlaying }
                engine.deferSeek = true
                controller.seekTo(4_000)
                eventually("group failure scene keeps a pending seek $index") { engine.seeks.size == 1 && !controller.state.value.seekEnabled }
                controller.scheduleSleepTimer(seed.id, 60_000)
                eventually("group failure scene arms a timer $index") { controller.sleepTimer.value.planId != null }
                val before = snapshot.value
                val target = checkNotNull(controller.playerState.value.queueEditor.target)
                val timer = controller.sleepTimer.value
                val native = engine.state.value
                val opens = engine.opens.toList()
                val seeks = engine.seeks.toList()
                val stops = engine.stops.toList()
                val pauseCount = pauses.get()
                val beforeWrites = writes.get()
                nextGate.set(gate)
                controller.editUpcoming(target, DesktopQueueGroupAction.MOVE_NEXT, listOf(entries[3].id))
                gate.entered.await()
                assertEquals(before, snapshot.value)
                assertSame(target, controller.playerState.value.queueEditor.target)
                assertEquals(native, engine.state.value)
                assertEquals(timer, controller.sleepTimer.value)
                delay(60)
                gate.release.complete(Unit)
                if (index == 0) {
                    eventually("ordinary group refusal keeps the live editor $index") { controller.error.value == "PLAYBACK_OPERATION_FAILED" }
                    controller.setQueueSessionTasteExcluded("group-failure-fence", false)
                    eventually("ordinary group refusal actor fence") { controller.taste.value.errorCode == "TASTE_SESSION_STALE" }
                    assertSame(target, controller.playerState.value.queueEditor.target)
                    assertEquals(before, snapshot.value)
                    assertEquals(native, engine.state.value)
                    assertEquals(timer, controller.sleepTimer.value)
                    assertEquals(stops, engine.stops.toList())
                    assertEquals(pauseCount, pauses.get())
                    assertFalse(controller.state.value.seekEnabled)
                    controller.editUpcoming(target, DesktopQueueGroupAction.MOVE_NEXT, listOf(entries[3].id))
                    eventually("ordinary refusal leaves the prior elapsed baseline available") {
                        controller.playerState.value.queueEditor.target != target
                    }
                    assertTrue(checkNotNull(snapshot.value.queue).playedMs >= checkNotNull(before.queue).playedMs + 40)
                    assertEquals(beforeWrites + 2, writes.get())
                    assertEquals(timer, controller.sleepTimer.value)
                } else {
                    eventually("group marker reaches the sole sticky boundary $index") {
                        controller.error.value == "desktop_snapshot_unavailable" && controller.playerState.value.queueEditor.target == null &&
                            controller.sleepTimer.value.planId == null && !controller.state.value.shuffleEnabled
                    }
                    assertEquals(before, snapshot.value)
                    assertEquals(beforeWrites + 1, writes.get())
                    assertEquals(stops.size + 1, engine.stops.size)
                    controller.editUpcoming(target, DesktopQueueGroupAction.REMOVE, listOf(entries[1].id))
                    controller.reportSnapshotUnavailable()
                    assertEquals(beforeWrites + 1, writes.get())
                    assertEquals("desktop_snapshot_unavailable", controller.error.value)
                }
                assertEquals(opens, engine.opens.toList())
                assertEquals(seeks, engine.seeks.toList())
            } finally {
                gate.release.complete(Unit)
                withContext(NonCancellable) { controller.close() }
                scope.cancel()
            }
        }
    }

    /** Source-only / UNVERIFIED: batch scenes extend the existing queue composite. */
    private suspend fun assertInitialTrackBatchPolicy(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        val realStore = fixture.store
        val track = realStore.state.value.tracks.single { it.id == trackId }.copy(collectionState = "ACTIVE")
        for (mode in listOf("append", "next", "missing-source", "unknown-mode", "missing-current")) {
            val entry = DesktopQueueEntry("batch-seed-entry", trackId)
            val seed = when (mode) {
                "unknown-mode" -> DesktopQueue("batch-opaque", listOf(entry), entry.id, queueType = "FUTURE_QUEUE")
                "missing-current" -> DesktopQueue("batch-no-current", listOf(entry), null)
                else -> null
            }
            val snapshot = MutableStateFlow(DesktopLibrarySnapshot(tracks = listOf(track), queue = seed))
            val gate = MetadataApplyGate()
            val nextGate = AtomicReference<MetadataApplyGate?>(if (seed == null) gate else null)
            val writes = CopyOnWriteArrayList<DesktopQueue>()
            val store = object : DesktopLibraryStore by realStore {
                override val state: StateFlow<DesktopLibrarySnapshot> = snapshot
                override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                    writes.add(queue)
                    nextGate.getAndSet(null)?.let { pending ->
                        pending.entered.complete(Unit)
                        pending.release.await()
                    }
                    snapshot.value = snapshot.value.copy(queue = queue)
                }
                override suspend fun resolveManagedFile(trackId: String): String? =
                    if (mode == "missing-source") null else "synthetic-managed-capability"
                override suspend fun updateTrackDuration(trackId: String, durationMs: Long): Unit = Unit
            }
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val engine = PolicyEngine()
            val controller = DesktopPlaybackController(store, engine, scope, periodicObservations = emptyFlow())
            try {
                if (seed == null) {
                    controller.enqueueTracks(emptyList())
                    controller.playTracksNext(emptyList())
                    controller.enqueueTracks(listOf(trackId, "missing-track"))
                    controller.playTracksNext(listOf(trackId, "private/path"))
                    controller.enqueueTracks(List(1_001) { trackId })
                    controller.playTracksNext(List(1_001) { trackId })
                    val selected = MutableList(1_000) { trackId }
                    if (mode == "next") controller.playTracksNext(selected) else controller.enqueueTracks(selected)
                    selected.clear()
                    selected.add("missing-after-dispatch")
                    gate.entered.await()
                    assertEquals(1, writes.size)
                    assertEquals(1_000, writes.single().entries.size)
                    assertEquals(List(1_000) { trackId }, writes.single().entries.map { it.trackId })
                    assertEquals(1_000, writes.single().entries.map { it.id }.distinct().size)
                    assertNull(snapshot.value.queue)
                    assertTrue(engine.opens.isEmpty())
                    gate.release.complete(Unit)
                    eventually("initial raw batch settles without autoplay $mode") {
                        snapshot.value.queue?.entries?.size == 1_000 &&
                            if (mode == "missing-source") controller.error.value == "SOURCE_UNAVAILABLE" &&
                                controller.state.value.playbackStatus == PlaybackStatus.Idle
                            else controller.state.value.playbackStatus == PlaybackStatus.Ready
                    }
                    assertEquals("LIBRARY", snapshot.value.queue?.queueType)
                    assertNotNull(snapshot.value.queue?.sessionId)
                    assertFalse(controller.state.value.isPlaying)
                    if (mode != "missing-source") assertFalse(engine.opens.single().autoplay)
                    else assertTrue(engine.opens.isEmpty())
                } else {
                    if (mode == "unknown-mode") controller.enqueueTracks(listOf(trackId, trackId))
                    controller.playTracksNext(listOf(trackId, trackId))
                }
                val before = snapshot.value
                val beforeWrites = writes.size
                val beforeNative = engine.state.value
                val beforeOpens = engine.opens.toList()
                val beforeStops = engine.stops.toList()
                controller.enqueueTracks(emptyList())
                controller.playTracksNext(emptyList())
                if (seed == null) {
                    controller.enqueueTracks(listOf(trackId))
                    controller.playTracksNext(listOf(trackId))
                }
                controller.setCurrentListenTasteExcluded("batch-fence", "missing", "missing", false)
                eventually("batch refusal fence $mode") { controller.taste.value.errorCode == "TASTE_LISTEN_STALE" }
                assertEquals(when (mode) {
                    "unknown-mode" -> "UNSUPPORTED_QUEUE_MODE"
                    "missing-current" -> "QUEUE_CURRENT_MISSING"
                    "missing-source" -> "SOURCE_UNAVAILABLE"
                    else -> "QUEUE_LIMIT"
                }, controller.error.value)
                assertEquals(before, snapshot.value)
                assertEquals(beforeWrites, writes.size)
                assertEquals(beforeNative, engine.state.value)
                assertEquals(beforeOpens, engine.opens.toList())
                assertEquals(beforeStops, engine.stops.toList())
                controller.close()
                val closedWrites = writes.size
                val closedState = snapshot.value
                val closedOpens = engine.opens.toList()
                val closedStops = engine.stops.toList()
                controller.enqueueTracks(listOf(trackId, trackId))
                controller.playTracksNext(listOf(trackId, trackId))
                delay(75)
                assertEquals(closedWrites, writes.size)
                assertEquals(closedState, snapshot.value)
                assertEquals(closedOpens, engine.opens.toList())
                assertEquals(closedStops, engine.stops.toList())
            } finally {
                gate.release.complete(Unit)
                withContext(NonCancellable) { controller.close() }
                scope.cancel()
            }
        }
    }

    /** Source-only / UNVERIFIED: explicit metadata scenes belong to the existing queue composite. */
    private suspend fun assertMetadataPolicy(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        val realStore = fixture.store
        realStore.restoreTrackToCollection(trackId)
        val entry = DesktopQueueEntry("metadata-entry", trackId)
        val queue = DesktopQueue("metadata-queue", listOf(entry), entry.id,
            sessionId = "metadata-listen", sessionStartedAtMs = 1_000)
        realStore.saveQueue(queue)
        val probe = MetadataReadProbe()
        val applies = AtomicInteger()
        val resolves = AtomicInteger()
        val provenanceReads = AtomicInteger()
        val applications = CopyOnWriteArrayList<DesktopEmbeddedTrackTextApplication>()
        val queueWrites = AtomicInteger()
        val applyGate = AtomicReference<MetadataApplyGate?>(null)
        val provenanceGate = AtomicReference<MetadataApplyGate?>(null)
        val nextProvenance = AtomicReference<DesktopTrackTextProvenance?>(null)
        val provenanceFailure = AtomicReference<Exception?>(null)
        val resolveFailure = AtomicReference<Exception?>(null)
        val missingFile = AtomicBoolean(false)
        val observedStore = object : DesktopLibraryStore by realStore {
            override suspend fun loadTrackTextProvenance(trackId: String): DesktopTrackTextProvenance? {
                provenanceReads.incrementAndGet()
                provenanceGate.getAndSet(null)?.let { gate ->
                    gate.entered.complete(Unit)
                    gate.release.await()
                }
                provenanceFailure.getAndSet(null)?.let { throw it }
                return nextProvenance.getAndSet(null) ?: realStore.loadTrackTextProvenance(trackId)
            }
            override suspend fun resolveMetadataFile(trackId: String): String? {
                resolves.incrementAndGet()
                resolveFailure.getAndSet(null)?.let { throw it }
                return if (missingFile.get()) null else realStore.resolveMetadataFile(trackId)
            }
            override suspend fun applyEmbeddedTrackText(baseline: DesktopTrackTextBaseline, title: String, artist: String): Unit =
                error("unexpected_legacy_metadata_apply")
            override suspend fun applyEmbeddedTrackText(application: DesktopEmbeddedTrackTextApplication): Unit {
                applies.incrementAndGet()
                applications.add(application)
                applyGate.getAndSet(null)?.let { gate ->
                    gate.entered.complete(Unit)
                    gate.release.await()
                    gate.failure?.let { throw it }
                }
                realStore.applyEmbeddedTrackText(application)
            }
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                queueWrites.incrementAndGet()
                realStore.saveQueue(queue, event)
            }
        }
        val engine = PolicyEngine()
        val playback = fixture.start(engine, observedStore, periodicObservations = emptyFlow())
        val metadataScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val owners = mutableListOf<DesktopMetadataController>()
        val writeGates = mutableListOf<MetadataApplyGate>()
        val copyOwners = mutableListOf<DesktopCopyInspectionController>()
        val copyGates = mutableListOf<MetadataReadGate>()
        val importGates = mutableListOf<CompletableDeferred<Unit>>()
        val runtimeOwners = mutableListOf<DesktopRuntime>()
        val refused = AtomicBoolean(false)
        val reports = AtomicInteger()
        fun owner(): DesktopMetadataController = DesktopMetadataController(
            observedStore, metadataScope,
            onSnapshotUnavailable = {
                reports.incrementAndGet()
                refused.set(true)
                playback.reportSnapshotUnavailable()
            },
            canRead = { !refused.get() }, reader = probe::read,
        ).also { owners.add(it) }
        fun gatedApply(failure: Exception? = null): MetadataApplyGate = MetadataApplyGate(failure).also {
            writeGates.add(it)
            applyGate.set(it)
        }
        val metadata = owner()
        try {
            eventually("metadata scene restores paused playback") { playback.state.value.playbackStatus == PlaybackStatus.Ready }
            playback.stopAfterCurrent(queue.id, entry.id, checkNotNull(queue.sessionId))
            eventually("metadata scene owns an independent timer") { playback.sleepTimer.value.planId != null }
            val timer = playback.sleepTimer.value
            val native = engine.state.value
            val opens = engine.opens.toList()
            val stops = engine.stops.toList()
            val seeks = engine.seeks.toList()
            val initial = realStore.state.value
            val writes = queueWrites.get()
            assertEquals(0, resolves.get())
            assertEquals(0, provenanceReads.get())
            assertEquals(0, probe.calls.get())
            suspend fun assertMetadataReadAndApply(): Unit {
                metadata.inspect(trackId)
                val firstId = checkNotNull(metadata.state.value.requestId)
                eventually("explicit inspection publishes frozen fields") { metadata.state.value.preview?.id == firstId }
                val first = checkNotNull(metadata.state.value.preview)
                assertEquals("Album evidence", first.embedded.album)
                assertEquals("ID3V24", first.embedded.format)
                assertTrue(first.canApply)
                assertEquals(initial.tracks.single { it.id == trackId }.title, first.baseline.title)
                assertEquals(realStore.loadTrackTextProvenance(trackId), first.provenance)
                assertEquals(first.baseline, first.provenance?.baseline)
                assertEquals(DesktopEmbeddedTextFormat.ID3V24, first.application?.format)
                assertEquals(1, provenanceReads.get())
                assertSame(initial, realStore.state.value)
                assertEquals(1, probe.calls.get())
                assertEquals(initial.tracks.single { it.id == trackId }.byteSize, probe.sizes.single())
                assertTrue(probe.paths.single().toAbsolutePath().normalize().startsWith(fixture.data))
                metadata.apply("retired-preview")
                metadata.dismiss("retired-preview")
                assertSame(first, metadata.state.value.preview)
                assertEquals(0, applies.get())
                metadata.dismiss(firstId)
                assertNull(metadata.state.value.preview)

                val resolvesBeforeMismatch = resolves.get()
                val firstProvenance = checkNotNull(first.provenance)
                nextProvenance.set(firstProvenance.copy(baseline = first.baseline.copy(title = "Different stored before-image")))
                metadata.inspect(trackId)
                eventually("mismatched provenance cannot publish a combined preview") { !metadata.state.value.busy }
                assertEquals("desktop_metadata_conflict", metadata.state.value.errorCode)
                assertNull(metadata.state.value.preview)
                assertEquals(resolvesBeforeMismatch, resolves.get())
                assertSame(initial, realStore.state.value)

                val loadGate = MetadataApplyGate().also { writeGates.add(it); provenanceGate.set(it) }
                val callsBeforeLoadCancel = probe.calls.get()
                metadata.inspect(trackId)
                val loadRequestId = checkNotNull(metadata.state.value.requestId)
                loadGate.entered.await()
                metadata.cancel(loadRequestId)
                val cancelledLoad = metadata.state.value
                loadGate.release.complete(Unit)
                assertNull(cancelledLoad.preview)
                assertNull(cancelledLoad.requestId)
                assertEquals(callsBeforeLoadCancel, probe.calls.get())
                metadata.inspect(trackId)
                val afterLoadCancelId = checkNotNull(metadata.state.value.requestId)
                eventually("a new explicit lookup owns the preview after provenance cancellation") {
                    metadata.state.value.preview?.id == afterLoadCancelId
                }
                assertEquals(callsBeforeLoadCancel + 1, probe.calls.get())

                val changedDuringRead = probe.gate()
                metadata.inspect(trackId)
                changedDuringRead.entered.await()
                realStore.updateTrackTextMetadata(trackId, "Changed while reading tags", first.baseline.artist)
                changedDuringRead.release.countDown()
                eventually("a changed snapshot cannot be paired with older source evidence") { !metadata.state.value.busy }
                assertEquals("desktop_metadata_conflict", metadata.state.value.errorCode)
                assertNull(metadata.state.value.preview)
                realStore.updateTrackTextMetadata(trackId, first.baseline.title, first.baseline.artist)

                val stale = probe.gate()
                metadata.inspect(trackId)
                val staleId = checkNotNull(metadata.state.value.requestId)
                stale.entered.await()
                val callsBeforeReplacement = probe.calls.get()
                repeat(24) { metadata.inspect(trackId) }
                val freshId = checkNotNull(metadata.state.value.requestId)
                metadata.cancel(staleId)
                metadata.dismiss(staleId)
                assertEquals(freshId, metadata.state.value.requestId)
                assertTrue(metadata.state.value.busy)
                assertEquals(callsBeforeReplacement, probe.calls.get())
                stale.release.countDown()
                eventually("latest request coalesces behind retired read") { metadata.state.value.preview?.id == freshId }
                assertEquals(callsBeforeReplacement + 1, probe.calls.get())
                assertEquals(1, probe.maximumActive.get())
                val cancelled = probe.gate()
                metadata.inspect(trackId)
                val cancelId = checkNotNull(metadata.state.value.requestId)
                cancelled.entered.await()
                metadata.cancel(cancelId)
                val afterCancel = metadata.state.value
                cancelled.release.countDown()
                eventually("cancelled blocking reader has exited") { probe.active.get() == 0 }
                assertEquals(afterCancel, metadata.state.value)
                assertNull(metadata.state.value.preview)

                probe.result.set(probe.available.copy(format = "FUTURE"))
                metadata.inspect(trackId)
                eventually("available text with unknown format stays read-only") { !metadata.state.value.busy }
                val unknownFormat = checkNotNull(metadata.state.value.preview)
                assertFalse(unknownFormat.canApply)
                assertNull(unknownFormat.application)
                val appliesBeforeUnknown = applies.get()
                metadata.apply(unknownFormat.id)
                assertEquals(appliesBeforeUnknown, applies.get())

                val staleFailure = probe.gate(IllegalStateException("retired_private_detail"))
                probe.result.set(probe.available)
                metadata.inspect(trackId)
                staleFailure.entered.await()
                metadata.inspect(trackId)
                val afterFailureId = checkNotNull(metadata.state.value.requestId)
                staleFailure.release.countDown()
                eventually("retired ordinary failure cannot replace a later preview") {
                    metadata.state.value.preview?.id == afterFailureId
                }
                assertNull(metadata.state.value.errorCode)

                probe.failure.set(IllegalStateException("private_path_must_not_escape"))
                metadata.inspect(trackId)
                val failedId = checkNotNull(metadata.state.value.requestId)
                eventually("read failure is a fixed safe code") { !metadata.state.value.busy }
                assertEquals("desktop_metadata_read_failed", metadata.state.value.errorCode)
                assertNull(metadata.state.value.preview)
                metadata.dismiss(failedId)
                assertNull(metadata.state.value.requestId)
                assertNull(metadata.state.value.errorCode)
                val resolvesBeforeMissingTrack = resolves.get()
                metadata.inspect("missing-metadata-track")
                val missingTrackId = checkNotNull(metadata.state.value.requestId)
                eventually("missing snapshot track retains a dismissible result identity") { !metadata.state.value.busy }
                assertEquals("desktop_metadata_missing", metadata.state.value.errorCode)
                assertEquals(resolvesBeforeMissingTrack, resolves.get())
                metadata.dismiss(missingTrackId)
                assertNull(metadata.state.value.requestId)
                missingFile.set(true)
                metadata.inspect(trackId)
                eventually("missing managed copy has an honest read-only preview") { metadata.state.value.preview?.embedded?.status == "UNAVAILABLE" }
                assertFalse(checkNotNull(metadata.state.value.preview).canApply)
                missingFile.set(false)
                probe.result.set(DesktopEmbeddedMetadata("UNSUPPORTED", "OGG"))
                metadata.inspect(trackId)
                eventually("unsupported metadata remains informational") { metadata.state.value.preview?.embedded?.status == "UNSUPPORTED" }
                assertFalse(checkNotNull(metadata.state.value.preview).canApply)
                probe.result.set(probe.available.copy(title = null, artist = "Only embedded artist"))
                metadata.inspect(trackId)
                eventually("partial embedded text retains the frozen baseline fallback") {
                    metadata.state.value.preview?.embedded?.artist == "Only embedded artist"
                }
                val partial = checkNotNull(metadata.state.value.preview)
                assertEquals(partial.baseline.title, partial.proposedTitle)
                assertEquals("Only embedded artist", partial.proposedArtist)
                assertNull(checkNotNull(partial.application).title)
                assertEquals(partial.provenance?.title, partial.proposedTitleProvenance)
                metadata.apply(partial.id)
                eventually("partial application preserves the unselected field and its origin") {
                    !metadata.state.value.applying && metadata.state.value.preview == null
                }
                assertEquals(partial.application, applications.last())
                val afterPartial = checkNotNull(realStore.loadTrackTextProvenance(trackId))
                assertEquals(partial.baseline.title, afterPartial.baseline.title)
                assertEquals(partial.provenance?.title, afterPartial.title)
                assertEquals(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, DesktopEmbeddedTextFormat.ID3V24),
                    afterPartial.artist)
                probe.result.set(DesktopEmbeddedMetadata("INVALID", "ID3V24"))
                metadata.inspect(trackId)
                eventually("invalid metadata remains a safe preview") { metadata.state.value.preview?.embedded?.status == "INVALID" }
                val invalid = checkNotNull(metadata.state.value.preview)
                val appliesBeforeInvalid = applies.get()
                metadata.apply(invalid.id)
                assertFalse(invalid.canApply)
                assertEquals(appliesBeforeInvalid, applies.get())
                probe.result.set(probe.available)

                metadata.inspect(trackId)
                eventually("fresh inspection can propose an explicit edit") { metadata.state.value.preview?.canApply == true }
                val conflict = checkNotNull(metadata.state.value.preview)
                realStore.updateTrackTextMetadata(trackId, "Concurrent manual title", "Concurrent manual artist")
                metadata.apply(conflict.id)
                eventually("real store CAS refuses stale frozen labels") { !metadata.state.value.applying }
                assertEquals("desktop_metadata_conflict", metadata.state.value.errorCode)
                assertSame(conflict, metadata.state.value.preview)
                val conflictCalls = applies.get()
                metadata.apply(conflict.id)
                assertEquals(conflictCalls, applies.get())
                assertEquals("Concurrent manual title", realStore.state.value.tracks.single { it.id == trackId }.title)
                metadata.inspect(trackId)
                eventually("ABA scene has a fresh provenance revision") { metadata.state.value.preview?.canApply == true }
                val aba = checkNotNull(metadata.state.value.preview)
                realStore.updateTrackTextMetadata(trackId, "ABA intermediate title", aba.baseline.artist)
                realStore.updateTrackTextMetadata(trackId, aba.baseline.title, aba.baseline.artist)
                val afterAba = checkNotNull(realStore.loadTrackTextProvenance(trackId))
                assertEquals(aba.baseline, afterAba.baseline)
                assertTrue(afterAba.metadataRevision > checkNotNull(aba.provenance).metadataRevision)
                metadata.apply(aba.id)
                eventually("same labels after ABA still refuse the old revision") { !metadata.state.value.applying }
                assertEquals("desktop_metadata_conflict", metadata.state.value.errorCode)
                assertSame(aba, metadata.state.value.preview)
                assertEquals(afterAba, realStore.loadTrackTextProvenance(trackId))
                metadata.inspect(trackId)
                eventually("explicit retry freezes a new baseline") {
                    metadata.state.value.preview?.baseline?.title == "Concurrent manual title"
                }
                val retry = checkNotNull(metadata.state.value.preview)
                val failedWrite = gatedApply(IllegalStateException("private_storage_failure"))
                metadata.apply(retry.id)
                failedWrite.entered.await()
                metadata.dismiss(retry.id)
                metadata.cancel(retry.id)
                metadata.inspect(trackId)
                assertSame(retry, metadata.state.value.preview)
                assertTrue(metadata.state.value.applying)
                failedWrite.release.complete(Unit)
                eventually("failed apply retains the frozen preview") { !metadata.state.value.applying }
                assertSame(retry, metadata.state.value.preview)
                assertEquals("desktop_metadata_apply_failed", metadata.state.value.errorCode)
                realStore.updateTrackDuration(trackId, 9_876)
                metadata.apply(retry.id)
                eventually("a native duration fact also retires the frozen metadata revision") { !metadata.state.value.applying }
                assertEquals("desktop_metadata_conflict", metadata.state.value.errorCode)
                assertSame(retry, metadata.state.value.preview)
                metadata.inspect(trackId)
                val freshRetryId = checkNotNull(metadata.state.value.requestId)
                eventually("explicit reinspection captures the new duration fact revision") {
                    metadata.state.value.preview?.id == freshRetryId
                }
                val freshRetry = checkNotNull(metadata.state.value.preview)
                val write = gatedApply()
                metadata.apply(freshRetry.id)
                write.entered.await()
                assertTrue(metadata.state.value.applying)
                assertSame(freshRetry, metadata.state.value.preview)
                assertEquals("Concurrent manual title", realStore.state.value.tracks.single { it.id == trackId }.title)
                metadata.beginClose()
                val closingWrite = metadataScope.async(start = CoroutineStart.UNDISPATCHED) { metadata.close() }
                assertFalse(closingWrite.isCompleted)
                write.release.complete(Unit)
                closingWrite.await()
                assertNull(metadata.state.value.preview)
                assertFalse(metadata.state.value.applying)
                val applied = realStore.state.value.tracks.single { it.id == trackId }
                assertEquals(probe.available.title, applied.title)
                assertEquals(probe.available.artist, applied.artist)
                assertEquals(9_876L, applied.durationMs)
                assertEquals(initial.queue, realStore.state.value.queue)
                assertEquals(initial.history, realStore.state.value.history)
                assertEquals(native, engine.state.value)
                assertEquals(opens, engine.opens.toList())
                assertEquals(stops, engine.stops.toList())
                assertEquals(seeks, engine.seeks.toList())
                assertEquals(timer, playback.sleepTimer.value)
                assertEquals(writes, queueWrites.get())
                assertEquals(freshRetry.application, applications.last())
                val afterApplied = checkNotNull(realStore.loadTrackTextProvenance(trackId))
                assertEquals(freshRetry.proposedTitleProvenance, afterApplied.title)
                assertEquals(freshRetry.proposedArtistProvenance, afterApplied.artist)

            }
            assertMetadataReadAndApply()

            suspend fun assertMetadataCacheAndClose(): Unit {
                val reopened = owner()
                assertTrue(reopened.state.value.artworks.isEmpty())
                assertNull(reopened.state.value.preview)
                val cacheIds = (0..DesktopEmbeddedMetadataLimits.MAX_CACHE_ENTRIES).map { index ->
                    fixture.importWav("Metadata cache $index.wav", durationMs = 1 + index)
                }
                val cacheSnapshot = realStore.state.value
                for (id in cacheIds) {
                    reopened.inspect(id)
                    eventually("bounded artwork inspection for $id") { reopened.state.value.preview?.baseline?.trackId == id }
                    assertTrue(reopened.state.value.artworks.size <= DesktopEmbeddedMetadataLimits.MAX_CACHE_ENTRIES)
                    assertTrue(reopened.state.value.artworks.values.sumOf { it.byteSize.toLong() } <= DesktopEmbeddedMetadataLimits.MAX_CACHE_BYTES)
                }
                assertEquals(16, reopened.state.value.artworks.size)
                assertFalse(reopened.state.value.artworks.containsKey(cacheIds.first()))
                reopened.inspect(cacheIds[1])
                eventually("explicit inspection touches the cached artwork") { reopened.state.value.preview?.baseline?.trackId == cacheIds[1] }
                reopened.inspect(cacheIds.first())
                eventually("newest artwork evicts the least recently inspected entry") { reopened.state.value.preview?.baseline?.trackId == cacheIds.first() }
                assertTrue(reopened.state.value.artworks.containsKey(cacheIds[1]))
                assertFalse(reopened.state.value.artworks.containsKey(cacheIds[2]))
                val bytes = checkNotNull(reopened.state.value.artworks[cacheIds.first()]).copyPngBytes()
                bytes.fill(0)
                assertArrayEquals(probe.png, checkNotNull(reopened.state.value.artworks[cacheIds.first()]).copyPngBytes())
                assertSame(cacheSnapshot, realStore.state.value)
                val closingRead = probe.gate()
                reopened.inspect(trackId)
                closingRead.entered.await()
                reopened.beginClose()
                val closedState = reopened.state.value
                val closingReadOwner = metadataScope.async(start = CoroutineStart.UNDISPATCHED) { reopened.close() }
                assertFalse(closingReadOwner.isCompleted)
                reopened.inspect(trackId)
                closingRead.release.countDown()
                closingReadOwner.await()
                assertEquals(closedState, reopened.state.value)
                assertTrue(reopened.state.value.artworks.isEmpty())
                assertEquals(0, probe.active.get())
                assertMetadataProvenanceRestart()

            }
            assertMetadataCacheAndClose()

            // Source-only / UNVERIFIED: availability observations extend this same composite.
            val copyBaseline = realStore.state.value
            val copyMarkers = listOf(
                DesktopStorageException("desktop_snapshot_unavailable"),
                IllegalStateException("private_cause", DesktopStorageException("desktop_snapshot_unavailable")),
                IllegalStateException("private_suppressed").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
                CancellationException("private_cancel").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
            )
            suspend fun assertManagedCopyObservations(): Unit {
                val copySnapshot = MutableStateFlow(copyBaseline)
                val copyCalls = CopyOnWriteArrayList<String>()
                val copyActive = AtomicInteger()
                val copyMaximum = AtomicInteger()
                val copyReports = AtomicInteger()
                val copyAllowed = AtomicBoolean(true)
                val copyStatus = AtomicReference("PRESENT")
                val copyMissing = AtomicBoolean(false)
                val copyTimeout = AtomicBoolean(false)
                val copyFailure = AtomicReference<Exception?>(null)
                val nextCopyGate = AtomicReference<MetadataReadGate?>(null)
                val copyStore = object : DesktopLibraryStore by realStore {
                    override val state: StateFlow<DesktopLibrarySnapshot> = copySnapshot
                    override suspend fun inspectManagedCopy(trackId: String): DesktopCopyInspection? {
                        copyCalls.add(trackId)
                        val inFlight = copyActive.incrementAndGet()
                        copyMaximum.updateAndGet { maxOf(it, inFlight) }
                        try {
                            val track = copySnapshot.value.tracks.single { it.id == trackId }
                            val frozen = DesktopCopyInspection(track.id, track.sha256, track.byteSize, copyStatus.get())
                            nextCopyGate.getAndSet(null)?.hold()
                            copyFailure.getAndSet(null)?.let { throw it }
                            if (copyTimeout.getAndSet(false)) withTimeout(1) { delay(Long.MAX_VALUE) }
                            return if (copyMissing.get()) null else frozen
                        } finally { copyActive.decrementAndGet() }
                    }
                    override suspend fun resolveManagedFile(trackId: String): String? = error("unexpected_playback_capability")
                    override suspend fun resolveMetadataFile(trackId: String): String? = error("unexpected_metadata_capability")
                }
                val copyOwner: () -> DesktopCopyInspectionController = {
                    DesktopCopyInspectionController(copyStore, metadataScope,
                        onSnapshotUnavailable = { copyReports.incrementAndGet() }, canRead = copyAllowed::get)
                        .also { copyOwners.add(it) }
                }
                val copyGate: (Exception?) -> MetadataReadGate = { failure ->
                    MetadataReadGate(failure).also { copyGates.add(it); check(nextCopyGate.compareAndSet(null, it)) }
                }
                val copies = copyOwner()
                val emptyCopyState = copies.state.value
                copies.inspect(emptyList())
                assertSame(emptyCopyState, copies.state.value)
                copyAllowed.set(false)
                copies.inspect(listOf(trackId))
                assertSame(emptyCopyState, copies.state.value)
                copyAllowed.set(true)
                copies.inspect(List(DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS + 1) { trackId })
                eventually("oversized observation request is explicit and bounded") {
                    !copies.state.value.busy && copies.state.value.errorCode == "desktop_copy_inspection_limit"
                }
                assertEquals(1_000, copies.state.value.requested)
                copies.inspect(listOf("invalid/private/path"))
                eventually("invalid observation IDs are rejected before store access") {
                    copies.state.value.errorCode == "desktop_copy_inspection_invalid"
                }
                copies.inspect(listOf("absent-observation-track"))
                eventually("unknown track is distinct from unavailable bytes") {
                    copies.state.value.errorCode == "desktop_copy_inspection_missing"
                }
                assertTrue(copyCalls.isEmpty())
                copies.inspect(listOf(trackId, trackId))
                eventually("duplicates inspect one identity and publish one observation") {
                    !copies.state.value.busy && copies.state.value.results[trackId]?.status == "PRESENT"
                }
                assertEquals(1, copies.state.value.requested)
                assertEquals(1, copies.state.value.completed)
                assertEquals(listOf(trackId), copyCalls.toList())
                copyStatus.set("FUTURE_OBSERVATION")
                copies.inspect(listOf(trackId))
                eventually("future observation status remains unknown evidence") {
                    !copies.state.value.busy && copies.state.value.results[trackId]?.status == "FUTURE_OBSERVATION"
                }
                copyStatus.set("UNAVAILABLE")
                copies.inspect(listOf(trackId))
                eventually("unavailable is an explicit last observation") {
                    !copies.state.value.busy && copies.state.value.results[trackId]?.status == "UNAVAILABLE"
                }
                copyMissing.set(true)
                copies.inspect(listOf(trackId))
                eventually("no returned observation cannot become unavailable") {
                    !copies.state.value.busy && copies.state.value.errorCode == "desktop_copy_inspection_stale"
                }
                assertTrue(copies.state.value.results.isEmpty())
                copyMissing.set(false)

                val staleCopy = copyGate(IllegalStateException("private_retired_failure"))
                copies.inspect(listOf(trackId))
                val cancelledCopyId = checkNotNull(copies.state.value.requestId)
                staleCopy.entered.await()
                copies.cancel("retired-unrelated-request")
                assertTrue(copies.state.value.busy)
                copies.cancel(cancelledCopyId)
                assertFalse(copies.state.value.busy)
                val beforeReplacements = copyCalls.size
                repeat(100) { copies.inspect(listOf(trackId)) }
                val latestCopyId = checkNotNull(copies.state.value.requestId)
                copies.cancel(cancelledCopyId)
                assertEquals(latestCopyId, copies.state.value.requestId)
                assertEquals(beforeReplacements, copyCalls.size)
                staleCopy.release.countDown()
                eventually("only latest conflated replacement can publish after a retired failure") {
                    !copies.state.value.busy && copies.state.value.requestId == latestCopyId
                }
                assertNull(copies.state.value.errorCode)
                assertEquals(beforeReplacements + 1, copyCalls.size)
                assertEquals(1, copyMaximum.get())

                val invalidatedRead = copyGate(null)
                copyStatus.set("PRESENT")
                copies.inspect(listOf(trackId))
                invalidatedRead.entered.await()
                copies.invalidate()
                assertTrue(copies.state.value.results.isEmpty())
                assertNull(copies.state.value.requestId)
                copyStatus.set("UNAVAILABLE")
                copies.inspect(listOf(trackId))
                val afterInvalidationId = copies.state.value.requestId
                invalidatedRead.release.countDown()
                eventually("invalidation retires both results and the old read's cache publication") {
                    !copies.state.value.busy && copies.state.value.requestId == afterInvalidationId &&
                        copies.state.value.results[trackId]?.status == "UNAVAILABLE"
                }
                val changedIdentity = copyGate(null)
                copies.inspect(listOf(trackId))
                changedIdentity.entered.await()
                val oldTrack = copySnapshot.value.tracks.single { it.id == trackId }
                copySnapshot.value = copySnapshot.value.copy(tracks = copySnapshot.value.tracks.map {
                    if (it.id == trackId) it.copy(sha256 = "a".repeat(64), byteSize = it.byteSize + 1) else it
                })
                changedIdentity.release.countDown()
                eventually("old bytes cannot publish against a replaced baseline") { !copies.state.value.busy }
                assertFalse(copies.state.value.results.containsKey(trackId))
                val readsAfterIdentityChange = copyCalls.size
                copySnapshot.value = copyBaseline
                assertEquals(readsAfterIdentityChange, copyCalls.size)
                copies.inspect(listOf(trackId))
                eventually("fresh explicit read observes the current bytes") { !copies.state.value.busy }
                assertEquals(oldTrack.sha256, copies.state.value.results[trackId]?.sha256)

                val syntheticTracks = (0..DesktopCopyInspectionLimits.MAX_CACHED_RESULTS).map {
                    oldTrack.copy(id = "copy-cache-$it")
                }
                copySnapshot.value = copyBaseline.copy(tracks = syntheticTracks)
                val chunkObserved = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                    copies.state.first { it.busy && it.completed == 16 }
                }
                copies.inspect(syntheticTracks.take(1_000).map { it.id })
                assertEquals(1_000, chunkObserved.await().requested)
                eventually("one bounded batch populates the process cache") {
                    !copies.state.value.busy && copies.state.value.completed == 1_000
                }
                assertEquals(1_000, copies.state.value.results.size)
                for (id in listOf(syntheticTracks.last().id, syntheticTracks[1].id, syntheticTracks.first().id)) {
                    copies.inspect(listOf(id))
                    eventually("cache touch observes $id") { !copies.state.value.busy }
                    assertEquals(1_000, copies.state.value.results.size)
                }
                assertTrue(copies.state.value.results.containsKey(syntheticTracks[1].id))
                assertFalse(copies.state.value.results.containsKey(syntheticTracks[2].id))
                copySnapshot.value = copyBaseline
                copyTimeout.set(true)
                copies.inspect(listOf(trackId))
                eventually("a store timeout remains a safe timeout without unavailable evidence") {
                    !copies.state.value.busy && copies.state.value.errorCode == "desktop_copy_inspection_timeout"
                }
                assertTrue(copies.state.value.results.isEmpty())
                copyFailure.set(IllegalStateException("private_current_failure"))
                copies.inspect(listOf(trackId))
                eventually("ordinary inspection errors expose only a fixed code") {
                    copies.state.value.errorCode == "desktop_copy_inspection_read_failed"
                }
                copies.inspect(listOf(trackId))
                eventually("ordinary failure allows a fresh explicit request") {
                    !copies.state.value.busy && copies.state.value.errorCode == null
                }
                assertSame(copyBaseline, realStore.state.value)
                assertEquals(native, engine.state.value)
                assertEquals(opens, engine.opens.toList())
                assertEquals(stops, engine.stops.toList())
                assertEquals(seeks, engine.seeks.toList())
                assertEquals(timer, playback.sleepTimer.value)
                assertEquals(writes, queueWrites.get())

                val reentrantCopy = copyOwner()
                val reentrantAdmission = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                    reentrantCopy.state.first { it.busy }
                    reentrantCopy.inspect(listOf(trackId))
                    checkNotNull(reentrantCopy.state.value.requestId)
                }
                val beforeReentrant = copyCalls.size
                reentrantCopy.inspect(listOf(trackId))
                val reentrantId = reentrantAdmission.await()
                eventually("synchronous admission replacement retains its mailbox ownership") {
                    reentrantCopy.state.value.requestId == reentrantId && !reentrantCopy.state.value.busy
                }
                assertEquals(beforeReentrant + 1, copyCalls.size)
                val reentrantCompletion = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                    reentrantCopy.state.first { it.busy }
                    reentrantCopy.state.first { !it.busy }
                    reentrantCopy.inspect(listOf(trackId))
                    checkNotNull(reentrantCopy.state.value.requestId)
                }
                reentrantCopy.inspect(listOf(trackId))
                val completionReplacementId = reentrantCompletion.await()
                eventually("completion publication cannot clear a synchronously admitted replacement") {
                    reentrantCopy.state.value.requestId == completionReplacementId && !reentrantCopy.state.value.busy
                }
                val closingCopyRead = copyGate(null)
                copies.inspect(listOf(trackId))
                closingCopyRead.entered.await()
                copies.beginClose()
                val copyClosedState = copies.state.value
                val joiningCopies = metadataScope.async(start = CoroutineStart.UNDISPATCHED) { copies.close() }
                assertFalse(joiningCopies.isCompleted)
                copies.inspect(listOf(trackId))
                closingCopyRead.release.countDown()
                joiningCopies.await()
                assertEquals(copyClosedState, copies.state.value)
                assertEquals(0, copyActive.get())
                assertTrue(copyOwner().state.value.results.isEmpty())

                for ((index, marker) in copyMarkers.withIndex()) {
                    val failedCopy = copyOwner()
                    val lateMarker = copyGate(marker)
                    failedCopy.inspect(listOf(trackId))
                    val failedId = checkNotNull(failedCopy.state.value.requestId)
                    lateMarker.entered.await()
                    when (index) {
                        0 -> failedCopy.cancel(failedId)
                        1 -> failedCopy.inspect(listOf(trackId))
                        2 -> failedCopy.invalidate()
                        else -> failedCopy.beginClose()
                    }
                    val beforeMarker = copyCalls.size
                    lateMarker.release.countDown()
                    eventually("retired copy marker $index reaches global refusal exactly once") {
                        copyReports.get() == index + 1 && failedCopy.state.value.errorCode == "desktop_snapshot_unavailable"
                    }
                    failedCopy.invalidate()
                    failedCopy.refuse()
                    failedCopy.inspect(listOf(trackId))
                    failedCopy.close()
                    assertEquals(beforeMarker, copyCalls.size)
                    assertEquals(index + 1, copyReports.get())
                    assertEquals("desktop_snapshot_unavailable", failedCopy.state.value.errorCode)
                    assertTrue(failedCopy.state.value.results.isEmpty())
                }

            }
            assertManagedCopyObservations()

            suspend fun assertRuntimeImportLifecycle(): Unit {
                // No OS chooser/native process or persistent settings are used by these host scenes.
                val runtimeSnapshot = MutableStateFlow(copyBaseline.copy(queue = null, history = emptyList()))
                val runtimeClosed = AtomicBoolean(false)
                val runtimeReadGate = AtomicReference<MetadataReadGate?>(null)
                val runtimeReadFailure = AtomicReference<Exception?>(null)
                val runtimeAppendCalls = CopyOnWriteArrayList<Pair<String, List<String>>>()
                val runtimeAppendGate = AtomicReference<MetadataApplyGate?>(null)
                val runtimeAppendFailure = AtomicReference<Exception?>(null)
                val runtimeAppendCompletions = AtomicInteger()
                val runtimeStore = object : DesktopLibraryStore by realStore {
                    override val state: StateFlow<DesktopLibrarySnapshot> = runtimeSnapshot
                    override suspend fun inspectManagedCopy(trackId: String): DesktopCopyInspection? {
                        val track = runtimeSnapshot.value.tracks.single { it.id == trackId }
                        runtimeReadGate.getAndSet(null)?.hold()
                        runtimeReadFailure.getAndSet(null)?.let { throw it }
                        return DesktopCopyInspection(track.id, track.sha256, track.byteSize, "PRESENT")
                    }
                    override suspend fun close(): Unit { runtimeClosed.set(true) }
                    override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit =
                        error("unexpected_runtime_queue_write")
                    override suspend fun appendToPlaylist(playlistId: String, trackId: String): Unit =
                        error("unexpected_sequential_playlist_append")
                    override suspend fun appendTracksToPlaylist(playlistId: String, trackIds: List<String>): Unit {
                        runtimeAppendCalls.add(playlistId to trackIds.toList())
                        runtimeAppendGate.getAndSet(null)?.let { pending ->
                            pending.entered.complete(Unit)
                            pending.release.await()
                        }
                        runtimeAppendFailure.getAndSet(null)?.let { throw it }
                        if (trackIds.size > 1_000) throw DesktopStorageException("desktop_playlist_batch_limit")
                        runtimeAppendCompletions.incrementAndGet()
                    }
                }
                val importAction = AtomicReference<suspend () -> DesktopImportResult>({ DesktopImportResult(0, 1, 0) })
                val runtimeImporter = object : DesktopImportPort {
                    override suspend fun importSelected(paths: List<String>): DesktopImportResult = importAction.get().invoke()
                }
                val runtimeConstructor = DesktopRuntime::class.java.declaredConstructors.single { it.parameterCount == 5 }
                    .apply { isAccessible = true }
                fun newRuntime(importPort: DesktopImportPort = runtimeImporter): DesktopRuntime {
                    val runtimeScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
                    val preferences = object : DesktopPreferencesStore {
                        override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState())
                        override suspend fun setAppLanguage(value: String): Unit = error("unexpected_settings_write")
                        override suspend fun setAppearanceMode(value: String): Unit = error("unexpected_settings_write")
                        override suspend fun setAccentPalette(value: String): Unit = error("unexpected_settings_write")
                        override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("unexpected_settings_write")
                        override suspend fun applyPortable(value: DesktopPreferences): Unit = error("unexpected_settings_write")
                        override suspend fun close(): Unit = Unit
                    }
                    val runtimeEngine = PolicyEngine()
                    val runtimePlayback = DesktopPlaybackController(runtimeStore, runtimeEngine, runtimeScope,
                        periodicObservations = emptyFlow())
                    return (runtimeConstructor.newInstance(runtimeStore, importPort, runtimePlayback, runtimeScope,
                        DesktopSettingsController(preferences, runtimeScope)) as DesktopRuntime).also { runtimeOwners.add(it) }
                }
                val runtime = newRuntime()
                // This fake observes the host handoff only; data scenarios own SQL atomicity.
                val beforeRuntimeAppend = runtimeSnapshot.value
                runtimeAppendFailure.set(DesktopStorageException("desktop_playlist_missing"))
                runtime.addTracksToPlaylist("synthetic-playlist", listOf(trackId))
                eventually("host maps a known batch target refusal") { runtime.error.value == "desktop_playlist_missing" }
                assertEquals(1, runtimeAppendCalls.size)
                val appendGate = MetadataApplyGate().also { writeGates.add(it) }
                runtimeAppendGate.set(appendGate)
                runtime.addTracksToPlaylist("synthetic-playlist", List(1_000) { trackId })
                appendGate.entered.await()
                assertEquals("desktop_playlist_missing", runtime.error.value)
                assertEquals(0, runtimeAppendCompletions.get())
                assertEquals(beforeRuntimeAppend, runtimeSnapshot.value)
                val mutableAppend = mutableListOf(trackId, trackId)
                runtime.addTracksToPlaylist("synthetic-playlist", mutableAppend)
                mutableAppend.clear()
                mutableAppend.addAll(List(2_000) { "mutated-after-dispatch" })
                assertEquals(2, runtimeAppendCalls.size)
                appendGate.release.complete(Unit)
                eventually("host completes each selected batch through one store invocation") {
                    runtimeAppendCompletions.get() == 2 && runtime.error.value == null
                }
                assertEquals(3, runtimeAppendCalls.size)
                assertEquals("synthetic-playlist" to List(1_000) { trackId }, runtimeAppendCalls[1])
                assertEquals("synthetic-playlist" to listOf(trackId, trackId), runtimeAppendCalls[2])
                assertEquals(beforeRuntimeAppend, runtimeSnapshot.value)
                runtime.addTracksToPlaylist("synthetic-playlist", emptyList())
                eventually("empty host batch settles as one store no-op") { runtimeAppendCompletions.get() == 3 }
                assertEquals(emptyList<String>(), runtimeAppendCalls.last().second)
                runtime.addTracksToPlaylist("synthetic-playlist", List(2_000) { trackId })
                eventually("host forwards only the bounded overflow witness") { runtime.error.value == "desktop_playlist_batch_limit" }
                assertEquals(1_001, runtimeAppendCalls.last().second.size)
                assertEquals(3, runtimeAppendCompletions.get())
                for (code in listOf("desktop_playlist_limit", "desktop_playlist_order_invalid", "desktop_track_missing",
                    "desktop_track_not_in_collection", "desktop_identity_invalid", "private_storage_failure")) {
                    val calls = runtimeAppendCalls.size
                    runtimeAppendFailure.set(DesktopStorageException(code))
                    runtime.addTracksToPlaylist("synthetic-playlist", listOf(trackId, trackId))
                    val expected = if (code == "private_storage_failure") "LIBRARY_OPERATION_FAILED" else code
                    eventually("host retains safe batch failure mapping $code") { runtime.error.value == expected }
                    assertEquals(calls + 1, runtimeAppendCalls.size)
                    assertEquals(beforeRuntimeAppend, runtimeSnapshot.value)
                }
                val selectionConstructor = Class.forName("app.autplay.desktop.DesktopSourceSelection")
                    .getDeclaredConstructor(List::class.java, Boolean::class.javaPrimitiveType, Boolean::class.javaPrimitiveType)
                    .apply { isAccessible = true }
                val selectedSource = selectionConstructor.newInstance(listOf("explicit-synthetic-source"), false, false)
                val cancelledSource = selectionConstructor.newInstance(emptyList<String>(), false, true)
                val beginImport = DesktopRuntime::class.java.declaredMethods.single { it.name == "beginImport" }
                    .apply { isAccessible = true }
                for (mode in listOf("success", "import_failure", "post_commit_failure", "cancel_import", "chooser_cancel", "chooser_failure", "cancel_chooser")) {
                    val entered = CompletableDeferred<Unit>()
                    val release = CompletableDeferred<Unit>().also { importGates.add(it) }
                    val waitForRelease: suspend () -> Unit = {
                        entered.complete(Unit)
                        withContext(NonCancellable) { release.await() }
                    }
                    importAction.set {
                        waitForRelease()
                        if (mode == "post_commit_failure") {
                            // Simulate an already-published effect followed by failed completion.
                            runtimeSnapshot.value = runtimeSnapshot.value.copy(tracks = runtimeSnapshot.value.tracks.map {
                                if (it.id == trackId) it.copy(title = "Settled synthetic title") else it
                            })
                        }
                        if (mode.endsWith("failure")) error("private_import_failure")
                        DesktopImportResult(0, 1, 0)
                    }
                    val select: suspend () -> Any = {
                        if (mode.contains("chooser")) waitForRelease()
                        if (mode == "chooser_failure") error("private_chooser_failure")
                        if (mode == "chooser_cancel") cancelledSource else selectedSource
                    }
                    assertTrue(beginImport.invoke(runtime, null, null, null, select) as Boolean)
                    entered.await()
                    runtime.copyInspection.inspect(listOf(trackId))
                    eventually("advisory observation is present during $mode") {
                        !runtime.copyInspection.state.value.busy && runtime.copyInspection.state.value.results.isNotEmpty()
                    }
                    val retiredImportRead = if (mode == "post_commit_failure") MetadataReadGate().also {
                        copyGates.add(it)
                        runtimeReadGate.set(it)
                        runtime.copyInspection.inspect(listOf(trackId))
                        it.entered.await()
                    } else null
                    if (mode.startsWith("cancel_")) runtime.cancelImport()
                    assertTrue(runtime.importing.value)
                    assertFalse(runtime.rejectOversizedImport())
                    val settled = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                        runtime.importing.first { !it }
                        assertTrue(runtime.copyInspection.state.value.results.isEmpty())
                        assertNull(runtime.copyInspection.state.value.requestId)
                    }
                    release.complete(Unit)
                    settled.await()
                    assertFalse(runtime.importing.value)
                    if (retiredImportRead != null) {
                        retiredImportRead.release.countDown()
                        runtime.copyInspection.inspect(listOf(trackId))
                        eventually("post-import explicit request owns evidence after a retired read settles") {
                            !runtime.copyInspection.state.value.busy && runtime.copyInspection.state.value.results.isNotEmpty()
                        }
                    }
                }
                runtime.copyInspection.inspect(listOf(trackId))
                eventually("observation exists before cancelling an unstarted import") { !runtime.copyInspection.state.value.busy }
                val cancelBeforeStart = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                    runtime.importing.first { it }
                    runtime.cancelImport()
                }
                assertTrue(runtime.rejectOversizedImport())
                cancelBeforeStart.await()
                assertFalse(runtime.importing.value)
                assertTrue(runtime.copyInspection.state.value.results.isEmpty())
                runtimeReadFailure.set(copyMarkers[2])
                runtime.copyInspection.inspect(listOf(trackId))
                eventually("copy marker invokes the host's shared sticky refusal") {
                    runtime.error.value == "desktop_snapshot_unavailable" &&
                        runtime.controller.error.value == "desktop_snapshot_unavailable"
                }
                assertEquals("desktop_snapshot_unavailable", runtime.history.state.value.errorCode)
                assertEquals("desktop_snapshot_unavailable", runtime.metadata.state.value.errorCode)
                runtime.copyInspection.invalidate()
                runtime.copyInspection.inspect(listOf(trackId))
                assertEquals("desktop_snapshot_unavailable", runtime.copyInspection.state.value.errorCode)
                assertFalse(runtime.rejectOversizedImport())
                val refusedAppendCalls = runtimeAppendCalls.size
                runtime.addTracksToPlaylist("synthetic-playlist", listOf(trackId, trackId))
                delay(75)
                assertEquals(refusedAppendCalls, runtimeAppendCalls.size)
                runtime.close()
                runtime.addTracksToPlaylist("synthetic-playlist", listOf(trackId, trackId))
                delay(75)
                assertEquals(refusedAppendCalls, runtimeAppendCalls.size)

                val appendRefused = newRuntime()
                val appendMarkerCalls = runtimeAppendCalls.size
                runtimeAppendFailure.set(DesktopStorageException("desktop_track_missing").apply {
                    addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
                })
                appendRefused.addTracksToPlaylist("synthetic-playlist", listOf(trackId, trackId))
                eventually("batch append snapshot marker takes priority over a safe membership error") {
                    appendRefused.error.value == "desktop_snapshot_unavailable" &&
                        appendRefused.controller.error.value == "desktop_snapshot_unavailable"
                }
                assertEquals(appendMarkerCalls + 1, runtimeAppendCalls.size)
                appendRefused.addTracksToPlaylist("synthetic-playlist", listOf(trackId))
                delay(75)
                assertEquals(appendMarkerCalls + 1, runtimeAppendCalls.size)
                appendRefused.close()

                suspend fun assertImportReviewLifecycle(): Unit {
                    // Review is an owned suspension, never permission inferred from selection or elapsed time.
                    val reviewPreview = AtomicReference(DesktopImportPreview("review-first", listOf(
                        DesktopImportPreviewFile("one", "First synthetic file", 10),
                        DesktopImportPreviewFile("two", "Second synthetic file", 20),
                        DesktopImportPreviewFile("three", "Third synthetic file", 30),
                    ), 60, failed = 1, errorCode = "import_unsupported_format"))
                    val prepareAction = AtomicReference<suspend () -> DesktopImportPreview>({ reviewPreview.get() })
                    val reviewImportAction = AtomicReference<suspend (String, (DesktopImportProgress) -> Unit) -> DesktopImportResult>(
                        { _, _ -> DesktopImportResult(2, 1, 1, "import_unsupported_format") },
                    )
                    val prepareCalls = AtomicInteger()
                    val importedPreviews = CopyOnWriteArrayList<String>()
                    val progressCallbacks = CopyOnWriteArrayList<(DesktopImportProgress) -> Unit>()
                    val discardedPreviews = CopyOnWriteArrayList<String>()
                    val discardGate = AtomicReference<MetadataReadGate?>(null)
                    val reviewImporter = object : DesktopImportReviewPort {
                        override suspend fun importSelected(paths: List<String>): DesktopImportResult = error("unexpected_legacy_import")
                        override suspend fun prepareSelected(paths: List<String>): DesktopImportPreview {
                            prepareCalls.incrementAndGet()
                            return prepareAction.get().invoke()
                        }
                        override suspend fun importPrepared(previewId: String, onProgress: (DesktopImportProgress) -> Unit): DesktopImportResult {
                            importedPreviews.add(previewId)
                            progressCallbacks.add(onProgress)
                            return reviewImportAction.get().invoke(previewId, onProgress)
                        }
                        override fun discardPrepared(previewId: String): Unit {
                            discardedPreviews.add(previewId)
                            discardGate.getAndSet(null)?.hold()
                        }
                    }
                    val reviewed = newRuntime(reviewImporter)
                    val explicitSelection: suspend () -> Any = { selectedSource }
                    suspend fun awaitReview(host: DesktopRuntime): DesktopImportPreview {
                        eventually("explicit selection exposes the exact prepared review") { host.importReview.value.phase == DesktopImportPhase.REVIEWING }
                        assertTrue(host.importing.value)
                        return checkNotNull(host.importReview.value.preview)
                    }
                    assertFalse(reviewed.confirmImport("review-first"))
                    val preparingEntered = CompletableDeferred<Unit>()
                    val preparingRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    prepareAction.set {
                        preparingEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { preparingRelease.await() } }
                        reviewPreview.get()
                    }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    withTimeout(5_000) { preparingEntered.await() }
                    assertEquals(DesktopImportPhase.PREPARING, reviewed.importReview.value.phase)
                    assertFalse(reviewed.confirmImport("review-first"))
                    assertFalse(reviewed.rejectOversizedImport())
                    assertTrue(importedPreviews.isEmpty())
                    preparingRelease.complete(Unit)
                    val firstReview = awaitReview(reviewed)
                    assertEquals(reviewPreview.get(), firstReview)
                    assertEquals(DesktopImportProgress(firstReview.id, 3, failed = 1, errorCode = "import_unsupported_format"), reviewed.importReview.value.progress)
                    assertTrue(importedPreviews.isEmpty())
                    assertFalse(reviewed.confirmImport("retired-preview"))
                    reviewed.copyInspection.inspect(listOf(trackId))
                    eventually("review retains advisory evidence until its import settles") { reviewed.copyInspection.state.value.results.isNotEmpty() }
                    val importEntered = CompletableDeferred<Unit>()
                    val importRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    reviewImportAction.set { id, progress ->
                        progress(DesktopImportProgress(id, 3, completedFiles = 1, imported = 1, failed = 1))
                        importEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { importRelease.await() } }
                        progress(DesktopImportProgress(id, 3, completedFiles = 2, imported = 1, duplicates = 1, failed = 1,
                            currentTitle = "Late synthetic title"))
                        throw CancellationException("synthetic_partial_import_cancelled")
                    }
                    assertTrue(reviewed.confirmImport(firstReview.id))
                    assertFalse(reviewed.confirmImport(firstReview.id))
                    withTimeout(5_000) { importEntered.await() }
                    assertEquals(listOf(firstReview.id), importedPreviews.toList())
                    assertEquals(DesktopImportPhase.IMPORTING, reviewed.importReview.value.phase)
                    val firstProgress = progressCallbacks.single()
                    val activeProgress = reviewed.importReview.value
                    firstProgress(DesktopImportProgress("retired-preview", 3, 3, 3))
                    firstProgress(DesktopImportProgress(firstReview.id, 3, 0, failed = 1))
                    firstProgress(DesktopImportProgress(firstReview.id, 4, 2, imported = 2, failed = 1))
                    assertEquals(activeProgress, reviewed.importReview.value)
                    val cleanupGate = MetadataReadGate().also { copyGates.add(it); discardGate.set(it) }
                    reviewed.cancelImport()
                    assertEquals(DesktopImportPhase.CANCELLING, reviewed.importReview.value.phase)
                    assertFalse(reviewed.confirmImport(firstReview.id))
                    assertFalse(reviewed.rejectOversizedImport())
                    importRelease.complete(Unit)
                    withTimeout(5_000) { cleanupGate.entered.await() }
                    assertTrue(reviewed.importing.value)
                    assertEquals(DesktopImportPhase.CANCELLING, reviewed.importReview.value.phase)
                    assertEquals(DesktopImportProgress(firstReview.id, 3, 2, 1, 1, 1), reviewed.importReview.value.progress)
                    assertFalse(reviewed.confirmImport(firstReview.id))
                    val cancelledSettled = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                        withTimeout(5_000) { reviewed.importing.first { !it } }
                        assertTrue(reviewed.copyInspection.state.value.results.isEmpty())
                        assertNull(reviewed.copyInspection.state.value.requestId)
                    }
                    cleanupGate.release.countDown()
                    withTimeout(5_000) { cancelledSettled.await() }
                    assertEquals(DesktopImportResult(1, 1, 1, cancelled = true), reviewed.importResult.value)
                    assertEquals("IMPORT_CANCELLED", reviewed.error.value)
                    assertEquals(DesktopImportReviewState(), reviewed.importReview.value)
                    val cancelledResult = reviewed.importResult.value
                    firstProgress(DesktopImportProgress(firstReview.id, 3, 3, 3, failed = 1))
                    assertEquals(cancelledResult, reviewed.importResult.value)
                    assertEquals(DesktopImportReviewState(), reviewed.importReview.value)

                    prepareAction.set { reviewPreview.get() }
                    reviewPreview.set(firstReview.copy(id = "review-second"))
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    val secondReview = awaitReview(reviewed)
                    val secondState = reviewed.importReview.value
                    firstProgress(DesktopImportProgress(firstReview.id, 3, 3, 3, failed = 1))
                    firstProgress(DesktopImportProgress(secondReview.id, 3, 3, 3, failed = 1))
                    assertEquals(secondState, reviewed.importReview.value)
                    assertFalse(reviewed.confirmImport(firstReview.id))
                    val previewCleanup = MetadataReadGate().also { copyGates.add(it); discardGate.set(it) }
                    reviewed.cancelImport()
                    withTimeout(5_000) { previewCleanup.entered.await() }
                    assertTrue(reviewed.importing.value)
                    assertFalse(reviewed.confirmImport(secondReview.id))
                    assertFalse(reviewed.rejectOversizedImport())
                    assertEquals(listOf(firstReview.id), importedPreviews.toList())
                    previewCleanup.release.countDown()
                    eventually("cancelled review retires its named plan before admission reopens") { !reviewed.importing.value }
                    assertEquals(DesktopImportResult(0, 0, 1, errorCode = "import_unsupported_format", cancelled = true), reviewed.importResult.value)
                    assertTrue(discardedPreviews.contains(secondReview.id))

                    reviewPreview.set(firstReview.copy(id = "review-success"))
                    reviewImportAction.set { id, progress ->
                        progress(DesktopImportProgress(id, 3, 3, 2, 1, 1))
                        DesktopImportResult(2, 1, 1, "import_unsupported_format")
                    }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    val approved = awaitReview(reviewed)
                    assertEquals(1, importedPreviews.size)
                    assertTrue(reviewed.confirmImport(approved.id))
                    assertFalse(reviewed.confirmImport(approved.id))
                    eventually("one explicit confirmation settles one exact plan") { !reviewed.importing.value }
                    assertEquals(listOf(firstReview.id, approved.id), importedPreviews.toList())
                    assertEquals(DesktopImportResult(2, 1, 1, "import_unsupported_format"), reviewed.importResult.value)
                    assertFalse(reviewed.confirmImport(approved.id))

                    reviewPreview.set(firstReview.copy(id = "cancel-preparation"))
                    val cancelledPrepareEntered = CompletableDeferred<Unit>()
                    val cancelledPrepareRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    prepareAction.set {
                        cancelledPrepareEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { cancelledPrepareRelease.await() } }
                        reviewPreview.get()
                    }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    withTimeout(5_000) { cancelledPrepareEntered.await() }
                    reviewed.cancelImport()
                    assertFalse(reviewed.confirmImport("cancel-preparation"))
                    assertTrue(reviewed.importing.value)
                    cancelledPrepareRelease.complete(Unit)
                    eventually("late preparation is discarded without ever exposing approval") { !reviewed.importing.value }
                    assertTrue(discardedPreviews.contains("cancel-preparation"))
                    assertEquals(2, importedPreviews.size)
                    val beforeCancelledSelection = prepareCalls.get()
                    val selectionEntered = CompletableDeferred<Unit>()
                    val selectionRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    val heldSelection: suspend () -> Any = {
                        selectionEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { selectionRelease.await() } }
                        selectedSource
                    }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, heldSelection) as Boolean)
                    withTimeout(5_000) { selectionEntered.await() }
                    assertEquals(DesktopImportPhase.SELECTING, reviewed.importReview.value.phase)
                    reviewed.cancelImport()
                    selectionRelease.complete(Unit)
                    eventually("cancelled selection never enters preparation") { !reviewed.importing.value }
                    assertEquals(beforeCancelledSelection, prepareCalls.get())
                    assertTrue(reviewed.rejectOversizedImport())
                    eventually("oversized review selection settles without preparation") { !reviewed.importing.value }
                    assertEquals(beforeCancelledSelection, prepareCalls.get())
                    assertEquals("import_selection_limit", reviewed.importResult.value?.errorCode)
                    prepareAction.set { DesktopImportPreview("empty-review", emptyList(), 0, 2, "import_file_limit") }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    val emptyPreview = awaitReview(reviewed)
                    assertTrue(emptyPreview.files.isEmpty())
                    assertEquals(2, emptyPreview.failed)
                    assertEquals("import_file_limit", emptyPreview.errorCode)
                    assertFalse(reviewed.confirmImport(emptyPreview.id))
                    assertFalse(reviewed.rejectOversizedImport())
                    assertEquals(2, importedPreviews.size)
                    reviewed.cancelImport()
                    eventually("empty preparation is dismissed without calling the copy adapter") { !reviewed.importing.value }
                    assertEquals(DesktopImportResult(0, 0, 2, errorCode = "import_file_limit", cancelled = true), reviewed.importResult.value)
                    assertTrue(discardedPreviews.contains("empty-review"))
                    prepareAction.set { DesktopImportPreview("no-audio-review", emptyList(), 0) }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    val noAudioPreview = awaitReview(reviewed)
                    assertFalse(reviewed.confirmImport(noAudioPreview.id))
                    reviewed.cancelImport()
                    eventually("a folder without supported audio needs dismissal rather than implying successful import") { !reviewed.importing.value }
                    assertEquals(DesktopImportResult(0, 0, 0, cancelled = true), reviewed.importResult.value)
                    assertEquals(2, importedPreviews.size)
                    prepareAction.set { error("private_synthetic_preparation_failure") }
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    eventually("preparation exceptions settle behind a safe host code") { !reviewed.importing.value }
                    assertEquals("IMPORT_FAILED", reviewed.error.value)
                    assertEquals(2, importedPreviews.size)

                    prepareAction.set { reviewPreview.get() }
                    reviewPreview.set(firstReview.copy(id = "sticky-review"))
                    assertTrue(beginImport.invoke(reviewed, null, null, null, explicitSelection) as Boolean)
                    val stickyPreview = awaitReview(reviewed)
                    reviewed.controller.reportSnapshotUnavailable()
                    eventually("review honors the shared sticky snapshot refusal") { reviewed.error.value == "desktop_snapshot_unavailable" }
                    assertFalse(reviewed.confirmImport(stickyPreview.id))
                    reviewed.cancelImport()
                    eventually("cancel completion retains sticky restart priority") { !reviewed.importing.value }
                    assertEquals("desktop_snapshot_unavailable", reviewed.error.value)
                    assertFalse(reviewed.rejectOversizedImport())
                    reviewed.close()
                    assertFalse(reviewed.confirmImport(stickyPreview.id))

                    val refusedImport = newRuntime(reviewImporter)
                    reviewPreview.set(firstReview.copy(id = "sticky-import"))
                    reviewImportAction.set { id, progress ->
                        progress(DesktopImportProgress(id, 3, 1, imported = 1, failed = 1))
                        DesktopImportResult(1, 0, 1, "import_library_reopen_required", cancelled = true)
                    }
                    assertTrue(beginImport.invoke(refusedImport, null, null, null, explicitSelection) as Boolean)
                    val refusedImportPreview = awaitReview(refusedImport)
                    assertTrue(refusedImport.confirmImport(refusedImportPreview.id))
                    eventually("cancelled post-commit refusal preserves partial counts and restart priority") { !refusedImport.importing.value }
                    assertEquals(DesktopImportResult(1, 0, 1, "import_library_reopen_required", cancelled = true), refusedImport.importResult.value)
                    assertEquals("desktop_snapshot_unavailable", refusedImport.error.value)
                    assertFalse(refusedImport.confirmImport(refusedImportPreview.id))
                    assertFalse(refusedImport.rejectOversizedImport())
                    refusedImport.close()

                    val markedImport = newRuntime(reviewImporter)
                    reviewPreview.set(firstReview.copy(id = "cancelled-import-marker"))
                    reviewImportAction.set { id, progress ->
                        progress(DesktopImportProgress(id, 3, 1, imported = 1, failed = 1))
                        throw CancellationException("private_synthetic_import_cancellation").apply {
                            addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
                        }
                    }
                    val markerCleanup = MetadataReadGate(IllegalStateException("private_synthetic_cleanup_failure")).also {
                        copyGates.add(it)
                        it.release.countDown()
                        discardGate.set(it)
                    }
                    assertTrue(beginImport.invoke(markedImport, null, null, null, explicitSelection) as Boolean)
                    val markedPreview = awaitReview(markedImport)
                    assertTrue(markedImport.confirmImport(markedPreview.id))
                    eventually("suppressed snapshot marker survives cancellation and a later cleanup failure") { !markedImport.importing.value }
                    assertTrue(markerCleanup.entered.isCompleted)
                    assertEquals(DesktopImportResult(1, 0, 1, cancelled = true), markedImport.importResult.value)
                    assertEquals("desktop_snapshot_unavailable", markedImport.error.value)
                    assertFalse(markedImport.rejectOversizedImport())
                    markedImport.close()

                    runtimeClosed.set(false)
                    val closingPreparation = newRuntime(reviewImporter)
                    reviewPreview.set(firstReview.copy(id = "close-preparation"))
                    val closePrepareEntered = CompletableDeferred<Unit>()
                    val closePrepareRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    prepareAction.set {
                        closePrepareEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { closePrepareRelease.await() } }
                        reviewPreview.get()
                    }
                    val closePrepareCleanup = MetadataReadGate().also { copyGates.add(it); discardGate.set(it) }
                    val beforePreparationClose = importedPreviews.size
                    assertTrue(beginImport.invoke(closingPreparation, null, null, null, explicitSelection) as Boolean)
                    withTimeout(5_000) { closePrepareEntered.await() }
                    val closingPreparationOwner = metadataScope.async(start = CoroutineStart.UNDISPATCHED) { closingPreparation.close() }
                    assertFalse(closingPreparationOwner.isCompleted)
                    assertFalse(runtimeClosed.get())
                    assertFalse(closingPreparation.confirmImport("close-preparation"))
                    assertFalse(closingPreparation.rejectOversizedImport())
                    closePrepareRelease.complete(Unit)
                    withTimeout(5_000) { closePrepareCleanup.entered.await() }
                    assertFalse(runtimeClosed.get())
                    assertFalse(closingPreparationOwner.isCompleted)
                    closePrepareCleanup.release.countDown()
                    withTimeout(5_000) { closingPreparationOwner.await() }
                    assertTrue(runtimeClosed.get())
                    assertEquals(beforePreparationClose, importedPreviews.size)
                    assertTrue(discardedPreviews.contains("close-preparation"))
                    assertEquals(DesktopImportResult(0, 0, 1, errorCode = "import_unsupported_format", cancelled = true), closingPreparation.importResult.value)
                    assertEquals(DesktopImportReviewState(), closingPreparation.importReview.value)

                    prepareAction.set { reviewPreview.get() }
                    val closingReview = newRuntime(reviewImporter)
                    reviewPreview.set(firstReview.copy(id = "close-review"))
                    assertTrue(beginImport.invoke(closingReview, null, null, null, explicitSelection) as Boolean)
                    val closePreview = awaitReview(closingReview)
                    val beforeReviewClose = importedPreviews.size
                    closingReview.close()
                    assertTrue(discardedPreviews.contains(closePreview.id))
                    assertEquals(beforeReviewClose, importedPreviews.size)
                    assertFalse(closingReview.confirmImport(closePreview.id))
                    assertFalse(closingReview.rejectOversizedImport())

                    runtimeClosed.set(false)
                    val closingRuntime = newRuntime(reviewImporter)
                    reviewPreview.set(firstReview.copy(id = "close-active-import"))
                    val closeImportEntered = CompletableDeferred<Unit>()
                    val closeImportRelease = CompletableDeferred<Unit>().also { importGates.add(it) }
                    reviewImportAction.set { id, progress ->
                        closeImportEntered.complete(Unit)
                        withContext(NonCancellable) { withTimeout(5_000) { closeImportRelease.await() } }
                        progress(DesktopImportProgress(id, 3, 1, imported = 1, failed = 1))
                        DesktopImportResult(1, 0, 1, cancelled = true)
                    }
                    assertTrue(beginImport.invoke(closingRuntime, null, null, null, explicitSelection) as Boolean)
                    val closingPreview = awaitReview(closingRuntime)
                    assertTrue(closingRuntime.confirmImport(closingPreview.id))
                    withTimeout(5_000) { closeImportEntered.await() }
                    val closeCleanup = MetadataReadGate().also { copyGates.add(it); discardGate.set(it) }
                    val hostRead = MetadataReadGate().also { copyGates.add(it); runtimeReadGate.set(it) }
                    closingRuntime.copyInspection.inspect(listOf(trackId))
                    withTimeout(5_000) { hostRead.entered.await() }
                    val closingHost = metadataScope.async(start = CoroutineStart.UNDISPATCHED) { closingRuntime.close() }
                    assertFalse(closingHost.isCompleted)
                    assertFalse(runtimeClosed.get())
                    assertFalse(closingRuntime.confirmImport(closingPreview.id))
                    assertFalse(closingRuntime.rejectOversizedImport())
                    assertEquals(DesktopImportPhase.CANCELLING, closingRuntime.importReview.value.phase)
                    hostRead.release.countDown()
                    closeImportRelease.complete(Unit)
                    withTimeout(5_000) { closeCleanup.entered.await() }
                    assertFalse(runtimeClosed.get())
                    assertFalse(closingHost.isCompleted)
                    closeCleanup.release.countDown()
                    withTimeout(5_000) { closingHost.await() }
                    assertTrue(runtimeClosed.get())
                    assertEquals(DesktopImportResult(1, 0, 1, cancelled = true), closingRuntime.importResult.value)
                    assertEquals(DesktopImportReviewState(), closingRuntime.importReview.value)
                    progressCallbacks.last()(DesktopImportProgress(closingPreview.id, 3, 3, 3, failed = 1))
                    assertEquals(DesktopImportResult(1, 0, 1, cancelled = true), closingRuntime.importResult.value)
                    assertFalse(closingRuntime.confirmImport(closingPreview.id))
                    assertSame(copyBaseline, realStore.state.value)

                }
                assertImportReviewLifecycle()

                val admissionRuntime = newRuntime()
                var admitted = 0
                admissionRuntime.admit { admitted += 1 }
                assertEquals(1, admitted)
                admissionRuntime.beginClose()
                admissionRuntime.admit { admitted += 1 }
                assertEquals(1, admitted)
                assertFalse(admissionRuntime.rejectOversizedImport())
                // No suspendable close has started yet: the synchronous barrier itself refused these.
                admissionRuntime.close()

            }
            assertRuntimeImportLifecycle()

            // This observer runs on the publishing read's stack, before its actor can join the
            // read and dequeue Apply. It makes the admitted-but-not-started refusal deterministic.
            val queuedApply = owner()
            probe.result.set(probe.available.copy(title = "Queued embedded title"))
            val applyCallsBeforeRefusal = applies.get()
            val refuseBeforeDequeue = metadataScope.async(Dispatchers.Unconfined, start = CoroutineStart.UNDISPATCHED) {
                val preview = checkNotNull(queuedApply.state.first { it.preview != null }.preview)
                queuedApply.apply(preview.id)
                assertTrue(queuedApply.state.value.applying)
                queuedApply.refuse()
            }
            queuedApply.inspect(trackId)
            refuseBeforeDequeue.await()
            queuedApply.close()
            assertEquals(applyCallsBeforeRefusal, applies.get())
            assertFalse(queuedApply.state.value.applying)
            assertNotNull(queuedApply.state.value.preview)
            assertEquals("desktop_snapshot_unavailable", queuedApply.state.value.errorCode)
            probe.result.set(probe.available)

            val markers = listOf(
                DesktopStorageException("desktop_snapshot_unavailable"),
                IllegalStateException("private_cause", DesktopStorageException("desktop_snapshot_unavailable")),
                IllegalStateException("private_suppressed").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
                CancellationException("private_cancel").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
            )
            for ((index, marker) in markers.withIndex()) {
                refused.set(false)
                val failed = owner()
                val late = probe.gate(marker)
                failed.inspect(trackId)
                late.entered.await()
                val before = probe.calls.get()
                failed.inspect(trackId)
                late.release.countDown()
                eventually("retired metadata marker $index reaches global refusal") {
                    reports.get() == index + 1 && failed.state.value.errorCode == "desktop_snapshot_unavailable"
                }
                failed.close()
                assertEquals(before, probe.calls.get())
            }
            refused.set(false)
            val refusedPreview = owner()
            probe.result.set(probe.available.copy(title = "Another embedded title"))
            refusedPreview.inspect(trackId)
            eventually("read-only preview exists before refusal") { refusedPreview.state.value.preview != null }
            val retained = checkNotNull(refusedPreview.state.value.preview)
            refusedPreview.refuse()
            val beforeRefusalCalls = resolves.get()
            val beforeRefusalApplies = applies.get()
            refusedPreview.inspect(trackId)
            refusedPreview.apply(retained.id)
            refusedPreview.dismiss(retained.id)
            assertNull(refusedPreview.state.value.preview)
            assertEquals("desktop_snapshot_unavailable", refusedPreview.state.value.errorCode)
            assertEquals(beforeRefusalCalls, resolves.get())
            assertEquals(beforeRefusalApplies, applies.get())
            refused.set(false)
            val failedApply = owner()
            failedApply.inspect(trackId)
            eventually("write marker scene has a frozen preview") { failedApply.state.value.preview?.canApply == true }
            val markerPreview = checkNotNull(failedApply.state.value.preview)
            val markerGate = gatedApply(markers.last())
            failedApply.apply(markerPreview.id)
            markerGate.entered.await()
            markerGate.release.complete(Unit)
            eventually("apply cancellation with marker has restart priority") {
                !failedApply.state.value.applying && failedApply.state.value.errorCode == "desktop_snapshot_unavailable"
            }
            assertSame(markerPreview, failedApply.state.value.preview)
            refused.set(false)
            val failedResolve = owner()
            resolveFailure.set(markers[1])
            failedResolve.inspect(trackId)
            eventually("read-only resolver marker also reaches global refusal") {
                failedResolve.state.value.errorCode == "desktop_snapshot_unavailable"
            }
            refused.set(false)
            val failedProvenance = owner()
            val resolvesBeforeProvenanceMarker = resolves.get()
            provenanceFailure.set(markers[1])
            failedProvenance.inspect(trackId)
            eventually("provenance lookup markers retain global restart refusal") {
                failedProvenance.state.value.errorCode == "desktop_snapshot_unavailable"
            }
            assertNull(failedProvenance.state.value.preview)
            assertEquals(resolvesBeforeProvenanceMarker, resolves.get())
        } finally {
            probe.gates.forEach { it.release.countDown() }
            writeGates.forEach { it.release.complete(Unit) }
            copyGates.forEach { it.release.countDown() }
            importGates.forEach { it.complete(Unit) }
            withContext(NonCancellable) {
                runtimeOwners.forEach { it.close() }
                copyOwners.forEach { it.close() }
                owners.forEach { it.close() }
            }
            metadataScope.cancel()
            fixture.stopController()
        }
    }

    /** Source-only / UNVERIFIED: restart scenes extend the same metadata composite, without a new test. */
    private suspend fun assertMetadataProvenanceRestart(): Unit {
        DesktopAcceptanceFixture().use { fixture ->
            fixture.openStore()
            val trackId = fixture.importWav("Provenance restart.wav", durationMs = 5)
            val imported = checkNotNull(fixture.store.loadTrackTextProvenance(trackId))
            assertEquals(DesktopTrackFieldProvenance(), imported.title)
            assertEquals(DesktopTrackFieldProvenance(), imported.artist)
            fixture.reopenStore()
            assertEquals(imported, fixture.store.loadTrackTextProvenance(trackId))
            fixture.store.updateTrackTextMetadata(trackId, "Manual restart title", "Preserved user artist")
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            val owners = mutableListOf<DesktopMetadataController>()
            val reads = AtomicInteger()
            val mediaReads = AtomicInteger()
            val applies = AtomicInteger()
            val source = AtomicReference(DesktopEmbeddedMetadata("AVAILABLE"))
            fun owner(): DesktopMetadataController {
                val realStore = fixture.store
                val observed = object : DesktopLibraryStore by realStore {
                    override suspend fun loadTrackTextProvenance(trackId: String): DesktopTrackTextProvenance? {
                        reads.incrementAndGet()
                        return realStore.loadTrackTextProvenance(trackId)
                    }
                    override suspend fun applyEmbeddedTrackText(baseline: DesktopTrackTextBaseline,
                        title: String, artist: String): Unit = error("unexpected_legacy_metadata_apply")
                    override suspend fun applyEmbeddedTrackText(application: DesktopEmbeddedTrackTextApplication): Unit {
                        applies.incrementAndGet()
                        realStore.applyEmbeddedTrackText(application)
                    }
                }
                return DesktopMetadataController(observed, scope,
                    onSnapshotUnavailable = { error("unexpected_restart_refusal") },
                    reader = { _, _ -> mediaReads.incrementAndGet(); source.get() },
                ).also { owners.add(it) }
            }
            try {
                for (format in DesktopEmbeddedTextFormat.entries) {
                    val before = checkNotNull(fixture.store.loadTrackTextProvenance(trackId))
                    source.set(DesktopEmbeddedMetadata("AVAILABLE", format.name, "Restart title ${format.name}",
                        artist = if (format == DesktopEmbeddedTextFormat.ID3V24) "" else null))
                    val readsBeforeOwner = reads.get()
                    val mediaBeforeOwner = mediaReads.get()
                    val metadata = owner()
                    assertEquals(readsBeforeOwner, reads.get())
                    assertEquals(mediaBeforeOwner, mediaReads.get())
                    assertNull(metadata.state.value.preview)
                    metadata.inspect(trackId)
                    eventually("explicit $format preview loads persisted per-field evidence") { !metadata.state.value.busy }
                    val preview = checkNotNull(metadata.state.value.preview)
                    assertEquals(before, preview.provenance)
                    assertEquals(format, preview.application?.format)
                    assertEquals(source.get().artist, checkNotNull(preview.application).artist)
                    assertTrue(preview.canApply)
                    metadata.apply(preview.id)
                    eventually("explicit $format application settles") { !metadata.state.value.applying }
                    assertNull(metadata.state.value.preview)
                    assertNull(metadata.state.value.errorCode)
                    val applied = checkNotNull(fixture.store.loadTrackTextProvenance(trackId))
                    assertEquals(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, format), applied.title)
                    assertEquals(preview.proposedArtistProvenance, applied.artist)
                    assertEquals(source.get().artist ?: before.baseline.artist, applied.baseline.artist)
                    if (source.get().artist == null) assertEquals(before.artist, applied.artist)
                    else assertEquals("", applied.baseline.artist)
                    metadata.close()
                    fixture.reopenStore()
                    val restarted = owner()
                    assertTrue(restarted.state.value.artworks.isEmpty())
                    assertNull(restarted.state.value.preview)
                    restarted.inspect(trackId)
                    eventually("restart $format preview recovers the exact persisted origins") { !restarted.state.value.busy }
                    val retained = checkNotNull(restarted.state.value.preview)
                    assertEquals(applied, retained.provenance)
                    assertEquals(applied.title, retained.proposedTitleProvenance)
                    assertEquals(applied.artist, retained.proposedArtistProvenance)
                    assertFalse(retained.canApply)
                    val callsBeforeNoop = applies.get()
                    restarted.apply(retained.id)
                    assertEquals(callsBeforeNoop, applies.get())
                    assertEquals(applied, fixture.store.loadTrackTextProvenance(trackId))
                    restarted.close()
                }
            } finally {
                withContext(NonCancellable) { owners.forEach { it.close() } }
                scope.cancel()
            }
        }
    }

    private class MetadataApplyGate(val failure: Exception? = null) {
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
    }

    private class MetadataReadGate(val failure: Exception? = null) {
        val entered = CompletableDeferred<Unit>()
        val release = CountDownLatch(1)
        fun hold(): Unit {
            entered.complete(Unit)
            var released = false
            while (!released) {
                try { release.await(); released = true }
                catch (_: InterruptedException) { /* Deliberately exercise non-cooperative source completion. */ }
            }
            failure?.let { throw it }
        }
    }

    private class MetadataReadProbe {
        val png: ByteArray = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3e0AAAAASUVORK5CYII=")
        private val artwork: DesktopArtwork = run {
            val input = png.copyOf()
            val hash = MessageDigest.getInstance("SHA-256").digest(input).joinToString("") { "%02x".format(it) }
            DesktopArtwork(hash, input, 1, 1).also {
                input.fill(0)
                assertArrayEquals(png, it.copyPngBytes())
            }
        }
        val available = DesktopEmbeddedMetadata("AVAILABLE", "ID3V24", "Embedded title", "Embedded artist", "Album evidence", artwork)
        val result = AtomicReference(available)
        val calls = AtomicInteger()
        val active = AtomicInteger()
        val maximumActive = AtomicInteger()
        val paths: MutableList<Path> = CopyOnWriteArrayList()
        val sizes: MutableList<Long> = CopyOnWriteArrayList()
        val gates: MutableList<MetadataReadGate> = CopyOnWriteArrayList()
        val failure = AtomicReference<Exception?>(null)
        private val nextGate = AtomicReference<MetadataReadGate?>(null)
        fun gate(failure: Exception? = null): MetadataReadGate = MetadataReadGate(failure).also {
            gates.add(it)
            check(nextGate.compareAndSet(null, it))
        }
        fun read(path: Path, size: Long): DesktopEmbeddedMetadata {
            calls.incrementAndGet()
            paths.add(path)
            sizes.add(size)
            val inFlight = active.incrementAndGet()
            maximumActive.updateAndGet { maxOf(it, inFlight) }
            try {
                val frozen = result.get()
                nextGate.getAndSet(null)?.hold()
                failure.getAndSet(null)?.let { throw it }
                return frozen
            } finally { active.decrementAndGet() }
        }
    }

    /** Source-only / UNVERIFIED: captured Home and transport targets cannot execute a later owner. */
    private suspend fun assertHomeContinuationFence(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        fixture.store.restoreTrackToCollection(trackId)
        val entries = listOf(DesktopQueueEntry("home-entry-a", trackId), DesktopQueueEntry("home-entry-b", trackId))
        val initial = DesktopQueue("home-owner-queue", entries, entries.first().id,
            sessionId = "home-owner-listen", sessionStartedAtMs = 1)
        fixture.store.saveQueue(initial)
        val engine = PolicyEngine()
        val plays = AtomicInteger()
        val pauses = AtomicInteger()
        val writes = AtomicInteger()
        val resolutions = AtomicInteger()
        val failPause = AtomicBoolean(false)
        val saveGate = AtomicReference<ReplacementSaveGate?>(null)
        val timerNow = AtomicLong(0)
        val observedEngine = object : DesktopAudioEngine by engine {
            override fun play(generation: Long): Unit { plays.incrementAndGet(); engine.play(generation) }
            override fun pause(generation: Long): Unit {
                pauses.incrementAndGet()
                if (failPause.getAndSet(false)) error("transport_pause_refusal")
                engine.pause(generation)
            }
        }
        val realStore = fixture.store
        val observedStore = object : DesktopLibraryStore by realStore {
            override suspend fun resolveManagedFile(trackId: String): String? {
                resolutions.incrementAndGet()
                return realStore.resolveManagedFile(trackId)
            }
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                writes.incrementAndGet()
                val gate = saveGate.get()?.takeIf { queue.id != it.oldQueueId }
                if (gate != null && !gate.entered.isCompleted) {
                    gate.entered.complete(QueueSaveObservation(queue, event, realStore.state.value,
                        engine.state.value, engine.opens.size, engine.stops.size))
                    gate.release.await()
                }
                realStore.saveQueue(queue, event)
            }
        }
        val controller = fixture.start(observedEngine, observedStore, timerNowNanos = timerNow::get, periodicObservations = emptyFlow())
        try {
            eventually("Home scene restores its paused target") { controller.state.value.playbackStatus == PlaybackStatus.Ready }
            // Model navigation completing after the UI captured its target and before Continue executes.
            controller.next()
            eventually("queued navigation installs another paused entry") {
                controller.state.value.playbackStatus == PlaybackStatus.Ready && controller.state.value.mediaId == entries.last().id
            }
            val current = checkNotNull(fixture.store.state.value.queue)
            val native = engine.state.value
            val opens = engine.opens.toList()
            controller.resumeCurrentQueue(initial.id, entries.first().id, initial.sessionId)
            controller.resumeCurrentQueue("retired-home-queue", entries.last().id, current.sessionId)
            controller.resumeCurrentQueue(current.id, entries.last().id, initial.sessionId)
            controller.setRepeat("ONE")
            eventually("mode checkpoint fences rejected Home targets") { fixture.store.state.value.queue?.repeatMode == "ONE" }
            assertEquals(0, plays.get())
            assertEquals(current.copy(repeatMode = "ONE"), fixture.store.state.value.queue)
            assertEquals(native, engine.state.value)
            assertEquals(opens, engine.opens.toList())
            controller.scheduleSleepTimer(current.id, 60_000)
            eventually("Home scene owns a paused duration plan") { controller.sleepTimer.value.planId != null }
            timerNow.addAndGet(60_000_000_000L)
            controller.resumeCurrentQueue(current.id, entries.last().id, current.sessionId)
            eventually("matching Home target consumes an expired plan before continuing") {
                controller.state.value.isPlaying && engine.state.value.isPlaying && controller.sleepTimer.value.planId == null
            }
            assertEquals(1, plays.get())
            assertEquals(current.id, fixture.store.state.value.queue?.id)
            assertEquals(current.sessionId, fixture.store.state.value.queue?.sessionId)
            assertEquals(opens, engine.opens.toList())
            controller.resumeCurrentQueue(current.id, entries.last().id, current.sessionId)
            controller.setRepeat("OFF")
            eventually("already playing Home request is fenced") { fixture.store.state.value.queue?.repeatMode == "OFF" }
            assertEquals(1, plays.get())
            controller.pause()
            eventually("Home scene pauses before retained membership edit") { !controller.state.value.isPlaying }
            fixture.store.removeTrackFromCollection(trackId)
            controller.resumeCurrentQueue(current.id, entries.last().id, current.sessionId)
            eventually("retained removed queue entry can continue") { controller.state.value.isPlaying }
            assertEquals(2, plays.get())
            controller.pause()
            eventually("Home scene pauses before terminal observation") { !controller.state.value.isPlaying }
            val endedQueue = checkNotNull(fixture.store.state.value.queue)
            engine.emit(DesktopAudioEvent(engine.state.value.generation, "ENDED", 8_000, 8_000))
            eventually("Home scene reaches terminal paused entry") { controller.state.value.playbackStatus == PlaybackStatus.Ended }
            controller.resumeCurrentQueue(endedQueue.id, checkNotNull(endedQueue.currentEntryId), endedQueue.sessionId)
            controller.setRepeat("ONE")
            eventually("terminal Home target is fenced before replay") { fixture.store.state.value.queue?.repeatMode == "ONE" }
            assertEquals(2, plays.get())
            assertEquals(opens, engine.opens.toList())
            assertEquals(endedQueue.sessionId, fixture.store.state.value.queue?.sessionId)
            assertEquals(PlaybackStatus.Ended, controller.state.value.playbackStatus)

            // UNVERIFIED: typed commands keep the same native/session/EOF policy as the legacy port.
            val endedTarget = checkNotNull(controller.playerState.value.target)
            controller.transport(endedTarget, DesktopTransportAction.PLAY)
            eventually("matching typed Play replays the ended listen with a new target") {
                controller.playerState.value.let { it.playback.isPlaying && it.target?.sessionId != endedTarget.sessionId &&
                    it.target?.generation != endedTarget.generation }
            }
            val replayTarget = checkNotNull(controller.playerState.value.target)
            controller.transport(replayTarget, DesktopTransportAction.PAUSE)
            eventually("matching typed Pause preserves the replay target") { !controller.playerState.value.playback.isPlaying }
            assertEquals(replayTarget, controller.playerState.value.target)
            controller.transport(replayTarget, DesktopTransportAction.SEEK, Long.MAX_VALUE)
            eventually("typed seek clamps to current native duration") {
                controller.playerState.value.playback.let { it.positionMs == 8_000L && it.seekEnabled }
            }
            assertEquals(PolicyEngine.Seek(replayTarget.generation, 8_000), engine.seeks.last())
            controller.transport(replayTarget, DesktopTransportAction.SEEK, -1)
            eventually("typed seek clamps negative position to zero") {
                controller.playerState.value.playback.let { it.positionMs == 0L && it.seekEnabled }
            }
            engine.deferSeek = true
            controller.transport(replayTarget, DesktopTransportAction.SEEK, 4_000)
            eventually("typed seek owns the existing pending barrier") { !controller.playerState.value.playback.seekEnabled }
            val pendingSeeks = engine.seeks.size
            controller.transport(replayTarget, DesktopTransportAction.SEEK, 6_000)
            controller.setCurrentListenTasteExcluded("transport-fence", "missing", "missing", false)
            eventually("typed second-seek refusal precedes a timer-free actor fence") { controller.taste.value.errorCode == "TASTE_LISTEN_STALE" }
            assertEquals(pendingSeeks, engine.seeks.size)
            assertEquals(0L, fixture.store.state.value.queue?.positionMs)
            engine.emit(DesktopAudioEvent(replayTarget.generation, "READY", 4_000, 8_000))
            eventually("typed pending seek still needs native confirmation") { controller.playerState.value.playback.seekEnabled }
            engine.deferSeek = false
            controller.transport(replayTarget, DesktopTransportAction.PREVIOUS)
            eventually("typed Previous resets a position beyond three seconds") { controller.playerState.value.playback.positionMs == 0L }
            assertEquals(replayTarget, controller.playerState.value.target)
            val navigationWrites = writes.get()
            val navigationPauses = pauses.get()
            val navigationSeeks = engine.seeks.size
            controller.transport(replayTarget, DesktopTransportAction.PREVIOUS)
            DesktopTransportAction.entries.forEach { controller.transport(replayTarget, it, 7_000) }
            controller.setQueueSessionTasteExcluded("transport-fence", false)
            eventually("queued old duplicate-entry commands retire after typed Previous") {
                controller.playerState.value.let { it.target?.entryId == entries.first().id && it.playback.playbackStatus == PlaybackStatus.Ready } &&
                    controller.taste.value.errorCode == "TASTE_SESSION_STALE"
            }
            assertEquals(navigationWrites + 3, writes.get()) // select + checkpoint + paused READY only
            assertEquals(navigationPauses, pauses.get())
            assertEquals(navigationSeeks, engine.seeks.size)
            assertFalse(controller.playerState.value.playback.isPlaying)
            val duplicateTarget = checkNotNull(controller.playerState.value.target)
            controller.transport(duplicateTarget, DesktopTransportAction.NEXT)
            eventually("matching typed Next selects the other occurrence of the same track") {
                controller.playerState.value.let { it.target?.entryId == entries.last().id && it.playback.playbackStatus == PlaybackStatus.Ready }
            }
            val queuedTarget = checkNotNull(controller.playerState.value.target)
            val retainedGate = ReplacementSaveGate("never-current-queue")
            saveGate.set(retainedGate)
            try {
                controller.setRepeat("OFF")
                withTimeout(5_000) { retainedGate.entered.await() }
                controller.transport(queuedTarget, DesktopTransportAction.SEEK, 1_000)
            } finally {
                retainedGate.release.complete(Unit)
            }
            eventually("typed command queued behind a nonretiring checkpoint remains valid") {
                controller.playerState.value.let { it.target == queuedTarget && it.playback.positionMs == 1_000L && it.playback.seekEnabled }
            }
            saveGate.set(null)
            fixture.store.restoreTrackToCollection(trackId)
            val replacementGate = ReplacementSaveGate(queuedTarget.queueId)
            val replacementWrites = writes.get()
            val replacementCalls = listOf(plays.get(), pauses.get(), resolutions.get(), engine.opens.size, engine.seeks.size, engine.stops.size)
            saveGate.set(replacementGate)
            try {
                controller.playTracks(listOf(trackId, trackId))
                withTimeout(5_000) { replacementGate.entered.await() }
                DesktopTransportAction.entries.forEach { controller.transport(queuedTarget, it, 7_000) }
                controller.setCurrentListenTasteExcluded("transport-fence", "missing", "missing", false)
            } finally {
                replacementGate.release.complete(Unit)
            }
            eventually("queued typed callbacks cannot affect an admitted replacement queue") {
                controller.playerState.value.let { it.target?.queueId != queuedTarget.queueId && it.playback.isPlaying } &&
                    controller.taste.value.errorCode == "TASTE_LISTEN_STALE"
            }
            saveGate.set(null)
            assertEquals(replacementWrites + 3, writes.get())
            assertEquals(replacementCalls.mapIndexed { index, count -> if (index in listOf(2, 3, 5)) count + 1 else count },
                listOf(plays.get(), pauses.get(), resolutions.get(), engine.opens.size, engine.seeks.size, engine.stops.size))
            val beforeReopen = checkNotNull(controller.playerState.value.target)
            engine.emit(DesktopAudioEvent(beforeReopen.generation, "ERROR", errorCode = "NATIVE_OPEN_FAILED"))
            eventually("native failure retires only the current opening") { controller.error.value == "AUDIO_ERROR" }
            controller.selectEntry(beforeReopen.entryId)
            eventually("explicit same-entry reopen retains its logical listen with a new generation") {
                controller.playerState.value.let { it.playback.isPlaying && it.target?.generation != beforeReopen.generation }
            }
            val reopenedTarget = checkNotNull(controller.playerState.value.target)
            assertEquals(beforeReopen.copy(generation = reopenedTarget.generation), reopenedTarget)
            controller.transport(reopenedTarget, DesktopTransportAction.PAUSE)
            eventually("same-entry reopen is paused before the due-timer stale-target scene") { !controller.playerState.value.playback.isPlaying }
            controller.scheduleSleepTimer(reopenedTarget.queueId, 60_000)
            eventually("typed stale-target scene arms a duration") { controller.sleepTimer.value.planId != null }
            val duePlan = controller.sleepTimer.value.planId
            val coherent = controller.playerState.value
            val targetQueue = checkNotNull(fixture.store.state.value.queue)
            assertEquals(DesktopPlaybackTarget(targetQueue.id, checkNotNull(targetQueue.currentEntryId), targetQueue.sessionId,
                engine.state.value.generation), coherent.target)
            assertEquals(coherent.target?.entryId, coherent.playback.mediaId)
            assertEquals(controller.state.value, coherent.playback)
            val beforeStale = fixture.store.state.value
            val staleWrites = writes.get()
            val staleCalls = listOf(plays.get(), pauses.get(), resolutions.get(), engine.opens.size, engine.seeks.size, engine.stops.size)
            timerNow.addAndGet(60_000_000_000L)
            val retiredTargets = listOf(endedTarget, replayTarget, duplicateTarget, queuedTarget, beforeReopen,
                reopenedTarget.copy(queueId = "retired-queue"), reopenedTarget.copy(entryId = "retired-entry"),
                reopenedTarget.copy(sessionId = "retired-listen"), reopenedTarget.copy(generation = reopenedTarget.generation - 1))
            for (target in retiredTargets) DesktopTransportAction.entries.forEach { controller.transport(target, it, 7_000) }
            controller.setQueueSessionTasteExcluded("transport-fence", false)
            eventually("all four target fields are checked before an expired plan can be observed") { controller.taste.value.errorCode == "TASTE_SESSION_STALE" }
            assertEquals(duePlan, controller.sleepTimer.value.planId)
            assertEquals(beforeStale, fixture.store.state.value)
            assertEquals(coherent, controller.playerState.value)
            assertEquals(staleWrites, writes.get())
            assertEquals(staleCalls, listOf(plays.get(), pauses.get(), resolutions.get(), engine.opens.size, engine.seeks.size, engine.stops.size))
            controller.transport(reopenedTarget, DesktopTransportAction.NEXT)
            eventually("matching typed navigation observes expiry and opens the next duplicate paused") {
                controller.playerState.value.let { it.target?.entryId != reopenedTarget.entryId &&
                    it.playback.playbackStatus == PlaybackStatus.Ready && !it.playback.isPlaying } && controller.sleepTimer.value.planId == null
            }
            assertEquals(staleCalls[1] + 1, pauses.get())
            assertFalse(engine.opens.last().autoplay)
            val finalTarget = checkNotNull(controller.playerState.value.target)
            engine.emit(DesktopAudioEvent(finalTarget.generation, "READY", 0, 0))
            eventually("zero native duration still disables typed seek") { controller.playerState.value.playback.durationMs == 0L }
            val zeroSeeks = engine.seeks.size
            val zeroWrites = writes.get()
            controller.transport(finalTarget, DesktopTransportAction.SEEK, 9_000)
            controller.setCurrentListenTasteExcluded("transport-fence", "missing", "missing", false)
            eventually("zero-duration refusal is processed") { controller.taste.value.errorCode == "TASTE_LISTEN_STALE" }
            assertEquals(zeroSeeks, engine.seeks.size)
            assertEquals(zeroWrites, writes.get())
            engine.emit(DesktopAudioEvent(finalTarget.generation, "READY", 0, 8_000))
            eventually("native duration restores the existing seek gate") { controller.playerState.value.playback.seekEnabled }
            controller.scheduleSleepTimer(finalTarget.queueId, 60_000)
            eventually("expiry-failure scene arms once") { controller.sleepTimer.value.planId != null }
            val expiryPlays = plays.get()
            val expiryOpens = engine.opens.size
            val expiryResolutions = resolutions.get()
            failPause.set(true)
            timerNow.addAndGet(60_000_000_000L)
            controller.transport(finalTarget, DesktopTransportAction.PLAY)
            eventually("expiry failure retires the target before its requested action") {
                controller.error.value == "AUDIO_ERROR" && controller.sleepTimer.value.planId == null &&
                    controller.playerState.value.target?.generation != finalTarget.generation
            }
            assertEquals(finalTarget.entryId, controller.playerState.value.target?.entryId)
            assertEquals(finalTarget.sessionId, controller.playerState.value.target?.sessionId)
            assertEquals(expiryPlays, plays.get())
            assertEquals(expiryOpens, engine.opens.size)
            assertEquals(expiryResolutions, resolutions.get())
        } finally {
            saveGate.get()?.release?.complete(Unit)
            fixture.stopController()
            fixture.store.restoreTrackToCollection(trackId)
        }
    }

    /** Source-only / UNVERIFIED: paging scenes remain within the existing queue composite. */
    private suspend fun assertHistoryReadPolicy(fixture: DesktopAcceptanceFixture, trackId: String): Unit {
        val realStore = fixture.store
        realStore.restoreTrackToCollection(trackId)
        val entry = DesktopQueueEntry("history-owner-entry", trackId)
        val queue = DesktopQueue("history-owner-queue", listOf(entry), entry.id,
            sessionId = "history-live-listen", sessionStartedAtMs = 1_000)
        val seedCount = DesktopHistoryLimits.MAX_VISIBLE_EVENTS + DesktopHistoryLimits.PAGE_SIZE + 3
        val seeded = (0 until seedCount).map { index ->
            DesktopListeningEvent("history-seed-${index.toString().padStart(4, '0')}", entry.id, trackId,
                9_000_000_000_000_000L + index / 3, index.toLong(), index.toLong(), finalized = index != seedCount - 1)
        }
        // Real store writes establish ties and enough persisted rows to advance the sliding window.
        for (event in seeded) realStore.saveQueue(queue, event)
        val expected = seeded.sortedWith(compareByDescending<DesktopListeningEvent> { it.startedAtMs }.thenBy { it.id })
        val engine = PolicyEngine()
        val writes = AtomicInteger()
        val probe = HistoryReadProbe()
        val observedStore = object : DesktopLibraryStore by realStore {
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                writes.incrementAndGet()
                realStore.saveQueue(queue, event)
            }
            override suspend fun loadHistoryPage(before: DesktopHistoryCursor?, limit: Int): DesktopHistoryPage =
                probe.read(realStore, before, limit)
        }
        val playback = fixture.start(engine, observedStore)
        val historyScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val owners = mutableListOf<DesktopHistoryController>()
        val refusal = AtomicBoolean(false)
        val reports = AtomicInteger()
        fun owner(): DesktopHistoryController = DesktopHistoryController(
            observedStore, historyScope,
            onSnapshotUnavailable = {
                reports.incrementAndGet()
                refusal.set(true)
                playback.reportSnapshotUnavailable()
            },
            canRead = { !refusal.get() },
        ).also { owners.add(it) }
        val history = owner()
        try {
            eventually("history scenes restore their paused sole playback owner") { playback.state.value.playbackStatus == PlaybackStatus.Ready }
            playback.stopAfterCurrent(queue.id, entry.id, checkNotNull(queue.sessionId))
            eventually("history reads begin with an armed stop-after-current plan") { playback.sleepTimer.value.planId != null }
            val timer = playback.sleepTimer.value
            val native = engine.state.value
            val opens = engine.opens.toList()
            val stops = engine.stops.toList()
            val seeks = engine.seeks.toList()
            val queueWrites = writes.get()
            assertTrue(probe.calls.isEmpty())
            assertFalse(history.state.value.initialized)

            history.refresh()
            eventually("history initializes from a bounded real-store page") { history.state.value.initialized && !history.state.value.loading }
            assertEquals(expected.take(100), history.state.value.events)
            assertEquals(100, realStore.state.value.history.size)
            val initial = history.state.value
            val failedCursor = checkNotNull(initial.nextCursor)
            probe.failure.set(IllegalStateException("private_fixture_detail"))
            history.loadMore(initial.revision)
            history.loadMore(initial.revision)
            eventually("append failure retains its exact read window") { history.state.value.errorCode == "desktop_history_load_failed" }
            assertEquals(initial.events, history.state.value.events)
            assertEquals(initial.nextCursor, history.state.value.nextCursor)
            assertEquals(initial.revision + 1, history.state.value.revision)
            assertEquals(2, probe.calls.size)
            history.retry(initial.revision)
            assertEquals(2, probe.calls.size)
            val failedRevision = history.state.value.revision
            probe.duplicateResult.set(true)
            history.retry(failedRevision)
            history.retry(failedRevision)
            eventually("retry appends the failed exact cursor once and deduplicates stable event IDs") {
                !history.state.value.loading && history.state.value.events.size == 200
            }
            assertEquals(listOf(failedCursor, failedCursor), probe.calls.takeLast(2).map { it.before })
            assertEquals(expected.take(200), history.state.value.events)
            assertEquals(failedRevision + 1, history.state.value.revision)
            assertEquals(3, probe.calls.size)

            val beforeFailedRefresh = history.state.value
            probe.failure.set(IllegalStateException("fixture_refresh_private_detail"))
            history.refresh()
            eventually("failed refresh retains loaded rows and its prior cursor") { history.state.value.errorCode == "desktop_history_load_failed" }
            assertEquals(beforeFailedRefresh.events, history.state.value.events)
            assertEquals(beforeFailedRefresh.nextCursor, history.state.value.nextCursor)
            history.retry(history.state.value.revision)
            eventually("refresh retry replaces with the newest page rather than appending") {
                !history.state.value.loading && history.state.value.events == expected.take(100)
            }
            assertEquals(listOf(null, null), probe.calls.takeLast(2).map { it.before })

            val stalePage = HistoryReadGate()
            probe.nextGate.set(stalePage)
            history.loadMore(history.state.value.revision)
            stalePage.entered.await()
            val retiredRevision = history.state.value.revision
            val callsBeforeRefresh = probe.calls.size
            repeat(24) { history.refresh() }
            assertEquals(retiredRevision + 24, history.state.value.revision)
            assertEquals(callsBeforeRefresh, probe.calls.size)
            stalePage.release.complete(Unit)
            eventually("repeated refresh coalesces after the retired non-cooperative read") {
                !history.state.value.loading && history.state.value.events == expected.take(100)
            }
            assertEquals(callsBeforeRefresh + 1, probe.calls.size)
            assertNull(probe.calls.last().before)
            assertEquals(1, probe.maximumActive.get())
            history.loadMore(retiredRevision)
            assertEquals(callsBeforeRefresh + 1, probe.calls.size)

            val staleFailure = HistoryReadGate(IllegalStateException("retired_private_detail"))
            probe.nextGate.set(staleFailure)
            history.loadMore(history.state.value.revision)
            staleFailure.entered.await()
            history.refresh()
            staleFailure.release.complete(Unit)
            eventually("retired ordinary error cannot replace the refreshed state") {
                !history.state.value.loading && history.state.value.events == expected.take(100)
            }
            assertNull(history.state.value.errorCode)

            val pageBeforeCheckpoint = history.state.value
            val callsBeforeCheckpoint = probe.calls.size
            val changed = expected.first { !it.finalized }.copy(playedMs = 9_000, positionMs = 9_000, finalized = true)
            realStore.saveQueue(queue, changed)
            assertEquals(pageBeforeCheckpoint, history.state.value)
            assertEquals(callsBeforeCheckpoint, probe.calls.size)
            history.refresh()
            eventually("current-row changes become visible only through an explicit history read") {
                !history.state.value.loading && history.state.value.events.any { it == changed }
            }
            val readSnapshot = realStore.state.value
            val expectedAfterCheckpoint = expected.map { if (it.id == changed.id) changed else it }
            // Ten explicit appends cross 1,000 visible rows without losing the older cursor.
            repeat(10) {
                val before = history.state.value
                history.loadMore(before.revision)
                eventually("history page $it finishes") { !history.state.value.loading && history.state.value.revision > before.revision }
            }
            assertEquals(1_000, history.state.value.events.size)
            assertTrue(history.state.value.newerItemsHidden)
            assertEquals(expectedAfterCheckpoint.take(1_100).takeLast(1_000), history.state.value.events)
            assertEquals(1_000, history.state.value.events.map { it.id }.distinct().size)
            history.refresh()
            eventually("refresh returns from the older sliding window to newest rows") {
                !history.state.value.loading && !history.state.value.newerItemsHidden
            }
            assertEquals(expectedAfterCheckpoint.take(100), history.state.value.events)

            // Cancellation is not a user-visible read failure and does not create a retry target.
            probe.failure.set(CancellationException("fixture_read_cancelled"))
            history.loadMore(history.state.value.revision)
            eventually("current read cancellation settles without an ordinary error") { !history.state.value.loading }
            assertNull(history.state.value.errorCode)
            assertEquals(expectedAfterCheckpoint.take(100), history.state.value.events)
            assertSame(readSnapshot, realStore.state.value)
            assertEquals(queue, realStore.state.value.queue)
            assertEquals(native, engine.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops, engine.stops.toList())
            assertEquals(seeks, engine.seeks.toList())
            assertEquals(queueWrites, writes.get())
            assertEquals(timer, playback.sleepTimer.value)
            assertTrue(probe.calls.all { it.limit == DesktopHistoryLimits.PAGE_SIZE })

            val closingRead = HistoryReadGate()
            probe.nextGate.set(closingRead)
            history.refresh()
            closingRead.entered.await()
            history.beginClose()
            val closedState = history.state.value
            val closedCalls = probe.calls.size
            val closing = historyScope.async(start = CoroutineStart.UNDISPATCHED) { history.close() }
            history.refresh()
            history.loadMore(closedState.revision)
            history.retry(closedState.revision)
            assertFalse(closing.isCompleted)
            closingRead.release.complete(Unit)
            closing.await()
            assertEquals(0, probe.active.get())
            assertEquals(closedState, history.state.value)
            assertEquals(closedCalls, probe.calls.size)

            val markers = listOf(
                DesktopStorageException("desktop_snapshot_unavailable"),
                IllegalStateException("fixture_cause", DesktopStorageException("desktop_snapshot_unavailable")),
                IllegalStateException("fixture_suppressed").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
                CancellationException("fixture_cancelled").apply { addSuppressed(DesktopStorageException("desktop_snapshot_unavailable")) },
            )
            for ((index, marker) in markers.withIndex()) {
                refusal.set(false)
                val failedOwner = owner()
                val lateMarker = HistoryReadGate(marker)
                probe.nextGate.set(lateMarker)
                failedOwner.refresh()
                lateMarker.entered.await()
                val beforeFailureCalls = probe.calls.size
                // Retired markers still indicate unsafe storage; their pending refresh must never run.
                failedOwner.refresh()
                lateMarker.release.complete(Unit)
                eventually("retired snapshot marker $index reaches the sole sticky refusal") {
                    reports.get() == index + 1 && failedOwner.state.value.errorCode == "desktop_snapshot_unavailable"
                }
                val failedState = failedOwner.state.value
                failedOwner.refresh()
                failedOwner.loadMore(failedState.revision)
                failedOwner.retry(failedState.revision)
                failedOwner.close()
                assertEquals(beforeFailureCalls, probe.calls.size)
                assertEquals("desktop_snapshot_unavailable", failedOwner.state.value.errorCode)
            }
            eventually("history marker disables playback and its timer through the owner") {
                playback.error.value == "desktop_snapshot_unavailable" && playback.sleepTimer.value.planId == null &&
                    !playback.taste.value.listenCommandsEnabled
            }
            val refusalSnapshot = realStore.state.value
            playback.playTracks(listOf(trackId))
            playback.enqueue(trackId)
            playback.scheduleSleepTimer(queue.id, 60_000)
            playback.setQueueSessionTasteExcluded(queue.id, true)
            fixture.stopController()
            assertEquals(queueWrites, writes.get())
            assertSame(refusalSnapshot, realStore.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops.size + 1, engine.stops.size)
        } finally {
            probe.gates.forEach { it.release.complete(Unit) }
            withContext(NonCancellable) { owners.forEach { it.close() } }
            historyScope.cancel()
        }
    }

    private data class HistoryReadCall(val before: DesktopHistoryCursor?, val limit: Int)
    private class HistoryReadGate(val failure: Exception? = null) {
        val entered = CompletableDeferred<DesktopHistoryPage>()
        val release = CompletableDeferred<Unit>()
    }

    private class HistoryReadProbe {
        val calls: MutableList<HistoryReadCall> = CopyOnWriteArrayList()
        val gates: MutableList<HistoryReadGate> = CopyOnWriteArrayList()
        val active = AtomicInteger()
        val maximumActive = AtomicInteger()
        val nextGate = AtomicReference<HistoryReadGate?>(null)
        val failure = AtomicReference<Exception?>(null)
        val duplicateResult = AtomicBoolean(false)

        suspend fun read(store: DesktopLibraryStore, before: DesktopHistoryCursor?, limit: Int): DesktopHistoryPage {
            calls.add(HistoryReadCall(before, limit))
            val inFlight = active.incrementAndGet()
            maximumActive.updateAndGet { maxOf(it, inFlight) }
            try {
                val page = store.loadHistoryPage(before, limit)
                nextGate.getAndSet(null)?.let { gate ->
                    gates.add(gate)
                    withContext(NonCancellable) {
                        gate.entered.complete(page)
                        gate.release.await()
                        gate.failure?.let { throw it }
                    }
                }
                failure.getAndSet(null)?.let { throw it }
                return if (duplicateResult.getAndSet(false) && page.events.isNotEmpty())
                    page.copy(events = page.events + page.events.first()) else page
            } finally {
                active.decrementAndGet()
            }
        }
    }

    /** Real-store annotation scenes stay inside the single queue composite. */
    private suspend fun exerciseTastePolicy(
        fixture: DesktopAcceptanceFixture,
        controller: DesktopPlaybackController,
        engine: PolicyEngine,
        trackId: String,
        batchTrackId: String,
        queueWrites: AtomicInteger,
        pauseRequests: AtomicInteger,
        probe: TasteProbe,
        replacementSaveGate: AtomicReference<ReplacementSaveGate?>,
    ): Unit {
        controller.playTracks(listOf(trackId, trackId, batchTrackId))
        eventually("taste scene starts a fresh supported queue") {
            controller.state.value.isPlaying && fixture.store.state.value.queue?.entries?.size == 3 &&
                controller.taste.value.queueId == fixture.store.state.value.queue?.id &&
                controller.taste.value.sessionTasteState == "INCLUDED" && controller.taste.value.listenTasteState == "INCLUDED"
        }
        controller.pause()
        eventually("taste scene checkpoints paused") { !controller.state.value.isPlaying && !engine.state.value.isPlaying }
        controller.seekTo(1_200)
        eventually("taste scene has a confirmed paused position") { fixture.store.state.value.queue?.positionMs == 1_200L }
        val base = checkNotNull(fixture.store.state.value.queue)
        controller.scheduleSleepTimer(base.id, 60_000)
        eventually("taste scene arms an independent timer") { controller.sleepTimer.value.planId != null }
        val native = engine.state.value
        val opens = engine.opens.toList()
        val stops = engine.stops.toList()
        val seeks = engine.seeks.toList()
        val pauses = pauseRequests.get()
        val writes = queueWrites.get()
        val tracks = fixture.store.state.value.tracks
        val playlists = fixture.store.state.value.playlists
        val timer = controller.sleepTimer.value
        val history = fixture.store.state.value.history
        suspend fun chooseListen(excluded: Boolean): Unit {
            val target = controller.taste.value
            val results = probe.results.size
            controller.setCurrentListenTasteExcluded(checkNotNull(target.queueId), checkNotNull(target.currentEntryId),
                checkNotNull(target.listeningEventId), excluded)
            eventually("explicit listen choice is confirmed") {
                probe.results.size > results && controller.taste.value.listenTasteState == DesktopTastePolicy.choice(excluded) &&
                    controller.taste.value.errorCode == null
            }
        }
        suspend fun chooseSession(excluded: Boolean): Unit {
            val results = probe.results.size
            controller.setQueueSessionTasteExcluded(checkNotNull(controller.taste.value.queueId), excluded)
            eventually("explicit queue-session choice is confirmed") {
                probe.results.size > results && controller.taste.value.sessionTasteState == DesktopTastePolicy.choice(excluded) &&
                    controller.taste.value.errorCode == null
            }
        }
        fun assertPausedOwner(): Unit {
            val current = checkNotNull(fixture.store.state.value.queue)
            assertEquals(base.copy(sessionTasteState = current.sessionTasteState, listenTasteState = current.listenTasteState), current)
            assertEquals(native, engine.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops, engine.stops.toList())
            assertEquals(seeks, engine.seeks.toList())
            assertEquals(pauses, pauseRequests.get())
            assertEquals(writes, queueWrites.get())
            assertEquals(timer, controller.sleepTimer.value)
            assertEquals(tracks, fixture.store.state.value.tracks)
            assertEquals(playlists, fixture.store.state.value.playlists)
            assertEquals(base.sessionId, controller.taste.value.listeningEventId)
            assertEquals(1_200L, controller.state.value.positionMs)
            assertFalse(controller.state.value.isPlaying)
            assertEquals(history.map {
                if (it.id == base.sessionId) it.copy(sessionTasteState = current.sessionTasteState, listenTasteState = current.listenTasteState) else it
            }, fixture.store.state.value.history)
        }
        chooseListen(true)
        assertEquals("INCLUDED", controller.taste.value.sessionTasteState)
        assertEquals(true, DesktopTastePolicy.isExcluded(controller.taste.value.sessionTasteState, controller.taste.value.listenTasteState))
        assertPausedOwner()
        val noOpQueue = fixture.store.state.value.queue
        val noOpHistory = fixture.store.state.value.history
        chooseListen(true)
        chooseSession(false)
        assertEquals(noOpQueue, fixture.store.state.value.queue)
        assertEquals(noOpHistory, fixture.store.state.value.history)
        assertPausedOwner()
        chooseSession(true)
        chooseListen(false)
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        assertEquals(true, DesktopTastePolicy.isExcluded(controller.taste.value.sessionTasteState, controller.taste.value.listenTasteState))
        assertPausedOwner()
        chooseSession(false)
        assertEquals(false, DesktopTastePolicy.isExcluded(controller.taste.value.sessionTasteState, controller.taste.value.listenTasteState))
        assertPausedOwner()

        // A successful local annotation clears only its own error, preserving the global owner.
        controller.playTracks(List(1_001) { trackId })
        eventually("global queue refusal precedes a taste success") { controller.error.value == "QUEUE_LIMIT" }
        chooseListen(true)
        assertEquals("QUEUE_LIMIT", controller.error.value)
        assertPausedOwner()
        val refused = fixture.store.state.value
        probe.failure.set(DesktopStorageException("desktop_outbox_payload_too_large"))
        controller.setQueueSessionTasteExcluded(base.id, true)
        eventually("ordinary outbox refusal remains local to taste") { controller.taste.value.errorCode == "TASTE_EXCLUSION_UNAVAILABLE" }
        assertEquals(refused, fixture.store.state.value)
        assertEquals("QUEUE_LIMIT", controller.error.value)
        assertPausedOwner()
        probe.retireNext.set(true)
        controller.setCurrentListenTasteExcluded(base.id, checkNotNull(base.currentEntryId), checkNotNull(base.sessionId), false)
        eventually("store-retired listen is stale without changing the owner") { controller.taste.value.errorCode == "TASTE_LISTEN_STALE" }
        assertEquals(refused, fixture.store.state.value)
        assertPausedOwner()
        probe.retireNext.set(true)
        controller.setQueueSessionTasteExcluded(base.id, true)
        eventually("store-retired queue session remains unchanged") { controller.taste.value.errorCode == "TASTE_SESSION_STALE" }
        assertEquals(refused, fixture.store.state.value)
        chooseSession(true)
        assertEquals("QUEUE_LIMIT", controller.error.value)
        assertPausedOwner()

        // Unrelated native/metadata/preferences/membership publications cannot replay queue flags.
        fixture.store.setTrackPreference(trackId, "LIKED")
        fixture.store.updateTrackTextMetadata(trackId, "Taste scene title", "Taste scene artist")
        fixture.store.removeTrackFromCollection(trackId)
        chooseListen(false)
        chooseListen(true)
        assertEquals("REMOVED", fixture.store.state.value.tracks.single { it.id == trackId }.collectionState)
        assertEquals("LIKED", fixture.store.state.value.tracks.single { it.id == trackId }.preference)
        assertEquals(timer, controller.sleepTimer.value)
        assertEquals(native, engine.state.value)
        fixture.store.restoreTrackToCollection(trackId)
        fixture.store.setTrackPreference(trackId, "DISLIKED")
        engine.emit(native.copy(durationMs = 9_000))
        eventually("native duration metadata retains both taste flags") {
            fixture.store.state.value.tracks.single { it.id == trackId }.durationMs == 9_000L && controller.state.value.durationMs == 9_000L
        }
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("EXCLUDED", controller.taste.value.listenTasteState)
        controller.playNext(batchTrackId)
        eventually("future edit retains taste scope") { fixture.store.state.value.queue?.entries?.size == 4 }
        val added = checkNotNull(fixture.store.state.value.queue).entries.single { candidate -> base.entries.none { it.id == candidate.id } }
        controller.moveUpcoming(added.id, 1)
        eventually("future reorder checkpoints both taste annotations") { fixture.store.state.value.queue?.entries?.get(2)?.id == added.id }
        controller.removeEntry(added.id)
        eventually("future removal retains the current taste annotations") { fixture.store.state.value.queue?.entries?.size == 3 }
        controller.setShuffle(true)
        eventually("shuffle retains both annotations") { fixture.store.state.value.queue?.shuffleEnabled == true }
        controller.setShuffle(false)
        controller.setRepeat("OFF")
        eventually("ordinary taste queue order is restored") {
            fixture.store.state.value.queue?.let { !it.shuffleEnabled && it.repeatMode == "OFF" } == true
        }
        assertEquals("EXCLUDED", fixture.store.state.value.queue?.listenTasteState)
        assertEquals("EXCLUDED", fixture.store.state.value.queue?.sessionTasteState)

        // Current in-memory position is newer than the store's paused checkpoint response.
        controller.play()
        eventually("taste annotations can edit a playing owner") { controller.state.value.isPlaying }
        engine.emit(DesktopAudioEvent(native.generation, "READY", 2_500, 9_000, true))
        eventually("native playing position is newer than the checkpoint") { controller.state.value.positionMs == 2_500L }
        assertEquals(1_200L, fixture.store.state.value.queue?.positionMs)
        val playingNative = engine.state.value
        val playingTimer = controller.sleepTimer.value
        val playingHistoryIds = fixture.store.state.value.history.map { it.id }
        val playingStops = engine.stops.toList()
        val playingOpens = engine.opens.toList()
        val playingWrites = queueWrites.get()
        val playingQueue = checkNotNull(fixture.store.state.value.queue)
        val playingTracks = fixture.store.state.value.tracks
        val playingPlaylists = fixture.store.state.value.playlists
        val playingPauses = pauseRequests.get()
        val playingSeeks = engine.seeks.toList()
        chooseListen(false)
        assertEquals(1_200L, probe.results.last()?.positionMs)
        assertEquals(2_500L, controller.state.value.positionMs)
        probe.failure.set(DesktopStorageException("desktop_outbox_payload_invalid"))
        controller.setQueueSessionTasteExcluded(base.id, false)
        eventually("playing outbox failure never stops the native owner") { controller.taste.value.errorCode == "TASTE_EXCLUSION_UNAVAILABLE" }
        assertEquals(playingNative, engine.state.value)
        assertEquals(playingStops, engine.stops.toList())
        assertEquals(playingOpens, engine.opens.toList())
        assertEquals(playingTimer, controller.sleepTimer.value)
        assertEquals(playingWrites, queueWrites.get())
        assertEquals(playingQueue.copy(listenTasteState = "INCLUDED"), fixture.store.state.value.queue)
        assertEquals(playingTracks, fixture.store.state.value.tracks)
        assertEquals(playingPlaylists, fixture.store.state.value.playlists)
        assertEquals(playingPauses, pauseRequests.get())
        assertEquals(playingSeeks, engine.seeks.toList())
        assertEquals(playingHistoryIds, fixture.store.state.value.history.map { it.id })
        assertEquals(base.sessionId, controller.taste.value.listeningEventId)
        assertEquals(2_500L, controller.state.value.positionMs)
        assertTrue(controller.state.value.isPlaying)
        chooseListen(true)
        controller.pause()
        eventually("later pause checkpoints the actor-owned playing position") {
            !controller.state.value.isPlaying && fixture.store.state.value.queue?.positionMs == 2_500L
        }
        assertTrue(checkNotNull(fixture.store.state.value.queue).playedMs >= base.playedMs)

        val oldListen = checkNotNull(controller.taste.value.listeningEventId)
        val calls = probe.listenCalls.get()
        controller.next()
        controller.setCurrentListenTasteExcluded(base.id, checkNotNull(base.currentEntryId), oldListen, false)
        // The queue-scoped command remains fresh across the preceding duplicate-entry transition.
        controller.setQueueSessionTasteExcluded(base.id, false)
        eventually("queued duplicate-entry callback is retired while the queue scope stays valid") {
            controller.taste.value.currentEntryId != base.currentEntryId && controller.taste.value.sessionTasteState == "INCLUDED" &&
                controller.taste.value.listeningEventId != oldListen && controller.taste.value.errorCode == null
        }
        assertEquals(calls, probe.listenCalls.get())
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        val oldFinal = fixture.store.state.value.history.single { it.id == oldListen }
        assertTrue(oldFinal.finalized)
        assertEquals("EXCLUDED", oldFinal.sessionTasteState)
        assertEquals("EXCLUDED", oldFinal.listenTasteState)
        chooseSession(true)
        chooseListen(true)
        val previousListen = checkNotNull(controller.taste.value.listeningEventId)
        controller.previous()
        eventually("previous retains the queue exclusion while starting a known included listen") {
            controller.taste.value.currentEntryId == base.currentEntryId && controller.taste.value.listeningEventId != previousListen
        }
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        controller.selectEntry(base.entries[1].id)
        eventually("explicit selection retains queue-wide taste scope") {
            controller.taste.value.currentEntryId == base.entries[1].id && controller.state.value.isPlaying
        }
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        chooseListen(true)
        controller.setRepeat("ONE")
        eventually("repeat taste checkpoint is persisted") { fixture.store.state.value.queue?.repeatMode == "ONE" }
        controller.play()
        eventually("repeat taste scene is playing") { controller.state.value.isPlaying }
        val repeatedListen = checkNotNull(controller.taste.value.listeningEventId)
        val repeatedEntry = checkNotNull(controller.taste.value.currentEntryId)
        engine.emit(DesktopAudioEvent(engine.opens.last().generation, "EOF", 8_000, 8_000))
        eventually("repeat creates only a new listen and retains the queue exclusion") {
            controller.taste.value.listeningEventId != repeatedListen && controller.state.value.isPlaying
        }
        assertEquals(repeatedEntry, controller.taste.value.currentEntryId)
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        val repeatFinal = fixture.store.state.value.history.single { it.id == repeatedListen }
        assertTrue(repeatFinal.finalized)
        assertEquals("EXCLUDED", repeatFinal.sessionTasteState)
        assertEquals("EXCLUDED", repeatFinal.listenTasteState)
        val replayCalls = probe.listenCalls.get()
        controller.setCurrentListenTasteExcluded(base.id, repeatedEntry, repeatedListen, false)
        eventually("same-entry replay retires the earlier listen callback") { controller.taste.value.errorCode == "TASTE_LISTEN_STALE" }
        assertEquals(replayCalls, probe.listenCalls.get())
        chooseListen(true)
        val endedListen = checkNotNull(controller.taste.value.listeningEventId)
        controller.stopAfterCurrent(base.id, repeatedEntry, endedListen)
        eventually("EOF annotation capture owns a stop-after plan") { controller.sleepTimer.value.stopAfterEntryId == repeatedEntry }
        engine.emit(DesktopAudioEvent(engine.opens.last().generation, "EOF", 8_000, 8_000))
        eventually("finished listen disables only listen annotation commands") {
            controller.state.value.playbackStatus == PlaybackStatus.Ended && controller.taste.value.listeningEventId == null &&
                !controller.taste.value.listenCommandsEnabled && controller.taste.value.sessionCommandsEnabled
        }
        val endedFinal = fixture.store.state.value.history.single { it.id == endedListen }
        assertEquals("EXCLUDED", endedFinal.sessionTasteState)
        assertEquals("EXCLUDED", endedFinal.listenTasteState)
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        val endedNative = engine.state.value
        chooseSession(false)
        assertEquals(endedNative, engine.state.value)
        assertEquals(endedFinal, fixture.store.state.value.history.single { it.id == endedListen })
        assertEquals(oldFinal, fixture.store.state.value.history.single { it.id == oldListen })
        assertEquals(repeatFinal, fixture.store.state.value.history.single { it.id == repeatedListen })

        // Commands queued behind a held replacement must recheck their IDs at execution.
        val retiringQueue = checkNotNull(fixture.store.state.value.queue)
        val gate = ReplacementSaveGate(retiringQueue.id)
        replacementSaveGate.set(gate)
        val staleListenCalls = probe.listenCalls.get()
        val staleSessionCalls = probe.sessionCalls.get()
        controller.playTracks(listOf(trackId, batchTrackId))
        val candidate = try {
            val attempt = withTimeout(5_000) { gate.entered.await() }
            controller.setCurrentListenTasteExcluded(retiringQueue.id, repeatedEntry, endedListen, true)
            controller.setQueueSessionTasteExcluded(retiringQueue.id, true)
            attempt.queue
        } finally {
            gate.release.complete(Unit)
        }
        eventually("old queue callbacks cannot annotate the admitted replacement") {
            controller.taste.value.queueId == candidate.id && controller.taste.value.errorCode == "TASTE_SESSION_STALE" &&
                controller.state.value.isPlaying
        }
        replacementSaveGate.set(null)
        assertEquals(staleListenCalls, probe.listenCalls.get())
        assertEquals(staleSessionCalls, probe.sessionCalls.get())
        assertEquals("INCLUDED", controller.taste.value.sessionTasteState)
        assertEquals("INCLUDED", controller.taste.value.listenTasteState)
        assertEquals(endedFinal, fixture.store.state.value.history.single { it.id == endedListen })
        chooseSession(true)
        chooseListen(true)
        // Native failure priority survives a successful local annotation on the same listen.
        engine.emit(engine.state.value.copy(status = "ERROR", isPlaying = false, errorCode = "NATIVE_OPEN_FAILED"))
        eventually("native error is visible before annotation recovery") { controller.error.value == "AUDIO_ERROR" }
        val failedStops = engine.stops.toList()
        val failedOpens = engine.opens.toList()
        assertTrue(controller.taste.value.listenCommandsEnabled)
        chooseSession(false)
        assertEquals("AUDIO_ERROR", controller.error.value)
        assertEquals(failedStops, engine.stops.toList())
        assertEquals(failedOpens, engine.opens.toList())
        controller.selectEntry(checkNotNull(controller.taste.value.currentEntryId))
        eventually("explicit native recovery keeps its retained listen exclusion") {
            controller.error.value == null && controller.state.value.isPlaying && controller.taste.value.listenTasteState == "EXCLUDED"
        }
        controller.pause()
        eventually("taste scene returns a paused current owner") { !controller.state.value.isPlaying }
        chooseSession(true)
        controller.clearUpcoming()
        eventually("clear-upcoming retains current annotations and queue-wide scope") { fixture.store.state.value.queue?.entries?.size == 1 }
        assertEquals("EXCLUDED", controller.taste.value.listenTasteState)
        assertEquals("EXCLUDED", controller.taste.value.sessionTasteState)
        controller.seekTo(1_200)
        eventually("taste scene returns the existing restart position") { fixture.store.state.value.queue?.positionMs == 1_200L }
        assertEquals("EXCLUDED", fixture.store.state.value.queue?.listenTasteState)
        assertEquals("EXCLUDED", fixture.store.state.value.queue?.sessionTasteState)
    }

    private class TasteProbe {
        val listenCalls: AtomicInteger = AtomicInteger()
        val sessionCalls: AtomicInteger = AtomicInteger()
        val failure: AtomicReference<Exception?> = AtomicReference(null)
        val retireNext: AtomicBoolean = AtomicBoolean(false)
        val results: MutableList<DesktopQueue?> = CopyOnWriteArrayList()
    }

    /** Authored policy scene within the existing composite; no additional test case. */
    private suspend fun exerciseCollectionAdmission(
        fixture: DesktopAcceptanceFixture,
        controller: DesktopPlaybackController,
        engine: PolicyEngine,
        trackId: String,
        batchTrackId: String,
        queueWrites: AtomicInteger,
        unknownCollectionTrack: AtomicReference<String?>,
        removeBeforeQueueSave: AtomicReference<String?>,
        rejectedQueueEvent: AtomicReference<DesktopListeningEvent?>,
        replacementSaveGate: AtomicReference<ReplacementSaveGate?>,
        insertionSaveGate: AtomicReference<MetadataApplyGate?>,
    ): String {
        // Membership changes retain current/upcoming references and both timer modes.
        val collectionActiveId = fixture.importWav("Collection active tone.wav", durationMs = 1_250)
        val mixedCollectionPlaylist = fixture.store.createPlaylist("Mixed collection admission")
        listOf(collectionActiveId, trackId).forEach { fixture.store.appendToPlaylist(mixedCollectionPlaylist.id, it) }
        controller.enqueue(batchTrackId)
        eventually("collection scene has a distinct upcoming track") {
            fixture.store.state.value.queue?.entries?.lastOrNull()?.trackId == batchTrackId
        }
        val collectionBase = checkNotNull(fixture.store.state.value.queue)
        controller.scheduleSleepTimer(collectionBase.id, 60_000)
        eventually("duration arms before collection admission refusal") { controller.sleepTimer.value.remainingMs == 60_000L }
        suspend fun assertCollectionRefusal(description: String, rejectedWrites: Int = 0, action: () -> Unit): Unit {
            val before = checkNotNull(fixture.store.state.value.queue)
            val history = fixture.store.state.value.history
            val native = engine.state.value
            val opens = engine.opens.toList()
            val stops = engine.stops.toList()
            val seeks = engine.seeks.toList()
            val timer = controller.sleepTimer.value
            val writes = queueWrites.get()
            action()
            // A changed persisted mode proves this request passed through the actor,
            // even when a previous refusal already exposed the same error code.
            val fenceMode = if (before.repeatMode == "OFF") "ONE" else "OFF"
            controller.setRepeat(fenceMode)
            eventually(description) { fixture.store.state.value.queue?.repeatMode == fenceMode }
            assertEquals("desktop_track_not_in_collection", controller.error.value)
            assertEquals(before.copy(repeatMode = fenceMode), fixture.store.state.value.queue)
            assertEquals(history, fixture.store.state.value.history)
            assertFalse(fixture.store.state.value.history.single { it.id == before.sessionId }.finalized)
            assertEquals(writes + rejectedWrites + 1, queueWrites.get())
            assertEquals(before.currentEntryId, controller.state.value.mediaId)
            assertEquals(before.positionMs, controller.state.value.positionMs)
            assertFalse(controller.state.value.isPlaying)
            assertEquals(native, engine.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops, engine.stops.toList())
            assertEquals(seeks, engine.seeks.toList())
            assertEquals(timer, controller.sleepTimer.value)
        }
        unknownCollectionTrack.set(collectionActiveId)
        for ((label, action) in listOf(
            "unknown Enqueue" to { controller.enqueue(collectionActiveId) },
            "unknown Play next" to { controller.playNext(collectionActiveId) },
            "unknown replacement" to { controller.playTracks(listOf(collectionActiveId)) },
            "unknown mixed replacement" to { controller.playTracks(listOf(trackId, collectionActiveId)) },
            "unknown playlist Enqueue" to { controller.enqueuePlaylist(mixedCollectionPlaylist.id) },
            "unknown playlist next" to { controller.playPlaylistNext(mixedCollectionPlaylist.id) },
            "unknown mixed raw Enqueue" to { controller.enqueueTracks(listOf(trackId, collectionActiveId, trackId)) },
            "unknown mixed raw next" to { controller.playTracksNext(listOf(trackId, collectionActiveId, trackId)) },
        )) assertCollectionRefusal(label, action = action)
        unknownCollectionTrack.set(null)
        val removedQueue = checkNotNull(fixture.store.state.value.queue)
        controller.stopAfterCurrent(removedQueue.id, checkNotNull(removedQueue.currentEntryId), checkNotNull(removedQueue.sessionId))
        eventually("stop-after arms before current and upcoming collection removal") {
            controller.sleepTimer.value.stopAfterEntryId == removedQueue.currentEntryId
        }
        val removedTimer = controller.sleepTimer.value
        val beforeMembership = fixture.store.state.value
        val membershipWrites = queueWrites.get()
        val membershipNative = engine.state.value
        val membershipOpens = engine.opens.toList()
        val membershipStops = engine.stops.toList()
        val managedCurrent = fixture.store.resolveManagedFile(trackId)
        for (removedId in listOf(trackId, batchTrackId)) fixture.store.removeTrackFromCollection(removedId)
        val afterMembership = fixture.store.state.value
        assertEquals(beforeMembership.tracks.map {
            if (it.id == trackId || it.id == batchTrackId) it.copy(collectionState = "REMOVED") else it
        }, afterMembership.tracks)
        assertEquals(beforeMembership.playlists, afterMembership.playlists)
        assertEquals(removedQueue, afterMembership.queue)
        assertEquals(beforeMembership.history, afterMembership.history)
        assertEquals(membershipWrites, queueWrites.get())
        assertEquals(managedCurrent, fixture.store.resolveManagedFile(trackId))
        assertEquals(membershipNative, engine.state.value)
        assertEquals(membershipOpens, engine.opens.toList())
        assertEquals(membershipStops, engine.stops.toList())
        assertEquals(removedTimer, controller.sleepTimer.value)
        for ((label, action) in listOf(
            "removed Enqueue" to { controller.enqueue(trackId) },
            "removed Play next" to { controller.playNext(batchTrackId) },
            "removed replacement" to { controller.playTracks(listOf(trackId)) },
            "removed mixed replacement" to { controller.playTracks(listOf(collectionActiveId, trackId), "PLAYLIST") },
            "removed playlist Enqueue" to { controller.enqueuePlaylist(mixedCollectionPlaylist.id) },
            "removed playlist next" to { controller.playPlaylistNext(mixedCollectionPlaylist.id) },
            "missing reference replacement" to { controller.playTracks(listOf(collectionActiveId, "missing-track")) },
            "removed mixed raw Enqueue" to { controller.enqueueTracks(listOf(collectionActiveId, trackId, collectionActiveId)) },
            "removed mixed raw next" to { controller.playTracksNext(listOf(collectionActiveId, batchTrackId)) },
            "missing mixed raw Enqueue" to { controller.enqueueTracks(listOf(collectionActiveId, "missing-track")) },
            "missing mixed raw next" to { controller.playTracksNext(listOf(collectionActiveId, "missing-track")) },
            "malformed mixed raw Enqueue" to { controller.enqueueTracks(listOf(collectionActiveId, "")) },
            "malformed mixed raw next" to { controller.playTracksNext(listOf(collectionActiveId, "private/path")) },
        )) assertCollectionRefusal(label, action = action)
        fixture.store.restoreTrackToCollection(trackId)
        for ((label, action) in listOf(
            "Enqueue removal race" to { controller.enqueue(collectionActiveId) },
            "Play next removal race" to { controller.playNext(collectionActiveId) },
            "replacement removal race" to { controller.playTracks(listOf(collectionActiveId)) },
            "playlist Enqueue removal race" to { controller.enqueuePlaylist(mixedCollectionPlaylist.id) },
            "playlist next removal race" to { controller.playPlaylistNext(mixedCollectionPlaylist.id) },
            "raw Enqueue removal race" to { controller.enqueueTracks(listOf(trackId, collectionActiveId, trackId)) },
            "raw next removal race" to { controller.playTracksNext(listOf(trackId, collectionActiveId, trackId)) },
        )) {
            fixture.store.restoreTrackToCollection(collectionActiveId)
            removeBeforeQueueSave.set(collectionActiveId)
            assertCollectionRefusal(label, rejectedWrites = 1, action = action)
            assertNull(removeBeforeQueueSave.get())
            assertEquals("REMOVED", fixture.store.state.value.tracks.single { it.id == collectionActiveId }.collectionState)
        }
        fixture.store.restoreTrackToCollection(collectionActiveId)
        fixture.store.restoreTrackToCollection(batchTrackId)
        val restoredMembershipQueue = checkNotNull(fixture.store.state.value.queue)
        controller.enqueue(collectionActiveId)
        controller.playNext(batchTrackId)
        controller.enqueuePlaylist(mixedCollectionPlaylist.id)
        controller.playPlaylistNext(mixedCollectionPlaylist.id)
        eventually("restoration enables both single and complete playlist insertions") {
            fixture.store.state.value.queue?.entries?.size == restoredMembershipQueue.entries.size + 6
        }
        assertEquals(restoredMembershipQueue.sessionId, fixture.store.state.value.queue?.sessionId)
        assertEquals(membershipNative, engine.state.value)
        assertEquals(removedTimer, controller.sleepTimer.value)
        controller.playTracks(listOf(trackId, batchTrackId, collectionActiveId))
        eventually("restored selection starts the playing owner before its replacement race") {
            fixture.store.state.value.queue?.id != restoredMembershipQueue.id && controller.state.value.isPlaying &&
                controller.error.value == null && controller.sleepTimer.value.planId == null
        }
        val playingOwner = checkNotNull(fixture.store.state.value.queue)
        controller.setQueueSessionTasteExcluded(playingOwner.id, true)
        controller.setCurrentListenTasteExcluded(playingOwner.id, checkNotNull(playingOwner.currentEntryId),
            checkNotNull(playingOwner.sessionId), true)
        eventually("old playing owner has both annotations before admission") {
            controller.taste.value.queueId == playingOwner.id &&
                controller.taste.value.sessionTasteState == "EXCLUDED" && controller.taste.value.listenTasteState == "EXCLUDED"
        }
        controller.stopAfterCurrent(playingOwner.id, checkNotNull(playingOwner.currentEntryId), checkNotNull(playingOwner.sessionId))
        eventually("stop-after arms on the playing owner before transactional refusal") {
            controller.sleepTimer.value.stopAfterEntryId == playingOwner.currentEntryId
        }
        delay(30)
        val playingRaceQueue = checkNotNull(fixture.store.state.value.queue)
        val playingRaceHistory = fixture.store.state.value.history
        val playingRaceNative = engine.state.value
        val playingRaceOpens = engine.opens.toList()
        val playingRaceStops = engine.stops.toList()
        val playingRaceTimer = controller.sleepTimer.value
        val playingRaceWrites = queueWrites.get()
        rejectedQueueEvent.set(null)
        removeBeforeQueueSave.set(collectionActiveId)
        controller.playTracks(listOf(collectionActiveId))
        eventually("transactional replacement refusal keeps the old owner playing") {
            removeBeforeQueueSave.get() == null && controller.error.value == "desktop_track_not_in_collection"
        }
        assertEquals(playingRaceQueue, fixture.store.state.value.queue)
        assertEquals(playingRaceHistory, fixture.store.state.value.history)
        assertEquals(playingRaceWrites + 1, queueWrites.get())
        assertEquals(playingRaceNative, engine.state.value)
        assertEquals(playingRaceOpens, engine.opens.toList())
        assertEquals(playingRaceStops, engine.stops.toList())
        assertEquals(playingRaceQueue.currentEntryId, controller.state.value.mediaId)
        assertTrue(controller.state.value.isPlaying)
        assertEquals(playingRaceTimer, controller.sleepTimer.value)
        val refusedCheckpoint = checkNotNull(rejectedQueueEvent.get())
        assertEquals(playingRaceQueue.sessionId, refusedCheckpoint.id)
        assertFalse(refusedCheckpoint.finalized)
        assertEquals("EXCLUDED", refusedCheckpoint.sessionTasteState)
        assertEquals("EXCLUDED", refusedCheckpoint.listenTasteState)
        var refusedBatchWaitMs = 0L
        for (afterCurrent in listOf(false, true)) {
            val beforeBatch = fixture.store.state.value
            val beforeBatchTarget = controller.playerState.value.target
            val beforeBatchWrites = queueWrites.get()
            val admission = MetadataApplyGate(DesktopStorageException("desktop_outbox_payload_invalid"))
            insertionSaveGate.set(admission)
            if (afterCurrent) controller.playTracksNext(listOf(trackId, trackId))
            else controller.enqueueTracks(listOf(trackId, trackId))
            try {
                admission.entered.await()
                val waitStarted = System.nanoTime()
                assertEquals(beforeBatch, fixture.store.state.value)
                assertEquals(beforeBatchTarget, controller.playerState.value.target)
                assertEquals(playingRaceNative, engine.state.value)
                assertTrue(controller.state.value.isPlaying)
                assertEquals(playingRaceTimer, controller.sleepTimer.value)
                delay(30)
                refusedBatchWaitMs += (System.nanoTime() - waitStarted) / 1_000_000
            } finally {
                admission.release.complete(Unit)
            }
            if (afterCurrent) controller.setQueueSessionTasteExcluded("playing-batch-fence", false)
            else controller.setCurrentListenTasteExcluded("playing-batch-fence", "missing", "missing", false)
            val fenceError = if (afterCurrent) "TASTE_SESSION_STALE" else "TASTE_LISTEN_STALE"
            eventually("playing raw refusal remains serialized $afterCurrent") { controller.taste.value.errorCode == fenceError }
            assertEquals("PLAYBACK_OPERATION_FAILED", controller.error.value)
            assertEquals(beforeBatch, fixture.store.state.value)
            assertEquals(beforeBatchWrites + 1, queueWrites.get())
            assertEquals(beforeBatchTarget, controller.playerState.value.target)
            assertEquals(playingRaceNative, engine.state.value)
            assertEquals(playingRaceOpens, engine.opens.toList())
            assertEquals(playingRaceStops, engine.stops.toList())
            assertEquals(playingRaceTimer, controller.sleepTimer.value)
            assertTrue(controller.state.value.isPlaying)
        }
        controller.pause()
        eventually("refused candidate did not consume cumulative played time") {
            !controller.state.value.isPlaying && fixture.store.state.value.queue?.let {
                it.sessionId == playingRaceQueue.sessionId && it.playedMs >= refusedCheckpoint.playedMs &&
                    it.playedMs >= playingRaceQueue.playedMs + refusedBatchWaitMs
            } == true
        }
        assertFalse(fixture.store.state.value.history.single { it.id == playingRaceQueue.sessionId }.finalized)
        fixture.store.restoreTrackToCollection(collectionActiveId)
        controller.play()
        eventually("old session resumes before delayed successful admission") {
            controller.state.value.isPlaying && fixture.store.state.value.queue?.sessionId == playingRaceQueue.sessionId
        }
        val delayedQueue = checkNotNull(fixture.store.state.value.queue)
        val delayedHistory = fixture.store.state.value.history
        val delayedState = controller.state.value
        val delayedNative = engine.state.value
        val delayedOpens = engine.opens.toList()
        val delayedStops = engine.stops.toList()
        val delayedSeeks = engine.seeks.toList()
        val delayedTimer = controller.sleepTimer.value
        val delayedWrites = queueWrites.get()
        assertEquals("READY", delayedNative.status)
        assertTrue(delayedNative.isPlaying)
        assertEquals(delayedNative.positionMs, delayedQueue.positionMs)
        assertEquals(playingRaceTimer, delayedTimer)
        val gate = ReplacementSaveGate(delayedQueue.id)
        replacementSaveGate.set(gate)
        controller.playTracks(listOf(collectionActiveId, trackId, collectionActiveId))
        val (candidate, observedWaitMs) = try {
            val attempt = withTimeout(5_000) { gate.entered.await() }
            val waitStartedNanos = System.nanoTime()
            val oldCheckpoint = checkNotNull(attempt.event)
            assertEquals(delayedQueue.sessionId, oldCheckpoint.id)
            assertFalse(oldCheckpoint.finalized)
            assertEquals(delayedQueue.sessionTasteState, oldCheckpoint.sessionTasteState)
            assertEquals(delayedQueue.listenTasteState, oldCheckpoint.listenTasteState)
            assertEquals(delayedQueue.positionMs, oldCheckpoint.positionMs)
            assertTrue(oldCheckpoint.playedMs >= delayedQueue.playedMs)
            assertTrue(attempt.queue.id != delayedQueue.id && attempt.queue.sessionId != delayedQueue.sessionId)
            assertEquals("INCLUDED", attempt.queue.sessionTasteState)
            assertEquals("INCLUDED", attempt.queue.listenTasteState)
            assertEquals(delayedQueue, attempt.snapshot.queue)
            assertEquals(delayedHistory, attempt.snapshot.history)
            fun assertOldOwnerDuringAdmission(): Unit {
                assertEquals(delayedQueue, fixture.store.state.value.queue)
                assertEquals(delayedHistory, fixture.store.state.value.history)
                assertEquals(delayedState, controller.state.value)
                assertTrue(controller.state.value.isPlaying)
                assertEquals(delayedNative, engine.state.value)
                assertEquals(delayedOpens, engine.opens.toList())
                assertEquals(delayedStops, engine.stops.toList())
                assertEquals(delayedSeeks, engine.seeks.toList())
                assertEquals(delayedTimer, controller.sleepTimer.value)
                assertEquals(delayedWrites + 1, queueWrites.get())
            }
            assertOldOwnerDuringAdmission()
            delay(100)
            assertOldOwnerDuringAdmission()
            // Lower bound measured while admission is still held, never an exact timing equality.
            val elapsedMs = (System.nanoTime() - waitStartedNanos) / 1_000_000
            assertTrue(elapsedMs > 0)
            attempt.queue to elapsedMs
        } finally {
            gate.release.complete(Unit)
        }
        eventually("restoration enables a new replacement and clears its prior timer") {
            fixture.store.state.value.queue?.id == candidate.id && candidate.id != restoredMembershipQueue.id &&
                controller.state.value.isPlaying && controller.sleepTimer.value.planId == null &&
                controller.error.value == null && gate.writes.size >= 3
        }
        replacementSaveGate.set(null)
        val admission = gate.writes[0]
        val oldCheckpoint = checkNotNull(admission.event)
        assertEquals(candidate, admission.snapshot.queue)
        assertEquals(oldCheckpoint, admission.snapshot.history.single { it.id == delayedQueue.sessionId })
        assertFalse(oldCheckpoint.finalized)
        assertTrue(admission.snapshot.history.none { it.id == candidate.sessionId })
        assertEquals(delayedNative, admission.native)
        assertEquals(delayedOpens.size, admission.opens)
        assertEquals(delayedStops.size, admission.stops)
        val finalization = gate.writes[1]
        val finalEvent = checkNotNull(finalization.event)
        assertTrue(finalEvent.finalized)
        assertEquals(oldCheckpoint.copy(playedMs = finalEvent.playedMs, finalized = true), finalEvent)
        assertTrue("final listen includes the observed admission wait",
            finalEvent.playedMs >= oldCheckpoint.playedMs + observedWaitMs)
        assertEquals(candidate, finalization.snapshot.queue)
        assertEquals(finalEvent, finalization.snapshot.history.single { it.id == delayedQueue.sessionId })
        assertEquals(delayedOpens.size, finalization.opens)
        assertEquals(delayedStops.size + 1, finalization.stops)
        val newCheckpoint = gate.writes[2]
        val newEvent = checkNotNull(newCheckpoint.event)
        assertEquals(candidate, newCheckpoint.snapshot.queue)
        assertEquals(candidate.sessionId, newEvent.id)
        assertEquals(0L, newEvent.playedMs)
        assertEquals(0L, newEvent.positionMs)
        assertFalse(newEvent.finalized)
        assertEquals("INCLUDED", newEvent.sessionTasteState)
        assertEquals("INCLUDED", newEvent.listenTasteState)
        assertEquals("EXCLUDED", finalEvent.sessionTasteState)
        assertEquals("EXCLUDED", finalEvent.listenTasteState)
        assertEquals(newEvent, newCheckpoint.snapshot.history.single { it.id == candidate.sessionId })
        assertEquals(finalEvent, newCheckpoint.snapshot.history.single { it.id == delayedQueue.sessionId })
        assertEquals(delayedOpens.size + 1, engine.opens.size)
        assertTrue(engine.state.value.generation > delayedNative.generation)
        controller.pause()
        eventually("collection restart scene pauses") { !controller.state.value.isPlaying && !engine.state.value.isPlaying }
        assertEquals(finalEvent, fixture.store.state.value.history.single { it.id == delayedQueue.sessionId })
        controller.seekTo(1_200)
        eventually("removed queue restart keeps its observed position") { fixture.store.state.value.queue?.positionMs == 1_200L }
        val removedRestart = checkNotNull(fixture.store.state.value.queue)
        controller.setQueueSessionTasteExcluded(removedRestart.id, true)
        controller.setCurrentListenTasteExcluded(removedRestart.id, checkNotNull(removedRestart.currentEntryId),
            checkNotNull(removedRestart.sessionId), true)
        eventually("restart fixture retains both confirmed annotations") {
            controller.taste.value.sessionTasteState == "EXCLUDED" && controller.taste.value.listenTasteState == "EXCLUDED"
        }
        val annotatedRestart = checkNotNull(fixture.store.state.value.queue)
        val removedRestartHistory = fixture.store.state.value.history
        controller.scheduleSleepTimer(removedRestart.id, 60_000)
        eventually("timer arms before removing the future restored queue") { controller.sleepTimer.value.remainingMs == 60_000L }
        val restartPlan = controller.sleepTimer.value.planId
        fixture.store.removeTrackFromCollection(collectionActiveId)
        fixture.store.removeTrackFromCollection(trackId)
        assertEquals(annotatedRestart, fixture.store.state.value.queue)
        assertEquals(removedRestartHistory, fixture.store.state.value.history)
        assertEquals(restartPlan, controller.sleepTimer.value.planId)
        return collectionActiveId
    }

    private data class QueueSaveObservation(
        val queue: DesktopQueue,
        val event: DesktopListeningEvent?,
        val snapshot: DesktopLibrarySnapshot,
        val native: DesktopAudioEvent,
        val opens: Int,
        val stops: Int,
    )

    private class ReplacementSaveGate(val oldQueueId: String) {
        val entered: CompletableDeferred<QueueSaveObservation> = CompletableDeferred()
        val release: CompletableDeferred<Unit> = CompletableDeferred()
        val writes: MutableList<QueueSaveObservation> = CopyOnWriteArrayList()
    }

    /** Seed opaque choices through the real public store, then restore the actual actor owner. */
    private suspend fun assertOpaqueTasteRestart(
        fixture: DesktopAcceptanceFixture,
        trackId: String,
        timerNowNanos: () -> Long,
    ): Unit {
        val saved = checkNotNull(fixture.store.state.value.queue)
        val event = fixture.store.state.value.history.single { it.id == saved.sessionId }
        assertFalse(event.finalized)
        val rawSession = "FUTURE_SESSION_TASTE"
        val rawListen = "future-listen-taste"
        // A fresh fixture queue/listen seeds raw values without bypassing the ordinary
        // checkpoint fence or editing an existing listen's annotations retroactively.
        val opaque = saved.copy(id = "opaque-taste-queue", sessionId = "opaque-taste-listen",
            sessionTasteState = rawSession, listenTasteState = rawListen)
        fixture.store.saveQueue(opaque, event.copy(id = checkNotNull(opaque.sessionId),
            sessionTasteState = rawSession, listenTasteState = rawListen))
        fixture.reopenStore()
        assertEquals(opaque, fixture.store.state.value.queue)
        val engine = PolicyEngine()
        val controller = fixture.start(engine, timerNowNanos = timerNowNanos)
        eventually("real restart restores raw taste values into the actor") {
            controller.state.value.playbackStatus == PlaybackStatus.Ready && controller.taste.value.sessionTasteState == rawSession &&
                controller.taste.value.listenTasteState == rawListen
        }
        assertEquals(opaque.sessionId, controller.taste.value.listeningEventId)
        assertNull(DesktopTastePolicy.isExcluded(rawSession, rawListen))
        controller.setRepeat("ONE")
        eventually("ordinary checkpoint preserves opaque taste flags") { fixture.store.state.value.queue?.repeatMode == "ONE" }
        controller.next()
        eventually("new logical listen retains unsupported raw meaning") { controller.taste.value.listeningEventId != opaque.sessionId }
        assertEquals(rawSession, controller.taste.value.sessionTasteState)
        assertEquals(rawListen, controller.taste.value.listenTasteState)
        val opaqueFinal = fixture.store.state.value.history.single { it.id == opaque.sessionId }
        assertTrue(opaqueFinal.finalized)
        assertEquals(rawSession, opaqueFinal.sessionTasteState)
        assertEquals(rawListen, opaqueFinal.listenTasteState)
        controller.play()
        eventually("opaque listen can play before repeat") { controller.state.value.isPlaying }
        val repeated = checkNotNull(controller.taste.value.listeningEventId)
        engine.emit(DesktopAudioEvent(engine.opens.last().generation, "EOF", 8_000, 8_000))
        eventually("repeat preserves both opaque choices") { controller.taste.value.listeningEventId != repeated && controller.state.value.isPlaying }
        assertEquals(rawSession, controller.taste.value.sessionTasteState)
        assertEquals(rawListen, controller.taste.value.listenTasteState)
        val opaqueEnd = checkNotNull(fixture.store.state.value.queue)
        controller.stopAfterCurrent(opaqueEnd.id, checkNotNull(opaqueEnd.currentEntryId), checkNotNull(opaqueEnd.sessionId))
        eventually("opaque EOF scene arms stop-after") { controller.sleepTimer.value.planId != null }
        engine.emit(DesktopAudioEvent(engine.opens.last().generation, "EOF", 8_000, 8_000))
        eventually("clearing a finished listen does not normalize its opaque flag") {
            controller.taste.value.listeningEventId == null && controller.state.value.playbackStatus == PlaybackStatus.Ended
        }
        assertEquals(rawSession, controller.taste.value.sessionTasteState)
        assertEquals(rawListen, controller.taste.value.listenTasteState)
        assertEquals(rawListen, fixture.store.state.value.history.single { it.id == opaqueEnd.sessionId }.listenTasteState)
        fixture.stopController()
        fixture.reopenStore()
        val restartedEngine = PolicyEngine()
        val restarted = fixture.start(restartedEngine, timerNowNanos = timerNowNanos)
        eventually("second real restart preserves opaque listen annotation") {
            restarted.state.value.playbackStatus == PlaybackStatus.Ready && restarted.taste.value.listenTasteState == rawListen
        }
        assertEquals(rawSession, restarted.taste.value.sessionTasteState)
        assertFalse(restarted.taste.value.listenCommandsEnabled)
        assertTrue(restarted.taste.value.sessionCommandsEnabled)
        restarted.play()
        eventually("explicit replay starts a listen while retaining unknown meaning") {
            restarted.taste.value.listeningEventId != null && restarted.state.value.isPlaying
        }
        assertEquals(rawListen, restarted.taste.value.listenTasteState)
        val retained = checkNotNull(fixture.store.state.value.queue)
        restarted.setCurrentListenTasteExcluded(retained.id, checkNotNull(retained.currentEntryId), checkNotNull(retained.sessionId), true)
        eventually("explicit known choice replaces only the raw listen flag") { restarted.taste.value.listenTasteState == "EXCLUDED" }
        assertEquals(rawSession, restarted.taste.value.sessionTasteState)
        assertEquals(true, DesktopTastePolicy.isExcluded(rawSession, restarted.taste.value.listenTasteState))
        restarted.next()
        eventually("only the known listen choice resets on navigation") { restarted.taste.value.listeningEventId != retained.sessionId }
        assertEquals(rawSession, restarted.taste.value.sessionTasteState)
        assertEquals("INCLUDED", restarted.taste.value.listenTasteState)
        assertEquals(opaqueFinal, fixture.store.state.value.history.single { it.id == opaque.sessionId })
        restarted.setQueueSessionTasteExcluded(retained.id, false)
        eventually("explicit queue choice replaces the remaining opaque flag") { restarted.taste.value.sessionTasteState == "INCLUDED" }
        restarted.playTracks(listOf(trackId, trackId))
        eventually("a new queue starts known taste defaults") {
            restarted.taste.value.queueId != retained.id && restarted.state.value.isPlaying &&
                restarted.taste.value.sessionTasteState == "INCLUDED" && restarted.taste.value.listenTasteState == "INCLUDED"
        }
        assertEquals(opaqueFinal, fixture.store.state.value.history.single { it.id == opaque.sessionId })
        restarted.pause()
        eventually("known queue pauses after opaque history retention") { !restarted.state.value.isPlaying }
        fixture.stopController()

        // Model interrupted admission with a real persisted session but no listening row.
        val previous = checkNotNull(fixture.store.state.value.queue)
        val admitted = previous.copy(id = "interrupted-taste-queue", sessionId = "interrupted-taste-listen",
            sessionStartedAtMs = 123L, playedMs = 0L, positionMs = 0L)
        fixture.store.saveQueue(admitted)
        fixture.reopenStore()
        assertTrue(fixture.store.state.value.history.none { it.id == admitted.sessionId })
        val pendingEngine = PolicyEngine().apply { deferOpen = true }
        val pending = fixture.start(pendingEngine, timerNowNanos = timerNowNanos)
        eventually("interrupted annotation scene opens before readiness") {
            pending.state.value.playbackStatus == PlaybackStatus.Buffering && pending.taste.value.listenCommandsEnabled
        }
        val ids = fixture.store.state.value.history.map { it.id }.toSet()
        pending.setCurrentListenTasteExcluded(admitted.id, checkNotNull(admitted.currentEntryId), checkNotNull(admitted.sessionId), false)
        pending.setQueueSessionTasteExcluded(admitted.id, false)
        pending.setCurrentListenTasteExcluded(admitted.id, checkNotNull(admitted.currentEntryId), "retired-listen", true)
        eventually("known no-ops precede a stale identity fence without creating a checkpoint") {
            pending.taste.value.errorCode == "TASTE_LISTEN_STALE"
        }
        assertEquals(ids, fixture.store.state.value.history.map { it.id }.toSet())
        assertEquals(admitted, fixture.store.state.value.queue)
        pending.setQueueSessionTasteExcluded(admitted.id, true)
        eventually("effective queue annotation completes the existing interrupted checkpoint") { pending.taste.value.sessionTasteState == "EXCLUDED" }
        val completed = fixture.store.state.value.history.single { it.id == admitted.sessionId }
        assertEquals(ids + checkNotNull(admitted.sessionId), fixture.store.state.value.history.map { it.id }.toSet())
        assertEquals(admitted.currentEntryId, completed.queueEntryId)
        assertEquals(123L, completed.startedAtMs)
        assertFalse(completed.finalized)
        assertEquals("EXCLUDED", completed.sessionTasteState)
        assertEquals("INCLUDED", completed.listenTasteState)
        assertEquals(admitted.sessionId, pending.taste.value.listeningEventId)
        assertEquals(1, pendingEngine.opens.size)
        assertFalse(pending.state.value.isPlaying)
        fixture.stopController()

        val unsupported = checkNotNull(fixture.store.state.value.queue).copy(queueType = "FUTURE_QUEUE")
        fixture.store.saveQueue(unsupported)
        val unsupportedEngine = PolicyEngine()
        val unsupportedController = fixture.start(unsupportedEngine, timerNowNanos = timerNowNanos)
        eventually("unknown queue modes retain raw annotations with disabled commands") {
            unsupportedController.error.value == "UNSUPPORTED_QUEUE_MODE" && unsupportedController.taste.value.queueId == unsupported.id
        }
        assertFalse(unsupportedController.taste.value.listenCommandsEnabled)
        assertFalse(unsupportedController.taste.value.sessionCommandsEnabled)
        assertEquals(unsupported.sessionTasteState, unsupportedController.taste.value.sessionTasteState)
        assertEquals(unsupported.listenTasteState, unsupportedController.taste.value.listenTasteState)
        val unsupportedSnapshot = fixture.store.state.value
        val unsupportedTarget = checkNotNull(unsupportedController.playerState.value.target)
        DesktopTransportAction.entries.forEach { unsupportedController.transport(unsupportedTarget, it, 7_000) }
        unsupportedController.setCurrentListenTasteExcluded("transport-fence", "missing", "missing", false)
        eventually("typed commands retain unsupported modes behind the actor fence") { unsupportedController.taste.value.errorCode == "TASTE_LISTEN_STALE" }
        assertEquals(unsupportedSnapshot, fixture.store.state.value)
        assertTrue(unsupportedEngine.opens.isEmpty())
        assertTrue(unsupportedEngine.seeks.isEmpty())
        assertTrue(unsupportedEngine.stops.isEmpty())
        fixture.stopController()
        // Return the supported retained context for the following failure-policy scenes.
        fixture.store.saveQueue(unsupported.copy(queueType = "LIBRARY"))
    }

    /** Wrapper faults model refusal/refresh codes; actual SQL rollback belongs to data tests. */
    private suspend fun assertTasteSnapshotFailures(
        fixture: DesktopAcceptanceFixture,
        trackId: String,
        timerNowNanos: () -> Long,
    ): Unit {
        val realStore = fixture.store
        // A missing source does not disable local annotation or clear its global failure.
        val missingSourceStore = object : DesktopLibraryStore by realStore {
            override suspend fun resolveManagedFile(trackId: String): String? = null
        }
        val missingEngine = PolicyEngine()
        val missing = fixture.start(missingEngine, missingSourceStore, timerNowNanos)
        eventually("source refusal still exposes the local annotation context") {
            missing.error.value == "SOURCE_UNAVAILABLE" && missing.taste.value.listenCommandsEnabled && missing.taste.value.sessionCommandsEnabled
        }
        val target = checkNotNull(realStore.state.value.queue)
        val missingHistoryIds = realStore.state.value.history.map { it.id }
        val missingStops = missingEngine.stops.toList()
        val missingSnapshot = realStore.state.value
        val missingTarget = checkNotNull(missing.playerState.value.target)
        DesktopTransportAction.entries.forEach { missing.transport(missingTarget, it, 7_000) }
        missing.setQueueSessionTasteExcluded("transport-fence", false)
        eventually("typed commands retain missing-source gates") { missing.taste.value.errorCode == "TASTE_SESSION_STALE" }
        assertEquals(missingSnapshot, realStore.state.value)
        assertTrue(missingEngine.seeks.isEmpty())
        missing.setCurrentListenTasteExcluded(target.id, checkNotNull(target.currentEntryId), checkNotNull(target.sessionId), true)
        eventually("source-refused listen can be explicitly annotated") { missing.taste.value.listenTasteState == "EXCLUDED" }
        assertEquals("SOURCE_UNAVAILABLE", missing.error.value)
        assertEquals(missingStops, missingEngine.stops.toList())
        assertTrue(missingEngine.opens.isEmpty())
        assertEquals(missingHistoryIds, realStore.state.value.history.map { it.id })
        val sourceQueue = checkNotNull(realStore.state.value.queue)
        val sourceTarget = missing.playerState.value.target
        missing.enqueueTracks(listOf(trackId, trackId))
        missing.playTracksNext(listOf(trackId, trackId))
        eventually("raw insertions keep a nonempty missing-source owner and its safe error") {
            realStore.state.value.queue?.entries?.size == sourceQueue.entries.size + 4 &&
                missing.error.value == "SOURCE_UNAVAILABLE"
        }
        assertEquals(sourceQueue.currentEntryId, realStore.state.value.queue?.currentEntryId)
        assertEquals(sourceQueue.sessionId, realStore.state.value.queue?.sessionId)
        assertEquals(sourceQueue.positionMs, realStore.state.value.queue?.positionMs)
        assertEquals(sourceTarget, missing.playerState.value.target)
        assertEquals(missingStops, missingEngine.stops.toList())
        assertTrue(missingEngine.opens.isEmpty())
        assertEquals(missingHistoryIds, realStore.state.value.history.map { it.id })
        fixture.stopController()

        val failures = listOf(
            DesktopStorageException("desktop_snapshot_unavailable"),
            IllegalStateException("fixture_refresh_failure", DesktopStorageException("desktop_snapshot_unavailable")),
            DesktopStorageException("desktop_outbox_payload_invalid").apply {
                addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
            },
            CancellationException("fixture_cancelled").apply {
                addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
            },
        )
        for ((index, failure) in failures.withIndex()) {
            val nextFailure = AtomicReference<Exception?>(null)
            val calls = AtomicInteger()
            val store = object : DesktopLibraryStore by realStore {
                override suspend fun setCurrentListenTasteExcluded(
                    expectedQueueId: String, expectedEntryId: String, expectedListeningEventId: String, excluded: Boolean,
                ): DesktopQueue? {
                    calls.incrementAndGet()
                    nextFailure.getAndSet(null)?.let { throw it }
                    return realStore.setCurrentListenTasteExcluded(expectedQueueId, expectedEntryId, expectedListeningEventId, excluded)
                }
                override suspend fun setQueueSessionTasteExcluded(expectedQueueId: String, excluded: Boolean): DesktopQueue? {
                    calls.incrementAndGet()
                    nextFailure.getAndSet(null)?.let { throw it }
                    return realStore.setQueueSessionTasteExcluded(expectedQueueId, excluded)
                }
            }
            val engine = PolicyEngine()
            val controller = fixture.start(engine, store, timerNowNanos)
            eventually("taste refresh failure $index restores its paused context") { controller.state.value.playbackStatus == PlaybackStatus.Ready }
            val bound = checkNotNull(realStore.state.value.queue)
            controller.play()
            eventually("taste refresh failure $index begins with live native playback") { controller.state.value.isPlaying }
            controller.stopAfterCurrent(bound.id, checkNotNull(bound.currentEntryId), checkNotNull(bound.sessionId))
            eventually("taste refresh failure $index owns an armed plan") { controller.sleepTimer.value.planId != null }
            val before = realStore.state.value
            val opens = engine.opens.toList()
            val stops = engine.stops.size
            nextFailure.set(failure)
            if (index % 2 == 0) controller.setCurrentListenTasteExcluded(bound.id, checkNotNull(bound.currentEntryId),
                checkNotNull(bound.sessionId), false) else controller.setQueueSessionTasteExcluded(bound.id, false)
            eventually("every direct/cause/suppressed marker retains global restart priority $index") {
                controller.error.value == "desktop_snapshot_unavailable" && controller.sleepTimer.value.planId == null &&
                    !controller.taste.value.listenCommandsEnabled && !controller.taste.value.sessionCommandsEnabled &&
                    !controller.state.value.shuffleEnabled && !controller.state.value.repeatEnabled
            }
            assertEquals(before, realStore.state.value)
            assertEquals(bound.sessionTasteState, controller.taste.value.sessionTasteState)
            assertEquals(bound.listenTasteState, controller.taste.value.listenTasteState)
            assertNull(controller.taste.value.errorCode)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops + 1, engine.stops.size)
            val refusedCalls = calls.get()
            controller.setCurrentListenTasteExcluded(bound.id, checkNotNull(bound.currentEntryId), checkNotNull(bound.sessionId), true)
            controller.setQueueSessionTasteExcluded(bound.id, true)
            controller.playTracks(listOf(trackId))
            controller.reportSnapshotUnavailable()
            assertEquals(refusedCalls, calls.get())
            assertEquals(before, realStore.state.value)
            assertEquals(opens, engine.opens.toList())
            assertEquals(stops + 1, engine.stops.size)
            assertEquals("desktop_snapshot_unavailable", controller.error.value)
            fixture.stopController()
        }
    }

    private suspend fun assertMembershipSnapshotFailure(
        fixture: DesktopAcceptanceFixture,
        trackId: String,
        timerNowNanos: () -> Long,
    ): Unit {
        val realStore = fixture.store
        val failWithStaleSnapshot = AtomicBoolean(false)
        val store = object : DesktopLibraryStore by realStore {
            override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
                if (failWithStaleSnapshot.get()) {
                    // Model the data contract's combined mutation/refresh failure marker.
                    throw DesktopStorageException("desktop_track_not_in_collection").apply {
                        addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
                    }
                }
                realStore.saveQueue(queue, event)
            }
        }
        val engine = PolicyEngine()
        val controller = fixture.start(engine, store, timerNowNanos)
        eventually("combined-failure scene restores paused") { controller.state.value.playbackStatus == PlaybackStatus.Ready }
        val before = realStore.state.value
        controller.scheduleSleepTimer(checkNotNull(before.queue).id, 60_000)
        eventually("combined-failure scene arms its timer") { controller.sleepTimer.value.planId != null }
        val opens = engine.opens.toList()
        val stops = engine.stops.size
        failWithStaleSnapshot.set(true)
        controller.playTracks(listOf(trackId))
        eventually("snapshot marker takes priority over safe membership refusal") {
            controller.error.value == "desktop_snapshot_unavailable" && controller.sleepTimer.value.planId == null &&
                !controller.state.value.shuffleEnabled && !controller.state.value.repeatEnabled && !controller.state.value.seekEnabled
        }
        assertEquals(before, realStore.state.value)
        assertEquals(opens, engine.opens.toList())
        assertEquals(stops + 1, engine.stops.size)
        controller.enqueue(trackId)
        controller.enqueueTracks(listOf(trackId, trackId))
        controller.playTracksNext(listOf(trackId, trackId))
        controller.reportSnapshotUnavailable()
        delay(100)
        assertEquals("desktop_snapshot_unavailable", controller.error.value)
        assertEquals(before, realStore.state.value)
        assertEquals(opens, engine.opens.toList())
        assertEquals(stops + 1, engine.stops.size)
        fixture.stopController()
    }
}
