package app.autplay.ui

import app.autplay.application.recommendation.HomeWheelMix

import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyGridScope
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.DisposableEffect
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onVisibilityChanged
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.artist.TrackTitleQuery
import app.autplay.application.search.LibrarySearchKind
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.LibrarySort
import app.autplay.ui.core.ListAnchor
import app.autplay.ui.core.browseSection
import app.autplay.ui.core.browseFilter
import app.autplay.ui.playlist.AddTrackToPlaylistDialog
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.ui.playlist.ManualPlaylistText
import app.autplay.ui.playlist.ManualPlaylistUi
import kotlinx.coroutines.flow.distinctUntilChanged

public data class CoreTrackUiItem(
    public val id: String,
    public val title: String,
    public val artist: String?,
    public val selected: Boolean = false,
    public val permissionRevoked: Boolean = false,
    public val downloaded: Boolean = false,
    public val loved: Boolean = false,
    public val canRemove: Boolean = true,
)

public data class CoreCollectionUiItem(
    public val id: String,
    public val title: String,
    public val subtitle: String?,
    public val itemCount: Int? = null,
    public val artworkTrackId: String? = null,
    public val releaseMbid: String? = null,
    public val releaseDate: String? = null,
)

public data class CoreArtistUiItem(
    public val id: String,
    public val name: String,
    public val subtitle: String?,
    public val portraitTrackIds: List<String> = emptyList(),
    public val portraitArtistIds: List<String> = emptyList(),
)

@Composable
private fun ArtistCard(artist: CoreArtistUiItem, onClick: () -> Unit) {
    AutPlayCard(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val image = rememberLibraryPortrait(artist.name, artist.portraitTrackIds, artist.portraitArtistIds)
            ArtistAvatar(artist.name, image)
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(artist.name, style = MaterialTheme.typography.titleMedium)
                artist.subtitle?.let { Text(it, color = AutPlayTokens.colors.mutedText) }
            }
        }
    }
}

public enum class ArtistBrowseUiState {
    Unavailable,
    Loading,
    Ready,
    Error,
}

public data class HomeReleaseUiItem(
    public val id: String,
    public val title: String,
    public val artist: String,
    public val dateText: String?,
    public val artworkTrackId: String? = null,
    public val inLibrary: Boolean = false,
    public val releaseMbid: String? = null,
)

public data class HomeRecommendationUiItem(
    public val id: String,
    public val title: String,
    public val artist: String,
    public val section: String,
    public val feedbackEnabled: Boolean,
    public val artworkTrackId: String? = null,
)

public data class HomeTrackUiItem(
    public val id: String,
    public val title: String,
    public val artist: String?,
)

public data class HomeContinueUiItem(
    public val trackId: String,
    public val title: String,
    public val artist: String?,
    public val positionText: String,
)

public data class HomeProblemUiItem(
    public val id: String,
    public val message: String,
)

public data class HomeScreenUiState(
    public val localMode: Boolean,
    public val recommendationLoading: Boolean,
    public val offlineFallback: Boolean,
    public val releases: List<HomeReleaseUiItem>,
    public val recommendations: List<HomeRecommendationUiItem>,
    public val continueListening: HomeContinueUiItem? = null,
    public val recentlyPlayed: List<HomeTrackUiItem> = emptyList(),
    public val recentlyAdded: List<HomeTrackUiItem> = emptyList(),
    public val offlineReady: List<HomeTrackUiItem> = emptyList(),
    public val problems: List<HomeProblemUiItem> = emptyList(),
    public val recommendationError: Boolean = false,
    public val likedTrackIds: Set<String> = emptySet(),
    public val localLibraryEmpty: Boolean = false,
    public val wheelOrder: List<String> = app.autplay.application.recommendation.HomeWheelRanking.ring(java.time.LocalTime.now().hour),
    public val wheelOwner: String = "",
    public val wheelMixes: Map<String, HomeWheelMix> = emptyMap(),
)

public data class SearchScreenUiState(
    public val query: String,
    public val results: List<CoreTrackUiItem>,
    public val searched: Boolean,
    public val loading: Boolean = false,
    public val error: Boolean = false,
    public val vaultAvailable: Boolean = false,
    public val vaultSelected: Boolean = false,
    public val vaultLoading: Boolean = false,
    public val vaultResults: List<VaultSearchUiItem> = emptyList(),
    public val vaultSearched: Boolean = false,
    public val vaultError: Boolean = false,
    public val kind: LibrarySearchKind = LibrarySearchKind.All,
    public val offlineTrackIds: Set<String> = emptySet(),
    public val serverLinkedTrackIds: Set<String> = emptySet(),
    public val artistMatches: List<CoreArtistUiItem> = emptyList(),
    public val vaultNextCursor: String? = null,
    public val vaultPageError: Boolean = false,
) {
    internal val serverPlaybackByTrack = if (vaultAvailable) {
        vaultResults.mapNotNull { row -> row.localTrackId?.let { it to row.playable } }
            .groupBy({ it.first }, { it.second }).mapValues { (_, matches) -> matches.any { it } }
    } else emptyMap()
    internal val distinctVaultResults = if (vaultAvailable) {
        val localIds = results.mapTo(mutableSetOf()) { it.id }
        vaultResults.filter { it.localTrackId == null || localIds.add(it.localTrackId) }
    } else emptyList()
}

public data class VaultSearchUiItem(
    public val id: String,
    public val title: String?,
    public val artist: String?,
    public val source: String,
    public val availability: String,
    public val playable: Boolean,
    public val localTrackId: String? = null,
    public val durationMs: Long? = null,
)

public data class LibraryScreenUiState(
    public val localMode: Boolean,
    public val tracks: List<CoreTrackUiItem>,
    public val section: LibrarySection = LibrarySection.Tracks,
    public val sort: LibrarySort = LibrarySort.RecentlyAdded,
    public val filter: LibraryFilter = LibraryFilter.All,
    public val albums: List<CoreCollectionUiItem> = emptyList(),
    public val artists: List<CoreArtistUiItem> = emptyList(),
    public val artistBrowseState: ArtistBrowseUiState = ArtistBrowseUiState.Unavailable,
    public val playlists: List<CoreCollectionUiItem> = emptyList(),
    public val reviewCount: Int = 0,
    public val importingLocalTrack: Boolean = false,
    public val lastImportedTitle: String? = null,
    public val error: Boolean = false,
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
public fun HomeProductScreen(
    state: HomeScreenUiState,
    contentPadding: PaddingValues,
    onOpenListenTogether: () -> Unit,
    onRecommendationVisible: (String) -> Unit,
    onLike: (String) -> Unit,
    onDislike: (String) -> Unit,
    onRetry: () -> Unit = {},
    onPlayTrack: (String) -> Unit = {},
    onPlayRecommendation: (String) -> Unit = {},
    recommendationPlaybackAllowed: Boolean = true,
    onOpenOffline: () -> Unit = {},
    onOpenProblems: () -> Unit = {},
    playerState: PlaybackPresentationState = PlaybackPresentationState(),
    currentTrackRefId: String? = null,
    currentTrackLiked: Boolean = false,
    onOpenPlayer: () -> Unit = {},
    onTogglePlayPause: () -> Unit = {},
    onLikeHeroTrack: (String) -> Unit = {},
    onOpenHeroArtist: (() -> Unit)? = null,
    onPreviousTrack: () -> Unit = {},
    onNextTrack: () -> Unit = {},
    swipeTargets: HomeSwipeTargets = HomeSwipeTargets(),
    onStartMyWave: () -> Unit = {},
    onPlayWheelMix: (HomeWheelMix) -> Unit = {},
    myWaveAllowed: Boolean = true,
    onOpenRelease: (String) -> Unit = {},
    onAddLocal: () -> Unit = {},
    importingLocalTrack: Boolean = false,
    onSeek: (Long) -> Unit = {},
    onTimelineObserving: (Boolean) -> Unit = {},
    onContinueListening: () -> Unit = {
        (state.continueListening?.trackId ?: state.recentlyPlayed.firstOrNull()?.id)?.let(onPlayTrack)
    },
) {
    val heroState = buildHomePlaybackHeroUiState(
        homeState = state,
        playerState = playerState,
        currentTrackRefId = currentTrackRefId,
        currentTrackLiked = currentTrackLiked,
    )
    val session = playerState.mediaId?.let { (playerState.context as? app.autplay.application.playback.ActiveQueueContext.Loaded)?.snapshotId ?: it }
    var playedSession by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(session, playerState.isPlaying) {
        if (session == null || playerState.isPlaying) playedSession = session
    }
    val showArtwork = heroState.hasActivePlayback && (playerState.isPlaying || playedSession == session)
    val listState = rememberLazyListState()
    val showFirstTrack = state.localMode && state.localLibraryEmpty && heroState.trackId == null && !heroState.hasActivePlayback
    Box(Modifier.fillMaxWidth().padding(contentPadding)) {
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxWidth().testTag("home-product-list"),
            contentPadding = PaddingValues(
                start = 20.dp,
                top = 0.dp,
                end = 20.dp,
                bottom = 28.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
        item(key = "home:playback-hero") {
            BoxWithConstraints(
                modifier = Modifier.fillMaxWidth(),
                contentAlignment = Alignment.Center,
            ) {
                val heroModifier = Modifier.requiredWidth(maxWidth + 40.dp)
                if (playerState.mediaId != null) {
                    DisposableEffect(onTimelineObserving) {
                        onTimelineObserving(true)
                        onDispose { onTimelineObserving(false) }
                    }
                }
                if (showArtwork) {
                    HomePlaybackHero(
                        state = heroState,
                        localMode = state.localMode,
                        onOpenPlayer = onOpenPlayer,
                        onOpenArtist = onOpenHeroArtist,
                        onPlayTrack = onPlayTrack,
                        onTogglePlayPause = onTogglePlayPause,
                        onLike = onLikeHeroTrack,
                        onOpenListenTogether = onOpenListenTogether,
                        modifier = heroModifier,
                        playerState = playerState,
                        onSeek = onSeek,
                        swipeTargets = swipeTargets,
                        onPrevious = onPreviousTrack,
                        onNext = onNextTrack,
                    )
                } else {
                    HomeWaveStartHero(
                        localMode = state.localMode,
                        state = heroState,
                        enabled = myWaveAllowed && !showFirstTrack,
                        emptyLibrary = showFirstTrack,
                        onStart = onStartMyWave,
                        onOpenArtist = onOpenHeroArtist,
                        onResume = if (heroState.hasActivePlayback) onTogglePlayPause else onContinueListening,
                        onLike = onLikeHeroTrack,
                        onOpenListenTogether = onOpenListenTogether,
                        modifier = heroModifier,
                        playerState = playerState,
                        onSeek = onSeek,
                    )
                }
            }
        }
        if (showArtwork) {
            item(key = "home:start-wave") {
                OutlinedButton(onClick = onStartMyWave, enabled = myWaveAllowed,
                    modifier = Modifier.fillMaxWidth().testTag("home-my-wave")) {
                    Text(stringResource(R.string.home_my_wave))
                }
            }
        }
        if (showFirstTrack) {
            item(key = "home:first-track") {
                Button(onClick = onAddLocal, enabled = !importingLocalTrack,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("home-add-first-track")) {
                    Text(stringResource(if (importingLocalTrack) R.string.library_adding_local else R.string.library_add_local))
                }
            }
        }
        item(key = "home:contexts:${state.wheelOwner}") {
            HomeWaveWheel(state.wheelOrder, state.wheelMixes, myWaveAllowed, playerState.isPlaying, onPlayWheelMix)
        }
        if (state.offlineFallback) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Offline,
                    stringResource(R.string.home_offline_fallback),
                )
            }
        }
        item(key = "home:new-releases") {
            HomeReleaseSection(releases = state.releases, onOpenRelease = onOpenRelease)
        }
        val recentItems = state.recentlyPlayed.ifEmpty { state.recentlyAdded }
        if (recentItems.isNotEmpty()) {
            items(recentItems, key = { "home-recent:${it.id}" }) { track ->
                AutPlayTrackRow(track.title, track.artist, track.id, { onPlayTrack(track.id) }, Modifier.testTag("home-recent"))
            }
        }
        if (state.recommendationError) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                    actionLabel = stringResource(R.string.action_retry),
                    onAction = onRetry,
                )
            }
        } else if (state.recommendationLoading) {
            item {
                AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.home_loading))
            }
        }
        items(state.recommendations, key = HomeRecommendationUiItem::id) { item ->
            AutPlayTrackRow(
                title = item.title,
                artist = item.artist,
                trackId = item.artworkTrackId,
                onPlay = { onPlayRecommendation(item.id) },
                playEnabled = item.feedbackEnabled && recommendationPlaybackAllowed,
                modifier = Modifier.testTag("home-recommendation")
                    .onVisibilityChanged(minFractionVisible = 0.01f) { onRecommendationVisible(item.id) },
            ) {
                AutPlayIconButton(AutPlayIcon.ThumbDown, R.string.action_dislike, { onDislike(item.id) }, enabled = item.feedbackEnabled)
                AutPlayIconButton(AutPlayIcon.ThumbUp, R.string.action_like, { onLike(item.id) }, enabled = item.feedbackEnabled)
            }
        }
        if (state.offlineReady.isNotEmpty()) {
            items(state.offlineReady, key = { "home-offline:${it.id}" }) { track ->
                AutPlayTrackRow(track.title, track.artist, track.id, { onPlayTrack(track.id) })
            }
            item(key = "home:offline-open") {
                OutlinedButton(onClick = onOpenOffline, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.home_offline_open))
                }
            }
        }
        if (state.problems.isNotEmpty()) {
            items(state.problems, key = { "home-problem:${it.id}" }) { problem ->
                AutPlayStateSurface(
                    AutPlayStateKind.Offline,
                    problem.message,
                    actionLabel = stringResource(R.string.home_problems_open),
                    onAction = onOpenProblems,
                )
            }
            }
        }
    }
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
public fun SearchProductScreen(
    state: SearchScreenUiState,
    contentPadding: PaddingValues,
    onQueryChange: (String) -> Unit,
    onSearch: () -> Unit,
    onPlay: (String) -> Unit,
    onPlayVault: (String) -> Unit = {},
    playlistVaultTarget: suspend (String) -> String? = { null },
    onRetry: () -> Unit = {},
    onVaultScopeChange: (Boolean) -> Unit = {},
    listAnchor: ListAnchor? = null,
    onListAnchorChange: (ListAnchor) -> Unit = {},
    onSearchKindChange: (LibrarySearchKind) -> Unit = {},
    onOpenArtist: (String) -> Unit = {},
    onOpenCatalog: ((app.autplay.ui.core.DetailTarget) -> Unit)? = null,
    onPlayAcquired: ((String) -> Unit)? = null,
    onMoreVault: () -> Unit = {},
    manualPlaylists: List<ManualPlaylistUi> = emptyList(),
    manualPlaylistActions: ManualPlaylistActions = ManualPlaylistActions(),
) {
    val addScope = androidx.compose.runtime.rememberCoroutineScope()
    var playlistTrackId by rememberSaveable { mutableStateOf<String?>(null) }
    playlistTrackId?.let { track ->
        AddTrackToPlaylistDialog(track, manualPlaylists, manualPlaylistActions, { playlistTrackId = null })
    }
    var internetRequest by rememberSaveable(state.kind, state.query) { androidx.compose.runtime.mutableIntStateOf(0) }
    var internetQuery by rememberSaveable(state.kind, state.query) { mutableStateOf("") }
    val listContextKey = "search:${state.kind.wireValue}:${state.query.trim().replace(Regex("\\s+"), " ")}:${state.vaultAvailable}"
    val listKeys = searchListKeys(state)
    val listState = rememberStableAnchorListState(
        contextKey = listContextKey,
        persistedAnchor = listAnchor,
        orderedKeys = listKeys,
        contentPrefixes = setOf(SEARCH_RESULT_PREFIX, SEARCH_VAULT_RESULT_PREFIX),
        onAnchorChange = onListAnchorChange,
    )
    LazyColumn(
        modifier = Modifier.testTag("search-product-list"),
        state = listState,
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item(key = "search:query") {
            OutlinedTextField(
                value = state.query,
                onValueChange = onQueryChange,
                label = { Text(stringResource(R.string.search_hint)) },
                singleLine = true,
                shape = MaterialTheme.shapes.large,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = {
                    if (state.query.isNotBlank()) { internetRequest = 0; onSearch() }
                }),
                trailingIcon = {
                    IconButton(
                        onClick = { internetRequest = 0; onSearch() },
                        enabled = state.query.isNotBlank(),
                        modifier = Modifier.testTag("local-search-submit"),
                    ) {
                        AutPlayPlatformIcon(AutPlayIcon.Search, stringResource(R.string.search_action))
                    }
                },
                modifier = Modifier.fillMaxWidth().testTag("unified-search-query"),
            )
        }
        item(key = "search:kind") { LibrarySearchKindPicker(state.kind, onSearchKindChange) }
        if (state.kind == LibrarySearchKind.Artist) {
            if (state.query.isNotBlank() && (state.searched || state.vaultSearched)) {
                items(state.artistMatches, key = { "artist:${it.id}" }) { artist ->
                    ArtistCard(artist) { onOpenArtist(artist.id) }
                }
                if (state.artistMatches.isEmpty()) item { Text(stringResource(R.string.search_empty)) }
                if (state.vaultAvailable) item {
                    OutlinedButton(onClick = { internetQuery = state.query; internetRequest++ }) {
                        Text(stringResource(R.string.music_search_internet))
                    }
                }
                if (internetRequest > 0 && internetQuery == state.query) item {
                    InternetMusicSearchSection(internetQuery, internetRequest, state.kind,
                        onPlaylistTrack = { playlistTrackId = it }, onOpenCatalog = onOpenCatalog)
                }
            }
            return@LazyColumn
        }
        if (state.loading || (state.vaultAvailable && state.vaultLoading)) {
            item(key = "search:loading") {
                AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.search_loading))
            }
        }
        if (state.error) {
            item(key = "search:error") {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                    actionLabel = stringResource(R.string.action_retry),
                    onAction = onRetry,
                )
            }
        }
        if (state.vaultAvailable && state.vaultError) {
            item(key = "search:vault-error") {
                AutPlayCard {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(
                            stringResource(R.string.ui_library_refresh_vault_unavailable),
                            style = MaterialTheme.typography.bodyMedium,
                            color = AutPlayTokens.colors.mutedText,
                        )
                        OutlinedButton(onClick = onRetry) { Text(stringResource(R.string.action_retry)) }
                    }
                }
            }
        }
        if (state.searched || state.vaultSearched) {
            item(key = "search:count") {
                Text(
                    pluralStringResource(
                        R.plurals.ui_library_refresh_search_count,
                        state.results.size + state.distinctVaultResults.size,
                        state.results.size + state.distinctVaultResults.size,
                    ),
                    color = AutPlayTokens.colors.mutedText,
                )
            }
            if (state.results.isEmpty() && state.distinctVaultResults.isEmpty() &&
                !state.loading && !state.vaultLoading && !state.error && !state.vaultError
            ) {
                item(key = "search:empty") {
                    AutPlayStateSurface(AutPlayStateKind.Empty, stringResource(R.string.search_empty))
                }
            }
        }
        if (state.vaultAvailable && (state.searched || state.vaultSearched)) {
            item(key = "search:internet-action") {
                OutlinedButton(onClick = { internetQuery = state.query; internetRequest++ }) {
                    Text(stringResource(R.string.music_search_internet))
                }
            }
        }
        if (internetRequest > 0 && internetQuery == state.query) {
            item(key = "search:internet") { InternetMusicSearchSection(internetQuery, internetRequest, state.kind,
                vaultTracks = state.vaultResults, onPlayVault = onPlayVault, onPlayAcquired = onPlayAcquired,
                onPlaylistTrack = { playlistTrackId = it }, onOpenCatalog = onOpenCatalog) }
        }
        items(state.results, key = { "$SEARCH_RESULT_PREFIX${it.id}" }) { track ->
            val offline = track.id in state.offlineTrackIds
            val serverPlayable = state.serverPlaybackByTrack[track.id]
            val server = serverPlayable != null || track.id in state.serverLinkedTrackIds
            AutPlayTrackRow(
                title = track.title, artist = track.artist, trackId = track.id,
                onPlay = if (offline || serverPlayable != false) ({ onPlay(track.id) }) else null,
                detail = stringResource(searchSourceLabel(offline, server, serverPlayable)),
                emphasizeDetail = offline,
                modifier = Modifier.testTag("local-result-${track.id}"),
                actions = { AddToPlaylistMenu { playlistTrackId = track.id } },
            )
        }
        items(state.distinctVaultResults, key = { "$SEARCH_VAULT_RESULT_PREFIX${it.id}" }) { track ->
            val offline = track.localTrackId in state.offlineTrackIds
            AutPlayTrackRow(
                title = track.title ?: stringResource(R.string.search_vault_metadata_unavailable),
                artist = track.artist,
                trackId = track.localTrackId,
                onPlay = if (offline || track.playable) ({ onPlayVault(track.id) }) else null,
                detail = stringResource(searchSourceLabel(offline, server = true, playable = track.playable)),
                emphasizeDetail = offline,
                modifier = Modifier.testTag("vault-result-${track.id}"),
                actions = {
                    AddToPlaylistMenu {
                        if (track.localTrackId != null) playlistTrackId = track.localTrackId
                        else addScope.launch { playlistTrackId = playlistVaultTarget(track.id) }
                    }
                },
            )
        }
        state.vaultNextCursor?.let { cursor ->
            item(key = "search:vault-more") {
                if (state.vaultPageError) {
                    OutlinedButton(onClick = onMoreVault) { Text(stringResource(R.string.action_retry)) }
                } else {
                    LaunchedEffect(cursor) { onMoreVault() }
                    LinearProgressIndicator(Modifier.fillMaxWidth().testTag("vault-next-page"))
                }
            }
        }
    }
}

@Composable
public fun LibraryProductScreen(
    state: LibraryScreenUiState,
    contentPadding: PaddingValues,
    onAddLocal: () -> Unit,
    onSelect: (String) -> Unit,
    onRemoveOrRestore: (String) -> Unit,
    onLike: (String) -> Unit,
    onSectionChange: (LibrarySection) -> Unit = {},
    onSortChange: (LibrarySort) -> Unit = {},
    onFilterChange: (LibraryFilter) -> Unit = {},
    onOpenCollection: (LibrarySection, String) -> Unit = { _, _ -> },
    onOpenReview: () -> Unit = {},
    listAnchor: ListAnchor? = null,
    onListAnchorChange: (ListAnchor) -> Unit = {},
    onOpenDownloads: () -> Unit = {},
    onPlayNext: (String) -> Unit = {},
    onAddToQueue: (String) -> Unit = {},
    onDownload: (String) -> Unit = {},
    onRepairAccess: () -> Unit = {},
    manualPlaylists: List<ManualPlaylistUi> = emptyList(),
    manualPlaylistActions: ManualPlaylistActions = ManualPlaylistActions(),
    searchPersonal: ((LibrarySection, LibraryFilter, String) -> kotlinx.coroutines.flow.Flow<LibraryScreenUiState>)? = null,
) {
    var query by rememberSaveable { mutableStateOf("") }
    val search = query.trim()
    val displayState = state.copy(section = state.section.browseSection(), filter = state.filter.browseFilter())
    var addTrackToPlaylistId by rememberSaveable { mutableStateOf<String?>(null) }
    val listContextKey = "library:${displayState.section}:${displayState.sort}:${displayState.filter}"
    val listKeys = libraryListKeys(displayState)
    val gridState = remember(listContextKey) { LazyGridState() }
    RestoreContentAnchor(listContextKey, listAnchor, listKeys, onListAnchorChange, gridState::scrollToItem) {
        val first = gridState.firstVisibleItemIndex
        gridState.layoutInfo.visibleItemsInfo.firstOrNull { item ->
            item.index >= first && LIBRARY_CONTENT_PREFIXES.any { (item.key as? String)?.startsWith(it) == true }
        }?.let { item ->
            ListAnchor(listContextKey, item.key as String, if (item.index == first) gridState.firstVisibleItemScrollOffset else 0)
        }
    }
    Column(Modifier.fillMaxSize().padding(top = contentPadding.calculateTopPadding() + 4.dp)) {
        androidx.compose.material3.OutlinedTextField(
            value = query, onValueChange = { query = it.take(200) }, singleLine = true,
            label = { Text(stringResource(R.string.personal_library_search)) },
            modifier = Modifier.fillMaxWidth().padding(horizontal = AutPlayTokens.dimensions.screenPadding)
                .testTag("personal-library-search"),
            trailingIcon = if (query.isEmpty()) null else { {
                androidx.compose.material3.TextButton(onClick = { query = "" }) { Text(stringResource(R.string.personal_search_clear)) }
            } },
        )
        LibraryBrowseControls(displayState, onSectionChange, onFilterChange, onSortChange, onOpenDownloads, onAddLocal)
        if (search.isNotEmpty()) {
            val matches = searchPersonal?.let { find ->
                androidx.compose.runtime.key(find, displayState.section, displayState.filter, search) {
                    remember { find(displayState.section, displayState.filter, search) }
                        .collectAsState(initial = null).value
                }
            }
            PersonalLibraryMatches(search, matches ?: displayState, contentPadding, onSelect, onOpenCollection)
            return@Column
        }
        if (displayState.section == LibrarySection.Playlists) ImportPlaylistAction()
        val collections = displayState.section == LibrarySection.Albums || displayState.section == LibrarySection.Playlists
        LazyVerticalGrid(
            columns = if (collections) GridCells.Adaptive(132.dp) else GridCells.Fixed(1),
            modifier = Modifier.fillMaxWidth().weight(1f).testTag("library-product-list"),
            state = gridState,
            contentPadding = PaddingValues(
                start = AutPlayTokens.dimensions.screenPadding,
                end = AutPlayTokens.dimensions.screenPadding,
                bottom = contentPadding.calculateBottomPadding() + 16.dp,
            ),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            state.lastImportedTitle?.let { title ->
                fullWidthItem(key = "library:import-success:$title") {
                    Text(
                        stringResource(R.string.library_add_local_success, title),
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            if (state.tracks.isEmpty() && displayState.section == LibrarySection.Tracks && displayState.filter == LibraryFilter.All) {
                fullWidthItem(key = "library:picker-help") {
                    Text(stringResource(R.string.local_audio_picker_hint), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (state.error) {
                fullWidthItem(key = "library:error") {
                    AutPlayStateSurface(
                        AutPlayStateKind.Error,
                        stringResource(R.string.state_error_body),
                    )
                }
            }
            when (displayState.section) {
                LibrarySection.Tracks, LibrarySection.Offline, LibrarySection.Unavailable -> {
                    if (state.tracks.isEmpty()) {
                        fullWidthItem(key = "library:tracks-empty") {
                            AutPlayStateSurface(
                                AutPlayStateKind.Empty,
                                stringResource(when (displayState.filter) {
                                    LibraryFilter.Loved -> R.string.library_favorites_empty
                                    LibraryFilter.OnDevice -> R.string.library_on_device_empty
                                    else -> R.string.library_empty_body
                                }),
                            )
                        }
                    }
                    gridItems(state.tracks, key = { "$LIBRARY_TRACK_PREFIX${it.id}" }) { track ->
                        LibraryTrackCard(
                            track, onSelect, onRemoveOrRestore, onLike,
                            onPlayNext, onAddToQueue, onDownload, onRepairAccess,
                            onAddToPlaylist = { addTrackToPlaylistId = it },
                            downloadsAvailable = !state.localMode,
                        )
                    }
                }
                LibrarySection.Artists -> when (state.artistBrowseState) {
                    ArtistBrowseUiState.Unavailable -> fullWidthItem(key = "library:artists-unavailable") {
                        AutPlayStateSurface(
                            AutPlayStateKind.Empty,
                            stringResource(R.string.library_artist_identity_unavailable),
                        )
                    }
                    ArtistBrowseUiState.Loading -> fullWidthItem(key = "library:artists-loading") {
                        AutPlayStateSurface(
                            AutPlayStateKind.Loading,
                            stringResource(R.string.library_artists_loading),
                        )
                    }
                    ArtistBrowseUiState.Error -> fullWidthItem(key = "library:artists-error") {
                        AutPlayStateSurface(
                            AutPlayStateKind.Error,
                            stringResource(R.string.library_artists_error),
                        )
                    }
                    ArtistBrowseUiState.Ready -> {
                        if (state.artists.isEmpty()) {
                            fullWidthItem(key = "library:artists-empty") {
                                AutPlayStateSurface(
                                    AutPlayStateKind.Empty,
                                    stringResource(R.string.library_artists_empty),
                                )
                            }
                        }
                        gridItems(state.artists, key = { "$LIBRARY_ARTIST_PREFIX${it.id}" }) { artist ->
                            ArtistCard(artist) { onOpenCollection(LibrarySection.Artists, artist.id) }
                        }
                    }
                }
                LibrarySection.Albums -> collectionItems(state.albums, LibrarySection.Albums, LIBRARY_ALBUM_PREFIX, onOpenCollection)
                LibrarySection.Playlists -> collectionItems(state.playlists, LibrarySection.Playlists, LIBRARY_PLAYLIST_PREFIX, onOpenCollection)
                LibrarySection.Review -> fullWidthItem(key = "library:review") {
                    AutPlayStateSurface(
                        if (state.reviewCount == 0) AutPlayStateKind.Empty else AutPlayStateKind.Offline,
                        if (state.reviewCount == 0) {
                            stringResource(R.string.library_review_empty)
                        } else {
                            pluralStringResource(R.plurals.library_review_count, state.reviewCount, state.reviewCount)
                        },
                        actionLabel = if (state.reviewCount > 0) stringResource(R.string.nav_import_review) else null,
                        onAction = if (state.reviewCount > 0) onOpenReview else null,
                    )
                }
            }
        }
    }
    addTrackToPlaylistId?.let { trackId ->
        AddTrackToPlaylistDialog(
            trackRefId = trackId,
            playlists = manualPlaylists,
            actions = manualPlaylistActions,
            onDismiss = { addTrackToPlaylistId = null },
        )
    }

}


@Composable
private fun PersonalLibraryMatches(query: String, state: LibraryScreenUiState, padding: PaddingValues,
    play: (String) -> Unit, open: (LibrarySection, String) -> Unit) {
    fun matches(value: String?) = value?.contains(query, ignoreCase = true) == true
    val trackQuery = remember(query) { TrackTitleQuery(query) }
    val tracks = state.tracks.filter { state.section == LibrarySection.Tracks && (trackQuery.matches(it.title) || matches(it.artist)) }
    val artists = state.artists.filter { state.section == LibrarySection.Artists && app.autplay.application.artist.artistNameMatches(it.name, query) }
    val albums = state.albums.filter { state.section == LibrarySection.Albums && (matches(it.title) || matches(it.subtitle)) }
    val playlists = state.playlists.filter { state.section == LibrarySection.Playlists && matches(it.title) }
    LazyColumn(contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = padding.calculateBottomPadding())) {
        if (tracks.isNotEmpty()) {
            items(tracks, key = { "track:${it.id}" }) { track ->
                AutPlayTrackRow(track.title, track.artist, track.id, { play(track.id) })
            }
        }
        if (artists.isNotEmpty()) {
            items(artists, key = { "artist:${it.id}" }) { artist ->
                ArtistCard(artist) { open(LibrarySection.Artists, artist.id) }
            }
        }
        for ((section, rows) in listOf(LibrarySection.Albums to albums, LibrarySection.Playlists to playlists)) {
            if (rows.isEmpty()) continue
            items(rows, key = { "${section.name}:${it.id}" }) { row ->
                AutPlayCard(onClick = { open(section, row.id) }) { Column {
                    Text(row.title)
                    row.subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                } }
            }
        }
        if (tracks.isEmpty() && artists.isEmpty() && albums.isEmpty() && playlists.isEmpty())
            item { Text(stringResource(R.string.search_empty)) }
    }
}

private fun LazyGridScope.fullWidthItem(key: String, content: @Composable () -> Unit) {
    item(key = key, span = { GridItemSpan(maxLineSpan) }) { content() }
}

private fun LazyGridScope.collectionItems(
    collections: List<CoreCollectionUiItem>,
    section: LibrarySection,
    keyPrefix: String,
    onOpenCollection: (LibrarySection, String) -> Unit,
) {
    if (collections.isEmpty()) {
        fullWidthItem(key = "${keyPrefix}empty") {
            AutPlayStateSurface(AutPlayStateKind.Empty, stringResource(R.string.library_collection_empty))
        }
    }
    gridItems(collections, key = { "$keyPrefix${it.id}" }) { collection ->
        val count = collection.itemCount?.let { pluralStringResource(R.plurals.ui_library_refresh_collection_track_count, it, it) }
        CollectionCoverTile(collection.title, collection.artworkTrackId, collection.subtitle, Modifier.testTag("$keyPrefix${collection.id}"),
            caption = listOfNotNull(releaseYear(collection.releaseDate), count).joinToString(" · ").ifEmpty { null }, catalogCoverId = collection.releaseMbid) {
            onOpenCollection(section, collection.id)
        }
    }
}

private const val SEARCH_RESULT_PREFIX = "search-result:"
private const val SEARCH_VAULT_RESULT_PREFIX = "search-vault-result:"
private const val LIBRARY_TRACK_PREFIX = "library-track:"
private const val LIBRARY_ARTIST_PREFIX = "library-artist:"
private const val LIBRARY_ALBUM_PREFIX = "library-album:"
private const val LIBRARY_PLAYLIST_PREFIX = "library-playlist:"
private val LIBRARY_CONTENT_PREFIXES = setOf(
    LIBRARY_TRACK_PREFIX,
    LIBRARY_ARTIST_PREFIX,
    LIBRARY_ALBUM_PREFIX,
    LIBRARY_PLAYLIST_PREFIX,
)

private fun searchSourceLabel(offline: Boolean, server: Boolean, playable: Boolean?): Int = when {
    offline && server -> R.string.search_source_both_offline
    offline -> R.string.search_source_phone_offline
    playable == true -> R.string.search_source_server_online
    playable == false -> R.string.search_source_server_unavailable
    server -> R.string.search_source_server_metadata
    else -> R.string.search_source_library_metadata
}

private fun searchListKeys(state: SearchScreenUiState): List<String> = buildList {
    addAll(listOf("search:query", "search:kind"))
    if (state.loading || (state.vaultAvailable && state.vaultLoading)) add("search:loading")
    if (state.error) add("search:error")
    if (state.vaultAvailable && state.vaultError) add("search:vault-error")
    if (state.searched || state.vaultSearched) {
        add("search:count")
        if (state.results.isEmpty() && state.distinctVaultResults.isEmpty() &&
            !state.loading && !state.vaultLoading && !state.error && !state.vaultError
        ) add("search:empty")
    }
    if (state.vaultAvailable && (state.searched || state.vaultSearched)) add("search:internet-action")
    state.results.forEach { add("$SEARCH_RESULT_PREFIX${it.id}") }
    state.distinctVaultResults.forEach { add("$SEARCH_VAULT_RESULT_PREFIX${it.id}") }
    if (state.vaultNextCursor != null) add("search:vault-more")
}

private fun libraryListKeys(state: LibraryScreenUiState): List<String> = buildList {
    state.lastImportedTitle?.let { add("library:import-success:$it") }
    if (state.tracks.isEmpty() && state.section == LibrarySection.Tracks && state.filter == LibraryFilter.All) add("library:picker-help")
    if (state.error) add("library:error")
    when (state.section) {
        LibrarySection.Tracks, LibrarySection.Offline, LibrarySection.Unavailable -> {
            if (state.tracks.isEmpty()) add("library:tracks-empty")
            state.tracks.forEach { add("$LIBRARY_TRACK_PREFIX${it.id}") }
        }
        LibrarySection.Artists -> when (state.artistBrowseState) {
            ArtistBrowseUiState.Unavailable -> add("library:artists-unavailable")
            ArtistBrowseUiState.Loading -> add("library:artists-loading")
            ArtistBrowseUiState.Error -> add("library:artists-error")
            ArtistBrowseUiState.Ready -> {
                if (state.artists.isEmpty()) add("library:artists-empty")
                state.artists.forEach { add("$LIBRARY_ARTIST_PREFIX${it.id}") }
            }
        }
        LibrarySection.Albums -> {
            if (state.albums.isEmpty()) add("${LIBRARY_ALBUM_PREFIX}empty")
            state.albums.forEach { add("$LIBRARY_ALBUM_PREFIX${it.id}") }
        }
        LibrarySection.Playlists -> {
            if (state.playlists.isEmpty()) add("${LIBRARY_PLAYLIST_PREFIX}empty")
            state.playlists.forEach { add("$LIBRARY_PLAYLIST_PREFIX${it.id}") }
        }
        LibrarySection.Review -> add("library:review")
    }
}

@Composable
private fun rememberStableAnchorListState(
    contextKey: String,
    persistedAnchor: ListAnchor?,
    orderedKeys: List<String>,
    contentPrefixes: Set<String>,
    onAnchorChange: (ListAnchor) -> Unit,
): LazyListState {
    val listState = remember(contextKey) { LazyListState() }
    RestoreContentAnchor(contextKey, persistedAnchor, orderedKeys, onAnchorChange, listState::scrollToItem) {
        val first = listState.firstVisibleItemIndex
        listState.layoutInfo.visibleItemsInfo.firstOrNull { item ->
            item.index >= first && contentPrefixes.any { (item.key as? String)?.startsWith(it) == true }
        }?.let { item ->
            ListAnchor(contextKey, item.key as String, if (item.index == first) listState.firstVisibleItemScrollOffset else 0)
        }
    }
    return listState
}

@Composable
private fun RestoreContentAnchor(
    contextKey: String,
    persistedAnchor: ListAnchor?,
    orderedKeys: List<String>,
    onAnchorChange: (ListAnchor) -> Unit,
    scrollTo: suspend (Int, Int) -> Unit,
    visibleAnchor: () -> ListAnchor?,
) {
    var restored by remember(contextKey) { mutableStateOf(false) }
    val report by rememberUpdatedState(onAnchorChange)
    LaunchedEffect(contextKey, persistedAnchor, orderedKeys) {
        if (restored) return@LaunchedEffect
        val anchor = persistedAnchor?.takeIf { it.contextKey == contextKey }
        if (anchor != null) {
            val index = orderedKeys.indexOf(anchor.itemKey)
            if (index < 0) return@LaunchedEffect
            scrollTo(index, anchor.scrollOffset.coerceAtLeast(0))
        }
        restored = true
    }
    LaunchedEffect(contextKey, orderedKeys, restored) {
        if (restored) snapshotFlow(visibleAnchor).collect { anchor -> if (anchor != null) report(anchor) }
    }
}

@Composable
private fun LibraryTrackCard(
    track: CoreTrackUiItem,
    onPlay: (String) -> Unit,
    onRemoveOrRestore: (String) -> Unit,
    onLike: (String) -> Unit,
    onPlayNext: (String) -> Unit,
    onAddToQueue: (String) -> Unit,
    onDownload: (String) -> Unit,
    onRepairAccess: () -> Unit,
    onAddToPlaylist: (String) -> Unit,
    downloadsAvailable: Boolean,
) {
    AutPlayTrackRow(
        title = track.title,
        artist = track.artist,
        trackId = track.id,
        onPlay = { onPlay(track.id) },
        selected = track.selected,
        modifier = Modifier.testTag("library-track-${track.id}"),
    ) {
        if (downloadsAvailable) app.autplay.ui.downloads.InlineTrackDownload(track.id, track.downloaded) { onDownload(track.id) }
        AutPlayIconButton(
            icon = if (track.loved) AutPlayIcon.FavoriteFilled else AutPlayIcon.Favorite,
            labelRes = if (track.loved) R.string.action_liked else R.string.action_like,
            onClick = { onLike(track.id) },
            modifier = Modifier.testTag("library-like-${track.id}").semantics { selected = track.loved },
            tint = if (track.loved) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
        )
        TrackActionsMenu(Modifier.testTag("library-actions-${track.id}")) { dismiss ->
            DropdownMenuItem(
                text = { Text(stringResource(R.string.queue_play_next)) },
                onClick = { dismiss(); onPlayNext(track.id) },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.queue_add_to_end)) },
                onClick = { dismiss(); onAddToQueue(track.id) },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.playlist_add_selected)) },
                onClick = { dismiss(); onAddToPlaylist(track.id) },
            )
            if (track.permissionRevoked) {
                DropdownMenuItem(
                    text = { Text(stringResource(R.string.action_choose_folder_again)) },
                    onClick = { dismiss(); onRepairAccess() },
                )
            }
            if (track.canRemove) {
                DropdownMenuItem(
                    text = { Text(stringResource(R.string.action_remove)) },
                    onClick = { dismiss(); onRemoveOrRestore(track.id) },
                )
            }
        }
    }
}

@Composable
internal fun librarySectionLabel(section: LibrarySection): String = stringResource(
    when (section) {
        LibrarySection.Tracks -> R.string.library_section_tracks
        LibrarySection.Artists -> R.string.library_section_artists
        LibrarySection.Albums -> R.string.library_section_albums
        LibrarySection.Playlists -> R.string.library_section_playlists
        LibrarySection.Offline -> R.string.library_section_offline
        LibrarySection.Unavailable -> R.string.library_section_unavailable
        LibrarySection.Review -> R.string.library_section_review
    },
)

@Composable
internal fun librarySortLabel(sort: LibrarySort): String = stringResource(
    when (sort) {
        LibrarySort.RecentlyAdded -> R.string.library_sort_recent
        LibrarySort.Title -> R.string.library_sort_title
        LibrarySort.Artist -> R.string.library_sort_artist
    },
)

@Composable
private fun TrackRow(title: String, artist: String, detail: String?, trackId: String? = null) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        AutPlayArtwork(title, trackId = trackId)
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(artist, color = AutPlayTokens.colors.mutedText, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (detail != null) Text(detail, style = MaterialTheme.typography.labelMedium, color = AutPlayTokens.colors.mutedText)
        }
    }
}
