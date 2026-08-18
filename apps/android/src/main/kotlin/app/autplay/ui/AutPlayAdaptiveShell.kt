package app.autplay.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Badge
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private val CompactBreakpoint: Dp = 600.dp
private val ExpandedBreakpoint: Dp = 840.dp

/**
 * Responsive application frame. It is intentionally stateless: data-backed screens and actions are
 * supplied by the parent, preserving the Android local-first domain boundaries.
 */
@Composable
public fun AutPlayAdaptiveShell(
    selectedDestination: UiDestination,
    onDestinationSelected: (UiDestination) -> Unit,
    modifier: Modifier = Modifier,
    unreadSyncConflicts: Int = 0,
    onProfileClick: () -> Unit = { onDestinationSelected(UiDestination.Profile) },
    onNowPlayingClick: () -> Unit = { onDestinationSelected(UiDestination.NowPlaying) },
    nowPlayingBar: @Composable () -> Unit = {},
    content: @Composable (destination: UiDestination, contentPadding: PaddingValues) -> Unit,
) {
    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        val widthClass = widthClassFor(maxWidth)
        if (widthClass == UiWidthClass.Compact) {
            CompactShell(
                selectedDestination = selectedDestination,
                onDestinationSelected = onDestinationSelected,
                unreadSyncConflicts = unreadSyncConflicts,
                onProfileClick = onProfileClick,
                onNowPlayingClick = onNowPlayingClick,
                nowPlayingBar = nowPlayingBar,
                content = content,
            )
        } else {
            RailShell(
                selectedDestination = selectedDestination,
                onDestinationSelected = onDestinationSelected,
                unreadSyncConflicts = unreadSyncConflicts,
                onProfileClick = onProfileClick,
                onNowPlayingClick = onNowPlayingClick,
                nowPlayingBar = nowPlayingBar,
                content = content,
            )
        }
    }
}

/** Public for previews and screen-level tests without requiring a device configuration. */
public fun widthClassFor(width: Dp): UiWidthClass = when {
    width < CompactBreakpoint -> UiWidthClass.Compact
    width < ExpandedBreakpoint -> UiWidthClass.Medium
    else -> UiWidthClass.Expanded
}

@Composable
private fun CompactShell(
    selectedDestination: UiDestination,
    onDestinationSelected: (UiDestination) -> Unit,
    unreadSyncConflicts: Int,
    onProfileClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingBar: @Composable () -> Unit,
    content: @Composable (UiDestination, PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = { AutPlayTopBar(onProfileClick, onNowPlayingClick) },
        bottomBar = {
            Column {
                nowPlayingBar()
                NavigationBar {
                    UiDestination.compactNavigation.forEach { destination ->
                        CompactNavigationItem(destination, selectedDestination, unreadSyncConflicts, onDestinationSelected)
                    }
                }
            }
        },
    ) { padding -> content(selectedDestination, padding) }
}

@Composable
private fun RailShell(
    selectedDestination: UiDestination,
    onDestinationSelected: (UiDestination) -> Unit,
    unreadSyncConflicts: Int,
    onProfileClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingBar: @Composable () -> Unit,
    content: @Composable (UiDestination, PaddingValues) -> Unit,
) {
    Row(Modifier.fillMaxSize()) {
        NavigationRail(modifier = Modifier.fillMaxHeight()) {
            Column(
                modifier = Modifier.weight(1f).verticalScroll(rememberScrollState()),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                UiDestination.railNavigation.forEach { destination ->
                    RailNavigationItem(destination, selectedDestination, unreadSyncConflicts, onDestinationSelected)
                }
            }
            RailAction("Profile", "●", onProfileClick)
            RailAction("Now playing", "▶", onNowPlayingClick)
        }
        Scaffold(
            modifier = Modifier.weight(1f),
            topBar = { AutPlayTopBar(onProfileClick, onNowPlayingClick) },
            bottomBar = nowPlayingBar,
        ) { padding -> content(selectedDestination, padding) }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AutPlayTopBar(onProfileClick: () -> Unit, onNowPlayingClick: () -> Unit) {
    TopAppBar(
        title = { Text("AutPlay", maxLines = 1, overflow = TextOverflow.Ellipsis) },
        actions = {
            HeaderAction("Now playing", "▶", onNowPlayingClick)
            HeaderAction("Profile", "●", onProfileClick)
        },
    )
}

@Composable
private fun HeaderAction(label: String, glyph: String, onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.semantics { contentDescription = label }) {
        Text(glyph, style = MaterialTheme.typography.titleLarge)
    }
}

@Composable
private fun RowScope.CompactNavigationItem(
    destination: UiDestination,
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
) {
    TextButton(
        onClick = { onDestinationSelected(destination) },
        modifier = Modifier.weight(1f),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            DestinationGlyph(destination, unreadSyncConflicts)
            Text(
                destination.label,
                style = MaterialTheme.typography.labelSmall,
                maxLines = 1,
                color = if (destination == selectedDestination) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun RailNavigationItem(
    destination: UiDestination,
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
) {
    NavigationRailItem(
        selected = destination == selectedDestination,
        onClick = { onDestinationSelected(destination) },
        icon = { DestinationGlyph(destination, unreadSyncConflicts) },
        label = { Text(destination.label, maxLines = 1) },
    )
}

@Composable
private fun DestinationGlyph(destination: UiDestination, unreadSyncConflicts: Int) {
    Box(contentAlignment = Alignment.TopEnd) {
        Text(
            destination.glyph,
            modifier = Modifier.size(24.dp).semantics { contentDescription = destination.label },
            style = MaterialTheme.typography.titleLarge,
        )
        if (destination == UiDestination.SyncStatus && unreadSyncConflicts > 0) {
            Badge { Text(unreadSyncConflicts.coerceAtMost(99).toString()) }
        }
    }
}

@Composable
private fun RailAction(label: String, glyph: String, onClick: () -> Unit) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClick = onClick)
            .padding(vertical = 12.dp),
        color = MaterialTheme.colorScheme.surface,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(glyph, style = MaterialTheme.typography.titleLarge)
            Text(label, style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
    }
}
