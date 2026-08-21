package app.autplay.playback.presentation

import app.autplay.application.playback.ActiveQueueContext

/** UI-safe projection: neither Player nor MediaController escapes this package. */
data class PlaybackPresentationState(
    val mediaId: String? = null,
    val title: String? = null,
    val artist: String? = null,
    val positionMs: Long = 0,
    /** Absolute Media3 buffered position; this intentionally differs from legacy bufferedMs. */
    val bufferedPositionMs: Long = 0,
    val durationMs: Long? = null,
    val isLive: Boolean = false,
    val isSeekable: Boolean = false,
    val isPlaying: Boolean = false,
    val connection: PlaybackConnectionState = PlaybackConnectionState.Disconnected,
    val playbackStatus: PlaybackStatus = PlaybackStatus.Idle,
    val source: PlaybackSourcePresentation = PlaybackSourcePresentation.Unknown,
    val context: ActiveQueueContext = ActiveQueueContext.Loading,
    val controls: PlaybackControlGate = PlaybackControlGate.Locked(PlaybackControlLockReason.CONTEXT_LOADING),
    val seekEnabled: Boolean = false,
    val shuffleEnabled: Boolean = false,
    val repeatEnabled: Boolean = false,
    val shuffleModeEnabled: Boolean = false,
    val repeatMode: RepeatModePresentation = RepeatModePresentation.Off,
    val seekPreviewPositionMs: Long? = null,
)

val PlaybackPresentationState.canSeek: Boolean
    get() = seekEnabled && isSeekable && durationMs != null && !isLive

enum class PlaybackConnectionState { Disconnected, Connected }
enum class PlaybackStatus { Idle, Buffering, Ready, Ended }
enum class RepeatModePresentation { Off, One, All }
enum class PlaybackSourcePresentation { Local, Download, Vault, Unknown }

sealed interface PlaybackControlGate {
    data object Allowed : PlaybackControlGate
    data class Locked(val reason: PlaybackControlLockReason) : PlaybackControlGate
}

enum class PlaybackControlLockReason {
    CONTEXT_LOADING,
    CONTEXT_UNAVAILABLE,
    NO_ACTIVE_QUEUE,
    WAVE_QUEUE,
    UNSUPPORTED_QUEUE_TYPE,
    QUEUE_ENTRY_MISSING,
    MEDIA_ITEM_MISSING,
    QUEUE_MEDIA_MISMATCH,
    COMMAND_UNAVAILABLE,
    NOT_SEEKABLE,
}

/** Pure fail-closed policy shared by every direct Media3 control. */
object PlaybackCommandGate {
    private val ordinaryQueueTypes = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")

    fun evaluate(
        context: ActiveQueueContext,
        mediaId: String?,
        commandAvailable: Boolean,
        seekable: Boolean? = null,
    ): PlaybackControlGate {
        val base = when (context) {
            ActiveQueueContext.Loading -> PlaybackControlLockReason.CONTEXT_LOADING
            ActiveQueueContext.Unavailable -> PlaybackControlLockReason.CONTEXT_UNAVAILABLE
            ActiveQueueContext.Absent -> PlaybackControlLockReason.NO_ACTIVE_QUEUE
            is ActiveQueueContext.Loaded -> when {
                context.queueType == "WAVE" -> PlaybackControlLockReason.WAVE_QUEUE
                context.queueType !in ordinaryQueueTypes -> PlaybackControlLockReason.UNSUPPORTED_QUEUE_TYPE
                context.currentEntryId == null -> PlaybackControlLockReason.QUEUE_ENTRY_MISSING
                mediaId == null -> PlaybackControlLockReason.MEDIA_ITEM_MISSING
                context.currentEntryId != mediaId -> PlaybackControlLockReason.QUEUE_MEDIA_MISMATCH
                !commandAvailable -> PlaybackControlLockReason.COMMAND_UNAVAILABLE
                seekable == false -> PlaybackControlLockReason.NOT_SEEKABLE
                else -> null
            }
        }
        return base?.let(PlaybackControlGate::Locked) ?: PlaybackControlGate.Allowed
    }
}
