package app.autplay.application.wave

import android.os.SystemClock
import app.autplay.data.local.AutPlayDatabase
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.data.local.entity.WavePreflightEntity
import app.autplay.data.local.entity.WaveQueueProjectionEntity
import app.autplay.data.local.entity.WaveRoomEntity
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
)
data class WaveSnapshotEntry(
    val queueEntryId: String,
    val serverRecordingId: String,
    val position: Long,
    val localTrackRefId: String?,
    val ready: Boolean,
)
data class WaveEvent(val epoch: String, val command: WaveCommand)
data class WaveUiState(val roomId: String? = null, val state: WaveRuntimeState = WaveRuntimeState.IDLE, val isHost: Boolean = false, val message: String? = null)

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
) {
    private val mutableUiState = kotlinx.coroutines.flow.MutableStateFlow(WaveUiState())
    val uiState: StateFlow<WaveUiState> = mutableUiState.asStateFlow()
    private var connection: AutoCloseable? = null
    private var generation = 0L
    private var serverClockOffsetMs = 0L
    private val clockEstimator = ServerClockEstimator()
    private val playbackPersistence = PlaybackPersistenceRepository(database)

    suspend fun join(roomId: String) {
        val activeGeneration = ++generation
        recover(roomId)
        connection?.close()
        val cached = database.waveDao().room(roomId) ?: return
        connection = runWaveTransportCall {
            transport.connect(
                roomId,
                cached.lastSequence,
                cached.roomEpoch,
                { event -> onEvent(roomId, activeGeneration, event) },
            ) { reconnect(roomId, activeGeneration) }
        }
    }
    suspend fun create(allowUserIds: List<String> = emptyList()): String? {
        val snapshot = runWaveTransportCall { transport.create(allowUserIds) }
        applySnapshot(snapshot)
        join(snapshot.roomId)
        return snapshot.roomCode
    }
    suspend fun joinByCode(code: String) {
        applySnapshot(runWaveTransportCall { transport.joinByCode(code) })
        join(requireNotNull(uiState.value.roomId))
    }
    suspend fun leave() {
        val roomId = uiState.value.roomId ?: return
        runWaveTransportCall { transport.leave(roomId) }
        close()
    }
    suspend fun closeRoom() {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        runWaveTransportCall { transport.close(roomId) }
        close()
    }
    suspend fun transferHost(targetDeviceId: String) {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        runWaveTransportCall { transport.transferHost(roomId, targetDeviceId) }
        recover(roomId)
    }
    suspend fun submitPreflight(availability: Map<String, WaveAvailability>, finalReady: Boolean) {
        val roomId = uiState.value.roomId ?: return
        val room = database.waveDao().room(roomId) ?: return
        val queue = database.waveDao().queue(roomId, room.lastSequence, 100).associateBy { it.queueEntryId }
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
        val room = database.waveDao().room(roomId) ?: return
        runWaveTransportCall { transport.hostCommand(roomId, command, room.queueVersion) }
    }
    /** UI-safe host pause; starting requires an authoritative queued recording. */
    suspend fun pauseRoom() {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return
        val room = database.waveDao().room(roomId) ?: return
        hostCommand(WaveCommand(room.lastSequence + 1, "PAUSE"))
    }
    suspend fun enqueueRecording(recordingId: String) {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        java.util.UUID.fromString(recordingId)
        val roomId = uiState.value.roomId ?: return
        val room = database.waveDao().room(roomId) ?: return
        hostCommand(WaveCommand(room.lastSequence + 1, "QUEUE", recordingId))
        recover(roomId)
    }
    suspend fun startFirstQueued(): Boolean {
        require(uiState.value.isHost) { "WAVE_HOST_REQUIRED" }
        val roomId = uiState.value.roomId ?: return false
        val room = database.waveDao().room(roomId) ?: return false
        refreshClock(roomId, room.lastSequence)
        val current = database.waveDao().queue(roomId, room.lastSequence, 100)
            .minByOrNull { it.position } ?: return false
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
    suspend fun submitTiming(report: WaveTimingReport) {
        val roomId = uiState.value.roomId ?: return
        serverClockOffsetMs = report.offsetMs
        runWaveTransportCall { transport.timing(roomId, report) }
    }
    suspend fun recover(roomId: String) {
        try { applySnapshot(runWaveTransportCall { transport.snapshot(roomId) }) }
        catch (_: SecurityException) { mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED, message = "WAVE_AUTH_REQUIRED") }
        catch (_: Exception) { mutableUiState.value = mutableUiState.value.copy(state = WaveRuntimeState.DEGRADED, message = "WAVE_SERVER_UNAVAILABLE") }
    }
    private fun onEvent(expectedRoomId: String, expectedGeneration: Long, event: WaveEvent) {
        if (!isCurrentWaveCallback(uiState.value.roomId, generation, expectedRoomId, expectedGeneration)) return
        scope.launch {
            if (!isCurrentWaveCallback(uiState.value.roomId, generation, expectedRoomId, expectedGeneration)) return@launch
            val roomId = expectedRoomId
            val room = database.waveDao().room(roomId) ?: return@launch
            if (event.epoch != room.roomEpoch) {
                recover(roomId)
                return@launch
            }
            when (WaveSequenceRecovery.accept(room.lastSequence, event.command)) {
                is CommandAcceptance.Applied -> {
                    database.waveDao().advanceSequence(roomId, event.command.sequence, System.currentTimeMillis())
                    when (event.command.kind) {
                        "PLAY", "SCHEDULED_PLAY" -> executeScheduledPlay(
                            roomId,
                            event.command.payload,
                        )
                        "PAUSE" -> playback?.pause()
                    }
                }
                CommandAcceptance.Duplicate -> Unit
                CommandAcceptance.CatchUpRequired -> recover(roomId)
            }
        }
    }
    private suspend fun executeScheduledPlay(roomId: String, payload: String) {
        val value = runCatching { Json.parseToJsonElement(payload).jsonObject }.getOrNull()
            ?: return
        val queueEntryId = value["queue_entry_id"]?.jsonPrimitive?.contentOrNull ?: return
        val room = database.waveDao().room(roomId) ?: return
        val projection = database.waveDao().queue(roomId, room.lastSequence, 100)
            .firstOrNull { it.queueEntryId == queueEntryId } ?: return
        val trackRefId = projection.localUserTrackRefId ?: return
        val snapshotId = activateWaveEntry(
            roomId,
            queueEntryId,
            trackRefId,
            room.serverProfileId,
        )
        playback?.prepareWaveQueue(snapshotId, queueEntryId)
        scheduledElapsedRealtime(payload)?.let {
            playback?.schedulePreparedPlayAtElapsedRealtime(it)
        }
    }
    private suspend fun activateWaveEntry(
        roomId: String,
        queueEntryId: String,
        trackRefId: String,
        serverProfileId: String,
    ): String {
        val snapshotId = java.util.UUID.nameUUIDFromBytes("wave:$roomId".toByteArray()).toString()
        playbackPersistence.activateQueue(
            snapshotId = LocalId(snapshotId),
            entries = listOf(
                NewPlaybackQueueEntry(
                    queueEntryId = LocalId(queueEntryId),
                    trackRefId = LocalId(trackRefId),
                    sourceOrigin = "WAVE",
                    sourceAudioPolicy = "PINNED",
                ),
            ),
            queueType = "WAVE",
            sourceContextId = roomId,
            serverProfileId = serverProfileId,
            listeningContext = "GENERAL",
            nowMs = System.currentTimeMillis(),
        )
        return snapshotId
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
        if (expectedGeneration != generation) return
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
    private suspend fun applySnapshot(snapshot: WaveSnapshot) {
        require(snapshot.entries.size <= 100) { "WAVE_SNAPSHOT_TOO_LARGE" }
        val resolvedEntries = snapshot.entries.map { entry ->
            val localTrackRefId = entry.localTrackRefId
                ?: database.libraryDao()
                    .trackRefByRecording(snapshot.profileId, entry.serverRecordingId)
                    ?.localUserTrackRefId
            entry to localTrackRefId
        }
        val entriesById = resolvedEntries.associateBy { it.first.queueEntryId }
        val nowMs = System.currentTimeMillis()
        database.waveDao().replaceSnapshot(
            WaveRoomEntity(
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
                WavePreflightEntity(
                    snapshot.roomId,
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
                WaveQueueProjectionEntity(
                    snapshot.roomId,
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
        runMediaPreflight(resolvedSnapshot)
        prefetch?.prefetch(resolvedSnapshot, prefetchMode(), unmetered(), nowMs)
        runCatching { refreshClock(snapshot.roomId, snapshot.sequence) }
        mutableUiState.value = WaveUiState(snapshot.roomId, if (snapshot.state == "CLOSED") WaveRuntimeState.CLOSED else WaveRuntimeState.PREFLIGHT, snapshot.role == "HOST", null)
    }

    private suspend fun refreshClock(roomId: String, commandSequence: Long) {
        repeat(INITIAL_CLOCK_SAMPLES) {
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

    private suspend fun runMediaPreflight(snapshot: WaveSnapshot) {
        val candidates = snapshot.entries.sortedBy { it.position }.take(4)
        if (candidates.isEmpty()) return
        val availability = linkedMapOf<String, WaveAvailability>()
        for (entry in candidates) {
            availability[entry.queueEntryId] = entry.localTrackRefId?.let { trackRefId ->
                sourceProbe?.resolve(trackRefId)
            } ?: WaveAvailability.UNAVAILABLE
        }
        var currentReady = false
        val current = candidates.first()
        val currentTrackRefId = current.localTrackRefId
        if (currentTrackRefId != null && playback != null) {
            val queueSnapshotId = activateWaveEntry(
                snapshot.roomId,
                current.queueEntryId,
                currentTrackRefId,
                snapshot.profileId,
            )
            val prepared = playback.prepareWaveQueue(queueSnapshotId, current.queueEntryId)
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
    }
    fun close() { generation++; connection?.close(); connection = null; mutableUiState.value = WaveUiState(state = WaveRuntimeState.CLOSED, message = "WAVE_CLOSED") }

    private companion object { const val INITIAL_CLOCK_SAMPLES = 7 }
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
