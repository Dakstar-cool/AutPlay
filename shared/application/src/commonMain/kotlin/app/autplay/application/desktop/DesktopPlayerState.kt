package app.autplay.application.desktop

import app.autplay.playback.presentation.PlaybackPresentationState

/** A command belongs to one queue entry, logical listen and native opening. Never persisted. */
data class DesktopPlaybackTarget(
    val queueId: String,
    val entryId: String,
    val sessionId: String?,
    val generation: Long,
)

enum class DesktopTransportAction { PLAY, PAUSE, PREVIOUS, NEXT, SEEK }

/** Desktop consumers capture playback and command identity from this single publication. */
data class DesktopPlayerState(
    val playback: PlaybackPresentationState = PlaybackPresentationState(),
    val target: DesktopPlaybackTarget? = null,
    val queueEditor: DesktopQueueEditorState = DesktopQueueEditorState(),
)
