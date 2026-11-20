package app.autplay.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.style.TextOverflow
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
            Text(title, Modifier.weight(1f).semantics { heading() },
                style = MaterialTheme.typography.titleLarge)
            if (releases.isNotEmpty()) {
                TextButton(onClick = { showAll = true }, modifier = Modifier.testTag("home-releases-all")) {
                    Text(stringResource(R.string.home_release_feed_all))
                }
            }
        }
        if (releases.isEmpty()) {
            Text(stringResource(R.string.home_empty_releases), color = AutPlayTokens.colors.mutedText,
                modifier = Modifier.testTag("home-releases-empty"))
        } else {
            val largeText = LocalDensity.current.fontScale >= 1.5f
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                // Two complete chips and a glimpse of the next; larger text gets more reading space.
                val chipWidth = if (largeText) (maxWidth * 0.8f).coerceIn(200.dp, 300.dp)
                    else ((maxWidth - 20.dp) / 2.15f).coerceIn(120.dp, 216.dp)
                LazyRow(
                    modifier = Modifier.fillMaxWidth().testTag("home-release-strip"),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding = PaddingValues(vertical = 2.dp),
                ) {
                    items(releases, key = HomeReleaseUiItem::id) { release ->
                        HomeReleaseChip(release, { onOpenRelease(release.id) }, Modifier.width(chipWidth))
                    }
                }
            }
        }
    }
    if (showAll) {
        ModalBottomSheet(
            onDismissRequest = { showAll = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = AutPlayTokens.colors.raisedSurface,
            tonalElevation = 0.dp,
        ) {
            Column(Modifier.fillMaxWidth().fillMaxHeight(0.8f).testTag("home-releases-sheet")) {
                Text(title, Modifier.padding(horizontal = 20.dp, vertical = 12.dp).semantics { heading() },
                    style = MaterialTheme.typography.headlineSmall)
                LazyColumn(
                    modifier = Modifier.weight(1f).fillMaxWidth().testTag("home-releases-list"),
                    contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 28.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(releases, key = HomeReleaseUiItem::id) { release ->
                        HomeReleaseChip(release, {
                            showAll = false
                            onOpenRelease(release.id)
                        }, Modifier.fillMaxWidth(), expanded = true)
                    }
                }
            }
        }
    }
}

@Composable
private fun HomeReleaseChip(
    release: HomeReleaseUiItem,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    expanded: Boolean = false,
) {
    Surface(
        onClick = onClick,
        modifier = modifier.heightIn(min = 104.dp).testTag("home-release:${release.id}"),
        shape = RoundedCornerShape(24.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        contentColor = MaterialTheme.colorScheme.onSurface,
        border = BorderStroke(1.dp, AutPlayTokens.colors.border.copy(alpha = 0.55f)),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AutPlayArtwork(release.title, size = if (expanded) 48.dp else 32.dp,
                    trackId = release.artworkTrackId)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(release.title, style = MaterialTheme.typography.labelLarge,
                        minLines = if (expanded) 1 else 2,
                        maxLines = if (expanded) 3 else 2, overflow = TextOverflow.Ellipsis)
                    Text(release.artist, style = MaterialTheme.typography.bodySmall,
                        color = AutPlayTokens.colors.mutedText,
                        maxLines = if (expanded) 2 else 1, overflow = TextOverflow.Ellipsis)
                }
            }
            Text(
                release.dateText?.takeIf { it.isNotBlank() } ?: stringResource(
                    if (release.inLibrary) R.string.home_release_in_library else R.string.home_release_known_artist),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
                maxLines = if (expanded) 2 else 1, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
