package app.autplay.playback.presentation

import android.content.ComponentName
import android.content.Context
import androidx.core.content.ContextCompat
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.application.playback.ActiveQueueContextRepository
import app.autplay.playback.AutPlayPlaybackService
import app.autplay.playback.isResolvedPlaybackSource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Activity-scoped MediaController owner. Compose sees [state] and intent methods only.
 * A single 400ms loop serves all observing mini/full-player surfaces.
 */
@UnstableApi
class PlaybackPresentationAdapter(
    context: Context,
    lifecycle: Lifecycle,
    private val queueContextRepository: ActiveQueueContextRepository,
) : DefaultLifecycleObserver, AutoCloseable {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _state = MutableStateFlow(PlaybackPresentationState())
    val state: StateFlow<PlaybackPresentationState> = _state.asStateFlow()

    private var controller: MediaController? = null
    private var foreground = false
    private var connecting = false
    private val observingSurfaces = mutableSetOf<String>()
    private var ticker: Job? = null
    private var gesture: TimelineSeekGesture.State = TimelineSeekGesture.Idle

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) = publishFromController()
    }

    init {
        lifecycle.addObserver(this)
        scope.launch {
            queueContextRepository.context.collect {
                publishFromController()
            }
        }
    }

    override fun onStart(owner: LifecycleOwner) {
        foreground = true
        connect()
        updateTicker()
    }

    override fun onStop(owner: LifecycleOwner) {
        foreground = false
        updateTicker()
        releaseController()
    }

    override fun onDestroy(owner: LifecycleOwner) = close()

    /** Call from each visible player surface with a stable local surface id. */
    fun setSurfaceObserving(surfaceId: String, observing: Boolean) {
        if (observing) observingSurfaces += surfaceId else observingSurfaces -= surfaceId
        updateTicker()
    }

    fun beginSeek(targetMs: Long) {
        gesture = TimelineSeekGesture.begin(_state.value, targetMs)
        publishFromController()
    }

    fun updateSeek(targetMs: Long) {
        gesture = TimelineSeekGesture.drag(gesture, targetMs)
        publishFromController()
    }

    /** Sends one seek only after the authoritative state and gate are rechecked. */
    fun commitSeek() {
        val result = TimelineSeekGesture.commit(gesture, _state.value)
        gesture = result.state
        val commit = result.commit ?: run { publishFromController(); return }
        val current = controller ?: run { publishFromController(); return }
        PlaybackCommandBoundary.commitSeek(
            port = MediaControllerCommandPort(current),
            context = queueContextRepository.context.value,
            commit = commit,
            seekable = current.isCurrentMediaItemSeekable && !current.isCurrentMediaItemLive,
        )
        publishFromController()
    }

    /** No Wave failure/rejection path can fall through to a local Media3 action. */
    fun togglePlayPause() {
        val current = controller ?: return
        PlaybackCommandBoundary.togglePlayPause(
            MediaControllerCommandPort(current),
            queueContextRepository.context.value,
        )
        publishFromController()
    }

    fun toggleShuffle() {
        val current = controller ?: return
        PlaybackCommandBoundary.toggleShuffle(
            MediaControllerCommandPort(current),
            queueContextRepository.context.value,
        )
        publishFromController()
    }

    fun cycleRepeatMode() {
        val current = controller ?: return
        PlaybackCommandBoundary.cycleRepeatMode(
            MediaControllerCommandPort(current),
            queueContextRepository.context.value,
        )
        publishFromController()
    }

    private fun connect() {
        if (!foreground || controller != null || connecting) return
        connecting = true
        val token = SessionToken(appContext, ComponentName(appContext, AutPlayPlaybackService::class.java))
        val future = MediaController.Builder(appContext, token).buildAsync()
        future.addListener(
            {
                val resolved = runCatching { future.get() }.getOrNull()
                connecting = false
                if (foreground) {
                    controller = resolved?.also { it.addListener(listener) }
                } else {
                    resolved?.release()
                }
                publishFromController()
            },
            ContextCompat.getMainExecutor(appContext),
        )
    }

    private fun publishFromController() {
        val current = controller
        val context = queueContextRepository.context.value
        val mediaId = current?.currentMediaItem?.mediaId
        val duration = current?.duration?.takeIf { it != androidx.media3.common.C.TIME_UNSET && it >= 0 }
        val live = current?.isCurrentMediaItemLive == true
        val seekable = current?.isCurrentMediaItemSeekable == true && !live && duration != null
        val gate = current?.let { gateFor(it, context, Player.COMMAND_PLAY_PAUSE) }
            ?: PlaybackCommandGate.evaluate(context, mediaId, commandAvailable = false)
        val seekEnabled = current?.let {
            gateFor(it, context, Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM, seekable = seekable) is PlaybackControlGate.Allowed
        } == true
        val next = PlaybackPresentationState(
            mediaId = mediaId,
            title = current?.mediaMetadata?.title?.toString(),
            artist = current?.mediaMetadata?.artist?.toString(),
            positionMs = current?.currentPosition?.coerceAtLeast(0) ?: 0,
            bufferedPositionMs = current?.bufferedPosition?.coerceAtLeast(0) ?: 0,
            durationMs = duration,
            isLive = live,
            isSeekable = seekable,
            isPlaying = current?.isPlaying == true,
            connection = if (current == null) PlaybackConnectionState.Disconnected else PlaybackConnectionState.Connected,
            playbackStatus = when (current?.playbackState) {
                Player.STATE_BUFFERING -> PlaybackStatus.Buffering
                Player.STATE_READY -> PlaybackStatus.Ready
                Player.STATE_ENDED -> PlaybackStatus.Ended
                else -> PlaybackStatus.Idle
            },
            source = when (current?.mediaMetadata?.extras?.getString("selected_source")) {
                "LOCAL_URI" -> PlaybackSourcePresentation.Local
                "MEDIA3_DOWNLOAD" -> PlaybackSourcePresentation.Download
                "VAULT_STREAM" -> PlaybackSourcePresentation.Vault
                else -> PlaybackSourcePresentation.Unknown
            },
            context = context,
            controls = gate,
            seekEnabled = seekEnabled,
            shuffleEnabled = current?.let {
                gateFor(it, context, Player.COMMAND_SET_SHUFFLE_MODE) is PlaybackControlGate.Allowed
            } == true,
            repeatEnabled = current?.let {
                gateFor(it, context, Player.COMMAND_SET_REPEAT_MODE) is PlaybackControlGate.Allowed
            } == true,
            shuffleModeEnabled = current?.shuffleModeEnabled == true,
            repeatMode = when (current?.repeatMode) {
                Player.REPEAT_MODE_ONE -> RepeatModePresentation.One
                Player.REPEAT_MODE_ALL -> RepeatModePresentation.All
                else -> RepeatModePresentation.Off
            },
        )
        gesture = TimelineSeekGesture.reconcile(gesture, next)
        _state.value = next.copy(seekPreviewPositionMs = (gesture as? TimelineSeekGesture.Dragging)?.targetMs)
        updateTicker()
    }

    private fun gateFor(
        controller: MediaController,
        context: ActiveQueueContext,
        command: Int,
        seekable: Boolean? = null,
    ): PlaybackControlGate =
        PlaybackCommandGate.evaluate(
            context = context,
            mediaId = controller.currentMediaItem?.mediaId,
            commandAvailable = controller.isCommandAvailable(command),
            sourceAvailable = controller.currentMediaItem.isResolvedPlaybackSource(),
            seekable = seekable,
        )

    private fun updateTicker() {
        val shouldTick = PlaybackPresentationCadence.shouldTick(
            foreground = foreground,
            hasObservingSurface = observingSurfaces.isNotEmpty(),
            isPlaying = controller?.isPlaying == true,
        )
        if (shouldTick && ticker == null) {
            ticker = scope.launch {
                while (true) {
                    delay(PRESENTATION_TICK_MS)
                    publishFromController()
                }
            }
        } else if (!shouldTick) {
            ticker?.cancel()
            ticker = null
        }
    }

    override fun close() {
        ticker?.cancel()
        ticker = null
        foreground = false
        releaseController()
        scope.cancel()
    }

    private fun releaseController() {
        controller?.removeListener(listener)
        controller?.release()
        controller = null
        publishDisconnected()
    }

    private fun publishDisconnected() {
        gesture = TimelineSeekGesture.Idle
        _state.value = _state.value.copy(
            connection = PlaybackConnectionState.Disconnected,
            controls = PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE),
            seekEnabled = false,
            shuffleEnabled = false,
            repeatEnabled = false,
            seekPreviewPositionMs = null,
        )
    }

    companion object { const val PRESENTATION_TICK_MS = PlaybackPresentationCadence.intervalMs }
}

/** Pure cadence gate keeps lifecycle policy independently testable. */
object PlaybackPresentationCadence {
    const val intervalMs = 400L
    fun shouldTick(foreground: Boolean, hasObservingSurface: Boolean, isPlaying: Boolean): Boolean =
        foreground && hasObservingSurface && isPlaying
}
