package app.autplay.application.wave

import androidx.room3.Room
import androidx.room3.useWriterConnection
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WaveCommand
import app.autplay.domain.wave.WaveRuntimeState
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class WaveRoomLifecycleTest {
    private lateinit var database: AutPlayDatabase
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var coordinator: WaveCoordinator? = null

    @Before fun setup() = runBlocking {
        database = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(ApplicationProvider.getApplicationContext<android.content.Context>())
            .setDriver(BundledSQLiteDriver()).build()
        database.libraryDao().upsertTrackRef(UserTrackRefEntity(
            TRACK, null, null, RECORDING, "RESOLVED", "Room song", "AutPlay", null, 120_000,
            1.0, "CLEAN", 1, 0, 1, 1, null, PROFILE,
        ))
    }

    @After fun cleanup() {
        coordinator?.close()
        scope.cancel()
        database.close()
    }

    @Test fun createQueueStartPauseAndCloseApplyPlaybackExactlyOnce() = runBlocking {
        val transport = Transport()
        val player = Player()
        val room = createCoordinator(transport, player)
        assertEquals("01ABCDEFGH", room.create())
        room.enqueueRecording(RECORDING)
        assertEquals("Room song", room.uiState.value.queue.single().title)
        assertEquals(1, player.prepares)
        assertTrue(room.startFirstQueued())
        assertEquals(1, player.starts)
        assertEquals(WaveRuntimeState.PLAYING, room.uiState.value.state)
        room.recover(ROOM)
        assertEquals(1, player.prepares)
        assertEquals(1, player.starts)
        assertEquals("01ABCDEFGH", room.uiState.value.roomCode)
        transport.sequence = 0
        transport.command = null
        room.recover(ROOM)
        assertEquals(WaveRuntimeState.PLAYING, room.uiState.value.state)
        assertEquals(1, player.starts)
        transport.sequence = 2
        room.pauseRoom()
        assertEquals(1, player.pauses)
        room.recover(ROOM)
        assertEquals(1, player.pauses)
        room.closeRoom()
        assertTrue(transport.closed)
        assertNull(room.uiState.value.roomId)
        assertEquals(WaveRuntimeState.CLOSED, room.uiState.value.state)
        assertNull(database.queueDao().activeSnapshotOnce())
    }

    @Test fun memberAppliesLiveInvalidationWithoutOpeningAnotherSocket() = runBlocking {
        val transport = Transport(role = "MEMBER")
        transport.queued = true
        transport.sequence = 1
        val player = Player()
        val room = createCoordinator(transport, player)
        room.joinByCode("01ABCDEFGH")
        transport.play()
        transport.event(WaveEvent("1"))
        withTimeout(5_000) { while (player.starts != 1) delay(20) }
        transport.event(WaveEvent("1"))
        room.recover(ROOM)
        assertEquals(1, player.starts)
        assertEquals(1, transport.connections)
        assertFalse(room.uiState.value.isHost)
        room.leave()
        assertTrue(transport.left)
        assertEquals(1, player.pauses)
    }

    @Test fun nextEntryIsPreparedAndPauseKeepsItsSelection() = runBlocking {
        val transport = Transport()
        val player = Player()
        val room = createCoordinator(transport, player)
        room.create()
        room.enqueueRecording(RECORDING)
        room.enqueueRecording(RECORDING)
        room.startFirstQueued()
        assertEquals(ENTRY, room.uiState.value.currentQueueEntryId)
        room.prepareNextQueued()
        assertEquals(NEXT_ENTRY, room.uiState.value.currentQueueEntryId)
        assertEquals(2, player.prepares)
        room.startFirstQueued()
        assertEquals(2, player.starts)
        room.pauseRoom()
        assertEquals(NEXT_ENTRY, room.uiState.value.currentQueueEntryId)
    }

    @Test fun exitRestoresPreviousLocalQueueAndPosition() = runBlocking {
        val previousId = UUID(0, 720).toString()
        val persistence = app.autplay.application.playback.PlaybackPersistenceRepository(database)
        persistence.activateQueue(app.autplay.domain.LocalId(previousId),
            listOf(app.autplay.application.playback.NewPlaybackQueueEntry(app.autplay.domain.LocalId(UUID(0, 721).toString()), app.autplay.domain.LocalId(TRACK), "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, PROFILE, "GENERAL", 1)
        val previous = requireNotNull(database.queueDao().activeSnapshotOnce())
        database.queueDao().upsertSnapshot(previous.copy(currentPositionMs = 1234))
        val player = Player()
        val room = createCoordinator(Transport(), player)
        room.create()
        room.enqueueRecording(RECORDING)
        room.startFirstQueued()
        room.closeRoom()
        val restored = requireNotNull(database.queueDao().activeSnapshotOnce())
        assertEquals(previousId, restored.queueSnapshotId)
        assertEquals("USER", restored.queueType)
        assertEquals(1234L, restored.currentPositionMs)
        assertEquals(previousId, player.restoredSnapshot)
        assertEquals(1, player.starts)
    }

    @Test fun exitDoesNotReplaceANewerOrdinaryQueue() = runBlocking {
        val player = Player()
        val room = createCoordinator(Transport(), player)
        room.create()
        room.enqueueRecording(RECORDING)
        room.startFirstQueued()
        val newId = app.autplay.domain.LocalId(UUID(0, 730).toString())
        player.onPause = {
            app.autplay.application.playback.PlaybackPersistenceRepository(database).activateQueue(newId,
                listOf(app.autplay.application.playback.NewPlaybackQueueEntry(app.autplay.domain.LocalId(UUID(0, 731).toString()), app.autplay.domain.LocalId(TRACK), "ORGANIC", "LOCAL_THEN_VAULT")),
                "USER", null, PROFILE, "GENERAL", 2)
        }
        room.closeRoom()
        assertEquals(newId.value, database.queueDao().activeSnapshotOnce()?.queueSnapshotId)
        assertEquals(0, player.restores)
    }

    @Test fun sameAuthorityReusesProcessOwnerAndDifferentDeviceReleasesIt() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val store = app.autplay.data.settings.applicationNonSecretSettingsStore(context)
        val original = store.settings.first()
        val binding = app.autplay.application.sync.ClientEventBinding(
            app.autplay.domain.UserId(UUID(0, 711).toString()),
            app.autplay.domain.DeviceId(UUID(0, 712).toString()),
            app.autplay.domain.ServerProfileId(PROFILE),
        )
        try {
            store.update(original.copy(activeServerProfileId = binding.serverProfileId, activeUserId = binding.userId,
                deviceId = binding.deviceId, serverBaseUrl = "http://127.0.0.1:9"))
            val first = app.autplay.AutPlayRuntime.waveCoordinator(context, binding)
            assertSame(first, app.autplay.AutPlayRuntime.waveCoordinator(context, binding))
            val other = binding.copy(deviceId = app.autplay.domain.DeviceId(UUID(0, 713).toString()))
            store.update(store.settings.first().copy(deviceId = other.deviceId))
            val second = app.autplay.AutPlayRuntime.waveCoordinator(context, other)
            assertNotSame(first, second)
            assertEquals(WaveRuntimeState.CLOSED, first.uiState.value.state)
            assertTrue(runCatching { app.autplay.AutPlayRuntime.waveCoordinator(context, binding) }.isFailure)
        } finally {
            app.autplay.AutPlayRuntime.closeWaveCoordinator()
            store.update(original)
        }
    }

    @androidx.media3.common.util.UnstableApi
    @Test fun roomStartsRealMedia3AndRestoresOrdinaryPlaybackPaused() = runBlocking {
        val fixture = app.autplay.playback.PlaybackServiceLifecycleTest.Fixture()
        var realRoom: WaveCoordinator? = null
        try {
            fixture.start(listOf("readable"))
            fixture.await { fixture.main { fixture.controller?.isPlaying == true } }
            fixture.owner.pause()
            fixture.await { fixture.main { fixture.controller?.playWhenReady == false } }
            fixture.switchProfile()
            val context = ApplicationProvider.getApplicationContext<android.content.Context>()
            val settings = app.autplay.data.settings.applicationNonSecretSettingsStore(context).settings.first()
            val track = requireNotNull(fixture.database.libraryDao().trackRef(fixture.tracks[0]))
            val profile = requireNotNull(settings.activeServerProfileId).value
            // Keep the ordinary session's captured local owner intact; room media has its own ref.
            fixture.database.libraryDao().upsertTrackRef(track.copy(localUserTrackRefId = fixture.tracks[1],
                serverProfileId = profile, serverRecordingId = RECORDING, resolutionStatus = "RESOLVED"))
            val audio = fixture.database.localAudioDao().states(fixture.tracks[0], 1).first().single()
            fixture.database.localAudioDao().upsertState(audio.copy(localAudioStateId = fixture.audioIds[1],
                localUserTrackRefId = fixture.tracks[1], contentUri = audio.contentUri.substringBeforeLast('/') + "/" + fixture.audioIds[1]))
            val transport = Transport()
            transport.profile = profile
            transport.snapshotTrack = fixture.tracks[1]
            val probe = app.autplay.playback.AndroidWaveSourceProbe(context, fixture.database)
            assertEquals(WaveAvailability.LOCAL_READABLE, probe.resolve(fixture.tracks[1]))
            var schedules = 0
            var prepared: WavePreparation? = null
            val executor = object : WavePlaybackExecutor {
                override suspend fun prepareWaveQueue(queueSnapshotId: String, queueEntryId: String): WavePreparation =
                    fixture.owner.prepareWaveQueue(queueSnapshotId, queueEntryId).also { prepared = it }
                override suspend fun schedulePreparedPlayAtElapsedRealtime(targetElapsedRealtimeMs: Long) {
                    schedules++
                    fixture.owner.schedulePreparedPlayAtElapsedRealtime(targetElapsedRealtimeMs)
                }
                override suspend fun seekPreparedTo(positionMs: Long) = fixture.owner.seekPreparedTo(positionMs)
                override suspend fun pause() = fixture.owner.pause()
                override suspend fun restoreQueueAfterRoom(snapshotId: String?) = fixture.owner.restoreQueueAfterRoom(snapshotId)
            }
            val active = WaveCoordinator(fixture.database, transport, executor, probe, scope = scope)
            realRoom = active
            active.create()
            active.enqueueRecording(RECORDING)
            fixture.rememberSnapshot(app.autplay.domain.LocalId(requireNotNull(fixture.database.queueDao().activeSnapshotOnce()).queueSnapshotId))
            assertTrue(active.startFirstQueued())
            assertEquals("room=${active.uiState.value.state} preparation=$prepared", 1, schedules)
            fixture.await("scheduled room audio plays") { fixture.main { fixture.controller?.isPlaying == true } }
            active.pauseRoom()
            fixture.await("room audio pauses") { fixture.main { fixture.controller?.playWhenReady == false } }
            active.closeRoom()
            fixture.await("ordinary queue restored paused") { fixture.main {
                fixture.controller?.currentMediaItem?.mediaId == fixture.entries[0].value && fixture.controller?.playWhenReady == false
            } }
            assertEquals("USER", fixture.database.queueDao().activeSnapshotOnce()?.queueType)
        } finally {
            realRoom?.disconnectLocally()
            fixture.database.useWriterConnection { connection ->
                connection.usePrepared("DELETE FROM wave_room WHERE room_id = ?") { it.bindText(1, ROOM); it.step() }
            }
            fixture.close()
        }
    }

    @Test fun snapshotReturningAfterExitCannotResurrectRoomOrPlayback() = runBlocking {
        val transport = Transport()
        val player = Player()
        val room = createCoordinator(transport, player)
        room.create()
        transport.blockSnapshot = CompletableDeferred()
        val refresh = launch(Dispatchers.IO) { room.recover(ROOM) }
        withTimeout(5_000) { transport.snapshotEntered.await() }
        room.close()
        transport.blockSnapshot!!.complete(Unit)
        refresh.join()
        assertNull(room.uiState.value.roomId)
        assertEquals(0, player.starts)
    }

    @Test fun unavailableRecordingNeverSchedulesPlayback() = runBlocking {
        val transport = Transport()
        transport.queued = true
        transport.sequence = 1
        val room = WaveCoordinator(database, transport, Player(), WaveSourceProbe { WaveAvailability.UNAVAILABLE }, scope = scope)
        coordinator = room
        transport.snapshotTrack = null
        transport.recording = UUID.randomUUID().toString()
        transport.play()
        room.joinByCode("01ABCDEFGH")
        assertEquals(WaveRuntimeState.DEGRADED, room.uiState.value.state)
        assertFalse(room.uiState.value.queue.single().available)
    }

    private fun createCoordinator(transport: Transport, player: Player) = WaveCoordinator(
        database, transport, player, WaveSourceProbe { WaveAvailability.LOCAL_READABLE }, scope = scope,
    ).also { coordinator = it }

    private class Player : WavePlaybackExecutor {
        @Volatile var prepares = 0
        @Volatile var starts = 0
        @Volatile var pauses = 0
        var restoredSnapshot: String? = null
        var restores = 0
        var onPause: (suspend () -> Unit)? = null
        override suspend fun prepareWaveQueue(queueSnapshotId: String, queueEntryId: String): WavePreparation {
            prepares++
            return WavePreparation(true, WaveAvailability.LOCAL_READABLE, 5_000)
        }
        override suspend fun schedulePreparedPlayAtElapsedRealtime(targetElapsedRealtimeMs: Long) { starts++ }
        override suspend fun pause() { pauses++; onPause?.invoke() }
        override suspend fun restoreQueueAfterRoom(snapshotId: String?) { restores++; restoredSnapshot = snapshotId }
    }

    private class Transport(val role: String = "HOST") : WaveTransport {
        var profile = PROFILE
        var sequence = 0L
        var queued = false
        var entryCount = 0
        var command: WaveCommand? = null
        var snapshotTrack: String? = TRACK
        var recording = RECORDING
        var connections = 0
        var closed = false
        var left = false
        lateinit var event: (WaveEvent) -> Unit
        var blockSnapshot: CompletableDeferred<Unit>? = null
        val snapshotEntered = CompletableDeferred<Unit>()

        private fun state() = WaveSnapshot(ROOM, profile, "1", if (queued) 2 else 1, role, "OPEN", sequence,
            if (queued) (0 until entryCount.coerceAtLeast(1)).map { WaveSnapshotEntry(if (it == 0) ENTRY else NEXT_ENTRY, recording, it.toLong(), snapshotTrack, true) } else emptyList(), emptyMap(),
            playbackCommand = command)
        override suspend fun create(allowUserIds: List<String>): WaveSnapshot = state().copy(roomCode = "01ABCDEFGH")
        override suspend fun joinByCode(code: String): WaveSnapshot = state()
        override suspend fun snapshot(roomId: String): WaveSnapshot {
            blockSnapshot?.let { snapshotEntered.complete(Unit); it.await() }
            return state()
        }
        override fun connect(roomId: String, afterSequence: Long, roomEpoch: String, onEvent: (WaveEvent) -> Unit, onFailure: () -> Unit): AutoCloseable {
            connections++; event = onEvent; return AutoCloseable {}
        }
        override suspend fun hostCommand(roomId: String, command: WaveCommand, queueVersion: Long) {
            sequence = command.sequence
            when (command.kind) {
                "QUEUE" -> { queued = true; entryCount++ }
                "SKIP" -> this.command = command.copy(payload = "{\"queue_entry_id\":\"$NEXT_ENTRY\"}")
                "PAUSE" -> this.command = command.copy(payload = this.command?.payload.orEmpty())
                else -> this.command = command
            }
        }
        fun play() {
            sequence++
            val entryId = if (command?.payload?.contains(NEXT_ENTRY) == true) NEXT_ENTRY else ENTRY
            command = WaveCommand(sequence, "PLAY", "{\"queue_entry_id\":\"$entryId\",\"effective_at\":\"${Instant.now().plusSeconds(2)}\"}")
        }
        override suspend fun start(roomId: String, queueEntryId: String, recordingId: String, queueVersion: Long, expectedSequence: Long): Boolean { play(); return true }
        override suspend fun clock(): WaveClockSample {
            val now = System.currentTimeMillis(); return WaveClockSample(now, now, now, now)
        }
        override suspend fun close(roomId: String) { closed = true }
        override suspend fun leave(roomId: String) { left = true }
    }

    private companion object {
        val ROOM = UUID(0, 701).toString()
        val PROFILE = UUID(0, 702).toString()
        val TRACK = UUID(0, 703).toString()
        val RECORDING = UUID(0, 704).toString()
        val ENTRY = UUID(0, 705).toString()
        val NEXT_ENTRY = UUID(0, 706).toString()
    }
}
