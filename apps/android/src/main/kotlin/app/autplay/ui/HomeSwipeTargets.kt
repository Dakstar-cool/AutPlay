package app.autplay.ui

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.application.playback.OrdinaryQueueEntry
import app.autplay.application.playback.OrdinaryQueueProjection
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.RepeatModePresentation
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

/** Adjacent previews follow the controller, then continue through the available library at a boundary. */
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
        val libraryTargets = libraryNeighbors(currentTrackRefId, libraryTracks, wrap = true, allowUnlisted = true)
        if (queue.entries.size > 1) {
            fun target(id: String?): HomeSwipeTarget? = id?.takeUnless { it == mediaId }?.let { adjacentId ->
                queue.entries.singleOrNull { it.queueEntryId.value == adjacentId }?.toSwipeTarget()
            }
            val shortUserQueueRepeats = queue.queueType == "USER" && queue.entries.size == 2 &&
                playerState.repeatMode == RepeatModePresentation.All && !playerState.shuffleModeEnabled
            val firstId = queue.entries.first().queueEntryId.value
            val lastId = queue.entries.last().queueEntryId.value
            val previousWraps = shortUserQueueRepeats && mediaId == firstId &&
                playerState.previousMediaId == lastId
            val nextWraps = shortUserQueueRepeats && mediaId == lastId &&
                playerState.nextMediaId == firstId
            return HomeSwipeTargets(
                previous = if (previousWraps) libraryTargets.previous else
                    target(playerState.previousMediaId)
                        ?: libraryTargets.previous.takeIf { playerState.previousMediaId == null },
                next = if (nextWraps) libraryTargets.next else
                    target(playerState.nextMediaId)
                        ?: libraryTargets.next.takeIf { playerState.nextMediaId == null },
            )
        }
        return libraryTargets
    }
    val currentId = homeState.continueListening?.trackId
        ?: homeState.recentlyPlayed.firstOrNull()?.id
        ?: homeState.recentlyAdded.firstOrNull()?.id
    if (currentId == null) return HomeSwipeTargets()
    return libraryNeighbors(currentId, libraryTracks, wrap = false)
}

private fun libraryNeighbors(
    currentId: String?,
    libraryTracks: List<CoreTrackSummary>,
    wrap: Boolean,
    allowUnlisted: Boolean = false,
): HomeSwipeTargets {
    if (currentId == null) return HomeSwipeTargets()
    val available = libraryTracks.filter { it.availability == TrackAvailability.Available }
    val currentIndex = available.indexOfFirst { it.stableId == currentId }
    if (currentIndex < 0 && allowUnlisted && available.isNotEmpty()) {
        fun target(track: CoreTrackSummary) = HomeSwipeTarget(
            key = track.stableId, trackRefId = track.stableId, title = track.title, artist = track.artist,
        )
        return HomeSwipeTargets(previous = target(available.last()), next = target(available.first()))
    }
    if (currentIndex < 0 || available.size < 2) return HomeSwipeTargets()
    fun target(index: Int): HomeSwipeTarget? = available.getOrNull(index)?.let {
        HomeSwipeTarget(key = it.stableId, trackRefId = it.stableId, title = it.title, artist = it.artist)
    }
    return HomeSwipeTargets(
        previous = target(currentIndex - 1) ?: if (wrap) target(available.lastIndex) else null,
        next = target(currentIndex + 1) ?: if (wrap) target(0) else null,
    )
}

private fun OrdinaryQueueEntry.toSwipeTarget(): HomeSwipeTarget = HomeSwipeTarget(
    key = queueEntryId.value,
    trackRefId = trackRefId.value,
    title = title,
    artist = artist,
    queueEntryId = queueEntryId.value,
)

private val ORDINARY_QUEUE_TYPES = setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")
