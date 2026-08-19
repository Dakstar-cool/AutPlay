package app.autplay.playback.presentation

import androidx.media3.common.util.UnstableApi
import app.autplay.application.wave.WaveCoordinator
import kotlinx.coroutines.CancellationException

/** High-level direct-player actions; implementations keep Media3 ownership outside Compose. */
internal interface DirectPlaybackActionPort {
    fun commitSeek()
    fun togglePlayPause()
    fun toggleShuffle()
    fun cycleRepeatMode()
}

/** Authoritative Wave host-command seam used by the UI playback action router. */
internal interface WaveHostPlaybackCommandPort {
    suspend fun startFirstQueued(): Boolean
    suspend fun pauseRoom()
}

@UnstableApi
internal class PlaybackPresentationActionPort(
    private val adapter: PlaybackPresentationAdapter,
) : DirectPlaybackActionPort {
    override fun commitSeek() = adapter.commitSeek()
    override fun togglePlayPause() = adapter.togglePlayPause()
    override fun toggleShuffle() = adapter.toggleShuffle()
    override fun cycleRepeatMode() = adapter.cycleRepeatMode()
}

internal class WaveCoordinatorHostPlaybackCommandPort(
    private val coordinator: WaveCoordinator,
) : WaveHostPlaybackCommandPort {
    override suspend fun startFirstQueued(): Boolean = coordinator.startFirstQueued()
    override suspend fun pauseRoom() = coordinator.pauseRoom()
}

internal enum class WavePlaybackCommandOutcome {
    Started,
    WaitingForReadyDevices,
    Paused,
    RoleRejected,
    CommandFailed,
}

/**
 * Owns the actual UI callback paths for ordinary and Wave playback actions.
 *
 * Wave rejection/failure returns an outcome and never dispatches through [direct]. Keeping both
 * routes here makes a future local fallback observable in fake-port tests.
 */
internal class PlaybackInteractionRouter(
    private val direct: DirectPlaybackActionPort,
    private val wave: WaveHostPlaybackCommandPort?,
) {
    fun commitDirectSeek() = direct.commitSeek()
    fun toggleDirectPlayPause() = direct.togglePlayPause()
    fun toggleDirectShuffle() = direct.toggleShuffle()
    fun cycleDirectRepeatMode() = direct.cycleRepeatMode()

    suspend fun startWavePlayback(): WavePlaybackCommandOutcome {
        val command = wave ?: return WavePlaybackCommandOutcome.RoleRejected
        return try {
            if (command.startFirstQueued()) {
                WavePlaybackCommandOutcome.Started
            } else {
                WavePlaybackCommandOutcome.WaitingForReadyDevices
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            classifyWaveFailure(error)
        }
    }

    suspend fun pauseWavePlayback(): WavePlaybackCommandOutcome {
        val command = wave ?: return WavePlaybackCommandOutcome.RoleRejected
        return try {
            command.pauseRoom()
            WavePlaybackCommandOutcome.Paused
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            classifyWaveFailure(error)
        }
    }

    private fun classifyWaveFailure(error: Exception): WavePlaybackCommandOutcome =
        if (error is SecurityException || error.message == "WAVE_HOST_REQUIRED") {
            WavePlaybackCommandOutcome.RoleRejected
        } else {
            WavePlaybackCommandOutcome.CommandFailed
        }
}
