package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.playback.ActiveQueueContext
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

public data class DesktopTransportCallbacks(
    public val onCommand: (DesktopPlaybackTarget, DesktopTransportAction, Long?) -> Unit = { _, _, _ -> },
)

internal fun desktopTransportCommandsEnabled(playerState: DesktopPlayerState?, library: DesktopLibrarySnapshot?,
    reopenRequired: Boolean): Boolean = !reopenRequired && (playerState == null || library != null)

/** Captures one coherent publication. Position updates do not retire an otherwise valid command. */
internal class DesktopTransportInput(
    val playerState: DesktopPlayerState?,
    legacyPlayback: PlaybackPresentationState,
    val adapterAvailable: Boolean,
    val commandsEnabled: Boolean,
    val section: DesktopSection,
    val navigationRevision: Long,
) {
    val playback: PlaybackPresentationState = playerState?.playback ?: legacyPlayback
    val target: DesktopPlaybackTarget? = playerState?.target

    fun controls(allowLegacy: Boolean): DesktopPlaybackControls = DesktopPlaybackControls.from(playback,
        adapterAvailable && commandsEnabled && if (playerState != null) hasTarget() else allowLegacy)

    private fun hasTarget(): Boolean {
        val context = playback.context as? ActiveQueueContext.Loaded ?: return false
        return target != null && target.queueId == context.snapshotId &&
            target.entryId == context.currentEntryId && target.entryId == playback.mediaId
    }

    fun canDispatch(current: DesktopTransportInput, action: DesktopTransportAction,
        navigationCurrent: Boolean, allowLegacy: Boolean): Boolean {
        if (!navigationCurrent || section != current.section || navigationRevision != current.navigationRevision ||
            playback.mediaId != current.playback.mediaId || playback.context != current.playback.context ||
            (playerState != null) != (current.playerState != null) || target != current.target) return false
        fun enabled(input: DesktopTransportInput): Boolean {
            val controls = input.controls(allowLegacy)
            return when (action) {
                DesktopTransportAction.PLAY -> controls.playPause && !input.playback.isPlaying
                DesktopTransportAction.PAUSE -> controls.playPause && input.playback.isPlaying
                DesktopTransportAction.PREVIOUS -> controls.previous && playback.previousMediaId == current.playback.previousMediaId
                DesktopTransportAction.NEXT -> controls.next && playback.nextMediaId == current.playback.nextMediaId
                DesktopTransportAction.SEEK -> controls.seek
            }
        }
        return enabled(this) && enabled(current)
    }
}

/** Authoritative wrappers never fall back to generic commands, including missing/stale targets. */
internal fun dispatchDesktopTransport(captured: DesktopTransportInput, current: DesktopTransportInput,
    action: DesktopTransportAction, positionMs: Long?, navigationCurrent: Boolean, allowLegacy: Boolean,
    callbacks: DesktopTransportCallbacks, legacy: DesktopPlaybackCallbacks): Boolean {
    if (!captured.canDispatch(current, action, navigationCurrent, allowLegacy)) return false
    if (action == DesktopTransportAction.SEEK &&
        (positionMs == null || positionMs < 0 || positionMs > (current.playback.durationMs ?: 0))) return false
    if (captured.playerState != null) {
        val target = captured.target ?: return false
        callbacks.onCommand(target, action, positionMs.takeIf { action == DesktopTransportAction.SEEK })
    } else when (action) {
        DesktopTransportAction.PLAY -> legacy.onPlay()
        DesktopTransportAction.PAUSE -> legacy.onPause()
        DesktopTransportAction.PREVIOUS -> legacy.onPrevious()
        DesktopTransportAction.NEXT -> legacy.onNext()
        DesktopTransportAction.SEEK -> legacy.onSeek(positionMs ?: return false)
    }
    return true
}

/** The first slider event freezes identity and duration until finish, even if the item changes. */
internal class DesktopSeekDraft(val input: DesktopTransportInput, val durationMs: Long, val fraction: Float) {
    fun matches(current: DesktopTransportInput, navigationCurrent: Boolean, allowLegacy: Boolean): Boolean =
        durationMs > 0 && durationMs == current.playback.durationMs &&
            input.playback.playbackStatus == current.playback.playbackStatus &&
            input.canDispatch(current, DesktopTransportAction.SEEK, navigationCurrent, allowLegacy)

    fun withFraction(value: Float): DesktopSeekDraft = DesktopSeekDraft(input, durationMs, value.coerceIn(0f, 1f))

    fun positionMs(): Long? = if (fraction.isFinite() && durationMs > 0) {
        (fraction.coerceIn(0f, 1f).toDouble() * durationMs).toLong().coerceIn(0, durationMs)
    } else null
}

/** Recreate the actual Slider on identity/gate changes, leaving an old gesture with its old owner. */
internal class DesktopSeekLifetime(private val input: DesktopTransportInput, private val allowLegacy: Boolean) {
    private val enabled: Boolean = input.controls(allowLegacy).seek

    override fun equals(other: Any?): Boolean = other is DesktopSeekLifetime &&
        allowLegacy == other.allowLegacy && enabled == other.enabled && input.target == other.input.target &&
        (input.playerState != null) == (other.input.playerState != null) &&
        input.playback.mediaId == other.input.playback.mediaId && input.playback.context == other.input.playback.context &&
        input.playback.durationMs == other.input.playback.durationMs &&
        input.playback.playbackStatus == other.input.playback.playbackStatus &&
        input.section == other.input.section && input.navigationRevision == other.input.navigationRevision

    override fun hashCode(): Int = 31 * (input.target?.hashCode() ?: 0) + input.navigationRevision.hashCode()
}

/** Cancellation frees this gesture; disposal permanently retires old callbacks, including A/B/A inputs. */
internal class DesktopSeekGesture {
    private var draft: DesktopSeekDraft? = null
    private var retired: Boolean = false

    fun change(captured: DesktopTransportInput, current: DesktopTransportInput, fraction: Float,
        navigationCurrent: Boolean, allowLegacy: Boolean): DesktopSeekDraft? {
        if (retired || !fraction.isFinite()) return draft
        val existing = draft
        if (existing != null) {
            if (existing.matches(current, navigationCurrent, allowLegacy)) draft = existing.withFraction(fraction)
        } else {
            val duration = captured.playback.durationMs
            if (duration != null && duration > 0 &&
                captured.canDispatch(current, DesktopTransportAction.SEEK, navigationCurrent, allowLegacy)) {
                draft = DesktopSeekDraft(captured, duration, fraction.coerceIn(0f, 1f))
            }
        }
        return draft
    }

    fun finish(current: DesktopTransportInput, navigationCurrent: Boolean, allowLegacy: Boolean): DesktopSeekDraft? {
        val finished = draft
        draft = null
        return finished?.takeIf { !retired && it.matches(current, navigationCurrent, allowLegacy) }
    }

    fun cancel(): Unit { draft = null }

    fun retire(): Unit {
        retired = true
        cancel()
    }
}
