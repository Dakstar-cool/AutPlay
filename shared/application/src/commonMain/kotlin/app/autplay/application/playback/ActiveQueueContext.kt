package app.autplay.application.playback

/** Durable queue identity and mode used by the shared fail-closed playback policy. */
sealed interface ActiveQueueContext {
    data object Loading : ActiveQueueContext
    data object Absent : ActiveQueueContext
    data object Unavailable : ActiveQueueContext

    data class Loaded(
        val snapshotId: String,
        val currentEntryId: String?,
        val queueType: String,
    ) : ActiveQueueContext
}
