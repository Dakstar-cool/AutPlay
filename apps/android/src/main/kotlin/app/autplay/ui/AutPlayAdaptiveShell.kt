package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.background
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Badge
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.ui.player.LocalPlayerCollapse
import app.autplay.ui.player.LocalPlayerCollapseTargetTop

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
    snackbarHost: @Composable () -> Unit = {},
    content: @Composable (
        destination: UiDestination,
        contentPadding: PaddingValues,
        widthClass: UiWidthClass,
    ) -> Unit,
) {
    val lightTheme = MaterialTheme.colorScheme.background.luminance() > 0.5f
    AutPlaySystemBarIconAppearance(
        useDarkStatusBarIcons = shouldUseDarkStatusBarIcons(lightTheme),
        useDarkNavigationBarIcons = lightTheme,
    )
    val stateHolder = rememberSaveableStateHolder()
    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        val widthClass = remember(maxWidth) { widthClassFor(maxWidth) }
        val routeContent: @Composable (PaddingValues) -> Unit = { padding ->
            stateHolder.SaveableStateProvider(selectedDestination.route) {
                content(selectedDestination, padding, widthClass)
            }
        }
        if (selectedDestination == UiDestination.NowPlaying) {
            CompositionLocalProvider(LocalPlayerCollapse provides {
                if (canNavigateBack) onNavigateBack() else onDestinationSelected(UiDestination.Home)
            }) {
                PlayerCollapseShell(
                    widthClass = widthClass,
                    unreadSyncConflicts = unreadSyncConflicts,
                    nowPlayingBar = nowPlayingBar,
                    snackbarHost = snackbarHost,
                    routeContent = routeContent,
                )
            }
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
                snackbarHost,
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
                snackbarHost,
                routeContent,
            )
        }
    }
}

internal fun shouldUseDarkStatusBarIcons(lightTheme: Boolean): Boolean = lightTheme

@Composable
private fun PlayerCollapseShell(
    widthClass: UiWidthClass,
    unreadSyncConflicts: Int,
    nowPlayingBar: @Composable () -> Unit,
    snackbarHost: @Composable () -> Unit,
    routeContent: @Composable (PaddingValues) -> Unit,
) {
    val density = LocalDensity.current
    var miniTopPx by remember { mutableStateOf<Float?>(null) }
    var playerContentTopPx by remember { mutableStateOf<Float?>(null) }
    val targetTop = miniTopPx?.let { miniTop ->
        playerContentTopPx?.let { contentTop -> with(density) { (miniTop - contentTop).coerceAtLeast(0f).toDp() } }
    }
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(
            Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                .padding(start = if (widthClass == UiWidthClass.Compact) 0.dp else 128.dp)
                .clearAndSetSemantics {},
        ) {
            Box(Modifier.fillMaxWidth().onGloballyPositioned { miniTopPx = it.positionInRoot().y }) {
                nowPlayingBar()
            }
            if (widthClass == UiWidthClass.Compact) {
                HorizontalDivider(color = AutPlayTokens.colors.border.copy(alpha = 0.65f))
                CompactNavigationBar(UiDestination.Home, unreadSyncConflicts, {})
            } else {
                Box(Modifier.navigationBarsPadding())
            }
        }
        CompositionLocalProvider(LocalPlayerCollapseTargetTop provides targetTop) {
            Scaffold(containerColor = Color.Transparent, snackbarHost = snackbarHost) { padding ->
                Box(Modifier.fillMaxSize().padding(padding)
                    .onGloballyPositioned { playerContentTopPx = it.positionInRoot().y }) {
                    routeContent(PaddingValues())
                }
            }
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
    snackbarHost: @Composable () -> Unit,
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        snackbarHost = snackbarHost,
        topBar = {
            AutPlayTopBar(
                selectedDestination,
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
                HorizontalDivider(color = AutPlayTokens.colors.border.copy(alpha = 0.65f))
                CompactNavigationBar(selectedDestination, unreadSyncConflicts, onDestinationSelected)
            }
        },
    ) { padding -> content(padding) }
}

@Composable
private fun CompactNavigationBar(
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLowest) {
        BoxWithConstraints(Modifier.fillMaxWidth().navigationBarsPadding()) {
            val inlineLabels = maxWidth >= 360.dp
            Row(
                Modifier.fillMaxWidth().height(61.dp).padding(horizontal = 6.dp)
                    .selectableGroup().testTag("compact-navigation"),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                UiDestination.compactNavigation.forEach { destination ->
                    CompactNavigationItem(destination, selectedDestination, unreadSyncConflicts,
                        onDestinationSelected, inlineLabels)
                }
            }
        }
    }
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
    snackbarHost: @Composable () -> Unit,
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
            snackbarHost = snackbarHost,
            topBar = {
                AutPlayTopBar(
                    selectedDestination,
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
                    Box(Modifier.navigationBarsPadding())
                }
            },
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
    destination: UiDestination,
    canNavigateBack: Boolean,
    onNavigateBack: () -> Unit,
    onProfileClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onNowPlayingClick: () -> Unit,
    nowPlayingAvailable: Boolean,
    immersive: Boolean = false,
) {
    CenterAlignedTopAppBar(
        title = {
            if (!immersive) {
                Text(
                    stringResource(topBarTitle(destination)),
                    style = MaterialTheme.typography.titleLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        },
        navigationIcon = {
            if (canNavigateBack && destination != UiDestination.Home) {
                AutPlayIconButton(AutPlayIcon.Back, R.string.action_back, onNavigateBack)
            } else if (!immersive) {
                AutPlayIconButton(AutPlayIcon.Profile, R.string.action_open_profile, onProfileClick)
            }
        },
        actions = {
            if (!immersive) {
                AutPlayIconButton(AutPlayIcon.Settings, R.string.action_open_settings, onSettingsClick)
            }
        },
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = when {
                immersive -> Color.Transparent
                else -> MaterialTheme.colorScheme.background.copy(alpha = 0.96f)
            },
        ),
    )
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.CompactNavigationItem(
    destination: UiDestination,
    selectedDestination: UiDestination,
    unreadSyncConflicts: Int,
    onDestinationSelected: (UiDestination) -> Unit,
    inlineLabels: Boolean,
) {
    val label = stringResource(destination.labelRes)
    val selected = destination == selectedDestination
    val color = if (selected) MaterialTheme.colorScheme.primary else AutPlayTokens.colors.mutedText
    Box(
        Modifier.weight(1f).fillMaxHeight()
            .selectable(selected, role = Role.Tab, onClick = { onDestinationSelected(destination) })
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        val itemIcon: @Composable () -> Unit = {
            Box(
                Modifier.background(
                    if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.13f) else Color.Transparent,
                    RoundedCornerShape(10.dp),
                ).padding(horizontal = 6.dp, vertical = 4.dp),
            ) {
                CompositionLocalProvider(LocalContentColor provides color) {
                    DestinationIcon(destination, unreadSyncConflicts)
                }
            }
        }
        val itemLabel: @Composable () -> Unit = {
            Text(label, color = color, style = MaterialTheme.typography.labelSmall, maxLines = 1,
                overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
        }
        if (inlineLabels) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                itemIcon()
                itemLabel()
            }
        } else {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                itemIcon()
                itemLabel()
            }
        }
    }
}

private fun topBarTitle(destination: UiDestination): Int = when (destination) {
    UiDestination.Home -> R.string.home_my_wave
    UiDestination.Search -> R.string.nav_search
    UiDestination.Library -> R.string.library_title
    else -> destination.labelRes
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
