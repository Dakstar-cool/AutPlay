package app.autplay.playback

import android.app.ActivityManager
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class PlaybackServiceLifecycleTest {
    @Test fun notificationExportsNativeFeedbackActionsAndRatesTheCapturedTrack() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            val platform = fixture.platformController()
            fixture.await { platform.playbackState?.customActions?.map { it.action } ==
                listOf(PlaybackNotification.ACTION_DISLIKE, PlaybackNotification.ACTION_LIKE) }
            val actions = checkNotNull(platform.playbackState).customActions
            actions.forEach {
                assertTrue(it.icon != 0)
                assertEquals(fixture.entries[0].value, it.extras?.getString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID))
            }
            val like = actions.single { it.action == PlaybackNotification.ACTION_LIKE }
            val dislike = actions.single { it.action == PlaybackNotification.ACTION_DISLIKE }
            val unfilledIcon = like.icon
            platform.transportControls.sendCustomAction(like.action, like.extras)
            fixture.await { fixture.database.libraryDao().preference(fixture.tracks[0])?.preference == "LIKED" }
            fixture.await { platform.playbackState?.customActions?.singleOrNull {
                it.action == PlaybackNotification.ACTION_LIKE
            }?.let { it.icon != unfilledIcon } == true }
            platform.transportControls.sendCustomAction(dislike.action, dislike.extras)
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value } }
            fixture.await { fixture.database.libraryDao().preference(fixture.tracks[0])?.preference == "DISLIKED" }
            assertNull(fixture.database.libraryDao().preference(fixture.tracks[1]))
        } finally { fixture.close() }
    }

    @Test fun notificationFeedbackPersistsTheRatedTrackAndRejectsStaleEntryCommands() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.database.libraryDao().upsertPreference(app.autplay.data.local.entity.UserTrackPreferenceEntity(
                fixture.tracks[0], "NEUTRAL", null, true, "LOCAL_ONLY", 0, 1,
            ))
            fixture.await { fixture.main { fixture.controller?.mediaButtonPreferences?.count { it.isEnabled } == 2 } }
            assertNotNull(fixture.main { fixture.controller?.sessionActivity })
            val firstEntry = fixture.entries[0].value
            suspend fun rate(action: String, expected: String = firstEntry): Int {
                val future = fixture.main {
                    checkNotNull(fixture.controller).sendCustomCommand(
                        SessionCommand(action, Bundle.EMPTY),
                        Bundle().apply { putString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID, expected) },
                    )
                }
                return withContext(Dispatchers.IO) { future.get(10, TimeUnit.SECONDS).resultCode }
            }
            assertEquals(SessionResult.RESULT_SUCCESS, rate(PlaybackNotification.ACTION_LIKE))
            assertEquals("LIKED", fixture.database.libraryDao().preference(fixture.tracks[0])?.preference)
            assertEquals(firstEntry, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            assertFalse(fixture.main { fixture.controller?.isPlaying == true })
            fixture.await { fixture.main {
                fixture.controller?.mediaButtonPreferences?.any { it.icon == androidx.media3.session.CommandButton.ICON_THUMB_UP_FILLED } == true
            } }
            assertEquals(SessionResult.RESULT_SUCCESS, rate(PlaybackNotification.ACTION_LIKE))
            assertEquals("NEUTRAL", fixture.database.libraryDao().preference(fixture.tracks[0])?.preference)
            assertEquals(SessionResult.RESULT_SUCCESS, rate(PlaybackNotification.ACTION_DISLIKE))
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value } }
            assertEquals("DISLIKED", fixture.database.libraryDao().preference(fixture.tracks[0])?.preference)
            assertTrue(fixture.database.libraryDao().preference(fixture.tracks[0])?.excludedFromTaste == true)
            assertNull(fixture.database.libraryDao().preference(fixture.tracks[1]))
            assertEquals(SessionResult.RESULT_ERROR_INVALID_STATE, rate(PlaybackNotification.ACTION_LIKE))
            assertNull(fixture.database.libraryDao().preference(fixture.tracks[1]))
            fixture.database.useWriterConnection { connection ->
                connection.usePrepared("SELECT COUNT(*) FROM local_mutation_outbox WHERE aggregate_local_id = ? AND event_type = 'USER_TRACK_PREFERENCE_SET'") {
                    it.bindText(1, fixture.tracks[0]); it.step(); assertEquals(3L, it.getLong(0))
                }
            }
        } finally { fixture.close() }
    }

    @Test fun consecutiveEntriesOfTheSameTrackRefreshNotificationCommandIdentity() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            val first = LocalId.random()
            val duplicate = LocalId.random()
            val snapshot = LocalId.random()
            PlaybackPersistenceRepository(fixture.database).activateQueue(snapshot,
                listOf(NewPlaybackQueueEntry(first, LocalId(fixture.tracks[0]), "ORGANIC", "LOCAL_THEN_VAULT"),
                    NewPlaybackQueueEntry(duplicate, LocalId(fixture.tracks[0]), "ORGANIC", "LOCAL_THEN_VAULT")),
                "USER", null, null, "GENERAL", System.currentTimeMillis())
            fixture.rememberSnapshot(snapshot)
            fixture.owner.dispatch(PlaybackCommand.StartQueue(snapshot))
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == first.value &&
                fixture.controller?.getMediaItemAt(1)?.mediaId == duplicate.value } }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { fixture.main { fixture.controller?.mediaButtonPreferences?.let { buttons -> buttons.size == 2 && buttons.all {
                it.sessionCommand?.customExtras?.getString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID) == duplicate.value && it.isEnabled
            } } == true } }
        } finally { fixture.close() }
    }

    @Test fun notificationCannotRateThePriorProfilesTrackAfterAnAccountSwitch() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.switchProfile()
            fixture.await { fixture.main { fixture.controller?.mediaButtonPreferences?.none { it.isEnabled } == true } }
            val future = fixture.main {
                checkNotNull(fixture.controller).sendCustomCommand(SessionCommand(PlaybackNotification.ACTION_DISLIKE, Bundle.EMPTY),
                    Bundle().apply { putString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID, fixture.entries[0].value) })
            }
            assertEquals(SessionResult.RESULT_ERROR_PERMISSION_DENIED, withContext(Dispatchers.IO) { future.get(10, TimeUnit.SECONDS).resultCode })
            assertNull(fixture.database.libraryDao().preference(fixture.tracks[0]))
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
        } finally { fixture.close() }
    }

    @Test fun completedTrackAutomaticallyStartsSuccessorAndPersistsNewEntry() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("short", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.await {
                fixture.main {
                    fixture.controller?.isPlaying == true && fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value
                }
            }
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.currentEntryId == fixture.entries[1].value }
            assertEquals(fixture.entries[1].value, PlaybackRuntimeState.state.value.queueEntryId)
        } finally { fixture.close() }
    }

    @Test fun dislikeSkipResumesPausedSuccessorAndRejectsDuplicateOrStaleCommand() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.await { fixture.main { fixture.controller?.isPlaying == false } }
            fixture.owner.dispatch(PlaybackCommand.NextIfCurrent(fixture.entries[0]))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true && fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value } }
            fixture.owner.dispatch(PlaybackCommand.NextIfCurrent(fixture.entries[0]))
            delay(250)
            assertEquals(fixture.entries[1].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            fixture.owner.dispatch(PlaybackCommand.NextIfCurrent(fixture.entries[1]))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true && fixture.controller?.currentMediaItem?.mediaId == fixture.entries[2].value } }
            fixture.owner.dispatch(PlaybackCommand.NextIfCurrent(fixture.entries[2]))
            fixture.await { fixture.main { fixture.controller?.isPlaying == false } }
        } finally { fixture.close() }
    }

    @Test fun smoothTransitionCutsTimePreservesVolumeAndDisablingKeepsSourcePosition() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable", "readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause(); fixture.controller?.volume = 0.8f }
            fixture.await("initial cut duration") { fixture.main { fixture.controller?.duration == 37_000L } }
            assertEquals(0L, fixture.main { fixture.controller?.currentMediaItem?.clippingConfiguration?.startPositionMs })
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await("middle cut duration") { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value && fixture.controller?.duration == 34_000L } }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(10_000))
            fixture.await("middle source seek") { fixture.main { fixture.controller?.duration == 34_000L && fixture.controller?.currentPosition == 7_000L } }
            assertEquals(0.8f, fixture.main { fixture.controller!!.volume }, 0.001f)
            fixture.await("source seek persisted") { fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs == 10_000L }
            fixture.smoothTransitions(false)
            fixture.await("disabled cut retains position") { fixture.main { fixture.controller?.duration == 40_000L && fixture.controller?.currentPosition == 10_000L } }
            assertEquals(0.8f, fixture.main { fixture.controller!!.volume }, 0.001f)
            fixture.main { fixture.controller?.volume = 0f }
            fixture.smoothTransitions(true)
            fixture.await("reenabled cut retains mute") { fixture.main { fixture.controller?.duration == 34_000L } }
            assertEquals(0f, fixture.main { fixture.controller!!.volume }, 0f)
        } finally { fixture.close() }
    }

    @Test fun cutTransitionStartsSuccessorAtSourceThreeSecondsAndKeepsFinalTail() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true && fixture.controller?.duration == 37_000L } }
            fixture.await("initial listening session persisted") {
                fixture.database.queueDao().activeSnapshotOnce()?.let {
                    it.currentEntryId == fixture.entries[0].value && it.activeListeningEventId != null
                } == true
            }
            fixture.main { fixture.controller?.pause(); fixture.controller?.volume = 0.35f }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(36_800))
            fixture.await { fixture.main { fixture.controller?.currentPosition == 36_800L } }
            val previousListen = requireNotNull(fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            fixture.main { fixture.controller?.play() }
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value && fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.await { fixture.main { fixture.controller?.duration == 37_000L } }
            assertEquals(3_000L, fixture.main { fixture.controller?.currentMediaItem?.clippingConfiguration?.startPositionMs })
            fixture.await("successor runtime source position") {
                PlaybackRuntimeState.state.value.queueEntryId == fixture.entries[1].value &&
                    PlaybackRuntimeState.state.value.positionMs in 3_000..5_000
            }
            assertEquals(0.35f, fixture.main { fixture.controller!!.volume }, 0.001f)
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.currentEntryId == fixture.entries[1].value }
            assertTrue((fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs ?: 0) >= 3_000)
            val event = requireNotNull(fixture.database.historyDao().event(previousListen))
            assertEquals(40_000L, event.trackDurationMs)
            assertEquals(37_000L, event.sessionEndPositionMs)
            assertTrue(event.playedMs < 5_000)
            fixture.owner.dispatch(PlaybackCommand.SeekTo(0))
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs == 3_000L }
        } finally { fixture.close() }
    }

    @Test fun lateResolvedSuccessorUsesItsRealHeadBeforePlaybackStarts() = runBlocking {
        for (source in listOf("readable", "short")) {
            val fixture = Fixture()
            try {
                fixture.start(listOf("readable", "readable", source), smooth = true)
                fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
                val firstPlayingSourcePosition = java.util.concurrent.atomic.AtomicLong(-1)
                fixture.main {
                    val controller = checkNotNull(fixture.controller)
                    controller.pause()
                    assertEquals("autplay-unresolved", controller.getMediaItemAt(2).localConfiguration?.uri?.scheme)
                    controller.addListener(object : Player.Listener {
                        override fun onIsPlayingChanged(isPlaying: Boolean) {
                            if (isPlaying && controller.currentMediaItem?.mediaId == fixture.entries[2].value) {
                                firstPlayingSourcePosition.compareAndSet(-1,
                                    controller.currentPosition + (controller.currentMediaItem?.clippingConfiguration?.startPositionMs ?: 0))
                            }
                        }
                    })
                    controller.seekToDefaultPosition(2)
                    controller.play()
                }
                fixture.await { firstPlayingSourcePosition.get() >= 0 }
                fixture.main { fixture.controller?.pause() }
                val expectedStart = if (source == "short") 0L else 3_000L
                assertTrue("$source started at ${firstPlayingSourcePosition.get()}",
                    firstPlayingSourcePosition.get() in expectedStart..expectedStart + 1_000)
                fixture.await { fixture.main { fixture.controller?.duration == if (source == "short") 2_000L else 37_000L } }
            } finally { fixture.close() }
        }
    }

    @Test fun cutRepeatOneReplaysFromThreeSecondsAndCreatesANewListen() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause(); fixture.controller?.repeatMode = Player.REPEAT_MODE_ONE }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(36_800))
            fixture.await { fixture.main { fixture.controller?.duration == 37_000L && fixture.controller?.currentPosition == 36_800L } }
            val previousListen = fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId
            assertTrue(previousListen != null)
            fixture.main { fixture.controller?.play() }
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId?.let { it != previousListen } == true }
            fixture.main { fixture.controller?.pause() }
            val event = requireNotNull(fixture.database.historyDao().event(requireNotNull(previousListen)))
            assertEquals(37_000L, event.sessionEndPositionMs)
            assertEquals(40_000L, event.trackDurationMs)
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            assertTrue(PlaybackRuntimeState.state.value.positionMs in 3_000..5_000)
        } finally { fixture.close() }
    }

    @Test fun stopAfterTrackRestoresFullEndAndPausesBeforeSuccessor() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(10_000))
            fixture.await { fixture.main { fixture.controller?.duration == 37_000L } }
            fixture.owner.dispatch(PlaybackCommand.StopAfterCurrentItem(fixture.entries[0]))
            fixture.await { fixture.main { fixture.controller?.duration == 40_000L } }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(39_800))
            fixture.await { fixture.main { fixture.controller?.currentPosition == 39_800L } }
            fixture.main { fixture.controller?.play() }
            fixture.await { fixture.main { fixture.controller?.playWhenReady == false } }
            assertEquals(fixture.entries[0].value, fixture.main { fixture.controller?.currentMediaItem?.mediaId })
            assertEquals(40_000L, PlaybackRuntimeState.state.value.positionMs)
        } finally { fixture.close() }
    }

    @Test fun cutPolicyUsesActualShortDurationAndLeavesWaveSynchronized() = runBlocking {
        val short = Fixture()
        try {
            // Deliberately stale 40s tags: the actual two-second source must remain whole.
            short.start(listOf("short", "readable"), smooth = true)
            short.await { short.main { short.controller?.isPlaying == true } }
            short.main { short.controller?.pause() }
            assertEquals(2_000L, short.main { short.controller?.duration })
            assertEquals(0L, short.main { short.controller?.currentMediaItem?.clippingConfiguration?.startPositionMs })
        } finally { short.close() }
        for (type in listOf("WAVE", "GUEST_WAVE")) {
            val wave = Fixture()
            try {
                wave.start(listOf("readable", "readable"), smooth = true, queueType = type)
                wave.await { wave.main { wave.controller?.isPlaying == true } }
                wave.main { wave.controller?.pause() }
                wave.owner.dispatch(PlaybackCommand.SeekTo(500))
                wave.await { wave.main { wave.controller?.currentPosition == 500L } }
                assertEquals(40_000L, wave.main { wave.controller?.duration })
                assertEquals(0L, wave.main { wave.controller?.currentMediaItem?.clippingConfiguration?.startPositionMs })
            } finally { wave.close() }
        }
    }

    @Test fun clippedQueueRestoreKeepsOriginalPositionAndListeningIdentity() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value && fixture.controller?.duration == 37_000L } }
            fixture.main { fixture.controller?.play() }
            fixture.await("successor listening session started") {
                fixture.database.queueDao().activeSnapshotOnce()?.let {
                    it.currentEntryId == fixture.entries[1].value && it.activeListeningEventId != null
                } == true
            }
            fixture.main { fixture.controller?.pause() }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(10_000))
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs == 10_000L }
            val snapshot = requireNotNull(fixture.database.queueDao().activeSnapshotOnce())
            assertNotNull(snapshot.activeListeningEventId)
            fixture.stop()
            PlaybackShutdownPersistence.awaitPending()
            fixture.owner.dispatch(PlaybackCommand.PrepareQueue(LocalId(snapshot.queueSnapshotId)))
            fixture.connect()
            fixture.await { fixture.main { fixture.controller?.playbackState == Player.STATE_READY && fixture.controller?.currentPosition == 7_000L } }
            assertEquals(10_000L, PlaybackRuntimeState.state.value.positionMs)
            assertEquals(snapshot.activeListeningEventId, fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            assertEquals(37_000L, fixture.main { fixture.controller?.duration })
        } finally { fixture.close() }
    }

    @Test fun repeatAllAndShuffleRecomputeTheActualLastTrackBoundary() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable", "readable"), smooth = true)
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[1].value } }
            fixture.owner.dispatch(PlaybackCommand.Next)
            fixture.await { fixture.main { fixture.controller?.currentMediaItem?.mediaId == fixture.entries[2].value && fixture.controller?.duration == 37_000L } }
            fixture.main { fixture.controller?.repeatMode = Player.REPEAT_MODE_ALL }
            fixture.await { fixture.main { fixture.controller?.duration == 34_000L } }
            fixture.main { fixture.controller?.repeatMode = Player.REPEAT_MODE_OFF; fixture.controller?.shuffleModeEnabled = true }
            fixture.await {
                fixture.main {
                    val controller = fixture.controller ?: return@main false
                    val expected = if (controller.nextMediaItemIndex >= 0) 34_000L else 37_000L
                    controller.duration == expected
                }
            }
            assertEquals(3_000L, fixture.main { fixture.controller?.currentMediaItem?.clippingConfiguration?.startPositionMs })
        } finally { fixture.close() }
    }

    @Test fun enablingAndRestoringInsideTailDoesNotRewindAnExistingPosition() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start(listOf("readable", "readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.main { fixture.controller?.pause() }
            fixture.owner.dispatch(PlaybackCommand.SeekTo(39_500))
            fixture.await { fixture.database.queueDao().activeSnapshotOnce()?.currentPositionMs == 39_500L }
            fixture.smoothTransitions(true)
            fixture.await { fixture.main { fixture.controller?.duration == 37_000L && fixture.controller?.currentPosition == 36_500L } }
            val snapshot = requireNotNull(fixture.database.queueDao().activeSnapshotOnce())
            fixture.stop()
            PlaybackShutdownPersistence.awaitPending()
            fixture.owner.dispatch(PlaybackCommand.PrepareQueue(LocalId(snapshot.queueSnapshotId)))
            fixture.connect()
            fixture.await { fixture.main { fixture.controller?.playbackState == Player.STATE_READY && fixture.controller?.currentPosition == 36_500L } }
            assertEquals(39_500L, PlaybackRuntimeState.state.value.positionMs)
            assertEquals(snapshot.activeListeningEventId, fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
        } finally { fixture.close() }
    }

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
        val tracks = ids.slice(6..8)
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

        suspend fun start(sources: List<String>, smooth: Boolean = false, queueType: String = "USER") {
            if (original == null) { original = settings.settings.first(); settings.update(NonSecretSettings()) }
            smoothTransitions(smooth)
            main { context.stopService(Intent(context, AutPlayPlaybackService::class.java)) }
            PlaybackShutdownPersistence.awaitPending()
            context.contentResolver.call(slowUri, "reset", null, null)
            sources.forEachIndexed { index, source ->
                database.libraryDao().upsertTrackRef(UserTrackRefEntity(tracks[index], if (source == "remote") ids[9] else null,
                    null, null, "UNRESOLVED", "Lifecycle fixture", "Fixture", null, 40_000, null,
                    "LOCAL_ONLY", null, 0, 1, 1, null))
                if (source in listOf("slow", "readable", "short")) {
                    val uri = when (source) {
                        "slow" -> slowUri.toString()
                        "short" -> "content://$testPackage.readable/audio/short"
                        else -> "content://$testPackage.readable/audio/${audioIds[index]}"
                    }
                    database.localAudioDao().upsertState(LocalAudioStateEntity(audioIds[index], tracks[index], null, null,
                        uri, false, null, null, null, null, "pcm_s16le", "wav", 128_000, 8_000, 1, 40_000,
                        "AVAILABLE", "USER_IMPORT", 640_044, null, null, 1, 1))
                }
            }
            val snapshot = activate(sources.indices.toList(), queueType)
            owner.dispatch(PlaybackCommand.StartQueue(snapshot))
            connect()
        }

        suspend fun activate(indices: List<Int>, queueType: String = "USER"): LocalId {
            val snapshot = LocalId.random()
            if (snapshots.isNotEmpty()) indices.forEach { entries[it] = LocalId.random() }
            snapshots += snapshot.value
            PlaybackPersistenceRepository(database).activateQueue(snapshot,
                indices.map { NewPlaybackQueueEntry(entries[it], LocalId(tracks[it]), "ORGANIC", "LOCAL_THEN_VAULT") },
                queueType, null, null, "GENERAL", System.currentTimeMillis())
            return snapshot
        }

        fun rememberSnapshot(snapshot: LocalId) { snapshots += snapshot.value }

        suspend fun switchProfile() {
            settings.update(NonSecretSettings(activeServerProfileId = profile,
                activeUserId = app.autplay.domain.UserId(ids[13]), deviceId = app.autplay.domain.DeviceId(ids[14])))
        }

        fun connect() {
            controller = MediaController.Builder(context, SessionToken(context,
                ComponentName(context, AutPlayPlaybackService::class.java))).buildAsync().get(10, TimeUnit.SECONDS)
        }
        suspend fun platformController(): android.media.session.MediaController {
            var token: android.media.session.MediaSession.Token? = null
            await {
                token = context.getSystemService(android.app.NotificationManager::class.java).activeNotifications
                    .firstNotNullOfOrNull { notification ->
                        androidx.core.os.BundleCompat.getParcelable(notification.notification.extras,
                            android.app.Notification.EXTRA_MEDIA_SESSION, android.media.session.MediaSession.Token::class.java)
                    }
                token != null
            }
            return android.media.session.MediaController(context, checkNotNull(token))
        }
        suspend fun stop() {
            main {
                controller?.release()
                controller = null
                // Serialize with Media3's main-thread foreground promotion (two Binder calls).
                context.stopService(Intent(context, AutPlayPlaybackService::class.java))
            }
            var absentSinceMs: Long? = null
            var lastStopMs = android.os.SystemClock.elapsedRealtime()
            var stopAttempts = 1
            await("service teardown settled") {
                val service = context.getSystemService(ActivityManager::class.java).getRunningServices(100)
                    .firstOrNull { it.service.className == AutPlayPlaybackService::class.java.name }
                val absent = service == null
                val now = android.os.SystemClock.elapsedRealtime()
                // A pending notification update can re-start this same foreground instance after
                // stopService. Reissue cleanup only for a confirmed started, promoted instance.
                if (service?.started == true && service.foreground && now - lastStopMs >= 250 && stopAttempts < 4) {
                    main { context.stopService(Intent(context, AutPlayPlaybackService::class.java)) }
                    lastStopMs = now
                    stopAttempts++
                }
                if (!absent) absentSinceMs = null
                else if (absentSinceMs == null) absentSinceMs = now
                // A late Media3 foreground self-start can briefly recreate the service. The next
                // fixture must not stop that instance before its stale-intent shutdown runs.
                absent && now - (absentSinceMs ?: now) >= 500
            }
        }
        fun providerCount(key: String): Int = context.contentResolver.call(slowUri, "status", null, null)?.getInt(key) ?: 0
        suspend fun smoothTransitions(enabled: Boolean) { settings.mutate { it.copy(smoothTrackTransitions = enabled) } }
        suspend fun <T> main(block: () -> T): T = withContext(Dispatchers.Main) { block() }
        suspend fun await(label: String = "condition", condition: suspend () -> Boolean) {
            try {
                withTimeout(10_000) { while (!condition()) delay(20) }
            } catch (timeout: kotlinx.coroutines.TimeoutCancellationException) {
                val details = main { controller?.let {
                    "entry=${it.currentMediaItemIndex} position=${it.currentPosition} duration=${it.duration} " +
                        "cutStart=${it.currentMediaItem?.clippingConfiguration?.startPositionMs} " +
                        "cutEnd=${it.currentMediaItem?.clippingConfiguration?.endPositionMs} playing=${it.isPlaying} state=${it.playbackState}"
                } }
                throw AssertionError("$label not reached: $details runtime=${PlaybackRuntimeState.state.value.positionMs}", timeout)
            }
        }

        suspend fun close() {
            remoteRelease.countDown()
            // Ordinary fixture cleanup must let the platform notification observe pause before
            // stopping a service that Media3 may still be promoting. Explicit stop() calls in
            // shutdown/restore tests retain their immediate teardown semantics.
            val hasPreparedPlayer = main { controller?.playbackState in listOf(Player.STATE_READY, Player.STATE_BUFFERING) }
            if (hasPreparedPlayer) {
                val platform = platformController()
                main { controller?.pause() }
                await("platform pause before cleanup") {
                    platform.playbackState?.state == android.media.session.PlaybackState.STATE_PAUSED
                }
                instrumentation.waitForIdleSync()
            }
            stop()
            PlaybackShutdownPersistence.awaitPending()
            original?.let { settings.update(it) }
            if (server != null) AndroidKeystoreCredentialStore(context).clear(profile)
            database.useWriterConnection { connection ->
                for (snapshot in snapshots) connection.usePrepared("DELETE FROM queue_snapshot WHERE queue_snapshot_id = ?") { it.bindText(1, snapshot); it.step() }
                for (track in tracks) {
                    connection.usePrepared("DELETE FROM local_mutation_outbox WHERE aggregate_local_id = ?") { it.bindText(1, track); it.step() }
                    for (table in listOf("user_track_preference", "listening_event", "local_audio_state", "user_track_ref")) {
                        connection.usePrepared("DELETE FROM $table WHERE local_user_track_ref_id = ?") { it.bindText(1, track); it.step() }
                    }
                }
            }
            server?.close()
        }
    }
}
