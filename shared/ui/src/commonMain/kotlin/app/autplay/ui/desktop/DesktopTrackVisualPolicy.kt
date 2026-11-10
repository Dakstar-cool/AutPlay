package app.autplay.ui.desktop

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackPresentationState

/** A queue occurrence, deliberately independent of position, labels, session and native generation. */
internal data class DesktopTrackVisualIdentity(val queueId: String, val entryId: String)

internal fun desktopTrackVisualIdentity(playback: PlaybackPresentationState): DesktopTrackVisualIdentity? {
    val context = playback.context as? ActiveQueueContext.Loaded ?: return null
    val entryId = playback.mediaId?.takeIf { it.isNotBlank() } ?: return null
    if (context.snapshotId.isBlank() || context.currentEntryId != entryId) return null
    return DesktopTrackVisualIdentity(context.snapshotId, entryId)
}

/** T is passive presentation data only. It must not contain commands or owners. */
internal data class DesktopTrackVisualFrame<T>(val identity: DesktopTrackVisualIdentity?, val value: T)

/** Referential token prevents completion/cancellation of an old A/B/A transition retiring a new one. */
internal class DesktopTrackVisualTransition

internal data class DesktopTrackVisualFrames<T>(
    val current: DesktopTrackVisualFrame<T>,
    val section: DesktopSection,
    val navigationRevision: Long,
    val navigationCurrent: Boolean,
    val outgoing: DesktopTrackVisualFrame<T>? = null,
    val transition: DesktopTrackVisualTransition? = null,
) {
    val frameCount: Int get() = if (outgoing == null) 1 else 2
}

/** Replace, never accumulate, the outgoing scene. All reset decisions apply before drawing. */
internal fun <T> advanceDesktopTrackVisualFrames(
    previous: DesktopTrackVisualFrames<T>?, current: DesktopTrackVisualFrame<T>,
    section: DesktopSection, navigationRevision: Long, navigationCurrent: Boolean,
    smoothTrackTransitions: Boolean,
): DesktopTrackVisualFrames<T> {
    val canRetain = smoothTrackTransitions && navigationCurrent && previous != null &&
        previous.navigationCurrent && previous.section == section && previous.navigationRevision == navigationRevision &&
        previous.current.identity != null && current.identity != null
    val changed = previous?.current?.identity != current.identity
    val outgoing = when {
        !canRetain -> null
        changed -> previous?.current
        else -> previous?.outgoing
    }
    val transition = when {
        outgoing == null -> null
        changed -> DesktopTrackVisualTransition()
        else -> previous?.transition
    }
    return DesktopTrackVisualFrames(current, section, navigationRevision, navigationCurrent, outgoing, transition)
}

internal fun <T> finishDesktopTrackVisualTransition(
    frames: DesktopTrackVisualFrames<T>, transition: DesktopTrackVisualTransition,
): DesktopTrackVisualFrames<T> = if (frames.transition === transition) {
    frames.copy(outgoing = null, transition = null)
} else frames
