package app.autplay.playback

import android.content.Intent
import androidx.core.net.toUri
import android.os.Bundle
import android.os.SystemClock
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ShuffleOrder
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import app.autplay.AutPlayRuntime
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.playback.RestoredPlaybackQueue
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Background player/session owner with bounded Room checkpoints and lazy current/next preflight. */
@UnstableApi
class AutPlayPlaybackService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val stateMutex = Mutex()
    private lateinit var player: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private lateinit var persistence: PlaybackPersistenceRepository
    private lateinit var sourceResolver: AndroidPlaybackSourceResolver
    private var restored: RestoredPlaybackQueue? = null
    private var logicalSession: LogicalListeningCheckpoint? = null
    private var observedPlaybackStartedAtMs: Long? = null
    private var shuffleSeed: Long? = null
    private var scheduledPlayJob: kotlinx.coroutines.Job? = null
    private var sleepTimerJob: kotlinx.coroutines.Job? = null
    private var sleepTimerDeadlineElapsedRealtimeMs: Long? = null
    private var stopAfterQueueEntryId: String? = null
    private var sleepTimerGeneration = 0L
    private val audioContourSink = PlaybackAudioContourSink()

    override fun onCreate() {
        super.onCreate()
        val database = AutPlayRuntime.database(applicationContext)
        persistence = PlaybackPersistenceRepository(database)
        sourceResolver = AndroidPlaybackSourceResolver(
            applicationContext,
            database,
            applicationNonSecretSettingsStore(applicationContext),
        )
        player = ExoPlayer.Builder(this, ReactivePlaybackRenderersFactory(this, audioContourSink))
            .setMediaSourceFactory(PlaybackMediaSourceFactory.create(this))
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .setUsage(C.USAGE_MEDIA)
                    .build(),
                true,
            )
            .setHandleAudioBecomingNoisy(true)
            .build()
            .also { it.addListener(PlayerListener()) }
        mediaSession = MediaSession.Builder(this, player)
            .setCallback(AutPlaySessionCallback(packageName))
            .build()
        scope.launch { restoreQueue(autoplay = false) }
        scope.launch {
            while (isActive) {
                delay(AUDIO_CONTOUR_PUBLISH_MS)
                if (player.isPlaying && PlaybackAudioContourRuntime.isObservationRequested()) {
                    PlaybackAudioContourRuntime.publish(audioContourSink.snapshot())
                } else {
                    PlaybackAudioContourRuntime.reset()
                }
            }
        }
        scope.launch {
            while (isActive) {
                delay(PERIODIC_CHECKPOINT_MS)
                stateMutex.withLock {
                    if (player.isPlaying) {
                        logicalSession?.let { checkpointCurrent(it, player.currentPosition.coerceAtLeast(0)) }
                    }
                    publishRuntimeState()
                }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = mediaSession

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action in APP_COMMAND_ACTIONS &&
            intent?.getStringExtra(PlaybackCommandAuthorization.EXTRA_PROCESS_TOKEN) !=
            PlaybackCommandAuthorization.processToken
        ) {
            return START_NOT_STICKY
        }
        when (intent?.action) {
            ACTION_START_QUEUE -> scope.launch {
                val requested = intent.getStringExtra(EXTRA_QUEUE_SNAPSHOT_ID)
                restoreQueue(autoplay = true, requiredSnapshotId = requested)
            }
            ACTION_PREPARE_QUEUE -> scope.launch {
                val requested = intent.getStringExtra(EXTRA_QUEUE_SNAPSHOT_ID)
                restoreQueue(autoplay = false, requiredSnapshotId = requested)
            }
            ACTION_REFRESH_QUEUE -> scope.launch {
                val requested = intent.getStringExtra(EXTRA_QUEUE_SNAPSHOT_ID)
                refreshQueue(requested)
            }
            ACTION_NEXT -> moveWithinOrdinaryQueue(next = true)
            ACTION_PREVIOUS -> moveWithinOrdinaryQueue(next = false)
            ACTION_RESUME -> player.play()
            ACTION_PAUSE -> player.pause()
            ACTION_STOP -> scope.launch { finalizeCurrent(); player.stop(); stopSelf() }
            ACTION_SEEK -> player.seekTo(intent.getLongExtra(EXTRA_POSITION_MS, 0).coerceAtLeast(0))
            ACTION_SET_SHUFFLE -> player.shuffleModeEnabled = intent.getBooleanExtra(EXTRA_SHUFFLE_ENABLED, false)
            ACTION_SET_REPEAT -> player.repeatMode = intent.getStringExtra(EXTRA_REPEAT_MODE).orEmpty().toMedia3RepeatMode()
            ACTION_SCHEDULED_PLAY -> {
                scheduledPlayJob?.cancel()
                // Wave timing is monotonic: wall-clock changes must not shift an accepted start.
                val delayMs = (intent.getLongExtra(EXTRA_SCHEDULED_AT_MS, 0) - SystemClock.elapsedRealtime()).coerceAtLeast(0)
                scheduledPlayJob = scope.launch { delay(delayMs); player.play() }
            }
            ACTION_SCHEDULE_SLEEP_TIMER -> scheduleSleepTimer(
                intent.getLongExtra(EXTRA_SLEEP_TIMER_DURATION_MS, 0),
            )
            ACTION_STOP_AFTER_CURRENT_ITEM -> stopAfterCurrentItem(
                intent.getStringExtra(EXTRA_EXPECTED_QUEUE_ENTRY_ID),
            )
            ACTION_CANCEL_SLEEP_TIMER -> cancelSleepTimer()
            ACTION_SET_SPEED -> player.playbackParameters = PlaybackParameters(intent.getFloatExtra(EXTRA_SPEED, 1f).coerceIn(.98f, 1.02f))
        }
        return super.onStartCommand(intent, flags, startId)
    }

    override fun onDestroy() {
        runCatching {
            val current = logicalSession
            if (current != null) {
                kotlinx.coroutines.runBlocking {
                    stateMutex.withLock {
                        checkpointCurrent(current, player.currentPosition.coerceAtLeast(0))
                    }
                }
            }
        }
        mediaSession.release()
        scheduledPlayJob?.cancel()
        sleepTimerJob?.cancel()
        sleepTimerJob = null
        ++sleepTimerGeneration
        sleepTimerDeadlineElapsedRealtimeMs = null
        stopAfterQueueEntryId = null
        player.setPauseAtEndOfMediaItems(false)
        audioContourSink.reset()
        PlaybackAudioContourRuntime.reset()
        publishRuntimeState()
        player.release()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun restoreQueue(autoplay: Boolean, requiredSnapshotId: String? = null) =
        stateMutex.withLock {
            val queue = persistence.restoreActive() ?: return@withLock
            if (requiredSnapshotId != null && queue.snapshot.queueSnapshotId != requiredSnapshotId) {
                return@withLock
            }
            val replacingQueue = restored?.snapshot?.queueSnapshotId?.let { it != queue.snapshot.queueSnapshotId } == true
            if (replacingQueue && logicalSession != null) finalizeCurrentLocked()
            restored = queue
            if (replacingQueue || !SleepTimerPolicy.allows(queue.snapshot.queueType)) {
                cancelSleepTimerLocked()
            }
            logicalSession = if (replacingQueue) persistence.recoverSession() else logicalSession ?: persistence.recoverSession()
            val placeholders = queue.entries.map { entry -> placeholder(entry.queueEntryId) }
            val index = queue.media.currentIndex.coerceIn(placeholders.indices)
            player.setMediaItems(placeholders, index, queue.media.currentPositionMs)
            resolveIndex(queue, index)
            if (index + 1 < queue.entries.size) resolveIndex(queue, index + 1)
            player.seekTo(index, queue.media.currentPositionMs)
            player.repeatMode = queue.snapshot.repeatMode.toMedia3RepeatMode()
            shuffleSeed = queue.snapshot.seed
            if (queue.snapshot.shuffleMode != "OFF") {
                val seed = shuffleSeed ?: queue.snapshot.queueSnapshotId.hashCode().toLong().also { shuffleSeed = it }
                player.setShuffleOrder(ShuffleOrder.DefaultShuffleOrder(placeholders.size, seed))
            }
            player.shuffleModeEnabled = queue.snapshot.shuffleMode != "OFF"
            player.prepare()
            if (autoplay) player.play()
            publishRuntimeState(unavailableReason = null)
        }

    /** Reloads a committed ordinary queue while preserving the active stable entry and player mode. */
    private suspend fun refreshQueue(requiredSnapshotId: String?) = stateMutex.withLock {
        val currentId = player.currentMediaItem?.mediaId ?: return@withLock
        val currentPositionMs = player.currentPosition.coerceAtLeast(0)
        val shouldPlay = player.playWhenReady
        val preservedRepeatMode = player.repeatMode
        val preservedShuffleEnabled = player.shuffleModeEnabled
        val preservedShuffleSeed = if (preservedShuffleEnabled) {
            shuffleSeed ?: System.currentTimeMillis()
        } else {
            shuffleSeed
        }
        shuffleSeed = preservedShuffleSeed
        logicalSession?.let { checkpointCurrent(it, currentPositionMs) }
        val queue = persistence.restoreActive() ?: return@withLock
        if (queue.snapshot.queueSnapshotId != requiredSnapshotId || queue.snapshot.currentEntryId != currentId) return@withLock
        restored = queue
        val placeholders = queue.entries.map { placeholder(it.queueEntryId) }
        val index = queue.entries.indexOfFirst { it.queueEntryId == currentId }
        if (index < 0) return@withLock
        player.setMediaItems(placeholders, index, currentPositionMs)
        resolveIndex(queue, index); if (index + 1 < queue.entries.size) resolveIndex(queue, index + 1)
        player.seekTo(index, currentPositionMs)
        player.repeatMode = preservedRepeatMode
        if (preservedShuffleEnabled) {
            player.setShuffleOrder(
                ShuffleOrder.DefaultShuffleOrder(placeholders.size, requireNotNull(preservedShuffleSeed)),
            )
        }
        player.shuffleModeEnabled = preservedShuffleEnabled
        player.prepare()
        if (shouldPlay) player.play()
        publishRuntimeState(unavailableReason = null)
    }

    /** Wave movement is room-authoritative; only ordinary queues may invoke local navigation. */
    private fun moveWithinOrdinaryQueue(next: Boolean) {
        scope.launch {
            stateMutex.withLock {
                if (restored?.snapshot?.queueType !in ORDINARY_QUEUE_TYPES) return@withLock
                if (next) player.seekToNextMediaItem() else player.seekToPreviousMediaItem()
            }
        }
    }

    private suspend fun resolveIndex(queue: RestoredPlaybackQueue, index: Int) {
        if (index !in queue.entries.indices) return
        val entry = queue.entries[index]
        val resolved = sourceResolver.resolve(LocalId(entry.localUserTrackRefId), System.currentTimeMillis())
        if (resolved is AndroidSourceResolution.Unavailable) {
            val reason = resolved.reason.name
            player.replaceMediaItem(index, unavailableItem(entry.queueEntryId, reason))
            if (index == player.currentMediaItemIndex) publishRuntimeState(unavailableReason = reason)
            return
        }
        resolved as AndroidSourceResolution.Available
        val track = AutPlayRuntime.database(applicationContext).libraryDao().trackRef(entry.localUserTrackRefId)
        val item = MediaItem.Builder()
            .setMediaId(entry.queueEntryId)
            .setUri(resolved.value.runtimeUri)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(track?.rawTitle ?: "Unavailable title")
                    .setArtist(track?.rawArtist)
                    .setExtras(Bundle().apply {
                        putString("queue_snapshot_id", queue.snapshot.queueSnapshotId)
                        putString("local_user_track_ref_id", entry.localUserTrackRefId)
                        putString("selected_source", resolved.value.source.name)
                    })
                    .build(),
            )
            .build()
        player.replaceMediaItem(index, item)
        if (index == player.currentMediaItemIndex) {
            publishRuntimeState(source = resolved.value.source.name, unavailableReason = null, title = track?.rawTitle)
        }
    }

    private fun placeholder(queueEntryId: String): MediaItem = MediaItem.Builder()
        .setMediaId(queueEntryId)
        .setUri("autplay-unresolved://queue/$queueEntryId".toUri())
        .build()

    private fun unavailableItem(queueEntryId: String, reason: String): MediaItem = MediaItem.Builder()
        .setMediaId(queueEntryId)
        .setUri("autplay-unavailable://$reason/$queueEntryId".toUri())
        .setMediaMetadata(MediaMetadata.Builder().setExtras(Bundle().apply {
            putString("unavailable_reason", reason)
        }).build())
        .build()

    private suspend fun ensureSession(): LogicalListeningCheckpoint? {
        logicalSession?.let { return it }
        val mediaId = player.currentMediaItem?.mediaId ?: return null
        return persistence.startSession(
            LocalId(mediaId),
            player.currentPosition.coerceAtLeast(0),
            System.currentTimeMillis(),
            sessionOwnerBinding(),
        ).also { logicalSession = it }
    }

    private suspend fun checkpointCurrent(
        current: LogicalListeningCheckpoint,
        positionMs: Long,
    ): LogicalListeningCheckpoint {
        val delta = consumeObservedDelta(continueIfPlaying = true)
        return persistence.checkpoint(
            current,
            positionMs,
            delta,
            if (player.shuffleModeEnabled) "SEEDED" else "OFF",
            player.repeatMode.fromMedia3RepeatMode(),
            shuffleSeed,
            System.currentTimeMillis(),
        ).also { logicalSession = it }
    }

    private suspend fun checkpointPlayerStateLocked() {
        val positionMs = player.currentPosition.coerceAtLeast(0)
        logicalSession?.let {
            checkpointCurrent(it, positionMs)
            return
        }
        val queue = restored ?: return
        val entryId = player.currentMediaItem?.mediaId ?: return
        persistence.selectIdleEntry(
            snapshotId = LocalId(queue.snapshot.queueSnapshotId),
            entryId = LocalId(entryId),
            positionMs = positionMs,
            shuffleMode = if (player.shuffleModeEnabled) "SEEDED" else "OFF",
            repeatMode = player.repeatMode.fromMedia3RepeatMode(),
            seed = shuffleSeed,
            nowMs = System.currentTimeMillis(),
        )
    }

    private suspend fun finalizeCurrent() {
        stateMutex.withLock {
            finalizeCurrentLocked()
        }
    }

    private suspend fun finalizeCurrentLocked() {
        val current = logicalSession ?: return
        val duration = player.duration.takeUnless { it == C.TIME_UNSET || it <= 0 }
        persistence.finalizeSession(
            current = current,
            endPositionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = duration,
            observedPlaybackDeltaMs = consumeObservedDelta(continueIfPlaying = false),
            nowMs = System.currentTimeMillis(),
        )
        logicalSession = null
        observedPlaybackStartedAtMs = null
    }

    private fun consumeObservedDelta(continueIfPlaying: Boolean): Long {
        val now = SystemClock.elapsedRealtime()
        val started = observedPlaybackStartedAtMs
        if (started == null) {
            observedPlaybackStartedAtMs = if (continueIfPlaying && player.isPlaying) now else null
            return 0
        }
        observedPlaybackStartedAtMs = if (continueIfPlaying && player.isPlaying) now else null
        return (now - started).coerceIn(0, MAX_CHECKPOINT_DELTA_MS)
    }

    private suspend fun sessionOwnerBinding(): PlaybackSessionOwnerBinding? {
        val queueProfileId = restored?.snapshot?.serverProfileId ?: return null
        val value = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val profile = value.activeServerProfileId?.takeIf { it.value == queueProfileId } ?: return null
        return PlaybackSessionOwnerBinding(
            userId = value.activeUserId?.value ?: return null,
            deviceId = value.deviceId?.value ?: return null,
            serverProfileId = profile.value,
        )
    }

    private fun publishRuntimeState(
        source: String? = PlaybackRuntimeState.state.value.source,
        unavailableReason: String? = PlaybackRuntimeState.state.value.unavailableReason,
        title: String? = player.mediaMetadata.title?.toString() ?: PlaybackRuntimeState.state.value.title,
    ) {
        val queueEntryId = player.currentMediaItem?.mediaId
        val localTrackRefId = resolveCurrentTrackRefId(
            queueEntryId,
            restored?.entries?.map { entry -> entry.queueEntryId to entry.localUserTrackRefId }.orEmpty(),
        )
        PlaybackRuntimeState.publish(
            PlaybackUiState(
                queueEntryId = queueEntryId,
                localUserTrackRefId = localTrackRefId,
                title = title,
                source = source,
                unavailableReason = unavailableReason,
                positionMs = player.currentPosition.coerceAtLeast(0),
                isPlaying = player.isPlaying,
                isPrepared = player.playbackState == Player.STATE_READY,
                bufferedMs = (player.bufferedPosition - player.currentPosition).coerceAtLeast(0),
                shuffleEnabled = player.shuffleModeEnabled,
                repeatMode = player.repeatMode.fromMedia3RepeatMode(),
                sleepTimerDeadlineElapsedRealtimeMs = sleepTimerDeadlineElapsedRealtimeMs,
                stopAfterQueueEntryId = stopAfterQueueEntryId,
            ),
        )
    }

    /**
     * Service-owned, session-local timer. It intentionally has no persistence: after process
     * death there is no trustworthy deadline or authorization context to resume from.
     */
    private fun scheduleSleepTimer(durationMs: Long) {
        if (durationMs !in SleepTimerPolicy.MIN_DURATION_MS..SleepTimerPolicy.MAX_DURATION_MS) return
        scope.launch {
            stateMutex.withLock {
                val queueType = restored?.snapshot?.queueType
                if (!SleepTimerPolicy.allows(queueType)) {
                    cancelSleepTimerLocked()
                    return@withLock
                }
                val deadline = SleepTimerPolicy.deadline(SystemClock.elapsedRealtime(), durationMs)
                val generation = ++sleepTimerGeneration
                sleepTimerJob?.cancel()
                stopAfterQueueEntryId = null
                player.setPauseAtEndOfMediaItems(false)
                sleepTimerDeadlineElapsedRealtimeMs = deadline
                publishRuntimeState()
                sleepTimerJob = scope.launch {
                    while (true) {
                        val waitMs = SleepTimerPolicy.nextDelay(deadline, SystemClock.elapsedRealtime())
                        if (waitMs <= 0L) break
                        delay(waitMs)
                    }
                    stateMutex.withLock {
                        if (generation != sleepTimerGeneration || sleepTimerDeadlineElapsedRealtimeMs != deadline) {
                            return@withLock
                        }
                        sleepTimerDeadlineElapsedRealtimeMs = null
                        sleepTimerJob = null
                        // Pause retains the Media3 queue and the durable playback checkpoint.
                        player.pause()
                        publishRuntimeState()
                    }
                }
            }
        }
    }

    private fun cancelSleepTimer() {
        scope.launch { stateMutex.withLock { cancelSleepTimerLocked() } }
    }

    private fun stopAfterCurrentItem(expectedQueueEntryId: String?) {
        scope.launch {
            stateMutex.withLock {
                val currentQueueEntryId = player.currentMediaItem?.mediaId
                when (SleepTimerPolicy.stopAfterCurrentItemDecision(
                    queueType = restored?.snapshot?.queueType,
                    expectedQueueEntryId = expectedQueueEntryId,
                    currentQueueEntryId = currentQueueEntryId,
                )) {
                    StopAfterCurrentItemDecision.CLEAR_UNSUPPORTED_QUEUE -> {
                        cancelSleepTimerLocked()
                        return@withLock
                    }
                    StopAfterCurrentItemDecision.REJECT_STALE -> return@withLock
                    StopAfterCurrentItemDecision.ARM -> Unit
                }
                ++sleepTimerGeneration
                sleepTimerJob?.cancel()
                sleepTimerJob = null
                sleepTimerDeadlineElapsedRealtimeMs = null
                stopAfterQueueEntryId = expectedQueueEntryId
                player.setPauseAtEndOfMediaItems(true)
                publishRuntimeState()
            }
        }
    }

    private fun cancelSleepTimerLocked() {
        ++sleepTimerGeneration
        sleepTimerJob?.cancel()
        sleepTimerJob = null
        val changed = sleepTimerDeadlineElapsedRealtimeMs != null || stopAfterQueueEntryId != null
        sleepTimerDeadlineElapsedRealtimeMs = null
        stopAfterQueueEntryId = null
        player.setPauseAtEndOfMediaItems(false)
        if (changed) publishRuntimeState()
    }

    private inner class PlayerListener : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            scope.launch {
                stateMutex.withLock {
                    if (isPlaying) {
                        ensureSession()
                        if (observedPlaybackStartedAtMs == null) {
                            observedPlaybackStartedAtMs = SystemClock.elapsedRealtime()
                        }
                    } else {
                        logicalSession?.let { checkpointCurrent(it, player.currentPosition.coerceAtLeast(0)) }
                    }
                    publishRuntimeState()
                }
            }
        }

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            scope.launch {
                stateMutex.withLock {
                    val current = logicalSession
                    val isSameRestore = reason == Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED &&
                        current?.queueEntryId?.value == mediaItem?.mediaId
                    if (stopAfterQueueEntryId != null && stopAfterQueueEntryId != mediaItem?.mediaId) {
                        cancelSleepTimerLocked()
                    }
                    audioContourSink.reset()
                    PlaybackAudioContourRuntime.reset()
                    if (current != null && !isSameRestore) finalizeCurrentLocked()
                    val queue = restored ?: return@withLock
                    val index = player.currentMediaItemIndex
                    resolveIndex(queue, index)
                    resolveIndex(queue, index + 1)
                    if (player.isPlaying) {
                        ensureSession()
                        observedPlaybackStartedAtMs = SystemClock.elapsedRealtime()
                    } else if (!isSameRestore && mediaItem != null) {
                        persistence.selectIdleEntry(
                            snapshotId = LocalId(queue.snapshot.queueSnapshotId),
                            entryId = LocalId(mediaItem.mediaId),
                            positionMs = player.currentPosition.coerceAtLeast(0),
                            shuffleMode = if (player.shuffleModeEnabled) "SEEDED" else "OFF",
                            repeatMode = player.repeatMode.fromMedia3RepeatMode(),
                            seed = shuffleSeed,
                            nowMs = System.currentTimeMillis(),
                        )
                    }
                    publishRuntimeState()
                }
            }
        }

        override fun onPositionDiscontinuity(
            oldPosition: Player.PositionInfo,
            newPosition: Player.PositionInfo,
            reason: Int,
        ) {
            if (reason != Player.DISCONTINUITY_REASON_SEEK) return
            scope.launch {
                stateMutex.withLock {
                    val positionMs = newPosition.positionMs.coerceAtLeast(0)
                    val current = logicalSession
                    if (current == null) {
                        checkpointPlayerStateLocked()
                    } else {
                        logicalSession = LogicalListeningSession.seek(current, positionMs)
                        checkpointCurrent(requireNotNull(logicalSession), positionMs)
                    }
                    publishRuntimeState()
                }
            }
        }

        override fun onShuffleModeEnabledChanged(shuffleModeEnabled: Boolean) {
            scope.launch {
                stateMutex.withLock {
                    if (shuffleModeEnabled && shuffleSeed == null) {
                        val size = player.mediaItemCount
                        shuffleSeed = System.currentTimeMillis()
                        player.setShuffleOrder(ShuffleOrder.DefaultShuffleOrder(size, requireNotNull(shuffleSeed)))
                    }
                    checkpointPlayerStateLocked()
                    publishRuntimeState()
                }
            }
        }

        override fun onRepeatModeChanged(repeatMode: Int) {
            scope.launch {
                stateMutex.withLock {
                    checkpointPlayerStateLocked()
                    publishRuntimeState()
                }
            }
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            if (playbackState == Player.STATE_ENDED) scope.launch { finalizeCurrent() }
            else scope.launch { stateMutex.withLock { publishRuntimeState() } }
        }

        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            if (reason != Player.PLAY_WHEN_READY_CHANGE_REASON_END_OF_MEDIA_ITEM) return
            scope.launch {
                stateMutex.withLock {
                    if (stopAfterQueueEntryId != null) cancelSleepTimerLocked()
                }
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            scope.launch {
                stateMutex.withLock {
                    logicalSession?.let { checkpointCurrent(it, player.currentPosition.coerceAtLeast(0)) }
                }
            }
        }
    }

    private fun String.toMedia3RepeatMode(): Int = when (this) {
        "ONE" -> Player.REPEAT_MODE_ONE
        "ALL" -> Player.REPEAT_MODE_ALL
        else -> Player.REPEAT_MODE_OFF
    }

    private fun Int.fromMedia3RepeatMode(): String = when (this) {
        Player.REPEAT_MODE_ONE -> "ONE"
        Player.REPEAT_MODE_ALL -> "ALL"
        else -> "OFF"
    }

    companion object {
        const val ACTION_START_QUEUE = "app.autplay.playback.START_QUEUE"
        const val ACTION_PREPARE_QUEUE = "app.autplay.playback.PREPARE_QUEUE"
        const val ACTION_REFRESH_QUEUE = "app.autplay.playback.REFRESH_QUEUE"
        const val ACTION_NEXT = "app.autplay.playback.NEXT"
        const val ACTION_PREVIOUS = "app.autplay.playback.PREVIOUS"
        const val ACTION_RESUME = "app.autplay.playback.RESUME"
        const val ACTION_PAUSE = "app.autplay.playback.PAUSE"
        const val ACTION_STOP = "app.autplay.playback.STOP"
        const val ACTION_SEEK = "app.autplay.playback.SEEK"
        const val ACTION_SET_SHUFFLE = "app.autplay.playback.SET_SHUFFLE"
        const val ACTION_SET_REPEAT = "app.autplay.playback.SET_REPEAT"
        const val ACTION_SCHEDULED_PLAY = "app.autplay.playback.SCHEDULED_PLAY"
        const val ACTION_SCHEDULE_SLEEP_TIMER = "app.autplay.playback.SCHEDULE_SLEEP_TIMER"
        const val ACTION_STOP_AFTER_CURRENT_ITEM = "app.autplay.playback.STOP_AFTER_CURRENT_ITEM"
        const val ACTION_CANCEL_SLEEP_TIMER = "app.autplay.playback.CANCEL_SLEEP_TIMER"
        const val ACTION_SET_SPEED = "app.autplay.playback.SET_SPEED"
        const val EXTRA_QUEUE_SNAPSHOT_ID = "queue_snapshot_id"
        const val EXTRA_POSITION_MS = "position_ms"
        const val EXTRA_SHUFFLE_ENABLED = "shuffle_enabled"
        const val EXTRA_REPEAT_MODE = "repeat_mode"
        const val EXTRA_SCHEDULED_AT_MS = "scheduled_at_ms"
        const val EXTRA_SLEEP_TIMER_DURATION_MS = "sleep_timer_duration_ms"
        const val EXTRA_EXPECTED_QUEUE_ENTRY_ID = "expected_queue_entry_id"
        const val EXTRA_SPEED = "speed"
        private val APP_COMMAND_ACTIONS = setOf(
            ACTION_START_QUEUE,
            ACTION_PREPARE_QUEUE,
            ACTION_REFRESH_QUEUE,
            ACTION_NEXT,
            ACTION_PREVIOUS,
            ACTION_RESUME,
            ACTION_PAUSE,
            ACTION_STOP,
            ACTION_SEEK,
            ACTION_SET_SHUFFLE,
            ACTION_SET_REPEAT,
            ACTION_SCHEDULED_PLAY,
            ACTION_SCHEDULE_SLEEP_TIMER,
            ACTION_STOP_AFTER_CURRENT_ITEM,
            ACTION_CANCEL_SLEEP_TIMER,
            ACTION_SET_SPEED,
        )
        private const val PERIODIC_CHECKPOINT_MS = 15_000L
        private const val AUDIO_CONTOUR_PUBLISH_MS = 50L
        private const val MAX_CHECKPOINT_DELTA_MS = 300_000L
        private val ORDINARY_QUEUE_TYPES = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
    }
}

internal fun resolveCurrentTrackRefId(
    queueEntryId: String?,
    entries: List<Pair<String, String>>,
): String? = entries.firstOrNull { (entryId, _) -> entryId == queueEntryId }?.second

@UnstableApi
internal class AutPlaySessionCallback(private val applicationPackage: String) : MediaSession.Callback {
    override fun onConnect(
        session: MediaSession,
        controller: MediaSession.ControllerInfo,
    ): MediaSession.ConnectionResult = if (isControllerAllowed(controller.packageName, controller.isTrusted)) {
        MediaSession.ConnectionResult.accept(
            MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS,
            MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS,
        )
    } else {
        MediaSession.ConnectionResult.reject()
    }

    internal fun isControllerAllowed(packageName: String, trusted: Boolean): Boolean =
        packageName == applicationPackage || trusted
}
