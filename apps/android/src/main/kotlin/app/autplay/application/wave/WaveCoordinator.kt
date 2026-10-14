package app.autplay.application.wave

import android.os.SystemClock
import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.application.guestroom.GuestInvitationReceipt
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.domain.wave.CommandAcceptance
import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WaveCommand
import app.autplay.domain.wave.WavePrefetchMode
import app.autplay.domain.wave.WaveSequenceRecovery
import app.autplay.domain.wave.WaveRuntimeState
import app.autplay.domain.wave.ServerClockEstimator
import app.autplay.domain.LocalId
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Narrow transport port: REST is authoritative; WS is only a prompt to recover or apply a command. */
interface WaveTransport {
    suspend fun create(allowUserIds: List<String>): WaveSnapshot =
        throw UnsupportedOperationException("WAVE_CREATE_UNAVAILABLE")
    suspend fun snapshot(roomId: String): WaveSnapshot
    fun connect(
        roomId: String,
        afterSequence: Long,
        roomEpoch: String,
        onEvent: (WaveEvent) -> Unit,
        onFailure: () -> Unit,
    ): AutoCloseable
    suspend fun joinByCode(code: String): WaveSnapshot = throw UnsupportedOperationException("WAVE_JOIN_UNAVAILABLE")
    suspend fun issueGuestDocument(roomId: String): GuestInvitationReceipt =
        throw UnsupportedOperationException("GUEST_INVITATION_UNAVAILABLE")
    suspend fun leave(roomId: String) = Unit
    suspend fun close(roomId: String) = Unit
    suspend fun transferHost(roomId: String, targetDeviceId: String) = Unit
    suspend fun hostCommand(roomId: String, command: WaveCommand, queueVersion: Long) = Unit
    suspend fun clock(): WaveClockSample = throw UnsupportedOperationException("WAVE_CLOCK_UNAVAILABLE")
    suspend fun start(
        roomId: String,
        queueEntryId: String,
        recordingId: String,
        queueVersion: Long,
        expectedSequence: Long,
    ): Boolean = throw UnsupportedOperationException("WAVE_START_UNAVAILABLE")
    suspend fun preflight(roomId: String, reports: List<WavePreflightReport>) = Unit
    suspend fun timing(roomId: String, report: WaveTimingReport) = Unit
}

data class WavePreflightReport(
    val queueEntryId: String,
    val recordingId: String,
    val queueVersion: Long,
    val availability: WaveAvailability,
    val finalReady: Boolean,
)

data class WaveTimingReport(
    val commandSequence: Long,
    val rttMs: Long,
    val offsetMs: Long,
    val uncertaintyMs: Long,
    val commandLagMs: Long? = null,
    val startSkewMs: Long? = null,
    val driftMs: Long? = null,
)

data class WaveClockSample(
    val clientSentMs: Long,
    val serverReceivedMs: Long,
    val serverSentMs: Long,
    val clientReceivedMs: Long,
)

data class WaveSnapshot(
    val roomId: String,
    val profileId: String,
    val roomEpoch: String,
    val queueVersion: Long,
    val role: String,
    val state: String,
    val sequence: Long,
    val entries: List<WaveSnapshotEntry>,
    val preflight: Map<String, WaveAvailability>,
    val roomCode: String? = null,
    val hostTransferTargets: List<WaveHostTransferTarget> = emptyList(),
    val playbackCommand: WaveCommand? = null,
)
data class WaveHostTransferTarget(val deviceId: String, val deviceName: String)
data class WaveSnapshotEntry(
    val queueEntryId: String,
    val serverRecordingId: String,
    val position: Long,
    val localTrackRefId: String?,
    val ready: Boolean,
)
/** A null command requests an authoritative refresh without disconnecting the live socket. */
data class WaveEvent(val epoch: String, val command: WaveCommand? = null)
data class WaveQueueItem(
    val queueEntryId: String,
    val title: String?,
    val artist: String?,
    val available: Boolean,
    val checked: Boolean = true,
)
data class WaveUiState(
    val roomId: String? = null,
    val state: WaveRuntimeState = WaveRuntimeState.IDLE,
    val isHost: Boolean = false,
    val message: String? = null,
    val hostTransferTargets: List<WaveHostTransferTarget> = emptyList(),
    val roomCode: String? = null,
    val queue: List<WaveQueueItem> = emptyList(),
    val currentQueueEntryId: String? = null,
)

/** REST/Room coordinator; playback and proactive bytes cross only the existing P08 owner ports. */
class WaveCoordinator(
    private val database: AutPlayDatabase, private val transport: WaveTransport,
    private val playback: WavePlaybackExecutor? = null,
    private val sourceProbe: WaveSourceProbe? = null,
    private val prefetch: WavePrefetchExecutor? = null,
    private val prefetchMode: suspend () -> WavePrefetchMode = { WavePrefetchMode.NEXT },
    private val unmetered: () -> Boolean = { false },
    private val scope: kotlinx.coroutines.CoroutineScope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO),
    private val wait: suspend (Long) -> Unit = { kotlinx.coroutines.delay(it) },
    private val projectionStore: WaveProjectionStore = RoomWaveProjectionStore(database),
    private val playbackSnapshotNamespace: String = "wave",
    private val playbackQueueType: String = "WAVE",
) {
    init {
        require(playbackSnapshotNamespace.isNotBlank())
        require(playbackQueueType.matches(Regex("^[A-Z][A-Z0-9_]{0,99}$")))
    }
    private val mutableUiState = kotlinx.coroutines.flow.MutableStateFlow(WaveUiState())
    val uiState: StateFlow<WaveUiState> = mutableUiState.asStateFlow()
    private var connection: AutoCloseable? = null
    private val generationCounter = java.util.concurrent.atomic.AtomicLong()
    private val generation: Long get() = generationCounter.get()
    private val snapshotMutex = Mutex()
    private var heartbeat: Job? = null
    private var preparedEntryId: String? = null
    private var preparedRoomId: String? = null
    private var preparedPlaybackEntryId: String? = null
    private var preparation: WavePreparation? = null
    private var appliedPlaybackSequence = 0L
    private var prefetchedKey: Triple<String, Long, String?>? = null
    private var previousOrdinarySnapshotId: String? = null
    private var roomPlaybackSnapshotId: String? = null
    private var serverClockOffsetMs = 0L
    private var clockValidUntilElapsedRealtimeMs = 0L
    private val clockEstimator = ServerClockEstimator()
    private val playbackPersistence = PlaybackPersistenceRepository(database)

    suspend fun join(roomId: String) {
        val activeGeneration = generationCounter.incrementAndGet()
        heartbeat?.cancel()
        connection?.close()
        connection = null
        recover(roomId)
        if (activeGeneration != generation || uiState.value.roomId != roomId) return
        val cached = projectionStore.room(roomId) ?: return
        connection = runWaveTransportCall {
            transport.connect(
                roomId,
                cached.lastSequence,
                cached.roomEpoch,
                { event -> onEvent(roomId, activeGeneration, event) },
            ) { reconnect(roomId, activeGeneration) }
        }
        heartbeat = scope.launch {
            while (isCurrentWaveCallback(uiState.value.roomId, generation, roomId, activeGeneration)) {
                wait(10_000)
                if (isCurrentWaveCallback(uiState.value.roomId, generation, roomId, activeGeneration)) recover(roomId)
            }
        }
    }
    suspend fun create(allowUserIds: List<String> = emptyList()): String? {
        check(uiState.value.roomId == null) { "WAVE_ACTIVE_ROOM_REQUIRED_EXIT" }
        val snapshot = runWaveTransportCall { transport.create(allowUserIds) }
        applySnapshot(snapshot)
        join(snapshot.roomId)
        return snapshot.roomCode
    }
    suspend fun issueGuestDocument(): GuestInvitationReceipt {
        val roomId = uiState.value.roomId ?: throw IllegalStateException("WAVE_ROOM_REQUIRED")
        check(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        return runWaveTransportCall { transport.issueGuestDocument(roomId) }
    }
    suspend fun joinByCode(code: String) {
        check(uiState.value.roomId == null) { "WAVE_ACTIVE_ROOM_REQUIRED_EXIT" }
        applySnapshot(runWaveTransportCall { transport.joinByCode(code) })
        join(requireNotNull(uiState.value.roomId))
    }
    suspend fun leave() {
        val roomId = uiState.value.roomId ?: return
        runWaveTransportCall { transport.leave(roomId) }
        disconnectLocally()
    }
    suspend fun closeRoom() {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        runWaveTransportCall { transport.close(roomId) }
        disconnectLocally()
    }
    suspend fun transferHost(targetDeviceId: String) {
        val current = uiState.value
        require(current.isHost) { "WAVE_HOST_REQUIRED" }
        require(current.hostTransferTargets.any { it.deviceId == targetDeviceId }) { "WAVE_TRANSFER_TARGET_STALE" }
        val roomId = current.roomId ?: return
        runWaveTransportCall { transport.transferHost(roomId, targetDeviceId) }
        applySnapshot(runWaveTransportCall { transport.snapshot(roomId) })
    }
    suspend fun submitPreflight(availability: Map<String, WaveAvailability>, finalReady: Boolean) {
        val roomId = uiState.value.roomId ?: return
        val room = projectionStore.room(roomId) ?: return
        val queue = projectionStore.queue(roomId, room.lastSequence, 100).associateBy { it.queueEntryId }
        val reports = availability.mapNotNull { (entryId, value) ->
            val entry = queue[entryId] ?: return@mapNotNull null
            WavePreflightReport(
                entryId,
                entry.serverRecordingId,
                room.queueVersion,
                value,
                finalReady,
            )
        }
        runWaveTransportCall { transport.preflight(roomId, reports) }
    }
    suspend fun hostCommand(command: WaveCommand) {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        val room = projectionStore.room(roomId) ?: return
        runWaveTransportCall { transport.hostCommand(roomId, command, room.queueVersion) }
    }
    /** UI-safe host pause; starting requires an authoritative queued recording. */
    suspend fun pauseRoom() {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        val room = projectionStore.room(roomId) ?: return
        hostCommand(WaveCommand(room.lastSequence + 1, "PAUSE"))
        recover(roomId)
    }
    suspend fun enqueueRecording(recordingId: String) {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        java.util.UUID.fromString(recordingId)
        val roomId = uiState.value.roomId ?: return
        val room = projectionStore.room(roomId) ?: return
        hostCommand(WaveCommand(room.lastSequence + 1, "QUEUE", recordingId))
        recover(roomId)
    }
    suspend fun startFirstQueued(): Boolean {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return false
        val room = projectionStore.room(roomId) ?: return false
        refreshClock(roomId, room.lastSequence)
        val queue = projectionStore.queue(roomId, room.lastSequence, 100).sortedBy { it.position }
        val current = queue.firstOrNull { it.queueEntryId == uiState.value.currentQueueEntryId }
            ?: queue.firstOrNull() ?: return false
        val started = runWaveTransportCall {
            transport.start(
                roomId,
                current.queueEntryId,
                current.serverRecordingId,
                room.queueVersion,
                room.lastSequence,
            )
        }
        recover(roomId)
        return started
    }
    suspend fun prepareNextQueued() {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        val room = projectionStore.room(roomId) ?: return
        hostCommand(WaveCommand(room.lastSequence + 1, "SKIP"))
        recover(roomId)
    }
    suspend fun submitTiming(report: WaveTimingReport) {
        val roomId = uiState.value.roomId ?: return
        serverClockOffsetMs = report.offsetMs
        runWaveTransportCall { transport.timing(roomId, report) }
    }
    suspend fun recover(roomId: String) {
        val expectedGeneration = generation
        try { applySnapshot(runWaveTransportCall { transport.snapshot(roomId) }, expectedGeneration) }
        catch (error: CancellationException) { throw error }
        catch (_: WaveRoomUnavailableException) {
            if (expectedGeneration == generation) disconnectLocally()
        }
        catch (_: SecurityException) {
            if (expectedGeneration == generation) {
                if (database.queueDao().activeSnapshotOnce()?.queueSnapshotId == roomPlaybackSnapshotId) playback?.pause()
                mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED, message = "WAVE_AUTH_REQUIRED")
            }
        }
        catch (_: Exception) {
            if (expectedGeneration == generation) mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED, message = "WAVE_SERVER_UNAVAILABLE")
        }
    }
    private fun onEvent(expectedRoomId: String, expectedGeneration: Long, event: WaveEvent) {
        if (!isCurrentWaveCallback(uiState.value.roomId, generation, expectedRoomId, expectedGeneration)) return
        scope.launch {
            if (!isCurrentWaveCallback(uiState.value.roomId, generation, expectedRoomId, expectedGeneration)) return@launch
            val roomId = expectedRoomId
            val command = event.command
            if (command == null) { recover(roomId); return@launch }
            val room = projectionStore.room(roomId) ?: return@launch
            if (event.epoch != room.roomEpoch) {
                recover(roomId)
                return@launch
            }
            when (WaveSequenceRecovery.accept(room.lastSequence, command)) {
                is CommandAcceptance.Applied -> {
                    // Recover the queue and latest playback intent together; do not advance the
                    // projection cursor before the associated media command has been applied.
                    recover(roomId)
                }
                CommandAcceptance.Duplicate -> Unit
                CommandAcceptance.CatchUpRequired -> recover(roomId)
            }
        }
    }
    private suspend fun executeScheduledPlay(roomId: String, command: WaveCommand): Boolean {
        val payload = command.payload
        val value = runCatching { Json.parseToJsonElement(payload).jsonObject }.getOrNull()
            ?: return false
        val queueEntryId = value["queue_entry_id"]?.jsonPrimitive?.contentOrNull ?: return false
        val room = projectionStore.room(roomId) ?: return false
        val projection = projectionStore.queue(roomId, room.lastSequence, 100)
            .firstOrNull { it.queueEntryId == queueEntryId } ?: return false
        val trackRefId = projection.localTrackRefId ?: return false
        val executor = playback ?: return false
        if (preparedRoomId != roomId || preparedEntryId != queueEntryId) {
            val (snapshotId, playbackEntryId) = activateWaveEntry(roomId, queueEntryId, trackRefId, room.profileId)
            preparation = executor.prepareWaveQueue(snapshotId, playbackEntryId)
            preparedPlaybackEntryId = playbackEntryId
            preparedRoomId = roomId
            preparedEntryId = queueEntryId
        }
        if (preparation?.ready != true) return false
        val target = scheduledElapsedRealtime(payload) ?: return false
        // A late join resumes at the room's elapsed position rather than restarting the track.
        executor.seekPreparedTo((SystemClock.elapsedRealtime() - target).coerceAtLeast(0))
        refreshPlaybackAnchor(roomId, command, target, startPlayback = true)
        return true
    }
    private suspend fun refreshPlaybackAnchor(roomId: String, command: WaveCommand, target: Long? = scheduledElapsedRealtime(command.payload), startPlayback: Boolean = false) {
        if (preparedRoomId != roomId || target == null) return
        val snapshotId = roomPlaybackSnapshotId ?: return
        val entryId = preparedPlaybackEntryId ?: return
        playback?.synchronizePreparedPlayback(WavePlaybackAnchor(
            snapshotId, entryId, command.sequence, target, clockValidUntilElapsedRealtimeMs,
        ), startPlayback)
    }
    private suspend fun activateWaveEntry(
        roomId: String,
        queueEntryId: String,
        trackRefId: String,
        serverProfileId: String,
    ): Pair<String, String> {
        val namespace = "$playbackSnapshotNamespace:$roomId:$queueEntryId:$trackRefId"
        val snapshotId = java.util.UUID.nameUUIDFromBytes(
            "$namespace:snapshot".toByteArray(),
        ).toString()
        val playbackEntryId = java.util.UUID.nameUUIDFromBytes("$namespace:entry".toByteArray()).toString()
        if (roomPlaybackSnapshotId == null) {
            previousOrdinarySnapshotId = database.queueDao().activeSnapshotOnce()
                ?.takeIf { it.queueType in setOf("USER", "LIBRARY", "SEARCH", "PLAYLIST") }
                ?.queueSnapshotId
        }
        val existing = database.queueDao().snapshot(snapshotId)
        if (existing != null) {
            database.withWriteTransaction {
                database.queueDao().deactivateOtherSnapshots(snapshotId, System.currentTimeMillis())
                database.queueDao().upsertSnapshot(existing.copy(isActive = true, activeSlot = "ACTIVE", updatedAtMs = System.currentTimeMillis()))
            }
        } else playbackPersistence.activateQueue(
            snapshotId = LocalId(snapshotId),
            entries = listOf(
                NewPlaybackQueueEntry(
                    queueEntryId = LocalId(playbackEntryId),
                    trackRefId = LocalId(trackRefId),
                    sourceOrigin = "WAVE",
                    sourceAudioPolicy = "PINNED",
                ),
            ),
            queueType = playbackQueueType,
            sourceContextId = roomId,
            serverProfileId = serverProfileId,
            listeningContext = "GENERAL",
            nowMs = System.currentTimeMillis(),
        )
        roomPlaybackSnapshotId = snapshotId
        return snapshotId to playbackEntryId
    }

    private suspend fun restoreLocalPlayback(roomId: String) {
        val ownedId = roomPlaybackSnapshotId ?: return
        val active = database.queueDao().activeSnapshotOnce() ?: return
        if (active.queueSnapshotId != ownedId || active.sourceContextId != roomId || active.queueType != playbackQueueType) return
        playback?.pause()
        val restored = database.withWriteTransaction {
            val current = database.queueDao().activeSnapshotOnce() ?: return@withWriteTransaction (false to null)
            if (current.queueSnapshotId != ownedId) return@withWriteTransaction (false to null)
            database.queueDao().upsertSnapshot(current.copy(isActive = false, activeSlot = null, updatedAtMs = System.currentTimeMillis()))
            val previous = previousOrdinarySnapshotId?.let { database.queueDao().snapshot(it) }
                ?.takeIf { it.queueType in setOf("USER", "LIBRARY", "SEARCH", "PLAYLIST") }
            previous?.let { database.queueDao().upsertSnapshot(it.copy(isActive = true, activeSlot = "ACTIVE", updatedAtMs = System.currentTimeMillis())) }
            true to previous?.queueSnapshotId
        }
        if (restored.first) playback?.restoreQueueAfterRoom(restored.second)
    }

    suspend fun disconnectLocally() {
        val roomId = uiState.value.roomId
        generationCounter.incrementAndGet()
        heartbeat?.cancel()
        snapshotMutex.withLock { if (roomId != null) restoreLocalPlayback(roomId) }
        close()
    }
    private fun scheduledElapsedRealtime(payload: String): Long? {
        payload.toLongOrNull()?.let { return it }
        val effectiveAt = runCatching {
            Json.parseToJsonElement(payload).jsonObject["effective_at"]
                ?.jsonPrimitive
                ?.contentOrNull
        }.getOrNull() ?: return null
        val serverEpochMs = runCatching { java.time.Instant.parse(effectiveAt).toEpochMilli() }
            .getOrNull() ?: return null
        val localEpochTarget = serverEpochMs - serverClockOffsetMs
        return SystemClock.elapsedRealtime() + (localEpochTarget - System.currentTimeMillis())
    }
    private fun reconnect(roomId: String, expectedGeneration: Long) {
        if (expectedGeneration != generation || uiState.value.state == WaveRuntimeState.REJOINING) return
        mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.REJOINING, message = "WAVE_REJOINING")
        scope.launch {
            for (delayMs in longArrayOf(500, 1_000, 2_000, 4_000, 8_000)) {
                if (expectedGeneration != generation) return@launch
                wait(delayMs)
                try { join(roomId); return@launch } catch (_: Exception) { continue }
            }
            if (expectedGeneration == generation) mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED, message = "WAVE_REJOIN_FAILED")
        }
    }
    private suspend fun applySnapshot(snapshot: WaveSnapshot, expectedGeneration: Long = generation) = snapshotMutex.withLock {
        if (expectedGeneration != generation) return@withLock
        val previous = projectionStore.room(snapshot.roomId)
        if (uiState.value.roomId == snapshot.roomId && previous != null &&
            previous.roomEpoch == snapshot.roomEpoch && snapshot.sequence < previous.lastSequence) return@withLock
        if (preparedRoomId != null && preparedRoomId != snapshot.roomId) {
            preparedEntryId = null; preparedRoomId = null; preparedPlaybackEntryId = null; preparation = null; appliedPlaybackSequence = 0
        }
        require(snapshot.entries.size <= 100) { "WAVE_SNAPSHOT_TOO_LARGE" }
        require(snapshot.playbackCommand == null || snapshot.playbackCommand.sequence <= snapshot.sequence) { "WAVE_PLAYBACK_SEQUENCE_INVALID" }
        val resolvedEntries = snapshot.entries.map { entry ->
            val localTrackRefId = entry.localTrackRefId
                ?: database.libraryDao()
                    .trackRefByRecording(snapshot.profileId, entry.serverRecordingId)
                    ?.localUserTrackRefId
            entry to localTrackRefId
        }
        val entriesById = resolvedEntries.associateBy { it.first.queueEntryId }
        val nowMs = System.currentTimeMillis()
        projectionStore.replaceSnapshot(
            StoredWaveRoom(
                snapshot.roomId,
                snapshot.profileId,
                snapshot.roomEpoch,
                snapshot.queueVersion,
                snapshot.role,
                snapshot.state,
                snapshot.sequence,
                nowMs,
            ),
            snapshot.preflight.mapNotNull { (queueEntryId, availability) ->
                val (entry, localTrackRefId) = entriesById[queueEntryId] ?: return@mapNotNull null
                StoredWavePreflight(
                    queueEntryId,
                    entry.serverRecordingId,
                    localTrackRefId,
                    snapshot.queueVersion,
                    availability.name,
                    entry.ready,
                    nowMs,
                )
            },
            resolvedEntries.map { (entry, localTrackRefId) ->
                StoredWaveQueueEntry(
                    snapshot.sequence,
                    entry.position,
                    entry.queueEntryId,
                    entry.serverRecordingId,
                    localTrackRefId,
                    entry.ready,
                )
            },
        )
        val resolvedSnapshot = snapshot.copy(
            entries = resolvedEntries.map { (entry, localTrackRefId) ->
                entry.copy(localTrackRefId = localTrackRefId)
            },
        )
        if (expectedGeneration != generation) return@withLock
        if (snapshot.state in setOf("CLOSED", "EXPIRED")) {
            restoreLocalPlayback(snapshot.roomId)
            close()
            return@withLock
        }
        if (snapshot.state == "ORPHANED") {
            playback?.pause()
            appliedPlaybackSequence = 0
            mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED)
            return@withLock
        }
        val availability = runMediaPreflight(resolvedSnapshot)
        val selectedId = selectedWaveEntryId(snapshot)
        val prefetchKey = Triple(snapshot.roomId, snapshot.queueVersion, selectedId)
        if (prefetchedKey != prefetchKey) {
            val sorted = resolvedSnapshot.entries.sortedBy { it.position }
            val index = sorted.indexOfFirst { it.queueEntryId == selectedId }.coerceAtLeast(0)
            prefetch?.prefetch(resolvedSnapshot.copy(entries = sorted.drop(index)), prefetchMode(), unmetered(), nowMs)
            prefetchedKey = prefetchKey
        }
        val clockReady = try { refreshClock(snapshot.roomId, snapshot.sequence); true }
        catch (error: CancellationException) { throw error }
        catch (_: Exception) { false }
        if (expectedGeneration != generation) return@withLock
        val command = snapshot.playbackCommand
        var runtimeState = when {
            snapshot.state in setOf("CLOSED", "EXPIRED") -> WaveRuntimeState.CLOSED
            snapshot.state == "ORPHANED" -> WaveRuntimeState.DEGRADED
            else -> WaveRuntimeState.PREFLIGHT
        }
        if (command?.kind == "PLAY" && availability[selectedId] == WaveAvailability.UNAVAILABLE) {
            playback?.pause()
            appliedPlaybackSequence = 0
            preparation = null
            runtimeState = WaveRuntimeState.DEGRADED
        } else if (command != null && command.sequence > appliedPlaybackSequence) {
            if (command.kind == "PLAY") {
                if (clockReady && executeScheduledPlay(snapshot.roomId, command)) {
                    appliedPlaybackSequence = command.sequence
                    runtimeState = WaveRuntimeState.PLAYING
                } else runtimeState = WaveRuntimeState.DEGRADED
            } else {
                playback?.pause()
                appliedPlaybackSequence = command.sequence
            }
        } else if (command?.kind == "PLAY") {
            if (clockReady && playback?.isWaveSynchronizationDegraded() != true) {
                refreshPlaybackAnchor(snapshot.roomId, command)
                runtimeState = WaveRuntimeState.PLAYING
            } else {
                playback?.pause()
                appliedPlaybackSequence = 0
                runtimeState = WaveRuntimeState.DEGRADED
            }
        }
        mutableUiState.value = WaveUiState(
            roomId = snapshot.roomId,
            state = runtimeState,
            isHost = snapshot.role == "HOST",
            message = if (command?.kind == "PAUSE") "WAVE_PAUSED" else null,
            hostTransferTargets = if (snapshot.role == "HOST") snapshot.hostTransferTargets.take(7) else emptyList(),
            roomCode = snapshot.roomCode ?: uiState.value.roomCode.takeIf { uiState.value.roomId == snapshot.roomId },
            queue = resolvedEntries.sortedBy { it.first.position }.map { (entry, localId) ->
                val track = localId?.let { database.libraryDao().trackRef(it) }
                WaveQueueItem(entry.queueEntryId, track?.rawTitle, track?.rawArtist,
                    availability[entry.queueEntryId]?.let { it != WaveAvailability.UNAVAILABLE } ?: false,
                    checked = availability.containsKey(entry.queueEntryId) || localId == null)
            },
            currentQueueEntryId = selectedId,
        )
    }

    private suspend fun refreshClock(roomId: String, commandSequence: Long) {
        repeat(if (clockEstimator.isEligible(System.currentTimeMillis())) 1 else INITIAL_CLOCK_SAMPLES) {
            val sample = runWaveTransportCall { transport.clock() }
            require(
                clockEstimator.addSample(
                    sample.clientSentMs,
                    sample.serverReceivedMs,
                    sample.serverSentMs,
                    sample.clientReceivedMs,
                ),
            ) { "WAVE_CLOCK_SAMPLE_INVALID" }
        }
        val nowMs = System.currentTimeMillis()
        require(clockEstimator.isEligible(nowMs)) { "WAVE_CLOCK_UNSTABLE" }
        serverClockOffsetMs = clockEstimator.serverNow(nowMs) - nowMs
        clockValidUntilElapsedRealtimeMs = SystemClock.elapsedRealtime() + 60_000
        runWaveTransportCall {
            transport.timing(
                roomId,
                WaveTimingReport(
                    commandSequence,
                    clockEstimator.p95Rtt(),
                    serverClockOffsetMs,
                    clockEstimator.uncertaintyMs(),
                ),
            )
        }
    }

    private suspend fun runMediaPreflight(snapshot: WaveSnapshot): Map<String, WaveAvailability> {
        val sorted = snapshot.entries.sortedBy { it.position }
        val selectedId = selectedWaveEntryId(snapshot)
        val candidates = sorted.drop(sorted.indexOfFirst { it.queueEntryId == selectedId }.coerceAtLeast(0)).take(4)
        if (candidates.isEmpty()) return emptyMap()
        val availability = linkedMapOf<String, WaveAvailability>()
        for (entry in candidates) {
            availability[entry.queueEntryId] = entry.localTrackRefId?.let { trackRefId ->
                sourceProbe?.resolve(trackRefId)
            } ?: WaveAvailability.UNAVAILABLE
        }
        var currentReady = false
        val current = candidates.first()
        val currentTrackRefId = current.localTrackRefId
        if (currentTrackRefId != null && playback != null && availability[current.queueEntryId] != WaveAvailability.UNAVAILABLE) {
            if (preparedRoomId != snapshot.roomId || preparedEntryId != current.queueEntryId || preparation?.ready != true) {
                val (queueSnapshotId, playbackEntryId) = activateWaveEntry(snapshot.roomId, current.queueEntryId, currentTrackRefId, snapshot.profileId)
                preparation = playback.prepareWaveQueue(queueSnapshotId, playbackEntryId)
                preparedPlaybackEntryId = playbackEntryId
                preparedRoomId = snapshot.roomId
                preparedEntryId = current.queueEntryId
            }
            val prepared = requireNotNull(preparation)
            availability[current.queueEntryId] = prepared.source
            currentReady = prepared.ready &&
                (prepared.source != WaveAvailability.VAULT_STREAMABLE || prepared.bufferedMs >= 3_000)
        }
        runWaveTransportCall {
            transport.preflight(
                snapshot.roomId,
                candidates.map { entry ->
                    WavePreflightReport(
                        entry.queueEntryId,
                        entry.serverRecordingId,
                        snapshot.queueVersion,
                        availability.getValue(entry.queueEntryId),
                        finalReady = entry.queueEntryId == current.queueEntryId && currentReady,
                    )
                },
            )
        }
        return availability
    }
    fun close() {
        generationCounter.incrementAndGet(); heartbeat?.cancel(); heartbeat = null
        connection?.close(); connection = null
        preparedEntryId = null; preparedRoomId = null; preparedPlaybackEntryId = null; preparation = null; appliedPlaybackSequence = 0
        prefetchedKey = null
        previousOrdinarySnapshotId = null; roomPlaybackSnapshotId = null
        mutableUiState.value = WaveUiState(state = WaveRuntimeState.CLOSED, message = "WAVE_CLOSED")
    }

    private companion object { const val INITIAL_CLOCK_SAMPLES = 7 }
}

private fun selectedWaveEntryId(snapshot: WaveSnapshot): String? {
    val selected = snapshot.playbackCommand?.payload?.let { payload ->
        runCatching { Json.parseToJsonElement(payload).jsonObject["queue_entry_id"]?.jsonPrimitive?.contentOrNull }.getOrNull()
    }
    return snapshot.entries.firstOrNull { it.queueEntryId == selected }?.queueEntryId
        ?: snapshot.entries.minByOrNull { it.position }?.queueEntryId
}

internal fun isCurrentWaveCallback(
    currentRoomId: String?,
    currentGeneration: Long,
    callbackRoomId: String,
    callbackGeneration: Long,
): Boolean = currentRoomId == callbackRoomId && currentGeneration == callbackGeneration

/** Keeps synchronous transport implementations away from Compose's main-thread coroutine. */
internal suspend fun <T> runWaveTransportCall(call: suspend () -> T): T =
    withContext(kotlinx.coroutines.Dispatchers.IO) { call() }
