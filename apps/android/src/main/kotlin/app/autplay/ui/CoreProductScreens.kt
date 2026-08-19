package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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

public data class CoreTrackUiItem(
    public val id: String,
    public val title: String,
    public val artist: String?,
    public val selected: Boolean = false,
    public val permissionRevoked: Boolean = false,
)

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

public data class HomeScreenUiState(
    public val localMode: Boolean,
    public val loading: Boolean,
    public val offlineFallback: Boolean,
    public val releases: List<HomeReleaseUiItem>,
    public val recommendations: List<HomeRecommendationUiItem>,
    public val error: Boolean = false,
)

public data class SearchScreenUiState(
    public val query: String,
    public val results: List<CoreTrackUiItem>,
    public val searched: Boolean,
    public val error: Boolean = false,
)

public data class LibraryScreenUiState(
    public val localMode: Boolean,
    public val tracks: List<CoreTrackUiItem>,
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
) {
    LazyColumn(
        modifier = Modifier.fillMaxWidth(),
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            AutPlayCard {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        stringResource(R.string.home_local_title),
                        style = MaterialTheme.typography.headlineMedium,
                        modifier = Modifier.semantics { heading() },
                    )
                    Text(
                        stringResource(R.string.home_local_subtitle),
                        color = AutPlayTokens.colors.mutedText,
                    )
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AutPlayChip(
                            text = stringResource(R.string.home_my_wave),
                            selected = !state.localMode && state.recommendations.isNotEmpty(),
                            onClick = {},
                            enabled = false,
                        )
                        AutPlayChip(
                            text = stringResource(R.string.home_listen_together),
                            selected = false,
                            onClick = onOpenListenTogether,
                        )
                    }
                    if (state.localMode) {
                        Text(
                            stringResource(R.string.state_local_continues),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
            }
        }
        if (state.error) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                    actionLabel = stringResource(R.string.action_retry),
                    onAction = onRetry,
                )
            }
        } else if (state.loading) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Loading,
                    stringResource(R.string.home_loading),
                )
            }
        } else {
            if (state.offlineFallback) {
                item {
                    AutPlayStateSurface(
                        AutPlayStateKind.Offline,
                        stringResource(R.string.home_offline_fallback),
                    )
                }
            }
            item { AutPlaySectionHeader(stringResource(R.string.home_new_releases)) }
            if (state.releases.isEmpty()) {
                item { Text(stringResource(R.string.home_empty_releases), color = AutPlayTokens.colors.mutedText) }
            } else {
                items(state.releases, key = HomeReleaseUiItem::id) { release ->
                    TrackRow(release.title, release.artist, release.dateText)
                }
            }
            item { AutPlaySectionHeader(stringResource(R.string.home_recommendations)) }
            if (state.recommendations.isEmpty()) {
                item { Text(stringResource(R.string.home_empty_recommendations), color = AutPlayTokens.colors.mutedText) }
            } else {
                items(state.recommendations, key = HomeRecommendationUiItem::id) { item ->
                    AutPlayCard(
                        modifier = Modifier.onVisibilityChanged(minFractionVisible = 0.01f) {
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
) {
    LazyColumn(
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Text(
                stringResource(R.string.nav_search),
                style = MaterialTheme.typography.headlineLarge,
                modifier = Modifier.semantics { heading() },
            )
        }
        item {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AutPlayChip(stringResource(R.string.search_local_scope), true, {}, enabled = false)
                AutPlayChip(stringResource(R.string.search_vault_scope), false, {}, enabled = false)
                AutPlayChip(stringResource(R.string.search_external_scope), false, {}, enabled = false)
            }
            Text(stringResource(R.string.search_remote_unavailable), color = AutPlayTokens.colors.mutedText)
            Text(stringResource(R.string.search_external_unavailable), color = AutPlayTokens.colors.mutedText)
        }
        item {
            OutlinedTextField(
                value = state.query,
                onValueChange = onQueryChange,
                label = { Text(stringResource(R.string.search_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item {
            Button(
                onClick = onSearch,
                modifier = Modifier
                    .heightIn(min = 48.dp)
                    .testTag("local-search-submit"),
            ) {
                Text(stringResource(R.string.search_action))
            }
        }
        if (state.error) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                    actionLabel = stringResource(R.string.action_retry),
                    onAction = onRetry,
                )
            }
        } else if (state.searched) {
            item { Text(pluralStringResource(R.plurals.search_result_count, state.results.size, state.results.size)) }
            if (state.results.isEmpty()) {
                item {
                    AutPlayStateSurface(
                        AutPlayStateKind.Empty,
                        stringResource(R.string.search_empty),
                    )
                }
            }
        }
        items(state.results, key = CoreTrackUiItem::id) { track ->
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
) {
    LazyColumn(
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Text(
                stringResource(R.string.library_title),
                style = MaterialTheme.typography.headlineLarge,
                modifier = Modifier.semantics { heading() },
            )
        }
        item {
            Text(
                pluralStringResource(R.plurals.library_track_count, state.tracks.size, state.tracks.size),
                color = AutPlayTokens.colors.mutedText,
            )
        }
        if (state.localMode) {
            item { AutPlayChip(stringResource(R.string.library_local_mode), true, {}, enabled = false) }
        }
        item {
            Button(onClick = onAddLocal, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(stringResource(R.string.library_add_local))
            }
        }
        if (state.error) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.state_error_body),
                )
            }
        }
        if (state.tracks.isEmpty()) {
            item {
                AutPlayStateSurface(
                    AutPlayStateKind.Empty,
                    stringResource(R.string.library_empty_body),
                )
            }
        }
        items(state.tracks, key = CoreTrackUiItem::id) { track ->
            AutPlayCard(onClick = { onSelect(track.id) }) {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
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
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { onRemoveOrRestore(track.id) }, modifier = Modifier.heightIn(min = 48.dp)) {
                                Text(stringResource(R.string.action_remove))
                            }
                            OutlinedButton(onClick = { onLike(track.id) }, modifier = Modifier.heightIn(min = 48.dp)) {
                                Text(stringResource(R.string.action_like))
                            }
                        }
                    }
                }
            }
        }
    }
}

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
