package app.autplay.ui.desktop

import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.canSeek

/** A renderer cannot enable commands until both the adapter and application gate are ready. */
public data class DesktopPlaybackControls(
    public val playPause: Boolean,
    public val previous: Boolean,
    public val next: Boolean,
    public val seek: Boolean,
) {
    public companion object {
        public fun from(state: PlaybackPresentationState, adapterAvailable: Boolean): DesktopPlaybackControls {
            val allowed = adapterAvailable &&
                state.connection == PlaybackConnectionState.Connected &&
                state.controls == PlaybackControlGate.Allowed && state.mediaId != null
            return DesktopPlaybackControls(
                playPause = allowed,
                previous = allowed && (state.previousMediaId != null ||
                    (state.canSeek && state.positionMs > 3_000 && state.playbackStatus != PlaybackStatus.Ended)),
                next = allowed && state.nextMediaId != null,
                seek = allowed && state.canSeek && (state.durationMs ?: 0) > 0,
            )
        }
    }
}

public data class DesktopPlaybackCallbacks(
    public val onPlay: () -> Unit,
    public val onPause: () -> Unit,
    public val onPrevious: () -> Unit,
    public val onNext: () -> Unit,
    public val onSeek: (Long) -> Unit,
)
