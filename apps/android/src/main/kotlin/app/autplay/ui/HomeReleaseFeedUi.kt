package app.autplay.ui

import app.autplay.application.releasewatch.ReleaseFeedAction
import app.autplay.application.releasewatch.ReleaseFeedState
import app.autplay.application.releasewatch.WatchReason

public enum class HomeReleaseFeedUiStatus { Disconnected, Loading, Ready, Offline, Session, Unavailable, Error }

public data class HomeReleaseFeedUiItem(
    val candidateId: String,
    val evidenceRunId: String,
    val title: String,
    val artistName: String,
    val releasedAt: String,
    val reason: WatchReason,
    val state: ReleaseFeedState,
    val availableAction: ReleaseFeedAction,
    val playableLocalTrackId: String?,
)

public data class HomeReleaseFeedUiState(
    val status: HomeReleaseFeedUiStatus = HomeReleaseFeedUiStatus.Disconnected,
    val items: List<HomeReleaseFeedUiItem> = emptyList(),
    val hasMore: Boolean = false,
    val loadingMore: Boolean = false,
    val pageFailure: HomeReleaseFeedUiStatus? = null,
)
