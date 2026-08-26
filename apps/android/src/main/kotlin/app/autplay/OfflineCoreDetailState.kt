package app.autplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistCatalogPort
import app.autplay.application.artist.ArtistCredit
import app.autplay.application.artist.ArtistDetail
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CoreProductRepository
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreTrackDetail
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CancellationException

internal data class OfflineDetailRequestKey(
    val generation: Long,
    val contextKey: String,
    val profileId: String?,
    val target: DetailTarget?,
)

internal class OfflineDetailRequestGuard(initialGeneration: Long = 0L) {
    private var nextGeneration = initialGeneration
    private var active: OfflineDetailRequestKey? = null

    fun begin(contextKey: String, profileId: String?, target: DetailTarget?): OfflineDetailRequestKey =
        OfflineDetailRequestKey(++nextGeneration, contextKey, profileId, target).also { active = it }

    fun accepts(key: OfflineDetailRequestKey): Boolean = active == key
}

/** Keeps detail loading data-flow out of the root Compose declaration. */
@Stable
internal class OfflineCoreDetailState {
    var track: CoreTrackDetail? by mutableStateOf(null)
    var release: CoreReleaseDetail? by mutableStateOf(null)
    var playlist: CorePlaylistDetail? by mutableStateOf(null)
    var artist: ArtistDetail? by mutableStateOf(null)
    var artistAppearances: List<ArtistAppearance> by mutableStateOf(emptyList())
    var subjectArtistCredits: List<ArtistCredit> by mutableStateOf(emptyList())
    var loading: Boolean by mutableStateOf(false)
    var error: Boolean by mutableStateOf(false)
    var loadedContextKey: String? by mutableStateOf(null)
    var loadedTarget: DetailTarget? by mutableStateOf(null)
    private val requestGuard = OfflineDetailRequestGuard()

    private fun reset(target: DetailTarget?) {
        track = null
        release = null
        playlist = null
        artist = null
        artistAppearances = emptyList()
        subjectArtistCredits = emptyList()
        error = false
        loadedContextKey = null
        loadedTarget = null
        loading = target != null
    }

    internal suspend fun load(
        repository: CoreProductRepository,
        artistCatalogPort: ArtistCatalogPort,
        target: DetailTarget?,
        profileId: String?,
        contextKey: String,
        reportError: (String) -> Unit,
    ) {
        val request = requestGuard.begin(contextKey, profileId, target)
        reset(target)
        if (target == null) return
        try {
            when (target.kind) {
                DetailKind.Track -> {
                    val loadedTrack = repository.trackDetail(target.stableId, profileId)
                    val credits = loadedTrack?.serverRecordingId?.let { recordingId ->
                        loadSubjectCredits(artistCatalogPort, profileId, SUBJECT_RECORDING, recordingId.value)
                    }.orEmpty()
                    if (requestGuard.accepts(request)) {
                        track = loadedTrack
                        subjectArtistCredits = credits
                    }
                }
                DetailKind.Release -> {
                    val loadedRelease = repository.releaseDetail(target.stableId, profileId)
                    val credits = loadedRelease?.serverReleaseId?.let { releaseId ->
                        loadSubjectCredits(artistCatalogPort, profileId, SUBJECT_RELEASE, releaseId.value)
                    }.orEmpty()
                    if (requestGuard.accepts(request)) {
                        release = loadedRelease
                        subjectArtistCredits = credits
                    }
                }
                DetailKind.Playlist -> {
                    val loadedPlaylist = repository.playlistDetail(target.stableId, profileId)
                    if (requestGuard.accepts(request)) playlist = loadedPlaylist
                }
                DetailKind.Artist -> {
                    val activeProfile = profileId?.let(::ServerProfileId)
                    val key = activeProfile?.let { ArtistKey(it, ArtistId(target.stableId)) }
                    val loadedArtist = key?.let {
                        artistCatalogPort.detail(it, MAX_ARTIST_CREDITS, MAX_ARTIST_CREDIT_MEMBERS)
                    }
                    val appearances = key?.let {
                        artistCatalogPort.appearances(it, MAX_ARTIST_APPEARANCES)
                    }.orEmpty()
                    if (requestGuard.accepts(request)) {
                        artist = loadedArtist
                        artistAppearances = appearances
                    }
                }
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            if (requestGuard.accepts(request)) {
                error = true
                loading = false
                loadedContextKey = contextKey
                loadedTarget = target
                reportError("DETAIL_UNAVAILABLE")
            }
            return
        }
        if (!requestGuard.accepts(request)) return
        loadedContextKey = contextKey
        loadedTarget = target
        error = when (target.kind) {
            DetailKind.Track -> track == null
            DetailKind.Release -> release == null
            DetailKind.Playlist -> playlist == null
            DetailKind.Artist -> artist == null
        }
        if (error) reportError("DETAIL_UNAVAILABLE")
        loading = false
    }

    internal fun matches(contextKey: String, target: DetailTarget?): Boolean =
        loadedContextKey == contextKey && loadedTarget == target

    private suspend fun loadSubjectCredits(
        artistCatalogPort: ArtistCatalogPort,
        profileId: String?,
        subjectType: String,
        subjectId: String,
    ): List<ArtistCredit> {
        val activeProfile = profileId?.let(::ServerProfileId) ?: return emptyList()
        return artistCatalogPort.subjectCredits(
            activeProfile,
            subjectType,
            app.autplay.domain.ServerId(subjectId),
            MAX_SUBJECT_CREDITS,
        ).mapNotNull { link ->
            artistCatalogPort.credit(activeProfile, link.creditId, MAX_ARTIST_CREDIT_MEMBERS)
        }
    }

    private companion object {
        const val SUBJECT_RECORDING = "RECORDING"
        const val SUBJECT_RELEASE = "RELEASE"
        const val MAX_SUBJECT_CREDITS = 8
        const val MAX_ARTIST_CREDITS = 100
        const val MAX_ARTIST_CREDIT_MEMBERS = 50
        const val MAX_ARTIST_APPEARANCES = 200
    }
}

@Composable
internal fun rememberOfflineCoreDetailState(
    repository: CoreProductRepository,
    artistCatalogPort: ArtistCatalogPort,
    target: DetailTarget?,
    profileId: String?,
    contextKey: String,
    reportError: (String) -> Unit,
): OfflineCoreDetailState {
    val state = remember(contextKey) { OfflineCoreDetailState() }
    LaunchedEffect(target, profileId, contextKey) {
        state.load(repository, artistCatalogPort, target, profileId, contextKey, reportError)
    }
    return state
}
