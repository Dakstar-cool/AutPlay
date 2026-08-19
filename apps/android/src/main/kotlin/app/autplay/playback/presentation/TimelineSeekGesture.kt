package app.autplay.playback.presentation

/** Pure, deterministic timeline gesture reducer. */
object TimelineSeekGesture {
    data object Idle : State

    data class Dragging(
        val mediaId: String,
        val durationMs: Long,
        val targetMs: Long,
    ) : State

    sealed interface State

    data class Commit(val mediaId: String, val positionMs: Long)
    data class Result(val state: State, val commit: Commit? = null)

    fun begin(state: PlaybackPresentationState, targetMs: Long): State =
        if (!state.canSeek || state.mediaId == null || state.durationMs == null) Idle
        else Dragging(state.mediaId, state.durationMs, clamp(targetMs, state.durationMs))

    fun drag(state: State, targetMs: Long): State = when (state) {
        Idle -> Idle
        is Dragging -> state.copy(targetMs = clamp(targetMs, state.durationMs))
    }

    /** Clears the gesture before dispatch, making repeat end-events harmless. */
    fun commit(state: State, authoritative: PlaybackPresentationState): Result = when (state) {
        Idle -> Result(Idle)
        is Dragging -> if (
            authoritative.canSeek &&
            authoritative.mediaId == state.mediaId &&
            authoritative.durationMs == state.durationMs
        ) Result(Idle, Commit(state.mediaId, state.targetMs)) else Result(Idle)
    }

    /** Queue, duration, live/seekability, or gate changes cancel an in-flight drag. */
    fun reconcile(state: State, authoritative: PlaybackPresentationState): State = when (state) {
        Idle -> Idle
        is Dragging -> if (
            authoritative.canSeek && authoritative.mediaId == state.mediaId &&
            authoritative.durationMs == state.durationMs
        ) state else Idle
    }

    fun clamp(positionMs: Long, durationMs: Long): Long = positionMs.coerceIn(0, durationMs.coerceAtLeast(0))
}
