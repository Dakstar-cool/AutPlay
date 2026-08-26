package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onVisibilityChanged
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.LibrarySort
import app.autplay.ui.core.ListAnchor
import kotlinx.coroutines.flow.distinctUntilChanged

public data class CoreTrackUiItem(
    public val id: String,
    public val title: String,
    public val artist: String?,
    public val selected: Boolean = false,
    public val permissionRevoked: Boolean = false,
    public val downloaded: Boolean = false,
    public val loved: Boolean = false,
)

public data class CoreCollectionUiItem(
    public val id: String,
    public val title: String,
    public val subtitle: String?,
    public val itemCount: Int? = null,
)

public data class CoreArtistUiItem(
    public val id: String,
    public val name: String,
    public val subtitle: String?,
)

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
)

public data class HomeRecommendationUiItem(
    public val id: String,
    public val title: String,
    public val artist: String,
    public val section: String,
    public val feedbackEnabled: Boolean,
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
    public val playlists: List<CoreCollectionUiItem> = emptyList(),
    public val offlineReady: List<HomeTrackUiItem> = emptyList(),
    public val problems: List<HomeProblemUiItem> = emptyList(),
    public val recommendationError: Boolean = false,
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
    public val vaultResultCount: Int? = null,
    public val vaultError: Boolean = false,
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

@Composable
@OptIn(ExperimentalLayoutApi::class)
public fun HomeProductScreen(
    state: HomeScreenUiState,
    contentPadding: PaddingValues,
    onOpenListenTogether: () -> Unit,
    onRecommendationVisible: (String) -> Unit,
    onLike: (String) -> Unit,
    onDislike: (String) -> Unit,
    onRetry: () -> Unit = {},
    onPlayTrack: (String) -> Unit = {},
    onOpenPlaylist: (String) -> Unit = {},
    onOpenOffline: () -> Unit = {},
    onOpenProblems: () -> Unit = {},
    playerState: PlaybackPresentationState = PlaybackPresentationState(),
    currentTrackRefId: String? = null,
    currentTrackLiked: Boolean = false,
    onOpenPlayer: () -> Unit = {},
    onTogglePlayPause: () -> Unit = {},
    onLikeHeroTrack: (String) -> Unit = {},
) {
    val heroState = buildHomePlaybackHeroUiState(
        homeState = state,
        playerState = playerState,
        currentTrackRefId = currentTrackRefId,
        currentTrackLiked = currentTrackLiked,
    )
    val listState = rememberLazyListState()
    val showStickyPlayback by remember(listState, heroState.hasActivePlayback) {
        derivedStateOf {
            val layout = listState.layoutInfo
            val hero = layout.visibleItemsInfo.firstOrNull { it.key == "home:playback-hero" }
            heroState.hasActivePlayback &&
                layout.totalItemsCount > 0 &&
                (hero == null || hero.offset + hero.size <= layout.viewportStartOffset)
        }
    }
    Box(Modifier.fillMaxWidth()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxWidth().testTag("home-product-list"),
            contentPadding = PaddingValues(
                start = 8.dp,
                top = 0.dp,
                end = 8.dp,
                bottom = contentPadding.calculateBottomPadding() + 28.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
        item(key = "home:playback-hero") {
            BoxWithConstraints(
                modifier = Modifier.fillMaxWidth(),
                contentAlignment = Alignment.Center,
            ) {
                HomePlaybackHero(
                    state = heroState,
                    localMode = state.localMode,
                    onOpenPlayer = onOpenPlayer,
                    onPlayTrack = onPlayTrack,
                    onTogglePlayPause = onTogglePlayPause,
                    onLike = onLikeHeroTrack,
                    onOpenListenTogether = onOpenListenTogether,
                    modifier = Modifier.requiredWidth(maxWidth + 16.dp),
                    topChromePadding = contentPadding.calculateTopPadding(),
                )
            }
        }
        if (state.offlineFallback) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Offline,
                    stringResource(R.string.home_offline_fallback),
                )
            }
        }
        item(key = "home:contexts-heading") {
            AutPlaySectionHeader(stringResource(R.string.home_context_collections))
        }
        item(key = "home:contexts") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    stringResource(R.string.home_context_collections_placeholder),
                    color = AutPlayTokens.colors.mutedText,
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf(
                        R.string.home_context_energy,
                        R.string.home_context_running,
                        R.string.home_context_focus,
                        R.string.home_context_evening,
                    ).forEach { label ->
                        AutPlayChip(
                            text = stringResource(label),
                            selected = false,
                            onClick = {},
                            enabled = false,
                        )
                    }
                }
            }
        }
        item(key = "home:new-releases-heading") {
            AutPlaySectionHeader(stringResource(R.string.home_new_releases))
        }
        if (state.releases.isEmpty()) {
            item(key = "home:new-releases-empty") {
                Text(stringResource(R.string.home_empty_releases), color = AutPlayTokens.colors.mutedText)
            }
        } else {
            items(state.releases, key = HomeReleaseUiItem::id) { release ->
                TrackRow(release.title, release.artist, release.dateText)
            }
        }
        state.continueListening?.let { item ->
            item(key = "home:continue:${item.trackId}") {
                AutPlaySectionHeader(stringResource(R.string.home_continue_listening))
            }
            item(key = "home:continue-card:${item.trackId}") {
                AutPlayCard(onClick = { onPlayTrack(item.trackId) }) {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        TrackRow(item.title, item.artist ?: stringResource(R.string.library_unknown_artist), item.positionText)
                        Text(stringResource(R.string.home_continue_action), color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
        val recentItems = state.recentlyPlayed.ifEmpty { state.recentlyAdded }
        item(key = "home:recent-heading") {
            AutPlaySectionHeader(
                stringResource(
                    if (state.recentlyPlayed.isNotEmpty()) R.string.home_recently_played else R.string.home_recently_added,
                ),
            )
        }
        if (recentItems.isEmpty()) {
            item(key = "home:recent-empty") {
                Text(stringResource(R.string.home_recent_empty), color = AutPlayTokens.colors.mutedText)
            }
        } else {
            items(recentItems, key = { "home-recent:${it.id}" }) { track ->
                AutPlayCard(
                    onClick = { onPlayTrack(track.id) },
                    modifier = Modifier.testTag("home-recent"),
                ) {
                    TrackRow(track.title, track.artist ?: stringResource(R.string.library_unknown_artist), null)
                }
            }
        }
        item { AutPlaySectionHeader(stringResource(R.string.home_recommendations)) }
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
        } else {
            if (state.recommendations.isEmpty()) {
                item { Text(stringResource(R.string.home_empty_recommendations), color = AutPlayTokens.colors.mutedText) }
            } else {
                items(state.recommendations, key = HomeRecommendationUiItem::id) { item ->
                    AutPlayCard(
                        modifier = Modifier
                            .testTag("home-recommendation")
                            .onVisibilityChanged(minFractionVisible = 0.01f) {
                                onRecommendationVisible(item.id)
                            },
                    ) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text(item.section, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                            TrackRow(item.title, item.artist, null)
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(
                                    enabled = item.feedbackEnabled,
                                    onClick = { onLike(item.id) },
                                    modifier = Modifier.heightIn(min = 48.dp),
                                ) { Text(stringResource(R.string.action_like)) }
                                OutlinedButton(
                                    enabled = item.feedbackEnabled,
                                    onClick = { onDislike(item.id) },
                                    modifier = Modifier.heightIn(min = 48.dp),
                                ) { Text(stringResource(R.string.action_dislike)) }
                            }
                        }
                    }
                }
            }
        }
        item(key = "home:playlists-heading") { AutPlaySectionHeader(stringResource(R.string.home_playlists)) }
        if (state.playlists.isEmpty()) {
            item(key = "home:playlists-empty") {
                Text(stringResource(R.string.home_playlists_empty), color = AutPlayTokens.colors.mutedText)
            }
        } else {
            items(state.playlists, key = { "home-playlist:${it.id}" }) { playlist ->
                AutPlayCard(onClick = { onOpenPlaylist(playlist.id) }) {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(playlist.title, style = MaterialTheme.typography.titleMedium)
                        playlist.subtitle?.let { Text(it, color = AutPlayTokens.colors.mutedText) }
                    }
                }
            }
        }
        item(key = "home:offline-heading") { AutPlaySectionHeader(stringResource(R.string.home_offline_ready)) }
        if (state.offlineReady.isEmpty()) {
            item(key = "home:offline-empty") {
                Text(stringResource(R.string.home_offline_empty), color = AutPlayTokens.colors.mutedText)
            }
        } else {
            items(state.offlineReady, key = { "home-offline:${it.id}" }) { track ->
                AutPlayCard(onClick = { onPlayTrack(track.id) }) {
                    TrackRow(track.title, track.artist ?: stringResource(R.string.library_unknown_artist), null)
                }
            }
            item(key = "home:offline-open") {
                OutlinedButton(onClick = onOpenOffline, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.home_offline_open))
                }
            }
        }
        if (state.problems.isNotEmpty()) {
            item(key = "home:problems-heading") {
                AutPlaySectionHeader(stringResource(R.string.home_problems))
            }
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
        if (showStickyPlayback) {
            HomePlaybackStickyControl(
                state = heroState,
                onOpenPlayer = onOpenPlayer,
                onTogglePlayPause = onTogglePlayPause,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = contentPadding.calculateTopPadding() + 8.dp, start = 16.dp, end = 16.dp),
            )
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
    onRetry: () -> Unit = {},
    onVaultScopeChange: (Boolean) -> Unit = {},
    listAnchor: ListAnchor? = null,
    onListAnchorChange: (ListAnchor) -> Unit = {},
) {
    val listContextKey = "search:${state.query.trim().replace(Regex("\\s+"), " ")}:${state.vaultSelected}"
    val listKeys = searchListKeys(state)
    val listState = rememberStableAnchorListState(
        contextKey = listContextKey,
        persistedAnchor = listAnchor,
        orderedKeys = listKeys,
        contentPrefixes = setOf(SEARCH_RESULT_PREFIX),
        onAnchorChange = onListAnchorChange,
    )
    LazyColumn(
        state = listState,
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item(key = "search:heading") {
            Text(
                stringResource(R.string.nav_search),
                style = MaterialTheme.typography.headlineLarge,
                modifier = Modifier.semantics { heading() },
            )
        }
        item(key = "search:scopes") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AutPlayChip(stringResource(R.string.search_local_scope), true, {}, enabled = false)
                if (state.vaultAvailable) {
                    AutPlayChip(
                        stringResource(R.string.search_vault_scope),
                        state.vaultSelected,
                        { onVaultScopeChange(!state.vaultSelected) },
                    )
                }
            }
        }
        item(key = "search:query") {
            OutlinedTextField(
                value = state.query,
                onValueChange = onQueryChange,
                label = { Text(stringResource(R.string.search_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item(key = "search:submit") {
            Button(
                onClick = onSearch,
                enabled = state.query.isNotBlank(),
                modifier = Modifier
                    .heightIn(min = 48.dp)
                    .testTag("local-search-submit"),
            ) {
                Text(stringResource(R.string.search_action))
            }
        }
        if (state.loading) {
            item(key = "search:loading") {
                AutPlayStateSurface(
                    AutPlayStateKind.Loading,
                    stringResource(R.string.search_loading),
                )
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
        } else if (state.searched && !state.loading) {
            item(key = "search:count") { Text(pluralStringResource(R.plurals.search_result_count, state.results.size, state.results.size)) }
            if (state.results.isEmpty()) {
                item(key = "search:empty") {
                    AutPlayStateSurface(
                        AutPlayStateKind.Empty,
                        stringResource(R.string.search_empty),
                    )
                }
            }
        }
        items(state.results, key = { "$SEARCH_RESULT_PREFIX${it.id}" }) { track ->
            AutPlayCard(onClick = { onPlay(track.id) }) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    AutPlayArtwork(track.title)
                    Column(Modifier.weight(1f)) {
                        Text(track.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(
                            track.artist ?: stringResource(R.string.library_unknown_artist),
                            color = AutPlayTokens.colors.mutedText,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    AutPlayPlatformIcon(AutPlayIcon.Play, stringResource(R.string.action_play), Modifier.size(24.dp))
                }
            }
        }
        if (state.vaultAvailable && state.vaultSelected) {
            item(key = "search:vault-heading") {
                AutPlaySectionHeader(stringResource(R.string.search_vault_results))
            }
            when {
                state.vaultLoading -> item(key = "search:vault-loading") {
                    AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.search_vault_loading))
                }
                state.vaultError -> item(key = "search:vault-error") {
                    AutPlayStateSurface(AutPlayStateKind.Offline, stringResource(R.string.search_vault_unavailable))
                }
                state.vaultResultCount == 0 -> item(key = "search:vault-empty") {
                    AutPlayStateSurface(AutPlayStateKind.Empty, stringResource(R.string.search_vault_empty))
                }
                state.vaultResultCount != null -> item(key = "search:vault-count") {
                    Text(
                        pluralStringResource(
                            R.plurals.search_vault_result_count,
                            state.vaultResultCount,
                            state.vaultResultCount,
                        ),
                        color = AutPlayTokens.colors.mutedText,
                    )
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
) {
    var controlsExpanded by rememberSaveable { mutableStateOf(false) }
    val listContextKey = "library:${state.section}:${state.sort}:${state.filter}"
    val listKeys = libraryListKeys(state)
    val listState = rememberStableAnchorListState(
        contextKey = listContextKey,
        persistedAnchor = listAnchor,
        orderedKeys = listKeys,
        contentPrefixes = LIBRARY_CONTENT_PREFIXES,
        onAnchorChange = onListAnchorChange,
    )
    LazyColumn(
        modifier = Modifier.testTag("library-product-list"),
        state = listState,
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item(key = "library:heading") {
            LibraryShortcutGrid(onSectionChange, onFilterChange)
        }
        item(key = "library:sections") {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(LibrarySection.entries) { section ->
                    AutPlayChip(
                        text = librarySectionLabel(section),
                        selected = state.section == section,
                        onClick = { onSectionChange(section) },
                    )
                }
            }
        }
        if (state.section in setOf(LibrarySection.Tracks, LibrarySection.Offline, LibrarySection.Unavailable)) {
            item(key = "library:controls") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Surface(
                        onClick = { controlsExpanded = !controlsExpanded },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
                        shape = MaterialTheme.shapes.medium,
                        color = AutPlayTokens.colors.raisedSurface,
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            AutPlayPlatformIcon(
                                icon = AutPlayIcon.Settings,
                                contentDescription = null,
                                modifier = Modifier.size(24.dp),
                                tint = MaterialTheme.colorScheme.primary,
                            )
                            Column(Modifier.weight(1f)) {
                                Text(stringResource(R.string.library_filter_label), style = MaterialTheme.typography.labelLarge)
                                Text(
                                    "${librarySortLabel(state.sort)} · ${libraryFilterLabel(state.filter)}",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = AutPlayTokens.colors.mutedText,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                    if (controlsExpanded) {
                        Text(stringResource(R.string.library_sort_label), style = MaterialTheme.typography.labelLarge)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            items(LibrarySort.entries) { sort ->
                                AutPlayChip(
                                    text = librarySortLabel(sort),
                                    selected = state.sort == sort,
                                    onClick = { onSortChange(sort) },
                                )
                            }
                        }
                        Text(stringResource(R.string.library_filter_label), style = MaterialTheme.typography.labelLarge)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            items(
                                listOf(
                                    LibraryFilter.All,
                                    LibraryFilter.Loved,
                                    LibraryFilter.Downloaded,
                                    LibraryFilter.Available,
                                    LibraryFilter.Unavailable,
                                ),
                            ) { filter ->
                                AutPlayChip(
                                    text = libraryFilterLabel(filter),
                                    selected = state.filter == filter,
                                    onClick = { onFilterChange(filter) },
                                )
                            }
                        }
                    }
                }
            }
        }
        if (state.section in setOf(LibrarySection.Tracks, LibrarySection.Offline, LibrarySection.Unavailable)) {
            item(key = "library:count") {
                Text(
                    pluralStringResource(R.plurals.library_track_count, state.tracks.size, state.tracks.size),
                    color = AutPlayTokens.colors.mutedText,
                )
            }
        }
        if (state.localMode) {
            item(key = "library:local-mode") {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    AutPlayPlatformIcon(
                        AutPlayIcon.Download,
                        contentDescription = null,
                        modifier = Modifier.size(22.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Text(
                        stringResource(R.string.library_local_mode),
                        style = MaterialTheme.typography.bodyMedium,
                        color = AutPlayTokens.colors.mutedText,
                    )
                }
            }
        }
        item(key = "library:add") {
            OutlinedButton(
                onClick = onAddLocal,
                enabled = !state.importingLocalTrack,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            ) {
                AutPlayPlatformIcon(
                    AutPlayIcon.Import,
                    contentDescription = null,
                    modifier = Modifier.size(20.dp),
                    tint = MaterialTheme.colorScheme.primary,
                )
                Text(
                    text = stringResource(
                        if (state.importingLocalTrack) R.string.library_adding_local else R.string.library_add_local,
                    ),
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
        }
        state.lastImportedTitle?.let { title ->
            item(key = "library:import-success:$title") {
                Text(
                    stringResource(R.string.library_add_local_success, title),
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
        if (state.error) {
            item(key = "library:error") {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                )
            }
        }
        when (state.section) {
            LibrarySection.Tracks, LibrarySection.Offline, LibrarySection.Unavailable -> {
                if (state.tracks.isEmpty()) {
                    item(key = "library:tracks-empty") {
                        AutPlayStateSurface(
                            AutPlayStateKind.Empty,
                            stringResource(R.string.library_empty_body),
                        )
                    }
                }
                items(state.tracks, key = { "$LIBRARY_TRACK_PREFIX${it.id}" }) { track ->
                    LibraryTrackCard(track, onSelect, onRemoveOrRestore, onLike)
                }
            }
            LibrarySection.Artists -> when (state.artistBrowseState) {
                ArtistBrowseUiState.Unavailable -> item(key = "library:artists-unavailable") {
                    AutPlayStateSurface(
                        AutPlayStateKind.Empty,
                        stringResource(R.string.library_artist_identity_unavailable),
                    )
                }
                ArtistBrowseUiState.Loading -> item(key = "library:artists-loading") {
                    AutPlayStateSurface(
                        AutPlayStateKind.Loading,
                        stringResource(R.string.library_artists_loading),
                    )
                }
                ArtistBrowseUiState.Error -> item(key = "library:artists-error") {
                    AutPlayStateSurface(
                        AutPlayStateKind.Error,
                        stringResource(R.string.library_artists_error),
                    )
                }
                ArtistBrowseUiState.Ready -> {
                    if (state.artists.isEmpty()) {
                        item(key = "library:artists-empty") {
                            AutPlayStateSurface(
                                AutPlayStateKind.Empty,
                                stringResource(R.string.library_artists_empty),
                            )
                        }
                    }
                    items(state.artists, key = { "$LIBRARY_ARTIST_PREFIX${it.id}" }) { artist ->
                        AutPlayCard(
                            onClick = { onOpenCollection(LibrarySection.Artists, artist.id) },
                        ) {
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(artist.name, style = MaterialTheme.typography.titleMedium)
                                artist.subtitle?.let {
                                    Text(it, color = AutPlayTokens.colors.mutedText)
                                }
                            }
                        }
                    }
                }
            }
            LibrarySection.Albums -> collectionItems(state.albums, LibrarySection.Albums, LIBRARY_ALBUM_PREFIX, onOpenCollection)
            LibrarySection.Playlists -> collectionItems(state.playlists, LibrarySection.Playlists, LIBRARY_PLAYLIST_PREFIX, onOpenCollection)
            LibrarySection.Review -> item(key = "library:review") {
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

@Composable
private fun LibraryShortcutGrid(
    onSectionChange: (LibrarySection) -> Unit,
    onFilterChange: (LibraryFilter) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            LibraryShortcut(
                label = libraryFilterLabel(LibraryFilter.Loved),
                icon = AutPlayIcon.Favorite,
                modifier = Modifier.weight(1f),
                onClick = {
                    onSectionChange(LibrarySection.Tracks)
                    onFilterChange(LibraryFilter.Loved)
                },
            )
            LibraryShortcut(
                label = libraryFilterLabel(LibraryFilter.Downloaded),
                icon = AutPlayIcon.Download,
                modifier = Modifier.weight(1f),
                onClick = {
                    onSectionChange(LibrarySection.Tracks)
                    onFilterChange(LibraryFilter.Downloaded)
                },
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            LibraryShortcut(
                label = librarySectionLabel(LibrarySection.Albums),
                icon = AutPlayIcon.Library,
                modifier = Modifier.weight(1f),
                onClick = { onSectionChange(LibrarySection.Albums) },
            )
            LibraryShortcut(
                label = librarySectionLabel(LibrarySection.Playlists),
                icon = AutPlayIcon.Playlist,
                modifier = Modifier.weight(1f),
                onClick = { onSectionChange(LibrarySection.Playlists) },
            )
        }
    }
}

@Composable
private fun LibraryShortcut(
    label: String,
    icon: AutPlayIcon,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        modifier = modifier.heightIn(min = 92.dp),
        shape = MaterialTheme.shapes.large,
        color = AutPlayTokens.colors.raisedSurface,
        tonalElevation = 1.dp,
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            AutPlayPlatformIcon(
                icon = icon,
                contentDescription = null,
                modifier = Modifier.size(26.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Text(label, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.collectionItems(
    collections: List<CoreCollectionUiItem>,
    section: LibrarySection,
    keyPrefix: String,
    onOpenCollection: (LibrarySection, String) -> Unit,
) {
    if (collections.isEmpty()) {
        item(key = "${keyPrefix}empty") {
            AutPlayStateSurface(AutPlayStateKind.Empty, stringResource(R.string.library_collection_empty))
        }
    }
    items(collections, key = { "$keyPrefix${it.id}" }) { collection ->
        AutPlayCard(onClick = { onOpenCollection(section, collection.id) }) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(collection.title, style = MaterialTheme.typography.titleMedium)
                collection.subtitle?.let { Text(it, color = AutPlayTokens.colors.mutedText) }
                collection.itemCount?.let { count ->
                    Text(
                        pluralStringResource(R.plurals.library_track_count, count, count),
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        }
    }
}

private const val SEARCH_RESULT_PREFIX = "search-result:"
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

private fun searchListKeys(state: SearchScreenUiState): List<String> = buildList {
    addAll(listOf("search:heading", "search:scopes", "search:query", "search:submit"))
    if (state.loading) add("search:loading")
    if (state.error) {
        add("search:error")
    } else if (state.searched && !state.loading) {
        add("search:count")
        if (state.results.isEmpty()) add("search:empty")
    }
    state.results.forEach { add("$SEARCH_RESULT_PREFIX${it.id}") }
    if (state.vaultAvailable && state.vaultSelected) {
        add("search:vault-heading")
        when {
            state.vaultLoading -> add("search:vault-loading")
            state.vaultError -> add("search:vault-error")
            state.vaultResultCount == 0 -> add("search:vault-empty")
            state.vaultResultCount != null -> add("search:vault-count")
        }
    }
}

private fun libraryListKeys(state: LibraryScreenUiState): List<String> = buildList {
    add("library:heading")
    add("library:sections")
    val isTrackSection = state.section in setOf(
        LibrarySection.Tracks,
        LibrarySection.Offline,
        LibrarySection.Unavailable,
    )
    if (isTrackSection) {
        add("library:controls")
        add("library:count")
    }
    if (state.localMode) add("library:local-mode")
    add("library:add")
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
    var restorationComplete by remember(contextKey) { mutableStateOf(false) }
    LaunchedEffect(contextKey, persistedAnchor, orderedKeys) {
        if (restorationComplete) return@LaunchedEffect
        val anchor = persistedAnchor?.takeIf { it.contextKey == contextKey }
        if (anchor == null) {
            restorationComplete = true
            return@LaunchedEffect
        }
        val itemIndex = orderedKeys.indexOf(anchor.itemKey)
        if (itemIndex >= 0) {
            listState.scrollToItem(itemIndex, anchor.scrollOffset.coerceAtLeast(0))
            restorationComplete = true
        }
    }
    LaunchedEffect(listState, contextKey, orderedKeys) {
        snapshotFlow {
            listState.layoutInfo.visibleItemsInfo.firstOrNull { item ->
                val key = item.key as? String
                key != null && contentPrefixes.any(key::startsWith)
            }?.let { item ->
                ListAnchor(
                    contextKey = contextKey,
                    itemKey = item.key as String,
                    scrollOffset = (-item.offset).coerceAtLeast(0),
                )
            }
        }.distinctUntilChanged().collect { anchor ->
            if (restorationComplete && anchor != null) onAnchorChange(anchor)
        }
    }
    return listState
}

@Composable
private fun LibraryTrackCard(
    track: CoreTrackUiItem,
    onSelect: (String) -> Unit,
    onRemoveOrRestore: (String) -> Unit,
    onLike: (String) -> Unit,
) {
    Surface(
        onClick = { onSelect(track.id) },
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = if (track.selected) {
            MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.52f)
        } else {
            AutPlayTokens.colors.raisedSurface
        },
        tonalElevation = if (track.selected) 2.dp else 0.dp,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                AutPlayArtwork(track.title)
                Column(Modifier.weight(1f)) {
                    Text(
                        track.title,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        track.artist ?: stringResource(R.string.library_unknown_artist),
                        color = AutPlayTokens.colors.mutedText,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (track.downloaded) {
                    AutPlayPlatformIcon(
                        icon = AutPlayIcon.Download,
                        contentDescription = stringResource(R.string.nav_downloads),
                        modifier = Modifier.size(24.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                }
                if (track.selected) {
                    AutPlayPlatformIcon(
                        icon = AutPlayIcon.Check,
                        contentDescription = stringResource(R.string.library_selected),
                        modifier = Modifier.size(24.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            if (track.permissionRevoked) {
                AutPlayStateSurface(
                    AutPlayStateKind.PermissionRevoked,
                    stringResource(R.string.library_permission_revoked),
                )
            }
            if (track.selected) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { onRemoveOrRestore(track.id) }, modifier = Modifier.heightIn(min = 48.dp)) {
                        Text(stringResource(R.string.action_remove))
                    }
                    OutlinedButton(
                        onClick = { onLike(track.id) },
                        enabled = !track.loved,
                        modifier = Modifier.heightIn(min = 48.dp),
                    ) {
                        Text(stringResource(if (track.loved) R.string.action_liked else R.string.action_like))
                    }
                }
            }
        }
    }
}

@Composable
private fun librarySectionLabel(section: LibrarySection): String = stringResource(
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
private fun librarySortLabel(sort: LibrarySort): String = stringResource(
    when (sort) {
        LibrarySort.RecentlyAdded -> R.string.library_sort_recent
        LibrarySort.Title -> R.string.library_sort_title
        LibrarySort.Artist -> R.string.library_sort_artist
    },
)

@Composable
private fun libraryFilterLabel(filter: LibraryFilter): String = stringResource(
    when (filter) {
        LibraryFilter.All -> R.string.library_filter_all
        LibraryFilter.Loved -> R.string.library_filter_loved
        LibraryFilter.Downloaded -> R.string.library_filter_downloaded
        LibraryFilter.Available -> R.string.library_filter_available
        LibraryFilter.Unavailable -> R.string.library_filter_unavailable
    },
)

@Composable
private fun TrackRow(title: String, artist: String, detail: String?) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        AutPlayArtwork(title)
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(artist, color = AutPlayTokens.colors.mutedText, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (detail != null) Text(detail, style = MaterialTheme.typography.labelMedium, color = AutPlayTokens.colors.mutedText)
        }
    }
}
