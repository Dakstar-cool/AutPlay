package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R

/** Browsing does not mark releases as seen, dismiss them, or create recommendation feedback. */
@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun HomeReleaseSection(releases: List<HomeReleaseUiItem>, onOpenRelease: (String) -> Unit) {
    var showAll by rememberSaveable { mutableStateOf(false) }
    val title = stringResource(R.string.home_release_feed_title)
    Column(Modifier.testTag("home-releases-section"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(title, Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleLarge)
            if (releases.isNotEmpty()) TextButton(onClick = { showAll = true }, modifier = Modifier.testTag("home-releases-all")) {
                Text(stringResource(R.string.home_release_feed_all))
            }
        }
        if (releases.isEmpty()) {
            Text(stringResource(R.string.home_empty_releases), color = AutPlayTokens.colors.mutedText)
            return@Column
        }
        val largeText = LocalDensity.current.fontScale >= 1.5f
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val coverWidth = if (largeText) (maxWidth * 0.8f).coerceIn(200.dp, 300.dp)
                else ((maxWidth - 20.dp) / 2.15f).coerceIn(120.dp, 216.dp)
            LazyRow(
                modifier = Modifier.fillMaxWidth().testTag("home-release-strip"),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(releases, key = HomeReleaseUiItem::id) { release ->
                    ReleaseCover(release, Modifier.width(coverWidth)) { onOpenRelease(release.id) }
                }
            }
        }
    }
    if (showAll && releases.isNotEmpty()) {
        ModalBottomSheet(
            onDismissRequest = { showAll = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = AutPlayTokens.colors.raisedSurface,
            tonalElevation = 0.dp,
        ) {
            Column(Modifier.fillMaxWidth().fillMaxHeight(0.8f).testTag("home-releases-sheet")) {
                Text(title, Modifier.padding(horizontal = 20.dp, vertical = 12.dp).semantics { heading() },
                    style = MaterialTheme.typography.headlineSmall)
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(132.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f).fillMaxWidth().testTag("home-releases-list"),
                    contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 28.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    gridItems(releases, key = HomeReleaseUiItem::id) { release ->
                        ReleaseCover(release, Modifier.fillMaxWidth()) {
                            showAll = false
                            onOpenRelease(release.id)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ReleaseCover(release: HomeReleaseUiItem, modifier: Modifier, onClick: () -> Unit) {
    CollectionCoverTile(release.title, release.artworkTrackId, release.artist, modifier.testTag("home-release:${release.id}"),
        caption = releaseYear(release.dateText), catalogCoverId = release.releaseMbid, onClick = onClick)
}
