package app.autplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.setValue
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistCatalogPort
import app.autplay.application.artist.ArtistCredit
import app.autplay.application.artist.ArtistDetail
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CoreProductRepository
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.library.CoreReleaseSummary
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreTrackDetail
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.collectLatest

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

private val artistDetailKinds = setOf(DetailKind.Artist, DetailKind.ArtistTracks, DetailKind.ArtistCollaborations, DetailKind.ArtistAlbums)

internal data class CachedArtistPage(
    val artist: ArtistDetail,
    val appearances: List<ArtistAppearance> = emptyList(),
    val albums: List<CoreReleaseSummary> = emptyList(),
    val completedRevision: Long? = null,
)

internal class ArtistPageCache {
    val pages = object : LinkedHashMap<ArtistKey, CachedArtistPage>(8, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<ArtistKey, CachedArtistPage>?): Boolean = size > 8
    }
    var revision by mutableLongStateOf(0L)
        private set

    fun invalidate() { revision++ }
}

/** Keeps detail loading data-flow out of the root Compose declaration. */
@Stable
internal class OfflineCoreDetailState(private val artistCache: ArtistPageCache = ArtistPageCache()) {
    var track: CoreTrackDetail? by mutableStateOf(null)
    var release: CoreReleaseDetail? by mutableStateOf(null)
    var playlist: CorePlaylistDetail? by mutableStateOf(null)
    var artist: ArtistDetail? by mutableStateOf(null)
    var artistAlbums: List<CoreReleaseSummary> by mutableStateOf(emptyList())
    var artistAppearances: List<ArtistAppearance> by mutableStateOf(emptyList())
    var subjectArtistCredits: List<ArtistCredit> by mutableStateOf(emptyList())
    var loading: Boolean by mutableStateOf(false)
    var error: Boolean by mutableStateOf(false)
    var loadedContextKey: String? by mutableStateOf(null)
    var loadedTarget: DetailTarget? by mutableStateOf(null)
    private val requestGuard = OfflineDetailRequestGuard()
    var artistContentLoading by mutableStateOf(false)
        private set

    private fun showArtist(page: CachedArtistPage, contextKey: String, target: DetailTarget) {
        artist = page.artist
        artistAppearances = page.appearances
        artistAlbums = page.albums
        artistContentLoading = page.completedRevision == null
        loading = false
        error = false
        loadedContextKey = contextKey
        loadedTarget = target
    }

    fun restoreArtist(profileId: String?, contextKey: String, target: DetailTarget?) {
        if (target == null || target.kind !in artistDetailKinds) return
        val key = runCatching { ArtistKey(app.autplay.application.artist.artistScope(profileId), ArtistId(target.stableId)) }.getOrNull()
        key?.let { artistCache.pages[it] }?.let { showArtist(it, contextKey, target) }
    }

    private fun reset(target: DetailTarget?) {
        track = null
        release = null
        playlist = null
        artist = null
        artistContentLoading = false
        artistAlbums = emptyList()
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
        if (!matches(contextKey, target)) reset(target)
        if (target == null) return
        try {
            when (target.kind) {
                DetailKind.CatalogRelease, DetailKind.CatalogArtistTracks -> Unit
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
                DetailKind.Artist, DetailKind.ArtistTracks, DetailKind.ArtistCollaborations, DetailKind.ArtistAlbums -> {
                    val key = ArtistKey(app.autplay.application.artist.artistScope(profileId), ArtistId(target.stableId))
                    val revision = artistCache.revision
                    val previous = artistCache.pages[key]
                    if (previous != null) showArtist(previous, contextKey, target)
                    if (previous?.completedRevision == revision) return

                    var detail: ArtistDetail? = null
                    val appearances = artistCatalogPort.loadArtistPage(key, MAX_ARTIST_CREDITS, MAX_ARTIST_CREDIT_MEMBERS) {
                        detail = it
                        if (previous?.completedRevision == null && requestGuard.accepts(request)) {
                            val header = CachedArtistPage(it, previous?.appearances.orEmpty())
                            artistCache.pages[key] = header
                            showArtist(header, contextKey, target)
                        }
                    }
                    if (!requestGuard.accepts(request)) return
                    if (appearances == null) {
                        artistCache.pages.remove(key)
                        reset(target)
                    } else {
                        val partial = CachedArtistPage(requireNotNull(detail), appearances)
                        if (previous?.completedRevision == null) {
                            artistCache.pages[key] = partial
                            showArtist(partial, contextKey, target)
                        }
                        val albums = withContext(Dispatchers.Default) {
                            val tracks = appearances.mapNotNull { (it.localTarget as? ArtistLocalTarget.Track)?.localUserTrackRefId }.toSet()
                            repository.artistReleases(profileId ?: "legacy-unscoped", tracks)
                        }
                        if (!requestGuard.accepts(request)) return
                        val page = partial.copy(albums = albums, completedRevision = revision)
                        artistCache.pages[key] = page
                        showArtist(page, contextKey, target)
                    }
                }
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            if (requestGuard.accepts(request)) {
                error = artist == null
                artistContentLoading = false
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
            DetailKind.CatalogRelease, DetailKind.CatalogArtistTracks -> false
            DetailKind.Track -> track == null
            DetailKind.Release -> release == null
            DetailKind.Playlist -> playlist == null
            DetailKind.Artist, DetailKind.ArtistTracks, DetailKind.ArtistCollaborations, DetailKind.ArtistAlbums -> artist == null
        }
        if (error) reportError("DETAIL_UNAVAILABLE")
        loading = false
    }

    internal fun matches(contextKey: String, target: DetailTarget?): Boolean =
        loadedContextKey == contextKey && (loadedTarget == target ||
            (target != null && target.kind in artistDetailKinds && loadedTarget?.kind in artistDetailKinds &&
                loadedTarget?.stableId == target.stableId))

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
    }
}

@Composable
internal fun rememberOfflineCoreDetailState(
    repository: CoreProductRepository,
    artistCatalogPort: ArtistCatalogPort,
    target: DetailTarget?,
    profileId: String?,
    contextKey: String,
    ownerKey: String,
    reportError: (String) -> Unit,
): OfflineCoreDetailState {
    val cache = remember(ownerKey, profileId, repository, artistCatalogPort) { ArtistPageCache() }
    LaunchedEffect(cache) { repository.artistChanges().collect { cache.invalidate() } }
    val route = if (target != null && target.kind in artistDetailKinds) DetailTarget(DetailKind.Artist, target.stableId) else target
    val state = remember(cache, contextKey, route) {
        OfflineCoreDetailState(cache).also { it.restoreArtist(profileId, contextKey, route) }
    }
    LaunchedEffect(state) {
        if (route?.kind == DetailKind.Artist) {
            snapshotFlow { cache.revision }.conflate().collect {
                state.load(repository, artistCatalogPort, route, profileId, contextKey, reportError)
            }
        } else if (target?.kind == DetailKind.Track) {
            repository.trackMetadataChanges(target.stableId, profileId).collectLatest {
                state.load(repository, artistCatalogPort, target, profileId, contextKey, reportError)
            }
        } else if (target?.kind in setOf(DetailKind.Release, DetailKind.Playlist)) {
            repository.releaseChanges().collectLatest {
                state.load(repository, artistCatalogPort, target, profileId, contextKey, reportError)
            }
        } else state.load(repository, artistCatalogPort, target, profileId, contextKey, reportError)
    }
    return state
}

internal data class ArtistPopularity(
    val artistMbid: String? = null,
    val counts: Map<String, Long> = emptyMap(),
    val publicCounts: Map<String, Long> = emptyMap(),
    val libraryRecordings: Map<String, String> = emptyMap(),
    val loading: Boolean = false,
)

@Composable
internal fun rememberArtistRanking(context: android.content.Context, binding: app.autplay.application.sync.ClientEventBinding?,
    artist: String?): ArtistPopularity {
    val recent = remember(binding) { android.util.LruCache<String, Pair<Long, ArtistPopularity>>(8) }
    val saved = remember(recent, artist) { artist?.let(recent::get) }
    var counts by remember(recent, artist) { mutableStateOf(saved?.second ?: ArtistPopularity(loading = binding != null && artist != null)) }
    LaunchedEffect(binding, artist) {
        if (binding == null || artist == null) return@LaunchedEffect
        if (saved != null && android.os.SystemClock.elapsedRealtime() - saved.first < 60_000L) return@LaunchedEffect
        if (runCatching { java.util.UUID.fromString(artist) }.isFailure) {
            counts = ArtistPopularity()
            return@LaunchedEffect
        }
        val dao = AutPlayRuntime.database(context).catalogProjectionDao()
        val key = "ranking-v2:${binding.userId.value}:$artist"
        val profile = binding.serverProfileId.value
        fun decode(value: kotlinx.serialization.json.JsonObject): ArtistPopularity {
            fun counts(field: String) = (value[field] as? kotlinx.serialization.json.JsonObject).orEmpty()
                .mapNotNull { (id, count) -> count.toString().toLongOrNull()?.takeIf { it >= 0 }?.let { id to it } }.toMap()
            val identities = (value["library_recordings"] as? kotlinx.serialization.json.JsonObject).orEmpty()
                .mapNotNull { (mbid, id) -> (id as? kotlinx.serialization.json.JsonPrimitive)?.content?.let { mbid to it } }.toMap()
            return ArtistPopularity((value["artist_mbid"] as? kotlinx.serialization.json.JsonPrimitive)?.content,
                counts("counts"), counts("recording_counts"), identities)
        }
        fun publish(value: ArtistPopularity) {
            counts = value
            recent.put(artist, android.os.SystemClock.elapsedRealtime() to value)
        }
        try {
            val cached = dao.catalogPage(profile, key)
            val previous = cached ?: dao.catalogPage(profile, "ranking:${binding.userId.value}:$artist")
            val prior = previous?.let { row -> withContext(Dispatchers.Default) {
                runCatching {
                    val json = kotlinx.serialization.json.Json.parseToJsonElement(row.document) as kotlinx.serialization.json.JsonObject
                    json to decode(json)
                }.getOrNull()
            } }
            if (prior != null) {
                counts = prior.second
                if (cached != null && System.currentTimeMillis() - cached.observedAtMs in 0 until 3_600_000L) {
                    publish(prior.second)
                    return@LaunchedEffect
                }
            }
            val document = AutPlayRuntime.serverFeatures(context, binding).artistRanking(artist)
            if (document["ranking_available"]?.toString() == "false") {
                val identity = withContext(Dispatchers.Default) { decode(document) }
                val sameArtist = counts.artistMbid == identity.artistMbid
                publish(if (sameArtist) identity.copy(counts = counts.counts, publicCounts = counts.publicCounts) else identity)
                val merged = if (prior != null && sameArtist) {
                    kotlinx.serialization.json.JsonObject(document + prior.first.filterKeys { it == "counts" || it == "recording_counts" })
                } else document
                dao.putCatalogPage(app.autplay.data.local.entity.CatalogPage(profile, key, merged.toString(), 0L))
                dao.trimCatalogPages(profile)
                return@LaunchedEffect
            }
            publish(withContext(Dispatchers.Default) { decode(document) })
            dao.putCatalogPage(app.autplay.data.local.entity.CatalogPage(profile, key, document.toString(), System.currentTimeMillis()))
            dao.trimCatalogPages(profile)
        } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { counts = counts.copy(loading = false) }
    }
    return counts
}
