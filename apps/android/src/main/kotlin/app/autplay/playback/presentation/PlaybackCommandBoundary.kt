package app.autplay.playback.presentation

import androidx.media3.common.Player
import androidx.media3.session.MediaController
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.isResolvedPlaybackSource

/** Narrow command port used to prove that fail-closed policy reaches the Media3 boundary. */
internal interface PlaybackCommandPort {
    val mediaId: String?
    val sourceAvailable: Boolean
    val isPlaying: Boolean
    var shuffleModeEnabled: Boolean
    var repeatMode: Int

    fun isCommandAvailable(command: Int): Boolean
    fun seekTo(positionMs: Long)
    fun play()
    fun pause()
}

/** Media3 adapter kept private to the playback-presentation package. */
internal class MediaControllerCommandPort(
    private val controller: MediaController,
) : PlaybackCommandPort {
    override val mediaId: String?
        get() = controller.currentMediaItem?.mediaId
    override val sourceAvailable: Boolean
        get() = controller.currentMediaItem.isResolvedPlaybackSource()
    override val isPlaying: Boolean
        get() = controller.isPlaying
    override var shuffleModeEnabled: Boolean
        get() = controller.shuffleModeEnabled
        set(value) {
            controller.shuffleModeEnabled = value
        }
    override var repeatMode: Int
        get() = controller.repeatMode
        set(value) {
            controller.repeatMode = value
        }

    override fun isCommandAvailable(command: Int): Boolean = controller.isCommandAvailable(command)

    override fun seekTo(positionMs: Long) = controller.seekTo(positionMs)

    override fun play() = controller.play()

    override fun pause() = controller.pause()
}

/**
 * Last in-process boundary before a direct Media3 command.
 *
 * Durable Wave, recovering/unknown, mismatched and unavailable-command contexts all stop here;
 * callers have no failure branch that can fall through to a local controller action.
 */
internal object PlaybackCommandBoundary {
    fun commitSeek(
        port: PlaybackCommandPort,
        context: ActiveQueueContext,
        commit: TimelineSeekGesture.Commit,
        seekable: Boolean,
    ): Boolean {
        val allowed = PlaybackCommandGate.evaluate(
            context = context,
            mediaId = port.mediaId,
            commandAvailable = port.isCommandAvailable(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM),
            sourceAvailable = port.sourceAvailable,
            seekable = seekable,
        ) is PlaybackControlGate.Allowed
        if (!allowed || port.mediaId != commit.mediaId) return false
        port.seekTo(commit.positionMs)
        return true
    }

    fun togglePlayPause(port: PlaybackCommandPort, context: ActiveQueueContext): Boolean {
        if (!isAllowed(port, context, Player.COMMAND_PLAY_PAUSE)) return false
        if (port.isPlaying) port.pause() else port.play()
        return true
    }

    fun toggleShuffle(port: PlaybackCommandPort, context: ActiveQueueContext): Boolean {
        if (!isAllowed(port, context, Player.COMMAND_SET_SHUFFLE_MODE)) return false
        port.shuffleModeEnabled = !port.shuffleModeEnabled
        return true
    }

    fun cycleRepeatMode(port: PlaybackCommandPort, context: ActiveQueueContext): Boolean {
        if (!isAllowed(port, context, Player.COMMAND_SET_REPEAT_MODE)) return false
        port.repeatMode = when (port.repeatMode) {
            Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
            Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
            else -> Player.REPEAT_MODE_OFF
        }
        return true
    }

    private fun isAllowed(
        port: PlaybackCommandPort,
        context: ActiveQueueContext,
        command: Int,
    ): Boolean = PlaybackCommandGate.evaluate(
        context = context,
        mediaId = port.mediaId,
        commandAvailable = port.isCommandAvailable(command),
        sourceAvailable = port.sourceAvailable,
    ) is PlaybackControlGate.Allowed
}
