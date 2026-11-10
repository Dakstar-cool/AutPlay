package app.autplay.application.releasewatch

import app.autplay.domain.ServerProfileId

enum class WatchReason { MANUAL_FOLLOW, LISTENING_HISTORY }

data class WatchedArtist(
    val artistId: String,
    val reason: WatchReason,
    val scoreMilli: Int?,
    val favoriteGeneration: Long?,
    val autoDownload: ArtistAutoDownloadStatus? = null,
)

enum class ArtistAutoDownloadReason {
    OWNER_CONSENT_OFF,
    ARTIST_REVIEW_REQUIRED,
    WATCH_POLICY_PENDING,
    STAGED_GATE_OFF,
    READY,
}

data class ArtistAutoDownloadStatus(
    val watchPolicyId: String?,
    val policyRevision: Long?,
    val policyImportMode: String?,
    val effectiveOptIn: Boolean,
    val executionEnabled: Boolean,
    val reason: ArtistAutoDownloadReason,
)

data class OwnerAutoDownloadSettings(
    val enabled: Boolean,
    val revision: Long,
    val executionEnabled: Boolean,
    val consequenceConfirmation: String?,
)

data class WatchPage<T>(val items: List<T>)

interface ReleaseWatchPort {
    suspend fun artists(profileId: ServerProfileId, afterArtistId: String? = null): WatchPage<WatchedArtist>
    suspend fun exclusions(profileId: ServerProfileId, afterArtistId: String? = null): WatchPage<String>
    suspend fun setFollow(profileId: ServerProfileId, artistId: String, follow: Boolean)
    suspend fun autoDownloadSettings(profileId: ServerProfileId): OwnerAutoDownloadSettings =
        throw ReleaseWatchException(WatchFailure.UNAVAILABLE)
    suspend fun setAutoDownload(
        profileId: ServerProfileId,
        enabled: Boolean,
        expectedRevision: Long,
        consequenceConfirmation: String?,
    ) { throw ReleaseWatchException(WatchFailure.UNAVAILABLE) }
}

enum class WatchFailure { OFFLINE, SESSION, UNAVAILABLE, SERVER }

class ReleaseWatchException(val failure: WatchFailure) : Exception(failure.name)
