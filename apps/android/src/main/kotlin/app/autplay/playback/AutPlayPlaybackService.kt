package app.autplay.playback

import app.autplay.application.library.decoded

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
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionError
import app.autplay.AutPlayRuntime
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.playback.RestoredPlaybackQueue
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.SettableFuture

/** Background player/session owner with bounded Room checkpoints and lazy current/next preflight. */
@UnstableApi
class AutPlayPlaybackService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val stateMutex = Mutex()
    private lateinit var player: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private lateinit var persistence: PlaybackPersistenceRepository
    private lateinit var libraryRepository: LibraryVerticalSliceRepository
    private lateinit var sourceResolver: AndroidPlaybackSourceResolver
    private var restored: RestoredPlaybackQueue? = null
    private var logicalSession: LogicalListeningCheckpoint? = null
    private var observedPlaybackStartedAtMs: Long? = null
    private var uncommittedPlaybackDeltaMs = 0L
    private var shuffleSeed: Long? = null
    private var scheduledPlayJob: kotlinx.coroutines.Job? = null
    private var sleepTimerJob: kotlinx.coroutines.Job? = null
    private var sleepTimerDeadlineElapsedRealtimeMs: Long? = null
    private var stopAfterQueueEntryId: String? = null
    private var sleepTimerGeneration = 0L
    private var queueGeneration = 0L
    private val resolutionJobs = mutableMapOf<String, Job>()
    private val resolvedQueueEntryIds = mutableSetOf<String>()
    private var lastPlayerMetrics: PlayerMetricsSnapshot? = null
    private var pendingTransitionMetrics: PlayerMetricsSnapshot? = null
    private val metadataTrack = MutableStateFlow<String?>(null)
    private val notificationTarget = MutableStateFlow<Pair<String?, String?>>(null to null)
    private var smoothTrackTransitions = false
    private val sourceTimelines = mutableMapOf<String, PlaybackSourceTimeline>()
    private var preservedHeadEntryId: String? = null
    private var preservedTailEntryId: String? = null
    private var applyingTransitionCuts = false

    override fun onCreate() {
        super.onCreate()
        val database = AutPlayRuntime.database(applicationContext)
        libraryRepository = LibraryVerticalSliceRepository(database, syncScheduler = AutPlayRuntime.syncScheduler(applicationContext))
        persistence = PlaybackPersistenceRepository(
            database,
            LibraryVerticalSliceRepository(
                database,
                syncScheduler = AutPlayRuntime.syncScheduler(applicationContext),
            ),
        )
        sourceResolver = AndroidPlaybackSourceResolver(
            applicationContext,
            database,
            applicationNonSecretSettingsStore(applicationContext),
        ) { profileId, serverUserTrackRefId ->
            AutPlayRuntime.serverPlaybackVariantId(
                applicationContext,
                profileId,
                serverUserTrackRefId,
            )
        }
        player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(PlaybackMediaSourceFactory.create(this, ::onSourceTimeline))
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
            .setSessionActivity(PlaybackNotification.sessionActivity(this))
            .setMediaButtonPreferences(PlaybackNotification.buttons(this, null, null, false))
            .setCallback(AutPlaySessionCallback(packageName, ::handleNotificationFeedback))
            .build()
        scope.launch {
            notificationTarget.collectLatest { (entryId, id) ->
                mediaSession.setMediaButtonPreferences(PlaybackNotification.buttons(this@AutPlayPlaybackService, entryId, null, false))
                if (id == null) return@collectLatest
                val ref = withContext(Dispatchers.IO) { database.libraryDao().trackRef(id) } ?: return@collectLatest
                combine(database.libraryDao().observePreference(id), applicationNonSecretSettingsStore(applicationContext).settings) { preference, settings ->
                    preference?.preference to (ref.deletedAtMs == null && ref.serverProfileId == (settings.activeServerProfileId?.value ?: "legacy-unscoped"))
                }.collectLatest { (preference, owned) ->
                    stateMutex.withLock {
                        val item = player.currentMediaItem ?: return@withLock
                        if (item.mediaId != entryId || item.mediaMetadata.extras?.getString("local_user_track_ref_id") != id) return@withLock
                        mediaSession.setMediaButtonPreferences(PlaybackNotification.buttons(this@AutPlayPlaybackService, item.mediaId, preference, owned))
                    }
                }
            }
        }
        scope.launch { restoreQueue(autoplay = false) }
        scope.launch {
            applicationNonSecretSettingsStore(applicationContext).settings.collectLatest { settings ->
                stateMutex.withLock {
                    if (smoothTrackTransitions != settings.smoothTrackTransitions) {
                        preserveCurrentCutPosition()
                        smoothTrackTransitions = settings.smoothTrackTransitions
                        updateTransitionCuts()
                    }
                }
            }
        }
        scope.launch {
            metadataTrack.collectLatest { id ->
                if (id == null) return@collectLatest
                val ref = withContext(Dispatchers.IO) { database.libraryDao().trackRef(id) } ?: return@collectLatest
                combine(database.trackMetadataDao().observe(ref.serverProfileId, id),
                    database.trackMetadataDao().observeArtwork(ref.serverProfileId, id)) { metadata, art -> metadata to art }
                    .collectLatest { (metadata, art) ->
                        val description = metadata?.decoded()
                        val bytes = withContext(Dispatchers.IO) { readArtwork(art?.filePath) }
                        stateMutex.withLock {
                            val item = player.currentMediaItem ?: return@withLock
                            if (item.mediaMetadata.extras?.getString("local_user_track_ref_id") != id) return@withLock
                            val updated = item.mediaMetadata.buildUpon()
                                .setTitle(mediaText(description, "title", ref.rawTitle))
                                .setArtist(mediaText(description, "artist", ref.rawArtist))
                                .setAlbumTitle(mediaText(description, "album", ref.rawAlbum))
                                .setArtworkData(bytes, MediaMetadata.PICTURE_TYPE_FRONT_COVER).build()
                            if (updated != item.mediaMetadata) {
                                // Preserve the URI, position and listening session: only description changes.
                                player.replaceMediaItem(player.currentMediaItemIndex, item.buildUpon().setMediaMetadata(updated).build())
                                publishRuntimeState(title = updated.title?.toString())
                            }
                        }
                    }
            }
        }
        scope.launch {
            while (isActive) {
                delay(PERIODIC_CHECKPOINT_MS)
                stateMutex.withLock {
                    if (player.isPlaying) {
                        logicalSession?.let { checkpointCurrent(it, sourcePositionMs()) }
                    }
                    publishRuntimeState()
                }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = mediaSession

    /** Capture the rated entry before any navigation or asynchronous Room work. */
    private fun handleNotificationFeedback(action: String, expectedEntryId: String?): ListenableFuture<SessionResult> {
        val item = player.currentMediaItem
        val trackId = item?.mediaMetadata?.extras?.getString("local_user_track_ref_id")
        if (item == null || trackId == null || expectedEntryId != item.mediaId) {
            return Futures.immediateFuture(SessionResult(SessionError.ERROR_INVALID_STATE))
        }
        val result = SettableFuture.create<SessionResult>()
        val job = scope.launch {
            try {
                stateMutex.withLock {
                    if (player.currentMediaItem?.mediaId != expectedEntryId) {
                        result.set(SessionResult(SessionError.ERROR_INVALID_STATE))
                        return@withLock
                    }
                    val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
                    val binding = settings.activeUserId?.let { user ->
                        val device = settings.deviceId ?: return@let null
                        val profile = settings.activeServerProfileId ?: return@let null
                        ClientEventBinding(user, device, profile)
                    }
                    val database = AutPlayRuntime.database(applicationContext)
                    val ref = database.libraryDao().trackRef(trackId)
                    if (ref == null || ref.deletedAtMs != null || ref.serverProfileId != (binding?.serverProfileId?.value ?: "legacy-unscoped")) {
                        result.set(SessionResult(SessionError.ERROR_PERMISSION_DENIED))
                        return@withLock
                    }
                    val requested = if (action == PlaybackNotification.ACTION_LIKE) "LIKED" else "DISLIKED"
                    val preference = if (database.libraryDao().preference(trackId)?.preference == requested) "NEUTRAL" else requested
                    // Match the in-app Dislike behavior while keeping Wave movement authoritative.
                    if (preference == "DISLIKED" && restored?.snapshot?.queueType in ORDINARY_QUEUE_TYPES && player.currentMediaItem?.mediaId == expectedEntryId) {
                        if (player.hasNextMediaItem() && player.getMediaItemAt(player.nextMediaItemIndex).mediaId != expectedEntryId) {
                            player.seekToNextMediaItem()
                            settleCurrentSource()
                            if (currentUnavailableReason() == null) player.play()
                        } else player.pause()
                    }
                    libraryRepository.setPlaybackPreference(binding, LocalId(trackId), LocalId.random(), preference, null, System.currentTimeMillis())
                    result.set(SessionResult(SessionResult.RESULT_SUCCESS))
                }
            } catch (error: CancellationException) {
                result.cancel(false)
                throw error
            } catch (_: Exception) {
                result.set(SessionResult(SessionError.ERROR_UNKNOWN))
            }
        }
        job.invokeOnCompletion { if (!result.isDone) result.cancel(false) }
        return result
    }

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
            ACTION_NEXT_IF_CURRENT -> moveWithinOrdinaryQueue(
                next = true,
                expectedQueueEntryId = intent.getStringExtra(EXTRA_EXPECTED_QUEUE_ENTRY_ID),
                resume = true,
            )
            ACTION_PREVIOUS -> moveWithinOrdinaryQueue(next = false)
            ACTION_RESUME -> player.play()
            ACTION_PAUSE -> player.pause()
            ACTION_STOP -> scope.launch {
                stateMutex.withLock {
                    finalizeCurrentLocked(capturePlayerMetrics())
                    player.stop()
                    stopSelf()
                }
            }
            ACTION_SEEK -> player.seekTo(currentCutBounds().playerPosition(intent.getLongExtra(EXTRA_POSITION_MS, 0)))
            ACTION_SET_SHUFFLE -> player.shuffleModeEnabled = intent.getBooleanExtra(EXTRA_SHUFFLE_ENABLED, false)
            ACTION_SET_REPEAT -> player.repeatMode = intent.getStringExtra(EXTRA_REPEAT_MODE).orEmpty().toMedia3RepeatMode()
            ACTION_SCHEDULED_PLAY -> {
                scheduledPlayJob?.cancel()
                // Wave timing is monotonic: wall-clock changes must not shift an accepted start.
                val delayMs = (intent.getLongExtra(EXTRA_SCHEDULED_AT_MS, 0) - SystemClock.elapsedRealtime()).coerceAtLeast(0)
                scheduledPlayJob = scope.launch {
                    delay(delayMs)
                    if (player.currentMediaItem.isResolvedPlaybackSource()) player.play()
                }
            }
            ACTION_SCHEDULE_SLEEP_TIMER -> scheduleSleepTimer(
                intent.getLongExtra(EXTRA_SLEEP_TIMER_DURATION_MS, 0),
            )
            ACTION_STOP_AFTER_CURRENT_ITEM -> stopAfterCurrentItem(
                intent.getStringExtra(EXTRA_EXPECTED_QUEUE_ENTRY_ID),
            )
            ACTION_CANCEL_SLEEP_TIMER -> cancelSleepTimer()
            ACTION_SET_SPEED -> player.playbackParameters = PlaybackParameters(intent.getFloatExtra(EXTRA_SPEED, 1f).coerceIn(.98f, 1.02f))
            ACTION_SET_CURRENT_LISTEN_TASTE_EXCLUDED -> scope.launch {
                stateMutex.withLock {
                    setCurrentListenTasteExcluded(
                        intent.getStringExtra(EXTRA_EXPECTED_QUEUE_ENTRY_ID),
                        intent.getStringExtra(EXTRA_EXPECTED_LISTENING_EVENT_ID),
                        intent.getBooleanExtra(EXTRA_TASTE_EXCLUDED, false),
                    )
                }
            }
            ACTION_SET_SESSION_TASTE_EXCLUDED -> scope.launch {
                stateMutex.withLock {
                    setSessionTasteExcluded(
                        intent.getStringExtra(EXTRA_QUEUE_SNAPSHOT_ID),
                        intent.getBooleanExtra(EXTRA_TASTE_EXCLUDED, false),
                    )
                }
            }
        }
        return super.onStartCommand(intent, flags, startId)
    }

    override fun onDestroy() {
        // Service callbacks and every player read run on the player looper. Persist this immutable
        // snapshot independently: lifecycle teardown must never block behind Room or stateMutex.
        val shutdownCheckpoint = logicalSession?.let { current ->
            ShutdownCheckpoint(
                current = current,
                positionMs = metricsFor(current)?.positionMs ?: sourcePositionMs(),
                observedPlaybackDeltaMs = collectObservedDelta(continueIfPlaying = false),
                shuffleMode = if (player.shuffleModeEnabled) "SEEDED" else "OFF",
                repeatMode = player.repeatMode.fromMedia3RepeatMode(),
                seed = shuffleSeed,
                nowMs = System.currentTimeMillis(),
            )
        }
        val cancelledService = requireNotNull(scope.coroutineContext[Job])
        scope.cancel()
        cancelResolutionJobs(resolutionJobs)
        resolvedQueueEntryIds.clear()
        mediaSession.release()
        scheduledPlayJob?.cancel()
        sleepTimerJob?.cancel()
        sleepTimerJob = null
        ++sleepTimerGeneration
        sleepTimerDeadlineElapsedRealtimeMs = null
        stopAfterQueueEntryId = null
        player.setPauseAtEndOfMediaItems(false)
        player.pause()
        player.stop()
        publishRuntimeState()
        player.release()
        shutdownCheckpoint?.let { checkpoint ->
            PlaybackShutdownPersistence.enqueue(cancelledService) {
                persistence.checkpointExistingSession(
                    checkpoint.current,
                    checkpoint.positionMs,
                    checkpoint.observedPlaybackDeltaMs,
                    checkpoint.shuffleMode,
                    checkpoint.repeatMode,
                    checkpoint.seed,
                    checkpoint.nowMs,
                )
            }
        }
        super.onDestroy()
    }

    private suspend fun restoreQueue(autoplay: Boolean, requiredSnapshotId: String? = null) {
        PlaybackShutdownPersistence.awaitPending()
        val plan = stateMutex.withLock {
            val queue = persistence.restoreActive() ?: return@withLock null
            if (!GuestQueueRestorePolicy.allows(queue.snapshot.queueType, requiredSnapshotId)) {
                return@withLock null
            }
            if (requiredSnapshotId != null && queue.snapshot.queueSnapshotId != requiredSnapshotId) {
                return@withLock null
            }
            val replacingQueue = restored?.snapshot?.queueSnapshotId?.let { it != queue.snapshot.queueSnapshotId } == true
            if (replacingQueue && logicalSession != null) finalizeCurrentLocked(capturePlayerMetrics())
            restored = queue
            if (replacingQueue || !SleepTimerPolicy.allows(queue.snapshot.queueType)) {
                cancelSleepTimerLocked()
            }
            logicalSession = if (replacingQueue) persistence.recoverSession() else logicalSession ?: persistence.recoverSession()
            val generation = beginQueueGeneration()
            val placeholders = queue.entries.map { entry -> placeholder(entry.queueEntryId) }
            val index = queue.media.currentIndex.coerceIn(placeholders.indices)
            sourceTimelines.clear()
            preservedHeadEntryId = placeholders[index].mediaId.takeIf { queue.media.currentPositionMs < PlaybackTransitionCuts.CUT_MS }
            preservedTailEntryId = placeholders[index].mediaId
            player.setMediaItems(placeholders, index, queue.media.currentPositionMs)
            player.seekTo(index, queue.media.currentPositionMs)
            player.playWhenReady = autoplay
            player.repeatMode = queue.snapshot.repeatMode.toMedia3RepeatMode()
            shuffleSeed = queue.snapshot.seed
            if (queue.snapshot.shuffleMode != "OFF") {
                val seed = shuffleSeed ?: queue.snapshot.queueSnapshotId.hashCode().toLong().also { shuffleSeed = it }
                player.setShuffleOrder(ShuffleOrder.DefaultShuffleOrder(placeholders.size, seed))
            }
            player.shuffleModeEnabled = queue.snapshot.shuffleMode != "OFF"
            publishRuntimeState(unavailableReason = currentUnavailableReason())
            QueueResolutionPlan(queue, generation, index)
        }
        plan ?: return
        scheduleResolveIndex(plan.queue, plan.currentIndex, plan.generation)
        scheduleResolveIndex(plan.queue, player.nextMediaItemIndex, plan.generation)
    }

    /** Reloads a committed ordinary queue while preserving the active stable entry and player mode. */
    private suspend fun refreshQueue(requiredSnapshotId: String?) {
        PlaybackShutdownPersistence.awaitPending()
        val plan = stateMutex.withLock {
            val currentId = player.currentMediaItem?.mediaId ?: return@withLock null
            val currentPositionMs = sourcePositionMs()
            val preservedRepeatMode = player.repeatMode
            val preservedShuffleEnabled = player.shuffleModeEnabled
            val preservedShuffleSeed = if (preservedShuffleEnabled) {
                shuffleSeed ?: System.currentTimeMillis()
            } else {
                shuffleSeed
            }
            shuffleSeed = preservedShuffleSeed
            logicalSession?.let { checkpointCurrent(it, currentPositionMs) }
            val queue = persistence.restoreActive() ?: return@withLock null
            if (queue.snapshot.queueSnapshotId != requiredSnapshotId || queue.snapshot.currentEntryId != currentId) return@withLock null
            val retainedResolved = resolvedQueueEntryIds.toSet()
            restored = queue
            val generation = beginQueueGeneration()
            val retainedIds = queue.entries.map { it.queueEntryId }.toSet()
            sourceTimelines.keys.retainAll(retainedIds)
            resolvedQueueEntryIds += retainedResolved.intersect(retainedIds)
            val index = queue.entries.indexOfFirst { it.queueEntryId == currentId }
            if (index < 0) return@withLock null
            // Media3 retains the current period for moves. Replacing the entire playlist
            // would reload its source and synthesize a seek/new listening session.
            for (oldIndex in player.mediaItemCount - 1 downTo 0) {
                if (player.getMediaItemAt(oldIndex).mediaId !in retainedIds) player.removeMediaItem(oldIndex)
            }
            queue.entries.forEachIndexed { desiredIndex, entry ->
                val existingIndex = (desiredIndex until player.mediaItemCount)
                    .firstOrNull { player.getMediaItemAt(it).mediaId == entry.queueEntryId }
                when {
                    existingIndex == null -> player.addMediaItem(desiredIndex, placeholder(entry.queueEntryId))
                    existingIndex != desiredIndex -> player.moveMediaItem(existingIndex, desiredIndex)
                }
            }
            player.repeatMode = preservedRepeatMode
            if (preservedShuffleEnabled) {
                player.setShuffleOrder(
                    ShuffleOrder.DefaultShuffleOrder(queue.entries.size, requireNotNull(preservedShuffleSeed)),
                )
            }
            player.shuffleModeEnabled = preservedShuffleEnabled
            updateTransitionCuts()
            publishRuntimeState(unavailableReason = currentUnavailableReason())
            QueueResolutionPlan(queue, generation, index)
        }
        plan ?: return
        scheduleResolveIndex(plan.queue, plan.currentIndex, plan.generation)
        scheduleResolveIndex(plan.queue, player.nextMediaItemIndex, plan.generation)
    }

    /** Wave movement is room-authoritative; only ordinary queues may invoke local navigation. */
    private fun moveWithinOrdinaryQueue(next: Boolean, expectedQueueEntryId: String? = null, resume: Boolean = false) {
        scope.launch {
            stateMutex.withLock {
                if (restored?.snapshot?.queueType !in ORDINARY_QUEUE_TYPES) return@withLock
                if (resume && (expectedQueueEntryId == null || player.currentMediaItem?.mediaId != expectedQueueEntryId)) {
                    return@withLock
                }
                if (resume && (!player.hasNextMediaItem() ||
                        player.getMediaItemAt(player.nextMediaItemIndex).mediaId == expectedQueueEntryId)) {
                    player.pause()
                    return@withLock
                }
                if (next) player.seekToNextMediaItem() else player.seekToPreviousMediaItem()
                if (resume) {
                    settleCurrentSource()
                    if (currentUnavailableReason() == null) player.play()
                }
            }
        }
    }

    private fun currentCutBounds(): PlaybackCutBounds = player.currentMediaItem.cutBounds()

    private fun MediaItem?.cutBounds(): PlaybackCutBounds = this?.clippingConfiguration?.let {
        PlaybackCutBounds(it.startPositionMs, it.endPositionMs.takeUnless { end -> end == C.TIME_END_OF_SOURCE })
    } ?: PlaybackCutBounds()

    private fun sourcePositionMs(): Long = currentCutBounds().sourcePosition(player.currentPosition)
        .coerceAtMost(sourceTimelines[player.currentMediaItem?.mediaId]?.durationMs ?: Long.MAX_VALUE)

    private fun preserveCurrentCutPosition() {
        preservedHeadEntryId = player.currentMediaItem?.mediaId.takeIf { sourcePositionMs() < PlaybackTransitionCuts.CUT_MS }
        preservedTailEntryId = player.currentMediaItem?.mediaId
    }

    /** Called by Media3's playback thread; all player mutation is dispatched to its app looper. */
    private fun onSourceTimeline(item: MediaItem, timeline: PlaybackSourceTimeline) {
        scope.launch {
            stateMutex.withLock {
                val index = (0 until player.mediaItemCount).firstOrNull {
                    val active = player.getMediaItemAt(it)
                    active.mediaId == item.mediaId && active.localConfiguration?.uri == item.localConfiguration?.uri &&
                        active.mediaMetadata.extras?.getString("queue_snapshot_id") ==
                        item.mediaMetadata.extras?.getString("queue_snapshot_id")
                } ?: return@withLock
                if (sourceTimelines[item.mediaId] == timeline) return@withLock
                sourceTimelines[player.getMediaItemAt(index).mediaId] = timeline
                updateTransitionCuts()
            }
        }
    }

    /** Reconcile real source boundaries after resolution, mode/timer changes and queue edits. */
    private fun updateTransitionCuts() {
        if (!scope.isActive || applyingTransitionCuts || player.mediaItemCount == 0) return
        applyingTransitionCuts = true
        try {
            val currentIndex = player.currentMediaItemIndex
            val positionMs = sourcePositionMs()
            val changes = (0 until player.mediaItemCount).mapNotNull { index ->
                val item = player.getMediaItemAt(index)
                if (!item.isResolvedPlaybackSource()) return@mapNotNull null
                val info = sourceTimelines[item.mediaId]
                val nextIndex = if (player.repeatMode == Player.REPEAT_MODE_ONE) index
                    else player.currentTimeline.takeUnless { it.isEmpty }
                        ?.getNextWindowIndex(index, player.repeatMode, player.shuffleModeEnabled) ?: C.INDEX_UNSET
                val bounds = PlaybackTransitionCuts.bounds(
                    enabled = smoothTrackTransitions,
                    queueType = restored?.snapshot?.queueType,
                    durationMs = info?.durationMs,
                    seekable = info?.seekable == true,
                    preserveHead = item.mediaId == preservedHeadEntryId,
                    hasPlayableSuccessor = nextIndex in 0 until player.mediaItemCount &&
                        player.getMediaItemAt(nextIndex).isResolvedPlaybackSource(),
                    stopAfterItem = item.mediaId == stopAfterQueueEntryId,
                    preserveTail = item.mediaId == preservedTailEntryId && index == currentIndex &&
                        info?.durationMs?.let { positionMs > it - PlaybackTransitionCuts.CUT_MS } == true,
                )
                val defaultStart = if (item.mediaId == preservedHeadEntryId && info == null) 0L
                    else PlaybackTransitionCuts.defaultStartMs(smoothTrackTransitions, restored?.snapshot?.queueType)
                if (bounds == item.cutBounds() &&
                    (item.mediaMetadata.extras?.getLong(CUT_DEFAULT_POSITION_MS, 0) ?: 0) == defaultStart) null
                else Triple(index, item, bounds to defaultStart)
            }
            for ((index, item, cut) in changes) {
                val (bounds, defaultStart) = cut
                val clipping = MediaItem.ClippingConfiguration.Builder()
                    .setStartPositionMs(bounds.startMs)
                    .setEndPositionMs(bounds.endMs ?: C.TIME_END_OF_SOURCE)
                    .build()
                val extras = Bundle(item.mediaMetadata.extras ?: Bundle()).apply {
                    putLong(CUT_DEFAULT_POSITION_MS, defaultStart)
                }
                player.replaceMediaItem(index, item.buildUpon().setClippingConfiguration(clipping)
                    .setMediaMetadata(item.mediaMetadata.buildUpon().setExtras(extras).build()).build())
                if (index == currentIndex && bounds != item.cutBounds()) player.seekTo(index, bounds.playerPosition(positionMs))
            }
        } finally {
            applyingTransitionCuts = false
        }
    }

    private fun beginQueueGeneration(): Long {
        queueGeneration += 1
        cancelResolutionJobs(resolutionJobs)
        resolvedQueueEntryIds.clear()
        pendingTransitionMetrics = null
        return queueGeneration
    }

    /** Resolves current and next independently; a slow remote next lookup cannot gate local play. */
    private fun scheduleResolveIndex(
        queue: RestoredPlaybackQueue,
        index: Int,
        generation: Long,
    ) {
        if (index !in queue.entries.indices) return
        val entry = queue.entries[index]
        if (entry.queueEntryId in resolvedQueueEntryIds) {
            if (index == player.currentMediaItemIndex) settleCurrentSource()
            return
        }
        if (resolutionJobs[entry.queueEntryId]?.isActive == true) return
        val job = scope.launch {
            try {
                val resolution = resolveQueueItem(queue, index)
                stateMutex.withLock {
                    if (generation != queueGeneration ||
                        restored?.snapshot?.queueSnapshotId != queue.snapshot.queueSnapshotId ||
                        index >= player.mediaItemCount ||
                        player.getMediaItemAt(index).mediaId != entry.queueEntryId
                    ) {
                        return@withLock
                    }
                    val currentPositionMs = if (index == player.currentMediaItemIndex) {
                        sourcePositionMs()
                    } else null
                    // A late-prepared successor must choose its real source head before audio starts.
                    val defaultStart = if (entry.queueEntryId == preservedHeadEntryId) 0L
                        else PlaybackTransitionCuts.defaultStartMs(smoothTrackTransitions, queue.snapshot.queueType)
                    val extras = Bundle(resolution.item.mediaMetadata.extras ?: Bundle()).apply {
                        putLong(CUT_DEFAULT_POSITION_MS, defaultStart)
                    }
                    player.replaceMediaItem(index, resolution.item.buildUpon().setMediaMetadata(
                        resolution.item.mediaMetadata.buildUpon().setExtras(extras).build(),
                    ).build())
                    // A new source has a new period and would otherwise reset the current position.
                    currentPositionMs?.let {
                        val usePreparedDefault = defaultStart > 0 && it == 0L && entry.queueEntryId != preservedHeadEntryId
                        player.seekTo(index, if (usePreparedDefault) C.TIME_UNSET else it)
                    }
                    resolvedQueueEntryIds += entry.queueEntryId
                    updateTransitionCuts()
                    if (index == player.currentMediaItemIndex) {
                        metadataTrack.value = entry.localUserTrackRefId
                        notificationTarget.value = entry.queueEntryId to entry.localUserTrackRefId
                        publishRuntimeState(
                            source = resolution.source,
                            unavailableReason = resolution.unavailableReason,
                            title = resolution.title,
                        )
                        settleCurrentSource()
                    }
                }
            } catch (error: CancellationException) {
                throw error
            }
        }
        resolutionJobs[entry.queueEntryId] = job
        job.invokeOnCompletion {
            if (resolutionJobs[entry.queueEntryId] === job) {
                resolutionJobs.remove(entry.queueEntryId)
            }
        }
    }

    private fun settleCurrentSource() {
        if (currentUnavailableReason() != null) {
            player.pause()
            player.stop()
            publishRuntimeState(unavailableReason = currentUnavailableReason())
        } else if (player.currentMediaItem.isResolvedPlaybackSource() && player.playbackState == Player.STATE_IDLE) {
            player.prepare()
        }
        publishRuntimeState(
            source = player.mediaMetadata.extras?.getString("selected_source"),
            unavailableReason = currentUnavailableReason(),
        )
    }

    private suspend fun resolveQueueItem(queue: RestoredPlaybackQueue, index: Int): QueueItemResolution =
        withContext(Dispatchers.IO) {
            val entry = queue.entries[index]
            val metadataDatabase = AutPlayRuntime.database(applicationContext)
            val originalTrack = metadataDatabase.libraryDao().trackRef(entry.localUserTrackRefId)
            val description = originalTrack?.let { metadataDatabase.trackMetadataDao().get(it.serverProfileId, it.localUserTrackRefId)?.decoded() }
            val track = if (description == null) originalTrack else originalTrack.copy(
                rawTitle = mediaText(description, "title", originalTrack.rawTitle), rawArtist = mediaText(description, "artist", originalTrack.rawArtist), rawAlbum = mediaText(description, "album", originalTrack.rawAlbum))
            val artwork = if (track != null && description?.artworkSha256 != null) metadataDatabase.trackMetadataDao().artwork(track.serverProfileId, description.artworkSha256) else null
            val artworkBytes = readArtwork(artwork?.filePath)
            when (val resolved = sourceResolver.resolve(LocalId(entry.localUserTrackRefId), System.currentTimeMillis())) {
                is AndroidSourceResolution.Unavailable -> {
                    val reason = resolved.reason.name
                    QueueItemResolution(
                        item = unavailableItem(entry.queueEntryId, reason, track?.rawTitle, track?.rawArtist),
                        source = null,
                        unavailableReason = reason,
                        title = track?.rawTitle,
                    )
                }
                is AndroidSourceResolution.Available -> QueueItemResolution(
                    item = MediaItem.Builder()
                        .setMediaId(entry.queueEntryId)
                        .setUri(resolved.value.runtimeUri)
                        .setMediaMetadata(
                            MediaMetadata.Builder()
                                .setTitle(track?.rawTitle ?: "Unavailable title")
                                .setArtist(track?.rawArtist)
                                .setAlbumTitle(track?.rawAlbum)
                                .setArtworkData(artworkBytes, MediaMetadata.PICTURE_TYPE_FRONT_COVER)
                                .setExtras(Bundle().apply {
                                    putString("queue_snapshot_id", queue.snapshot.queueSnapshotId)
                                    putString("local_user_track_ref_id", entry.localUserTrackRefId)
                                    putString("selected_source", resolved.value.source.name)
                                })
                                .build(),
                        )
                        .build(),
                    source = resolved.value.source.name,
                    unavailableReason = null,
                    title = track?.rawTitle,
                )
            }
        }

    private fun mediaText(description: app.autplay.application.library.TrackMetadata?, field: String, fallback: String?): String? =
        // Media3 fills nulls from embedded tags; an explicit user clear must override them.
        if (description?.fields?.get(field) == kotlinx.serialization.json.JsonNull) ""
        else if (description == null) fallback else description.text(field, fallback)

    private fun readArtwork(path: String?): ByteArray? = runCatching {
        path?.let {
            val file = java.io.File(it)
            val root = java.io.File(filesDir, "metadata-art").canonicalPath + java.io.File.separator
            file.takeIf { it.canonicalPath.startsWith(root) && it.isFile && it.length() in 1..2_097_152 }?.readBytes()
        }
    }.getOrNull()

    private data class QueueItemResolution(
        val item: MediaItem,
        val source: String?,
        val unavailableReason: String?,
        val title: String?,
    )

    private data class QueueResolutionPlan(
        val queue: RestoredPlaybackQueue,
        val generation: Long,
        val currentIndex: Int,
    )

    private data class PlayerMetricsSnapshot(
        val queueEntryId: String,
        val positionMs: Long,
        val durationMs: Long?,
    )

    private data class ShutdownCheckpoint(
        val current: LogicalListeningCheckpoint,
        val positionMs: Long,
        val observedPlaybackDeltaMs: Long,
        val shuffleMode: String,
        val repeatMode: String,
        val seed: Long?,
        val nowMs: Long,
    )

    private fun placeholder(queueEntryId: String): MediaItem = MediaItem.Builder()
        .setMediaId(queueEntryId)
        .setUri("autplay-unresolved://queue/$queueEntryId".toUri())
        .build()

    private fun unavailableItem(
        queueEntryId: String,
        reason: String,
        title: String?,
        artist: String?,
    ): MediaItem = MediaItem.Builder()
        .setMediaId(queueEntryId)
        .setUri("autplay-unavailable://$reason/$queueEntryId".toUri())
        .setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(title)
                .setArtist(artist)
                .setExtras(Bundle().apply { putString("unavailable_reason", reason) })
                .build(),
        )
        .build()

    private fun currentUnavailableReason(): String? =
        player.currentMediaItem?.mediaMetadata?.extras?.getString("unavailable_reason")

    private suspend fun ensureSession(): LogicalListeningCheckpoint? {
        logicalSession?.let { return it }
        val queue = restored ?: return null
        val mediaId = player.currentMediaItem?.mediaId ?: return null
        return persistence.startSessionIfActive(
            LocalId(queue.snapshot.queueSnapshotId),
            LocalId(mediaId),
            sourcePositionMs(),
            System.currentTimeMillis(),
            sessionOwnerBinding(),
        ).also { logicalSession = it }
    }

    private suspend fun checkpointCurrent(
        current: LogicalListeningCheckpoint,
        positionMs: Long,
    ): LogicalListeningCheckpoint {
        val delta = collectObservedDelta(continueIfPlaying = true)
        return persistence.checkpoint(
            current,
            positionMs,
            delta,
            if (player.shuffleModeEnabled) "SEEDED" else "OFF",
            player.repeatMode.fromMedia3RepeatMode(),
            shuffleSeed,
            System.currentTimeMillis(),
        ).also { logicalSession = it; uncommittedPlaybackDeltaMs = 0 }
    }

    private suspend fun checkpointPlayerStateLocked() {
        val positionMs = sourcePositionMs()
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

    private suspend fun finalizeCurrentLocked(metrics: PlayerMetricsSnapshot? = null) {
        val current = logicalSession ?: return
        val stableMetrics = metrics?.takeIf { it.queueEntryId == current.queueEntryId.value }
            ?: metricsFor(current)
        val duration = stableMetrics?.durationMs ?: withContext(Dispatchers.IO) {
            AutPlayRuntime.database(applicationContext).libraryDao()
                .trackRef(current.trackRefId.value)
                ?.rawDurationMs
                ?.takeIf { it > 0 }
        }
        persistence.finalizeSession(
            current = current,
            endPositionMs = stableMetrics?.positionMs ?: current.lastObservedPositionMs,
            durationMs = duration,
            observedPlaybackDeltaMs = collectObservedDelta(continueIfPlaying = false),
            nowMs = System.currentTimeMillis(),
        )
        logicalSession = null
        uncommittedPlaybackDeltaMs = 0
        restored = restored?.let { queue ->
            queue.copy(snapshot = queue.snapshot.copy(activeListenExcludedFromTaste = false))
        }
        observedPlaybackStartedAtMs = null
    }

    private suspend fun setCurrentListenTasteExcluded(
        expectedQueueEntryId: String?,
        expectedListeningEventId: String?,
        excluded: Boolean,
    ) {
        val current = logicalSession
        if (current == null || current.queueEntryId.value != expectedQueueEntryId ||
            current.listeningEventId.value != expectedListeningEventId
        ) {
            publishRuntimeState(tasteExclusionError = "TASTE_LISTEN_STALE")
            return
        }
        runCatching {
            persistence.setCurrentListenTasteExcluded(current, excluded)
        }.onSuccess { updated ->
            logicalSession = updated
            restored = restored?.let { queue ->
                queue.copy(snapshot = queue.snapshot.copy(activeListenExcludedFromTaste = excluded))
            }
            publishRuntimeState(tasteExclusionError = null)
        }.onFailure {
            publishRuntimeState(tasteExclusionError = "TASTE_EXCLUSION_UNAVAILABLE")
        }
    }

    private suspend fun setSessionTasteExcluded(expectedSnapshotId: String?, excluded: Boolean) {
        val queue = restored
        if (queue == null || queue.snapshot.queueSnapshotId != expectedSnapshotId) {
            publishRuntimeState(tasteExclusionError = "TASTE_SESSION_STALE")
            return
        }
        runCatching {
            persistence.setSessionTasteExcluded(LocalId(queue.snapshot.queueSnapshotId), logicalSession, excluded)
        }.onSuccess { updated ->
            logicalSession = updated
            restored = queue.copy(
                snapshot = queue.snapshot.copy(
                    sessionExcludedFromTaste = excluded,
                ),
            )
            publishRuntimeState(tasteExclusionError = null)
        }.onFailure {
            publishRuntimeState(tasteExclusionError = "TASTE_EXCLUSION_UNAVAILABLE")
        }
    }

    private fun capturePlayerMetrics(): PlayerMetricsSnapshot? {
        val queueEntryId = player.currentMediaItem?.mediaId ?: return null
        return PlayerMetricsSnapshot(
            queueEntryId = queueEntryId,
            positionMs = sourcePositionMs(),
            durationMs = sourceTimelines[queueEntryId]?.durationMs
                ?: player.duration.takeUnless { it == C.TIME_UNSET || it <= 0 },
        )
    }

    private fun metricsFor(current: LogicalListeningCheckpoint): PlayerMetricsSnapshot? =
        sequenceOf(pendingTransitionMetrics, capturePlayerMetrics(), lastPlayerMetrics)
            .filterNotNull()
            .firstOrNull { it.queueEntryId == current.queueEntryId.value }

    private fun collectObservedDelta(continueIfPlaying: Boolean): Long {
        uncommittedPlaybackDeltaMs = (uncommittedPlaybackDeltaMs + consumeObservedDelta(continueIfPlaying))
            .coerceAtMost(MAX_CHECKPOINT_DELTA_MS)
        return uncommittedPlaybackDeltaMs
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
        tasteExclusionError: String? = PlaybackRuntimeState.state.value.tasteExclusionError,
    ) {
        lastPlayerMetrics = capturePlayerMetrics()
        val queueEntryId = player.currentMediaItem?.mediaId
        val localTrackRefId = resolveCurrentTrackRefId(
            queueEntryId,
            restored?.entries?.map { entry -> entry.queueEntryId to entry.localUserTrackRefId }.orEmpty(),
        )
        PlaybackRuntimeState.publish(
            PlaybackUiState(
                queueSnapshotId = restored?.snapshot?.queueSnapshotId,
                queueEntryId = queueEntryId,
                localUserTrackRefId = localTrackRefId,
                title = title,
                source = source,
                unavailableReason = unavailableReason,
                positionMs = sourcePositionMs(),
                isPlaying = player.isPlaying,
                isPrepared = player.playbackState == Player.STATE_READY,
                bufferedMs = (player.bufferedPosition - player.currentPosition).coerceAtLeast(0),
                shuffleEnabled = player.shuffleModeEnabled,
                repeatMode = player.repeatMode.fromMedia3RepeatMode(),
                sleepTimerDeadlineElapsedRealtimeMs = sleepTimerDeadlineElapsedRealtimeMs,
                stopAfterQueueEntryId = stopAfterQueueEntryId,
                listeningEventId = logicalSession?.listeningEventId?.value,
                listenExcludedFromTaste = logicalSession?.excludedFromTaste
                    ?: restored?.snapshot?.activeListenExcludedFromTaste
                    ?: false,
                sessionExcludedFromTaste = restored?.snapshot?.sessionExcludedFromTaste ?: false,
                tasteExclusionError = tasteExclusionError,
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
                updateTransitionCuts()
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
                updateTransitionCuts()
                publishRuntimeState()
            }
        }
    }

    private fun cancelSleepTimerLocked() {
        // Clearing pause-at-end must not seek a completed full tail backwards into a cut.
        if (stopAfterQueueEntryId != null) preservedTailEntryId = player.currentMediaItem?.mediaId
        ++sleepTimerGeneration
        sleepTimerJob?.cancel()
        sleepTimerJob = null
        val changed = sleepTimerDeadlineElapsedRealtimeMs != null || stopAfterQueueEntryId != null
        sleepTimerDeadlineElapsedRealtimeMs = null
        stopAfterQueueEntryId = null
        player.setPauseAtEndOfMediaItems(false)
        updateTransitionCuts()
        if (changed) publishRuntimeState()
    }

    private inner class PlayerListener : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            scope.launch {
                stateMutex.withLock {
                    if (isPlaying) {
                        if (ensureSession() != null && observedPlaybackStartedAtMs == null) {
                            observedPlaybackStartedAtMs = SystemClock.elapsedRealtime()
                        }
                    } else {
                        logicalSession?.let { checkpointCurrent(it, sourcePositionMs()) }
                    }
                    publishRuntimeState()
                }
            }
        }

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            val replacingCut = applyingTransitionCuts
            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO || reason == Player.MEDIA_ITEM_TRANSITION_REASON_REPEAT) {
                preservedHeadEntryId = null
                preservedTailEntryId = null
            } else if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK && replacingCut) {
                // Source replacement can synthesize SEEK for the same stable queue entry.
                preservedHeadEntryId = preservedHeadEntryId?.takeIf { it == mediaItem?.mediaId }
                preservedTailEntryId = preservedTailEntryId?.takeIf { it == mediaItem?.mediaId }
            } else if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK) {
                preservedHeadEntryId = null
                preservedTailEntryId = null
            }
            metadataTrack.value = mediaItem?.mediaMetadata?.extras?.getString("local_user_track_ref_id")
            notificationTarget.value = mediaItem?.mediaId to metadataTrack.value
            // Capture before later playlist edits or queued persistence can replace this occurrence.
            val departingMetrics = pendingTransitionMetrics
            scope.launch {
                stateMutex.withLock {
                    val current = logicalSession
                    val isSameRestore = current?.queueEntryId?.value == mediaItem?.mediaId &&
                        (reason == Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED ||
                            replacingCut && reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK)
                    // A clipping/metadata replacement during repeat must not consume the
                    // departing occurrence's metrics before the actual repeat is finalized.
                    val transitionMetrics = departingMetrics
                        ?.takeIf { !isSameRestore && it.queueEntryId == current?.queueEntryId?.value }
                    if (!isSameRestore && pendingTransitionMetrics === departingMetrics) pendingTransitionMetrics = null
                    if (stopAfterQueueEntryId != null && stopAfterQueueEntryId != mediaItem?.mediaId) {
                        cancelSleepTimerLocked()
                    }
                    if (current != null && !isSameRestore) finalizeCurrentLocked(transitionMetrics)
                    val queue = restored ?: return@withLock
                    val index = player.currentMediaItemIndex
                    scheduleResolveIndex(
                        queue,
                        index,
                        queueGeneration,
                    )
                    scheduleResolveIndex(queue, player.nextMediaItemIndex, queueGeneration)
                    if (player.isPlaying) {
                        if (ensureSession() != null) observedPlaybackStartedAtMs = SystemClock.elapsedRealtime()
                    } else if (!isSameRestore && mediaItem != null) {
                        persistence.selectIdleEntry(
                            snapshotId = LocalId(queue.snapshot.queueSnapshotId),
                            entryId = LocalId(mediaItem.mediaId),
                            positionMs = sourcePositionMs(),
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
            if (oldPosition.mediaItem?.mediaId != newPosition.mediaItem?.mediaId ||
                reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                val oldQueueEntryId = oldPosition.mediaItem?.mediaId
                if (oldQueueEntryId != null) {
                    pendingTransitionMetrics = PlayerMetricsSnapshot(
                        queueEntryId = oldQueueEntryId,
                        positionMs = if (reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION)
                            oldPosition.mediaItem.cutBounds().endMs ?: sourceTimelines[oldQueueEntryId]?.durationMs
                                ?: oldPosition.mediaItem.cutBounds().sourcePosition(oldPosition.positionMs)
                            else oldPosition.mediaItem.cutBounds().sourcePosition(oldPosition.positionMs),
                        durationMs = sourceTimelines[oldQueueEntryId]?.durationMs ?: lastPlayerMetrics
                            ?.takeIf { it.queueEntryId == oldQueueEntryId }
                            ?.durationMs,
                    )
                }
            }
            if (reason != Player.DISCONTINUITY_REASON_SEEK) return
            scope.launch {
                stateMutex.withLock {
                    val positionMs = newPosition.mediaItem.cutBounds().sourcePosition(newPosition.positionMs)
                    // A navigation seek belongs to the new session, never the departing item.
                    if (oldPosition.mediaItem?.mediaId != newPosition.mediaItem?.mediaId) return@withLock
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
                    restored?.let { scheduleResolveIndex(it, player.nextMediaItemIndex, queueGeneration) }
                    updateTransitionCuts()
                    publishRuntimeState()
                }
            }
        }

        override fun onRepeatModeChanged(repeatMode: Int) {
            scope.launch {
                stateMutex.withLock {
                    checkpointPlayerStateLocked()
                    restored?.let { scheduleResolveIndex(it, player.nextMediaItemIndex, queueGeneration) }
                    updateTransitionCuts()
                    publishRuntimeState()
                }
            }
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            capturePlayerMetrics()?.let { lastPlayerMetrics = it }
            if (playbackState == Player.STATE_ENDED && !applyingTransitionCuts) scope.launch {
                stateMutex.withLock {
                    // Replacing clipping can briefly end the old source before its replacement
                    // is prepared. Only finalize a queue that is still ended after that batch.
                    if (player.playbackState == Player.STATE_ENDED) finalizeCurrentLocked(capturePlayerMetrics())
                }
            }
            else scope.launch { stateMutex.withLock { publishRuntimeState() } }
        }

        override fun onEvents(player: Player, events: Player.Events) {
            // Individual callback arguments belong to the previous occurrence; mutate only after
            // the complete event batch has captured its transition and seek checkpoints.
            if (events.containsAny(Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_TIMELINE_CHANGED,
                    Player.EVENT_POSITION_DISCONTINUITY, Player.EVENT_PLAYBACK_STATE_CHANGED)) updateTransitionCuts()
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
                    logicalSession?.let { checkpointCurrent(it, sourcePositionMs()) }
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
        const val ACTION_NEXT_IF_CURRENT = "app.autplay.playback.NEXT_IF_CURRENT"
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
        const val ACTION_SET_CURRENT_LISTEN_TASTE_EXCLUDED = "app.autplay.playback.SET_CURRENT_LISTEN_TASTE_EXCLUDED"
        const val ACTION_SET_SESSION_TASTE_EXCLUDED = "app.autplay.playback.SET_SESSION_TASTE_EXCLUDED"
        const val EXTRA_QUEUE_SNAPSHOT_ID = "queue_snapshot_id"
        const val EXTRA_POSITION_MS = "position_ms"
        const val EXTRA_SHUFFLE_ENABLED = "shuffle_enabled"
        const val EXTRA_REPEAT_MODE = "repeat_mode"
        const val EXTRA_SCHEDULED_AT_MS = "scheduled_at_ms"
        const val EXTRA_SLEEP_TIMER_DURATION_MS = "sleep_timer_duration_ms"
        const val EXTRA_EXPECTED_QUEUE_ENTRY_ID = "expected_queue_entry_id"
        const val EXTRA_SPEED = "speed"
        const val EXTRA_EXPECTED_LISTENING_EVENT_ID = "expected_listening_event_id"
        const val EXTRA_TASTE_EXCLUDED = "taste_excluded"
        private val APP_COMMAND_ACTIONS = setOf(
            ACTION_START_QUEUE,
            ACTION_PREPARE_QUEUE,
            ACTION_REFRESH_QUEUE,
            ACTION_NEXT,
            ACTION_NEXT_IF_CURRENT,
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
            ACTION_SET_CURRENT_LISTEN_TASTE_EXCLUDED,
            ACTION_SET_SESSION_TASTE_EXCLUDED,
        )
        private const val PERIODIC_CHECKPOINT_MS = 15_000L
        private const val TRANSITION_VOLUME_UPDATE_MS = 50L
        private const val MAX_CHECKPOINT_DELTA_MS = 300_000L
        private val ORDINARY_QUEUE_TYPES = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
    }
}

internal fun resolveCurrentTrackRefId(
    queueEntryId: String?,
    entries: List<Pair<String, String>>,
): String? = entries.firstOrNull { (entryId, _) -> entryId == queueEntryId }?.second

/** A guest queue needs a fresh process-local command and is never restored as ambient authority. */
internal object GuestQueueRestorePolicy {
    fun allows(queueType: String, requiredSnapshotId: String?): Boolean =
        queueType != "GUEST_WAVE" || !requiredSnapshotId.isNullOrBlank()
}

@UnstableApi
internal class AutPlaySessionCallback(
    private val applicationPackage: String,
    private val feedback: (String, String?) -> ListenableFuture<SessionResult> = { _, _ ->
        Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
    },
) : MediaSession.Callback {
    override fun onConnect(
        session: MediaSession,
        controller: MediaSession.ControllerInfo,
    ): MediaSession.ConnectionResult = if (isControllerAllowed(controller.packageName, controller.isTrusted)) {
        MediaSession.ConnectionResult.accept(
            PlaybackNotification.commands(),
            MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS,
        )
    } else {
        MediaSession.ConnectionResult.reject()
    }

    internal fun isControllerAllowed(packageName: String, trusted: Boolean): Boolean =
        packageName == applicationPackage || trusted

    override fun onCustomCommand(session: MediaSession, controller: MediaSession.ControllerInfo, customCommand: SessionCommand, args: Bundle): ListenableFuture<SessionResult> {
        if (!isControllerAllowed(controller.packageName, controller.isTrusted)) {
            return Futures.immediateFuture(SessionResult(SessionError.ERROR_PERMISSION_DENIED))
        }
        if (customCommand.customAction !in setOf(PlaybackNotification.ACTION_LIKE, PlaybackNotification.ACTION_DISLIKE)) {
            return Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
        }
        val entryId = args.getString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID)
            ?: customCommand.customExtras.getString(PlaybackNotification.EXTRA_QUEUE_ENTRY_ID)
        return feedback(customCommand.customAction, entryId)
    }
}
