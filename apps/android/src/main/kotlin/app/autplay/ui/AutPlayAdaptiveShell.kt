package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Badge
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.R

private val CompactBreakpoint: Dp = 600.dp
private val ExpandedBreakpoint: Dp = 840.dp

/** Adaptive product frame with three compact primary destinations and saveable route content. */
@Composable
public fun AutPlayAdaptiveShell(
    selectedDestination: UiDestination,
    onDestinationSelected: (UiDestination) -> Unit,
    modifier: Modifier = Modifier,
    unreadSyncConflicts: Int = 0,
    canNavigateBack: Boolean = false,
    onNavigateBack: () -> Unit = {},
    onProfileClick: () -> Unit = { onDestinationSelected(UiDestination.Profile) },
    onSettingsClick: () -> Unit = { onDestinationSelected(UiDestination.Settings) },
    onNowPlayingClick: () -> Unit = { onDestinationSelected(UiDestination.NowPlaying) },
    nowPlayingAvailable: Boolean = false,
    nowPlayingBar: @Composable () -> Unit = {},
    detailPane: @Composable (UiWidthClass) -> Unit = {},
    content: @Composable (
        destination: UiDestination,
        contentPadding: PaddingValues,
        widthClass: UiWidthClass,
    ) -> Unit,
) {
    val stateHolder = rememberSaveableStateHolder()
    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        val widthClass = remember(maxWidth) { widthClassFor(maxWidth) }
        val routeContent: @Composable (PaddingValues) -> Unit = { padding ->
            stateHolder.SaveableStateProvider(selectedDestination.route) {
                content(selectedDestination, padding, widthClass)
            }
        }
        if (selectedDestination == UiDestination.NowPlaying) {
            Scaffold(
                topBar = {
                    AutPlayTopBar(
                        canNavigateBack = true,
                        onNavigateBack = onNavigateBack,
                        onProfileClick = onProfileClick,
                        onSettingsClick = onSettingsClick,
                        onNowPlayingClick = onNowPlayingClick,
                        nowPlayingAvailable = false,
                    )
                },
            ) { padding -> routeContent(padding) }
        } else if (widthClass == UiWidthClass.Compact) {
            CompactShell(
                selectedDestination,
                onDestinationSelected,
                unreadSyncConflicts,
                canNavigateBack,
                onNavigateBack,
                onProfileClick,
                onSettingsClick,
                onNowPlayingClick,
                nowPlayingAvailable,
                nowPlayingBar,
                routeContent,
            )
        } else {
            RailShell(
                widthClass,
                selectedDestination,
                onDestinationSelected,
                unreadSyncConflicts,
                canNavigateBack,
                onNavigateBack,
                onProfileClick,
                onSettingsClick,
                onNowPlayingClick,
                nowPlayingAvailable,
                nowPlayingBar,
                detailPane,
                routeContent,
            )
        }
    }
}

/** Public for previews and deterministic width-class tests. */
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
    canNavigateBack: Boolean,
    onNavigateBack: () -> Unit,
    onProfileClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingAvailable: Boolean,
    nowPlayingBar: @Composable () -> Unit,
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = {
            AutPlayTopBar(
                canNavigateBack,
                onNavigateBack,
                onProfileClick,
                onSettingsClick,
                onNowPlayingClick,
                nowPlayingAvailable,
            )
        },
        bottomBar = {
            Column {
                nowPlayingBar()
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    UiDestination.compactNavigation.forEach { destination ->
                        CompactNavigationItem(
                            destination,
                            selectedDestination,
                            unreadSyncConflicts,
                            onDestinationSelected,
                        )
                    }
                }
            }
        },
    ) { padding -> content(padding) }
}

@Composable
private fun RailShell(
    widthClass: UiWidthClass,
    selectedDestination: UiDestination,
    onDestinationSelected: (UiDestination) -> Unit,
    unreadSyncConflicts: Int,
    canNavigateBack: Boolean,
    onNavigateBack: () -> Unit,
    onProfileClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingAvailable: Boolean,
    nowPlayingBar: @Composable () -> Unit,
    detailPane: @Composable (UiWidthClass) -> Unit,
    content: @Composable (PaddingValues) -> Unit,
) {
    Row(Modifier.fillMaxSize()) {
        NavigationRail(
            modifier = Modifier.fillMaxHeight().width(128.dp),
            containerColor = MaterialTheme.colorScheme.surface,
        ) {
            Column(
                modifier = Modifier.weight(1f).verticalScroll(rememberScrollState()),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                UiDestination.railNavigation.forEach { destination ->
                    RailNavigationItem(
                        destination,
                        selectedDestination,
                        unreadSyncConflicts,
                        onDestinationSelected,
                    )
                }
            }
        }
        Scaffold(
            modifier = Modifier.weight(1f),
            topBar = {
                AutPlayTopBar(
                    canNavigateBack,
                    onNavigateBack,
                    onProfileClick,
                    onSettingsClick,
                    onNowPlayingClick,
                    nowPlayingAvailable,
                )
            },
            bottomBar = nowPlayingBar,
        ) { padding ->
            if (widthClass == UiWidthClass.Expanded) {
                Row(Modifier.fillMaxSize()) {
                    Box(Modifier.weight(1f).fillMaxHeight()) { content(padding) }
                    Surface(
                        modifier = Modifier.width(320.dp).fillMaxHeight().padding(padding),
                        color = AutPlayTokens.colors.raisedSurface,
                        tonalElevation = 1.dp,
                    ) {
                        Box(Modifier.padding(20.dp)) { detailPane(widthClass) }
                    }
                }
            } else {
                content(padding)
            }
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AutPlayTopBar(
    canNavigateBack: Boolean,
    onNavigateBack: () -> Unit,
    onProfileClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingAvailable: Boolean,
) {
    TopAppBar(
        title = { Text(stringResource(R.string.app_name), maxLines = 1, overflow = TextOverflow.Ellipsis) },
        navigationIcon = {
            if (canNavigateBack) {
                AutPlayIconButton(AutPlayIcon.Back, R.string.action_back, onNavigateBack)
            }
        },
        actions = {
            if (nowPlayingAvailable) {
                AutPlayIconButton(AutPlayIcon.Play, R.string.nav_now_playing, onNowPlayingClick)
            }
            AutPlayIconButton(AutPlayIcon.Settings, R.string.action_open_settings, onSettingsClick)
            AutPlayIconButton(AutPlayIcon.Profile, R.string.action_open_profile, onProfileClick)
        },
        colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
    )
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.CompactNavigationItem(
    destination: UiDestination,
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
) {
    val label = stringResource(destination.labelRes)
    NavigationBarItem(
        selected = destination == selectedDestination,
        onClick = { onDestinationSelected(destination) },
        icon = { DestinationIcon(destination, unreadSyncConflicts) },
        label = { Text(label, maxLines = 2, textAlign = TextAlign.Center, overflow = TextOverflow.Ellipsis) },
    )
}

@Composable
private fun RailNavigationItem(
    destination: UiDestination,
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
) {
    val label = stringResource(destination.labelRes)
    NavigationRailItem(
        selected = destination == selectedDestination,
        onClick = { onDestinationSelected(destination) },
        icon = { DestinationIcon(destination, unreadSyncConflicts) },
        label = { Text(label, maxLines = 1) },
    )
}

@Composable
private fun DestinationIcon(destination: UiDestination, unreadSyncConflicts: Int) {
    val label = stringResource(destination.labelRes)
    Box(contentAlignment = Alignment.TopEnd) {
        AutPlayPlatformIcon(
            destination.icon,
            null,
            Modifier.semantics { contentDescription = label },
        )
        if (destination == UiDestination.SyncStatus && unreadSyncConflicts > 0) {
            Badge { Text(unreadSyncConflicts.coerceAtMost(99).toString()) }
        }
    }
}
