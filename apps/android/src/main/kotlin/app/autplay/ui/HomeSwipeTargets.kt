package app.autplay.ui

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.application.playback.OrdinaryQueueEntry
import app.autplay.application.playback.OrdinaryQueueProjection
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.core.CoreTrackSummary
import app.autplay.ui.core.TrackAvailability

public data class HomeSwipeTarget(
    public val key: String,
    public val trackRefId: String,
    public val title: String?,
    public val artist: String?,
    public val queueEntryId: String? = null,
)

public data class HomeSwipeTargets(
    public val previous: HomeSwipeTarget? = null,
    public val next: HomeSwipeTarget? = null,
)

/** Adjacent previews and commands share the controller's order and the same queue identity. */
internal fun buildHomeSwipeTargets(
    playerState: PlaybackPresentationState,
    currentTrackRefId: String?,
    homeState: HomeScreenUiState,
    queue: OrdinaryQueueProjection?,
    libraryTracks: List<CoreTrackSummary>,
): HomeSwipeTargets {
    val mediaId = playerState.mediaId
    if (mediaId != null) {
        if (playerState.controls !is PlaybackControlGate.Allowed) return HomeSwipeTargets()
        val context = playerState.context as? ActiveQueueContext.Loaded ?: return HomeSwipeTargets()
        if (
            queue == null || queue.queueType !in ORDINARY_QUEUE_TYPES ||
            context.queueType != queue.queueType || context.snapshotId != queue.snapshotId.value ||
            context.currentEntryId != mediaId || queue.currentEntryId?.value != mediaId
        ) return HomeSwipeTargets()
        val currentEntry = queue.entries.singleOrNull { it.queueEntryId.value == mediaId }
            ?: return HomeSwipeTargets()
        if (currentEntry.trackRefId.value != currentTrackRefId) return HomeSwipeTargets()
        if (queue.entries.size > 1) {
            fun target(id: String?): HomeSwipeTarget? = id?.takeUnless { it == mediaId }?.let { adjacentId ->
                queue.entries.singleOrNull { it.queueEntryId.value == adjacentId }?.toSwipeTarget()
            }
            return HomeSwipeTargets(
                previous = target(playerState.previousMediaId),
                next = target(playerState.nextMediaId),
            )
        }
    }
    val currentId = if (mediaId != null) currentTrackRefId else homeState.continueListening?.trackId
        ?: homeState.recentlyPlayed.firstOrNull()?.id
        ?: homeState.recentlyAdded.firstOrNull()?.id
    if (currentId == null) return HomeSwipeTargets()
    val available = libraryTracks.filter { it.availability == TrackAvailability.Available }
    val currentIndex = available.indexOfFirst { it.stableId == currentId }
    if (currentIndex < 0) return HomeSwipeTargets()
    fun target(index: Int): HomeSwipeTarget? = available.getOrNull(index)?.let {
        HomeSwipeTarget(key = it.stableId, trackRefId = it.stableId, title = it.title, artist = it.artist)
    }
    return HomeSwipeTargets(previous = target(currentIndex - 1), next = target(currentIndex + 1))
}

private fun OrdinaryQueueEntry.toSwipeTarget(): HomeSwipeTarget = HomeSwipeTarget(
    key = queueEntryId.value,
    trackRefId = trackRefId.value,
    title = title,
    artist = artist,
    queueEntryId = queueEntryId.value,
)

private val ORDINARY_QUEUE_TYPES = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
