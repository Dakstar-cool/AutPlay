package app.autplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.produceState
import app.autplay.application.artist.ArtistCatalogPort
import app.autplay.application.artist.ArtistSummary
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.collect

internal enum class OfflineArtistBrowseStatus {
    Unavailable,
    Loading,
    Ready,
    Error,
}

internal data class OfflineArtistBrowseSnapshot(
    val status: OfflineArtistBrowseStatus,
    val artists: List<ArtistSummary> = emptyList(),
)

/** Profile-keyed derived state; cancelled collectors never publish into the next binding. */
@Composable
internal fun rememberOfflineArtistBrowseSnapshot(
    artistCatalogPort: ArtistCatalogPort,
    profileId: String?,
): OfflineArtistBrowseSnapshot {
    val initial = if (profileId == null) {
        OfflineArtistBrowseSnapshot(OfflineArtistBrowseStatus.Unavailable)
    } else {
        OfflineArtistBrowseSnapshot(OfflineArtistBrowseStatus.Loading)
    }
    return produceState(initial, artistCatalogPort, profileId) {
        if (profileId == null) {
            value = OfflineArtistBrowseSnapshot(OfflineArtistBrowseStatus.Unavailable)
            return@produceState
        }
        try {
            artistCatalogPort.observeBrowse(ServerProfileId(profileId), MAX_ARTISTS).collect { artists ->
                value = OfflineArtistBrowseSnapshot(OfflineArtistBrowseStatus.Ready, artists)
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            value = OfflineArtistBrowseSnapshot(OfflineArtistBrowseStatus.Error)
        }
    }.value
}

private const val MAX_ARTISTS = 500
