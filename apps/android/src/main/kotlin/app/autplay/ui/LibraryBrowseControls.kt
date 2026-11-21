package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.LibrarySort

@Composable
internal fun LibraryBrowseControls(
    state: LibraryScreenUiState,
    onSectionChange: (LibrarySection) -> Unit,
    onFilterChange: (LibraryFilter) -> Unit,
    onSortChange: (LibrarySort) -> Unit,
    onDownloads: () -> Unit,
    onAdd: () -> Unit,
) {
    val sections = listOf(LibrarySection.Tracks, LibrarySection.Artists, LibrarySection.Albums, LibrarySection.Playlists, LibrarySection.Review)
    LazyRow(
        Modifier.fillMaxWidth().testTag("library-sections"),
        contentPadding = PaddingValues(horizontal = AutPlayTokens.dimensions.screenPadding),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items(sections) { section ->
            val active = state.section == section
            AutPlayChip(librarySectionLabel(section), active, { onSectionChange(section) },
                Modifier.testTag("library-tab-$section").semantics { selected = active })
        }
    }
    BoxWithConstraints(Modifier.fillMaxWidth().padding(horizontal = AutPlayTokens.dimensions.screenPadding, vertical = 4.dp)) {
        val showIcons = maxWidth >= 340.dp && LocalDensity.current.fontScale <= 1.3f
        val favorites = state.section == LibrarySection.Tracks && state.filter == LibraryFilter.Loved
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            QuickLibraryAction(
                stringResource(R.string.library_favorites_shortcut), AutPlayIcon.Favorite, showIcons, favorites,
                Modifier.weight(1f).testTag("library-favorites").semantics { selected = favorites },
            ) {
                onSectionChange(LibrarySection.Tracks)
                onFilterChange(if (favorites) LibraryFilter.All else LibraryFilter.Loved)
            }
            QuickLibraryAction(stringResource(R.string.nav_downloads), AutPlayIcon.Download, showIcons, false,
                Modifier.weight(1f).testTag("library-downloads"), onDownloads)
            if (state.section == LibrarySection.Tracks) {
                var expanded by remember { mutableStateOf(false) }
                val sortLabel = librarySortLabel(state.sort)
                Box {
                    IconButton(onClick = { expanded = true },
                        modifier = Modifier.size(48.dp).testTag("library-sort").semantics { stateDescription = sortLabel }) {
                        Icon(painterResource(R.drawable.ic_autplay_sort), stringResource(R.string.library_sort_label))
                    }
                    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                        LibrarySort.entries.forEach { sort ->
                            DropdownMenuItem(
                                text = { Text(librarySortLabel(sort)) },
                                trailingIcon = { if (sort == state.sort) AutPlayPlatformIcon(AutPlayIcon.Check, null) },
                                modifier = Modifier.testTag("library-sort-$sort").semantics { selected = state.sort == sort },
                                onClick = {
                                    expanded = false
                                    onSortChange(sort)
                                },
                            )
                        }
                    }
                }
            }
            IconButton(onClick = onAdd, enabled = !state.importingLocalTrack,
                modifier = Modifier.size(48.dp).testTag("library-add")) {
                Icon(painterResource(R.drawable.ic_autplay_add),
                    stringResource(if (state.importingLocalTrack) R.string.library_adding_local else R.string.library_add_local))
            }
        }
    }
}

@Composable
private fun QuickLibraryAction(
    label: String, icon: AutPlayIcon, showIcon: Boolean, active: Boolean, modifier: Modifier, onClick: () -> Unit,
) {
    Surface(onClick = onClick, modifier = modifier.heightIn(min = 48.dp).semantics { role = Role.Button },
        shape = MaterialTheme.shapes.medium,
        color = if (active) MaterialTheme.colorScheme.primaryContainer else AutPlayTokens.colors.raisedSurface) {
        Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp, Alignment.CenterHorizontally)) {
            if (showIcon) AutPlayPlatformIcon(icon, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
            Text(label, style = MaterialTheme.typography.labelLarge)
        }
    }
}
