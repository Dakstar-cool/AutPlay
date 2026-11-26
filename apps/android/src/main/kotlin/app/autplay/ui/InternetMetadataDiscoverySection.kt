package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onVisibilityChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import android.util.LruCache
import app.autplay.artistCatalogCacheKey
import app.autplay.AutPlayRuntime
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.R
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.server.*
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.SessionRequiredException
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.time.Instant
import kotlinx.serialization.json.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.combine
import app.autplay.data.local.dao.HeardCatalogueTrack

private data class DiscoveryScope(val query: String, val kind: LibrarySearchKind, val request: Int,
    val profile: String?, val user: String?, val device: String?)
internal data class CatalogArtistVisitKey(val profile: String, val user: String, val device: String, val artist: String)
internal data class CatalogArtistVisit(val releases: Boolean, val page: InternetMetadataDiscoveryPage,
    val cards: List<InternetMetadataDiscoveryCard>, val observedAt: Long)

private data class SelectedCatalogueTrack(val card: InternetMetadataDiscoveryCard, val attempt: Int, val playlist: Boolean = false)
private data class AdmittedCatalogueLookup(val query: String, val contextId: String, val request: Int)

/** Separate catalogue navigation. Only a user-selected track starts the existing source search. */
@Composable
internal fun InternetMetadataDiscoverySection(query: String, kind: LibrarySearchKind, request: Int,
    session: InternetMusicUiSession? = null, onPlaylistTrack: ((String) -> Unit)? = null,
    initialTarget: InternetMetadataBrowseTarget? = null, initialCard: InternetMetadataDiscoveryCard? = null,
    onOpenCatalog: ((DetailTarget) -> Unit)? = null,
    artistVisits: LruCache<CatalogArtistVisitKey, CatalogArtistVisit>? = null,
    artistOverview: app.autplay.ArtistOverview? = null, overviewLoading: Boolean = false,
    onPlayHeardTrack: ((String) -> Unit)? = null, selectArtistForDownload: Boolean = false, initialTitle: String? = null) {
    val context = LocalContext.current
    val settings by remember { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = if (session != null) session.binding?.serverProfileId else settings?.activeServerProfileId
    val user = if (session != null) session.binding?.userId else settings?.activeUserId
    val device = if (session != null) session.binding?.deviceId else settings?.deviceId
    val scope = DiscoveryScope(query.trim().take(200), kind, request, profile?.value, user?.value, device?.value)
    val visitKey = if (initialTarget?.entity == InternetMetadataDiscoveryEntity.ARTIST && session == null &&
        profile != null && user != null && device != null) CatalogArtistVisitKey(profile.value, user.value, device.value, initialTarget.entityId) else null
    val visit = remember(artistVisits, visitKey) { visitKey?.let { artistVisits?.get(it) } }
    val freshVisit = remember(visit) { visit?.takeIf { System.currentTimeMillis() - it.observedAt in 0 until 3_600_000L } }
    val currentScope by rememberUpdatedState(scope)
    var target by remember(scope, initialTarget) { mutableStateOf(initialTarget) }
    var releases by remember(scope, initialTarget) { mutableStateOf(visit?.releases ?: false) }
    var parentArtist by remember(scope) { mutableStateOf<InternetMetadataBrowseTarget?>(null) }
    var offset by remember(scope, initialTarget) { mutableIntStateOf(freshVisit?.page?.offset ?: 0) }
    var fullArtistList by remember(scope, initialTarget, releases) { mutableStateOf(offset > 0) }
    var advanceAfterPreview by remember(scope, initialTarget, releases) { mutableStateOf(false) }
    var retry by remember(scope) { mutableIntStateOf(0) }
    var page by remember(scope, initialTarget) { mutableStateOf(freshVisit?.page) }
    var cards by remember(scope, initialTarget) { mutableStateOf(freshVisit?.cards.orEmpty()) }
    var loading by remember(scope) { mutableStateOf(false) }
    var pageError by remember(scope) { mutableStateOf<String?>(null) }
    var selected by remember(scope) { mutableStateOf<SelectedCatalogueTrack?>(null) }
    var selectionSequence by remember(scope) { mutableIntStateOf(0) }
    var contextLoading by remember(scope) { mutableStateOf(false) }
    var contextError by remember(scope) { mutableStateOf<String?>(null) }
    var admitted by remember(scope) { mutableStateOf<AdmittedCatalogueLookup?>(null) }

    LaunchedEffect(scope, target, offset) {
        val release = target?.takeIf { it.entity == InternetMetadataDiscoveryEntity.RELEASE }
        if (session == null && release != null && profile != null && user != null) {
            val key = "${user.value}:RELEASE:${release.entityId}:false:$offset"
            AutPlayRuntime.database(context).catalogProjectionDao().catalogPageTimestamp(profile.value, key)
                .collect { if (it == 0L) retry++ }
        }
    }

    val usesArtistPreview = visitKey != null && target == initialTarget && offset == 0 && !fullArtistList && retry == 0
    val preview = if (usesArtistPreview) artistOverview else null
    val waitingForPreview = usesArtistPreview && overviewLoading
    LaunchedEffect(scope, target, releases, offset, retry, preview, waitingForPreview, fullArtistList) {
        val expectedTarget = target
        if (initialCard != null) {
            cards = listOf(initialCard)
            return@LaunchedEffect
        }
        val expectedOffset = offset
        loading = false
        pageError = null
        if (preview != null) {
            val first = if (releases) preview.releases else preview.tracks
            page = first
            cards = if (releases) (preview.latest.items + first.items).distinctBy { it.id } else first.items
            visitKey?.let { artistVisits?.put(it, CatalogArtistVisit(releases, first, cards, System.currentTimeMillis())) }
            return@LaunchedEffect
        }
        if (waitingForPreview) {
            loading = page == null
            return@LaunchedEffect
        }
        if (!advanceAfterPreview && freshVisit != null && retry == 0 && page === freshVisit.page && releases == freshVisit.releases &&
            expectedOffset == freshVisit.page.offset && expectedTarget == initialTarget) return@LaunchedEffect
        if (expectedOffset == 0 && !advanceAfterPreview) { cards = emptyList(); page = null }
        if (profile == null || user == null || device == null || request == 0 || scope.query.isBlank()) return@LaunchedEffect
        val discoveryKind = when (kind) {
            LibrarySearchKind.Artist -> InternetMetadataDiscoveryKind.ARTIST
            LibrarySearchKind.Album -> InternetMetadataDiscoveryKind.ALBUM
            else -> return@LaunchedEffect
        }
        val cache = if (session == null) AutPlayRuntime.database(context).catalogProjectionDao() else null
        val key = when (expectedTarget?.entity) {
            InternetMetadataDiscoveryEntity.ARTIST -> artistCatalogCacheKey(user.value, expectedTarget.entityId, releases, expectedOffset)
            null -> "${user.value}:search:${discoveryKind.wireValue}:$expectedOffset:${scope.query}"
            else -> "${user.value}:${expectedTarget.entity}:${expectedTarget.entityId}:$releases:$expectedOffset"
        }
        fun show(response: InternetMetadataDiscoveryPage, observedAt: Long) {
            val matches = if (target == null && kind == LibrarySearchKind.Artist) response.items.filter {
                it.entityType == "artist" && app.autplay.application.artist.artistNameMatches(it.title, scope.query)
            } else response.items
            cards = (if (expectedOffset == 0) matches else cards + matches).distinctBy { it.id }
            page = response
            if (visitKey != null && expectedTarget == initialTarget) {
                artistVisits?.put(visitKey, CatalogArtistVisit(releases, response, cards, observedAt))
            }
        }
        loading = true
        try {
            val cached = cache?.catalogPage(profile.value, key) ?: expectedTarget?.takeIf {
                it.entity == InternetMetadataDiscoveryEntity.ARTIST
            }?.let { cache?.catalogPage(profile.value, "${user.value}:${it.entity}:${it.entityId}:$releases:$expectedOffset") }
            if (cached != null) {
                val decoded = withContext(Dispatchers.Default) {
                    runCatching { InternetMetadataDiscoveryCodec.decode(cached.document) }.getOrNull()
                }
                if (decoded != null && decoded.offset == expectedOffset && decoded.limit == 25) {
                    show(decoded, cached.observedAtMs)
                    if (retry == 0 && System.currentTimeMillis() - cached.observedAtMs in 0 until 3_600_000L) return@LaunchedEffect
                }
            }
            val server = session?.client ?: AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
            val response = when (expectedTarget?.entity) {
                InternetMetadataDiscoveryEntity.ARTIST -> if (releases)
                    server.discoveryArtistReleases(expectedTarget.entityId, offset = expectedOffset)
                    else server.discoveryArtistTracks(expectedTarget.entityId, offset = expectedOffset)
                InternetMetadataDiscoveryEntity.RELEASE -> server.discoveryReleaseTracks(expectedTarget.entityId, offset = expectedOffset)
                else -> server.discoverMusic(scope.query, discoveryKind, offset = expectedOffset)
            }
            check(response.offset == expectedOffset && response.limit == 25) { "SERVER_RESPONSE_INVALID" }
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) {
                val now = System.currentTimeMillis()
                show(response, now)
                cache?.putCatalogPage(app.autplay.data.local.entity.CatalogPage(profile.value, key, response.rawPayload.toString(), now))
                cache?.trimCatalogPages(profile.value)
            }
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) pageError = catalogueErrorCode(error)
        } finally {
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) {
                loading = false
                if (advanceAfterPreview && page != null && pageError == null) {
                    advanceAfterPreview = false
                    page?.nextOffset?.let { offset = it }
                }
            }
        }
    }

    LaunchedEffect(scope, target, selected) {
        val selection = selected
        val expectedTarget = target
        admitted = null; contextLoading = false; contextError = null
        if (selection == null || profile == null || user == null || device == null) return@LaunchedEffect
        val lookup = selection.card.trackLookup ?: return@LaunchedEffect
        val identities = MusicCatalogueContextRequest.fromCard(selection.card) ?: return@LaunchedEffect
        contextLoading = true
        try {
            val server = session?.client ?: AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
            val receipt = server.createMusicCatalogueContext(identities)
            check(receipt.matches(identities)) { "SERVER_RESPONSE_INVALID" }
            val contextId = receipt.admissionContextId(Instant.now()) ?: throw IllegalStateException("CONTEXT_EXPIRED")
            if (currentScope == scope && target == expectedTarget && selected == selection) {
                if (selection.playlist) {
                    val reference = server.catalogueTrackReference(contextId)
                    val localId = reference.localTarget(AutPlayRuntime.database(context), profile.value)
                    if (currentScope == scope && target == expectedTarget && selected == selection) onPlaylistTrack?.invoke(localId)
                } else {
                    admitted = AdmittedCatalogueLookup(lookup.searchQuery, contextId, selection.attempt)
                }
            }
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (currentScope == scope && target == expectedTarget && selected == selection) contextError = catalogueErrorCode(error)
        } finally {
            if (currentScope == scope && target == expectedTarget && selected == selection) contextLoading = false
        }
    }

    val artistCards = remember(target, initialTarget, cards, artistOverview, releases) {
        if (target?.entity != InternetMetadataDiscoveryEntity.ARTIST) emptyMap() else
            (artistOverview.takeIf { target == initialTarget }?.tracks?.items.orEmpty() + if (releases) emptyList() else cards)
                .filter { it.entityType == "recording" && it.entityId == it.recordingId }.associateBy { it.entityId }
    }
    val libraryArtistId = artistOverview?.libraryArtistId?.takeIf {
        target == initialTarget && target?.entity == InternetMetadataDiscoveryEntity.ARTIST
    }
    val heardTracks = key(scope, target, libraryArtistId, artistCards.keys) {
        val owner = profile?.value
        if (session != null || owner == null || (artistCards.isEmpty() && libraryArtistId == null)) emptyList() else {
            val heard = remember {
                val dao = AutPlayRuntime.database(context).searchDao()
                val batches = artistCards.keys.chunked(500).ifEmpty { listOf(emptyList()) }
                combine(batches.mapIndexed { index, ids ->
                    dao.heardCatalogueRecordings(owner, libraryArtistId.takeIf { index == 0 }, ids)
                }) { chunks ->
                    chunks.flatMap { it }.sortedWith(compareByDescending<HeardCatalogueTrack> { it.lastPlayedMs }
                        .thenBy { it.trackId }).distinctBy { row ->
                        row.mbid?.let { "mb:$it" } ?: row.recordingId?.let { "recording:$it" } ?: "ref:${row.trackId}"
                    }
                }
            }
            heard.collectAsState(initial = emptyList()).value
        }
    }
    val heardMbids = remember(heardTracks) { heardTracks.mapNotNullTo(hashSetOf()) { it.mbid } }
    val offlineMbids = remember { mutableStateListOf<String>() }
    LaunchedEffect(scope, cards) {
        offlineMbids.clear()
        if (profile != null && session == null) {
            val mbids = cards.mapNotNull { it.recordingId }.distinct()
            val dao = AutPlayRuntime.database(context).searchDao()
            if (mbids.isNotEmpty()) {
                kotlinx.coroutines.flow.combine(mbids.chunked(500).map { dao.offlineCatalogueRecordings(profile.value, it) }) {
                    chunks -> chunks.flatMap { it }
                }.collect { offlineMbids.clear(); offlineMbids.addAll(it) }
            }
        }
    }
    Column(Modifier.fillMaxWidth().testTag("internet-metadata-discovery"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (heardTracks.isNotEmpty() && onPlayHeardTrack != null) {
            AutPlaySectionHeader(stringResource(R.string.detail_artist_popular))
            var heardLimit by rememberSaveable(scope.user, scope.profile, target?.entityId) { mutableIntStateOf(5) }
            heardTracks.take(heardLimit).forEach { heard ->
                val card = artistCards[heard.mbid]
                AutPlayTrackRow(heard.title ?: card?.title ?: stringResource(R.string.detail_artist_recording),
                    heard.artist ?: card?.artist, heard.trackId, { onPlayHeardTrack(heard.trackId) })
            }
            if (heardTracks.size > heardLimit) TextButton(onClick = { heardLimit += 25 }) {
                Text(stringResource(R.string.music_catalogue_more))
            }
        }
        Text(stringResource(R.string.music_catalogue_title), style = MaterialTheme.typography.titleLarge)
        Text(stringResource(R.string.music_catalogue_metadata_only), style = MaterialTheme.typography.bodySmall)
        if (target != null && target != initialTarget) TextButton(modifier = Modifier.testTag("catalogue-back"), onClick = {
            target = parentArtist ?: initialTarget; releases = parentArtist != null; parentArtist = null; offset = 0; cards = emptyList(); page = null; selected = null; admitted = null
        }) { Text(stringResource(R.string.music_catalogue_back)) }
        if (session == null) target?.let { subject ->
            CatalogDownloadAction(if (subject.entity == InternetMetadataDiscoveryEntity.ARTIST) "artist" else "release",
                subject.entityId, initialTitle.takeIf { subject == initialTarget })
        }
        when {
            profile == null || user == null || device == null -> Text(stringResource(R.string.music_need_server))
            loading -> LinearProgressIndicator(Modifier.fillMaxWidth())
            pageError != null -> {
                CatalogueErrorText(pageError)
                TextButton(onClick = { retry++ }) { Text(stringResource(R.string.music_retry)) }
            }
            page != null && cards.isEmpty() -> Text(stringResource(R.string.music_internet_empty))
        }
        if (target?.entity == InternetMetadataDiscoveryEntity.RELEASE) {
            val count = page?.rawPayload?.get("vault_count")?.jsonPrimitive?.intOrNull
            if (count != null) Text(stringResource(R.string.catalogue_vault_count, count, page?.totalCount ?: 0))
        }
        if (initialTarget?.entity == InternetMetadataDiscoveryEntity.ARTIST && target == initialTarget) {
            TextButton(onClick = {
                releases = !releases
                offset = 0
                cards = emptyList()
                page = null
                selected = null
                admitted = null
            }) { Text(stringResource(if (releases) R.string.catalog_popular_tracks else R.string.library_section_albums)) }
        }
        if (target?.entity == InternetMetadataDiscoveryEntity.RELEASE && session == null) {
            val cover = rememberCatalogCover(target?.entityId)
            if (cover != null) AutPlayArtwork(scope.query, size = 124.dp, painter = cover)
        }
        val visibleCards = if (releases && target?.entity == InternetMetadataDiscoveryEntity.ARTIST)
            cards.sortedWith(compareByDescending<InternetMetadataDiscoveryCard> { it.releaseDate }.thenBy { it.title }) else cards
        visibleCards.forEach { card ->
            var visible by remember(card.id) { mutableStateOf(false) }
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer,
                modifier = Modifier.fillMaxWidth().onVisibilityChanged(minFractionVisible = 0.01f) { visible = it }
                    .testTag("catalogue-card-${card.id}")) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    val kind = card.browseTarget?.entity
                    if (session == null && (kind == InternetMetadataDiscoveryEntity.ARTIST || kind == InternetMetadataDiscoveryEntity.RELEASE)) {
                        val photo = if (visible && !selectArtistForDownload) rememberCatalogCover(card.entityId, portrait = kind == InternetMetadataDiscoveryEntity.ARTIST) else null
                        AutPlayArtwork(card.title, size = 72.dp, painter = photo)
                    }
                    Text(card.title, style = MaterialTheme.typography.titleMedium)
                    val partners = card.performerLinks().distinctBy { it.first }
                    if (partners.size > 1 && onOpenCatalog != null) {
                        ArtistCreditLine(partners.map { (id, name) ->
                            name to DetailTarget(DetailKind.CatalogArtistTracks, id, name)
                        }, onOpenCatalog)
                    } else card.artist?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                    listOfNotNull(releaseYear(card.releaseDate), card.country, card.disambiguation).takeIf { it.isNotEmpty() }
                        ?.let { Text(it.joinToString(" · "), style = MaterialTheme.typography.bodySmall) }
                    card.discNumber?.let { Text(stringResource(R.string.music_catalogue_disc, it), style = MaterialTheme.typography.bodySmall) }
                    card.trackNumber?.let { Text(stringResource(R.string.music_catalogue_track, it), style = MaterialTheme.typography.bodySmall) }
                    if (card.recordingId in offlineMbids) Text(stringResource(R.string.catalogue_on_phone), style = MaterialTheme.typography.bodySmall)
                    if (card.recordingId in heardMbids) Text(stringResource(R.string.artist_track_heard), style = MaterialTheme.typography.bodySmall)
                    if (card.rawPayload["vault_available"]?.jsonPrimitive?.booleanOrNull == true) {
                        Text(stringResource(R.string.catalogue_in_vault), style = MaterialTheme.typography.bodySmall)
                    }
                    card.recordingTitle?.takeIf { it != card.title }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    card.browseTarget?.let { browse ->
                        if (selectArtistForDownload && browse.entity == InternetMetadataDiscoveryEntity.ARTIST) {
                            CatalogDownloadAction("artist", browse.entityId, card.title)
                            return@let
                        }
                        TextButton(modifier = Modifier.testTag("catalogue-browse-${card.id}"), enabled = !loading, onClick = {
                            if (onOpenCatalog != null) {
                                onOpenCatalog(DetailTarget(if (browse.entity == InternetMetadataDiscoveryEntity.ARTIST)
                                    DetailKind.CatalogArtistTracks else DetailKind.CatalogRelease, browse.entityId, card.title))
                                return@TextButton
                            }
                            parentArtist = target?.takeIf { it.entity == InternetMetadataDiscoveryEntity.ARTIST }; releases = false
                            target = browse; offset = 0; cards = emptyList(); page = null; selected = null; admitted = null
                        }) { Text(stringResource(R.string.music_catalogue_browse)) }
                        if (browse.entity == InternetMetadataDiscoveryEntity.ARTIST) TextButton(onClick = {
                            parentArtist = null; target = browse; releases = true; offset = 0
                            cards = emptyList(); page = null; selected = null; admitted = null
                        }) { Text(stringResource(R.string.library_section_albums)) }
                    }
                    if (session == null && card.trackLookup != null) card.recordingId?.let { CatalogDownloadAction("recording", it, card.title) }
                    if (card.trackLookup != null && onPlaylistTrack != null) TextButton(enabled = !contextLoading, onClick = {
                        selectionSequence++; selected = SelectedCatalogueTrack(card, selectionSequence, playlist = true)
                    }) { Text(stringResource(R.string.catalogue_add_to_playlist)) }
                    if (card.trackLookup != null) TextButton(modifier = Modifier.testTag("catalogue-source-${card.id}"), enabled = !contextLoading, onClick = {
                        selectionSequence++; selected = SelectedCatalogueTrack(card, selectionSequence); admitted = null
                    }) { Text(stringResource(R.string.music_catalogue_find_source)) }
                }
            }
        }
        if (page?.truncated == true) Text(stringResource(R.string.music_catalogue_truncated))
        val nextOffset = page?.nextOffset
        val moreRecentReleases = releases && preview != null && preview.latest.items.size == preview.latest.limit &&
            preview.latest.items.any { recent -> preview.releases.items.none { it.id == recent.id } }
        if (nextOffset != null || moreRecentReleases) {
            TextButton(modifier = Modifier.testTag("catalogue-more"), enabled = !loading, onClick = {
                if (preview != null) {
                    // Re-read the live first page before using its cursor for a complete list.
                    advanceAfterPreview = true
                    fullArtistList = true
                } else if (nextOffset != null) offset = nextOffset
            }) { Text(stringResource(R.string.music_catalogue_more)) }
        }
        if (contextLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (contextError != null) {
            CatalogueErrorText(contextError)
            TextButton(modifier = Modifier.testTag("catalogue-context-retry"), onClick = { selected?.let { selectionSequence++; selected = it.copy(attempt = selectionSequence) } }) {
                Text(stringResource(R.string.music_retry))
            }
        }
        admitted?.let { lookup ->
            InternetMusicSearchSection(query = lookup.query, request = lookup.request,
                kind = LibrarySearchKind.Track, catalogueContextId = lookup.contextId, session = session,
                onCatalogueContextExpired = {
                    admitted = null
                    selected?.let { selectionSequence++; selected = it.copy(attempt = selectionSequence) }
                })
        }
    }
}

internal fun catalogueErrorCode(error: Exception): String = when (error) {
    is SessionRequiredException -> "SESSION_REQUIRED"
    is ServerFeatureHttpException -> error.errorCode ?: "UNAVAILABLE"
    else -> if (error.message == "CONTEXT_EXPIRED") "music_catalogue_context_expired" else "UNAVAILABLE"
}

@Composable
internal fun CatalogueErrorText(code: String?) {
    val message = when (code) {
        "SESSION_REQUIRED" -> R.string.music_need_server
        "music_catalogue_disabled", "music_discovery_disabled" -> R.string.music_catalogue_disabled
        "music_catalogue_busy", "music_discovery_busy" -> R.string.music_catalogue_busy
        "music_catalogue_unavailable", "music_discovery_unavailable" -> R.string.music_catalogue_unavailable
        "music_catalogue_context_expired" -> R.string.music_catalogue_expired
        "music_discovery_entity_not_found", "music_catalogue_context_not_found", "music_catalogue_entity_not_found", "catalog_download_missing" -> R.string.music_catalogue_unavailable_selection
        "music_operation_conflict" -> R.string.music_catalogue_conflict
        "UNAVAILABLE" -> R.string.music_catalogue_unavailable
        else -> R.string.music_internet_error
    }
    Text(stringResource(message), color = MaterialTheme.colorScheme.error)
}
