package app.autplay.application.releasewatch

import app.autplay.domain.ServerProfileId

enum class ReleaseFeedState { DISCOVERED, REVIEW_REQUIRED, ACQUIRING, READY, UNAVAILABLE, FAILED }
enum class ReleaseFeedAction { PLAY, REVIEW, OPEN_DISCOVERY, NONE }

data class ReleaseFeedItem(
    val candidateId: String,
    val canonicalArtistId: String,
    val artistName: String,
    val title: String,
    val releasedAt: String,
    val reason: WatchReason,
    val state: ReleaseFeedState,
    val scoreMilli: Int,
    val releaseId: String?,
    val recordingId: String?,
    val userTrackRefId: String?,
    val providerId: String,
    val evidenceRunId: String,
    val evidenceAdapterVersion: String,
    val availableAction: ReleaseFeedAction,
)

data class ReleaseFeedPage(val items: List<ReleaseFeedItem>, val nextCursor: String?)

interface ReleaseFeedPort {
    suspend fun page(profileId: ServerProfileId, cursor: String? = null): ReleaseFeedPage
}

enum class ReleaseFeedFailure { OFFLINE, SESSION, UNAVAILABLE, SERVER }
class ReleaseFeedException(val failure: ReleaseFeedFailure) : Exception(failure.name)
