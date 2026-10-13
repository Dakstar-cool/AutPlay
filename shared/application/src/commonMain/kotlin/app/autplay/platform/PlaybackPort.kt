package app.autplay.platform

import app.autplay.playback.presentation.PlaybackPresentationState
import kotlinx.coroutines.flow.StateFlow

/**
 * Platform playback commands and the current UI-safe projection of the existing playback owner.
 *
 * Implementations dispatch commands on their engine's required thread and revalidate the current
 * queue entry, source and command availability through the shared fail-closed policy. A stale UI
 * projection cannot authorize a command. Wave queues require their own authoritative room commands.
 *
 * This port does not own or replace the durable queue. Resolved URLs, credentials and native
 * player handles remain inside the adapter. Commands are requests; returning from a command
 * does not imply playback has changed. Callers observe [state] for the resulting playback state.
 */
interface PlaybackPort {
    val state: StateFlow<PlaybackPresentationState>

    fun play(): Unit
    fun pause(): Unit
    fun seekTo(positionMs: Long): Unit
    fun next(): Unit
    fun previous(): Unit
}
