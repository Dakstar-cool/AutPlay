package app.autplay.ui.desktop

import androidx.compose.foundation.clickable
import app.autplay.application.desktop.DesktopTransportAction
import androidx.compose.material3.TextButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopHistoryState
import app.autplay.application.desktop.DesktopLocalStatisticsState
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportMappingReview
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopSavedImportsState
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopSleepTimerState
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopSettingsTransferState
import app.autplay.application.desktop.DesktopTasteState
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.application.sourceaccess.SourceAccessActions
import app.autplay.application.sourceaccess.SourceAccessState
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackPresentationState

public enum class DesktopLibraryKind { Track, Playlist, Artist, Album }

/** Preferences belong to the host; this renderer never owns library or queue state. */
@Composable
@NonSkippableComposable // Lifecycle callback owners can be equal values with different identities.
public fun AutPlayDesktopShell(
    section: DesktopSection,
    language: DesktopLanguage,
    themeMode: DesktopThemeMode,
    playback: PlaybackPresentationState,
    playbackAdapterAvailable: Boolean,
    onSectionChange: (DesktopSection) -> Unit,
    onLanguageChange: (DesktopLanguage) -> Unit,
    onThemeChange: (DesktopThemeMode) -> Unit,
    playbackCallbacks: DesktopPlaybackCallbacks,
    modifier: Modifier = Modifier,
    library: DesktopLibrarySnapshot? = null,
    libraryCallbacks: DesktopLibraryCallbacks = DesktopLibraryCallbacks(),
    importing: Boolean = false,
    importResult: DesktopImportResult? = null,
    errorCode: String? = null,
    sleepTimer: DesktopSleepTimerState = DesktopSleepTimerState(),
    sleepTimerCallbacks: DesktopSleepTimerCallbacks = DesktopSleepTimerCallbacks(),
    preferencesState: DesktopPreferencesState? = null,
    preferencesTransfer: DesktopSettingsTransferState = DesktopSettingsTransferState(),
    preferencesCallbacks: DesktopSettingsCallbacks = DesktopSettingsCallbacks(),
    accentPalette: DesktopAccentPalette = DesktopAccentPalette.Coral,
    tasteState: DesktopTasteState = DesktopTasteState(),
    tasteCallbacks: DesktopTasteCallbacks = DesktopTasteCallbacks(),
    historyState: DesktopHistoryState = DesktopHistoryState(),
    historyCallbacks: DesktopHistoryCallbacks = DesktopHistoryCallbacks(),
    metadataState: DesktopMetadataState = DesktopMetadataState(),
    metadataCallbacks: DesktopMetadataCallbacks = DesktopMetadataCallbacks(),
    copyInspectionState: DesktopCopyInspectionState = DesktopCopyInspectionState(),
    copyInspectionCallbacks: DesktopCopyInspectionCallbacks = DesktopCopyInspectionCallbacks(),
    playerState: DesktopPlayerState? = null,
    transportCallbacks: DesktopTransportCallbacks = DesktopTransportCallbacks(),
    importReviewState: DesktopImportReviewState = DesktopImportReviewState(),
    importReviewCallbacks: DesktopImportReviewCallbacks = DesktopImportReviewCallbacks(),
    lifecycleState: DesktopLifecycleState = DesktopLifecycleState(),
    lifecycleCallbacks: DesktopLifecycleCallbacks = DesktopLifecycleCallbacks(),
    textSearch: DesktopTextSearchPort? = null,
    savedImportsState: DesktopSavedImportsState = DesktopSavedImportsState(),
    savedImportsCallbacks: DesktopSavedImportsCallbacks = DesktopSavedImportsCallbacks(),
    importMapping: DesktopImportMappingReview? = null,
    importJobProgress: DesktopImportProgress? = null,
    localStatisticsState: DesktopLocalStatisticsState = DesktopLocalStatisticsState(),
    localStatisticsCallbacks: DesktopLocalStatisticsCallbacks = DesktopLocalStatisticsCallbacks(),
    onOpenAccount: (() -> Unit)? = null,
    sourceAccessState: SourceAccessState? = null,
    sourceAccessActions: SourceAccessActions? = null,
    onResumeSourceAccess: ((SourceAccessState) -> Unit)? = null,
    homeAccountContent: (@Composable () -> Unit)? = null,
    libraryKind: DesktopLibraryKind = DesktopLibraryKind.Track,
    onLibraryKindChange: ((DesktopLibraryKind) -> Unit)? = null,
    libraryCatalogContent: (@Composable () -> Unit)? = null,
    playerDownload: (@Composable (String) -> Unit)? = null,
    onOpenDownloads: (() -> Unit)? = null,
    homeAlbums: (@Composable () -> Unit)? = null,
    homeWheel: (@Composable () -> Unit)? = null,
    serverSearch: (@Composable () -> Unit)? = null,
    playlistTransfer: (@Composable (app.autplay.application.desktop.DesktopPlaylist?) -> Unit)? = null,
    playbackLibrary: DesktopLibrarySnapshot? = library,
    onPlayerPreference: (String, String) -> Unit = libraryCallbacks.onSetTrackPreference,
) {
    // Read these holders inside content lambdas, so strong skipping cannot retain an equal old owner.
    val currentLifecycleState by rememberDesktopBrowseReference(lifecycleState)
    val currentLifecycleCallbacks by rememberDesktopBrowseReference(lifecycleCallbacks)
    val currentOpenAccount by rememberUpdatedState(onOpenAccount)
    val browseLifetime = remember { DesktopBrowseLifetime() }
    val currentLibrary by rememberDesktopBrowseReference(library)
    DisposableEffect(browseLifetime) { onDispose { browseLifetime.retire() } }
    var searchingServer by remember(section, serverSearch != null) { mutableStateOf(false) }
    val presentation = playerState?.playback ?: playback
    var selectedTrackId by remember { mutableStateOf<String?>(null) }
    var trackSelectionRevision by remember { mutableStateOf(0L) }
    var metadataTarget by remember { mutableStateOf<DesktopMetadataTarget?>(null) }
    val librarySort = remember { mutableStateOf(DesktopLibrarySort.Title) }
    val librarySortDirection = remember { mutableStateOf(DesktopSortDirection.Ascending) }
    val collectionFilter = remember { mutableStateOf(DesktopTrackCollectionFilter.ACTIVE) }
    var detailsSection by remember { mutableStateOf(section) }
    var navigationRevision by remember { mutableStateOf(0L) }
    val currentSection by rememberUpdatedState(section)
    var restartRequired by remember { mutableStateOf(false) }
    val restartReported = desktopTasteRequiresReopen(errorCode) || desktopTasteRequiresReopen(importResult?.errorCode) ||
        desktopTasteRequiresReopen(tasteState.errorCode) || desktopTasteRequiresReopen(historyState.errorCode) ||
        desktopTasteRequiresReopen(metadataState.errorCode) || desktopTasteRequiresReopen(copyInspectionState.errorCode) ||
        desktopTasteRequiresReopen(importReviewState.preview?.errorCode) ||
        desktopTasteRequiresReopen(importReviewState.progress?.errorCode) ||
        desktopTasteRequiresReopen(savedImportsState.errorCode) || desktopTasteRequiresReopen(importJobProgress?.errorCode) ||
        desktopTasteRequiresReopen(localStatisticsState.errorCode)
    val reopenRequired = restartRequired || restartReported
    SideEffect { if (restartReported) restartRequired = true }
    SideEffect {
        if (detailsSection != section) {
            selectedTrackId = null
            trackSelectionRevision += 1
            navigationRevision += 1
            detailsSection = section
        }
    }
    val selectedIdAtInput = selectedTrackId
    val revisionAtInput = trackSelectionRevision
    val navigationAtInput = navigationRevision
    val isNavigationCurrent: () -> Boolean = {
        browseLifetime.isActive && currentSection == section && detailsSection == section && navigationRevision == navigationAtInput
    }
    val importReconciliationRequired = importJobProgress?.errorCode == "import_job_reconciliation_required" ||
        importResult?.errorCode == "import_job_reconciliation_required"
    val importInput = DesktopImportReviewInput(importReviewState, importReviewCallbacks, library, section,
        navigationAtInput, reopenRequired || lifecycleState.closing || importReconciliationRequired, importing)
    val currentImportInput by rememberUpdatedState(importInput)
    val currentLibraryCallbacks by rememberDesktopBrowseReference(libraryCallbacks)
    val importActions = rememberDesktopImportReviewActions(importInput, isNavigationCurrent)
    val importActive = importing || importReviewState.phase != DesktopImportPhase.IDLE
    val importActionLabel = desktopImportPhaseText(importReviewState.phase)?.label(language)
    val importCancelEnabled = if (importReviewState.phase == DesktopImportPhase.IDLE) importing else importActions.canCancel
    val guardedLibraryCallbacks = libraryCallbacks.copy(
        onImport = {
            if (isNavigationCurrent() && importInput.matches(currentImportInput) &&
                libraryCallbacks === currentLibraryCallbacks && !currentImportInput.hostBusy &&
                currentImportInput.state.phase == DesktopImportPhase.IDLE &&
                currentImportInput.library != null && !currentImportInput.reopenRequired) libraryCallbacks.onImport()
        },
        onCancelImport = {
            if (importReviewState.phase != DesktopImportPhase.IDLE) importActions.cancel()
            else if (isNavigationCurrent() && importInput.matches(currentImportInput) &&
                libraryCallbacks === currentLibraryCallbacks && currentImportInput.hostBusy &&
                currentImportInput.library != null) libraryCallbacks.onCancelImport()
        },
    )
    val transportInput = DesktopTransportInput(playerState, presentation, playbackAdapterAvailable,
        desktopTransportCommandsEnabled(playerState, library, reopenRequired), section, navigationAtInput)
    val latestTransport by rememberUpdatedState(transportInput)
    val tasteInput = DesktopTasteInput(tasteState, playbackLibrary, section, navigationAtInput, revisionAtInput, reopenRequired)
    val isTasteNavigationCurrent: () -> Boolean = {
        isNavigationCurrent() && trackSelectionRevision == revisionAtInput && !restartRequired
    }
    val trackInput = DesktopTrackInput(library, selectedIdAtInput,
        presentation.localTrackRefId.takeIf { presentation.mediaId != null }, revisionAtInput,
        isSelectionCurrent = { selectedTrackId == selectedIdAtInput && trackSelectionRevision == revisionAtInput &&
            currentSection == section && detailsSection == section })
    val trackView = rememberDesktopTrackView(trackInput)
    val currentTrackInput by rememberUpdatedState(trackInput)
    val currentTrackView by rememberUpdatedState(trackView)
    val currentMetadataState by rememberUpdatedState(metadataState)
    val currentMetadataCallbacks by rememberUpdatedState(metadataCallbacks)
    val currentReopenRequired by rememberUpdatedState(reopenRequired)
    val inspectMetadata: () -> Unit = {
        if (currentImportInput.state.phase == DesktopImportPhase.IDLE && !currentImportInput.hostBusy &&
            trackInput.isSelectionCurrent() && currentTrackView === trackView &&
            trackInput == currentTrackInput && desktopMetadataCanInspect(currentTrackInput, currentTrackView,
                currentMetadataState, currentReopenRequired)) {
            currentTrackView.selectedTrack?.let { track ->
                metadataTarget = DesktopMetadataTarget(currentTrackInput, track)
                currentMetadataCallbacks.onInspect(track.id)
            }
        }
    }
    val currentTrack = remember(playbackLibrary?.tracks, presentation.localTrackRefId) {
        playbackLibrary?.tracks?.firstOrNull { it.id == presentation.localTrackRefId }
    }
    val currentPlaybackLibrary by rememberDesktopBrowseReference(playbackLibrary)
    val currentPlayerPreference by rememberUpdatedState(onPlayerPreference)
    val navigate: (DesktopSection) -> Unit = { destination ->
        if (destination != section) {
            selectedTrackId = null
            trackSelectionRevision += 1
            navigationRevision += 1
        }
        onSectionChange(destination)
    }
    AutPlayDesktopTheme(themeMode, accentPalette) {
        Surface(modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            BoxWithConstraints {
                val compact = maxWidth < 720.dp
                Column {
                    if (compact) CompactNavigation(section, language, navigate)
                    DesktopLifecycleClosingStatus(currentLifecycleState, language)
                    DesktopImportReviewControls(importReviewState, importActions, reopenRequired, language, mappingReview = importMapping != null)
                    val savedImportControls: @Composable () -> Unit = { DesktopSavedImportsControls(DesktopSavedImportsInput(savedImportsState, importMapping,
                        savedImportsCallbacks, library, section, navigationAtInput, revisionAtInput,
                        reopenRequired || lifecycleState.closing, importActive || importReconciliationRequired), importJobProgress,
                        importReviewState.phase, importActions, language,
                        isNavigationCurrent = { isNavigationCurrent() && trackSelectionRevision == revisionAtInput }) }
                    val showingImport = savedImportsState.open || importActive
                    if (showingImport) savedImportControls()
                    Row(Modifier.weight(1f).fillMaxWidth()) {
                        if (!compact) Sidebar(section, language, navigate, serverSearch != null)
                        Column(
                            Modifier.weight(1f).fillMaxHeight().padding(if (compact) 12.dp else 24.dp),
                            verticalArrangement = Arrangement.spacedBy(24.dp),
                        ) {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(section.label(language), style = MaterialTheme.typography.headlineLarge)
                                if (onOpenAccount != null) OutlinedButton(
                                    enabled = !currentLifecycleState.closing,
                                    onClick = {
                                        if (isNavigationCurrent() && !currentLifecycleState.closing) currentOpenAccount?.invoke()
                                    },
                                ) {
                                    Text(if (language == DesktopLanguage.Russian) "Аккаунты" else "Accounts")
                                }
                            }
                            if (reopenRequired) {
                                Text(DesktopText.LibraryReopenRequired.inLanguage(language), color = MaterialTheme.colorScheme.error,
                                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                            }
                            if (section == DesktopSection.Library && onLibraryKindChange != null) {
                                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    listOf(DesktopLibraryKind.Track, DesktopLibraryKind.Artist, DesktopLibraryKind.Album, DesktopLibraryKind.Playlist).forEach { kind ->
                                        FilterChip(selected = kind == libraryKind, enabled = !currentLifecycleState.closing, onClick = {
                                            if (isNavigationCurrent() && !currentLifecycleState.closing && libraryKind != kind) {
                                                selectedTrackId = null; trackSelectionRevision += 1; navigationRevision += 1
                                                onLibraryKindChange(kind)
                                            }
                                        }, label = {
                                            val catalogKind = when (kind) {
                                                DesktopLibraryKind.Track -> DesktopCatalogKind.Track
                                                DesktopLibraryKind.Playlist -> DesktopCatalogKind.Playlist
                                                DesktopLibraryKind.Artist -> DesktopCatalogKind.Artist
                                                DesktopLibraryKind.Album -> DesktopCatalogKind.Album
                                            }
                                            Text(desktopCatalogKindText(catalogKind, language))
                                        })
                                    }
                                }
                            }
                            if (section == DesktopSection.Library && libraryKind != DesktopLibraryKind.Track && onOpenDownloads != null) {
                                TextButton(enabled = !currentLifecycleState.closing, onClick = {
                                    if (isNavigationCurrent()) onOpenDownloads()
                                }) { Text(if (language == DesktopLanguage.Russian) "Загрузки" else "Downloads") }
                            }
                            if (section == DesktopSection.Search && serverSearch != null) {
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    listOf(false, true).forEach { remote ->
                                        FilterChip(selected = remote == searchingServer, onClick = { searchingServer = remote },
                                            label = { Text(if (remote) (if (language == DesktopLanguage.Russian) "Сервер" else "Server")
                                                else (if (language == DesktopLanguage.Russian) "Моя музыка" else "My music")) })
                                    }
                                }
                            }
                            if (section == DesktopSection.Search && searchingServer && serverSearch != null) {
                                Column(Modifier.fillMaxWidth().weight(1f)) { serverSearch() }
                            } else if (section == DesktopSection.Player) {
                                DesktopPlayerContent(playbackLibrary, transportInput, language, transportCallbacks, playbackCallbacks,
                                    metadataState, sleepTimer, sleepTimerCallbacks, tasteInput, tasteCallbacks,
                                    isNavigationCurrent, isTasteNavigationCurrent,
                                    onTrackDetails = {
                                        if (isNavigationCurrent()) {
                                            trackSelectionRevision += 1
                                            selectedTrackId = it
                                        }
                                    }, onQueue = { if (isNavigationCurrent()) navigate(DesktopSection.Queue) },
                                    modifier = Modifier.weight(1f).fillMaxWidth(),
                                    smoothTrackTransitions = preferencesState?.value?.smoothTrackTransitions == true,
                                    libraryCallbacks = guardedLibraryCallbacks.copy(onSetTrackPreference = onPlayerPreference), download = playerDownload)
                            } else if (section == DesktopSection.Home) {
                                DesktopHomeContent(library, presentation, playbackAdapterAvailable, language,
                                    guardedLibraryCallbacks, importActive, importResult, errorCode, reopenRequired,
                                    isNavigationCurrent = { currentSection == section && navigationRevision == navigationAtInput },
                                    onNavigate = { destination ->
                                        if (currentSection == section && navigationRevision == navigationAtInput) {
                                            navigate(destination)
                                        }
                                    },
                                    onTrackDetails = {
                                        trackSelectionRevision += 1
                                        selectedTrackId = it
                                    }, modifier = Modifier.weight(1f).fillMaxWidth(),
                                    importActionLabel = importActionLabel, importCancelEnabled = importCancelEnabled,
                                    accountContent = homeAccountContent, homeWheel = homeWheel, homeAlbums = homeAlbums, metadata = metadataState, playingTrack = currentTrack,
                                    toggleEnabled = transportInput.controls(false).playPause,
                                    togglePlayback = {
                                        val action = if (presentation.isPlaying) DesktopTransportAction.PAUSE else DesktopTransportAction.PLAY
                                        dispatchDesktopTransport(transportInput, latestTransport, action, null, isNavigationCurrent(), false,
                                            transportCallbacks, playbackCallbacks)
                                    }, preferTrack = onPlayerPreference,
                                    artworkGesture = Modifier.desktopSkipGesture(transportInput, transportCallbacks, playbackCallbacks, false, isNavigationCurrent))
                            } else if (section == DesktopSection.History) {
                                DesktopHistoryContent(historyState, library, language, historyCallbacks, libraryCallbacks,
                                    playbackAdapterAvailable && presentation.connection == PlaybackConnectionState.Connected,
                                    reopenRequired,
                                    isNavigationCurrent = { currentSection == section && navigationRevision == navigationAtInput },
                                    onTrackDetails = {
                                        trackSelectionRevision += 1
                                        selectedTrackId = it
                                    }, errorCode = errorCode, modifier = Modifier.weight(1f).fillMaxWidth(),
                                    localStatisticsState = localStatisticsState, localStatisticsCallbacks = localStatisticsCallbacks)
                            } else if (section == DesktopSection.Library &&
                                (libraryKind == DesktopLibraryKind.Artist || libraryKind == DesktopLibraryKind.Album)) {
                                Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
                                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                    if (libraryCatalogContent != null) libraryCatalogContent()
                                    else Text(if (language == DesktopLanguage.Russian) "Каталог текущего аккаунта недоступен."
                                        else "Current account catalog unavailable.")
                                }
                            } else if (library != null && section != DesktopSection.Settings) {
                                val sourceSection = when {
                                    section == DesktopSection.Search -> DesktopSection.Library
                                    section == DesktopSection.Library && libraryKind == DesktopLibraryKind.Playlist -> DesktopSection.Playlists
                                    else -> section
                                }
                                key(sourceSection, libraryKind) {
                                DesktopLibraryContent(sourceSection, if (section == DesktopSection.Queue) playbackLibrary ?: library else library, language, importActive, importResult,
                                    errorCode, guardedLibraryCallbacks, presentation.shuffleEnabled && presentation.repeatEnabled,
                                    playbackAdapterAvailable && presentation.connection == PlaybackConnectionState.Connected,
                                    reopenRequired, onTrackDetails = {
                                        trackSelectionRevision += 1
                                        selectedTrackId = it
                                    },
                                    sortState = librarySort, directionState = librarySortDirection, collectionFilterState = collectionFilter,
                                    isNavigationCurrent = desktopBrowseIdentityGuard(browseLifetime, library, { currentLibrary }) {
                                        isNavigationCurrent() && trackSelectionRevision == revisionAtInput
                                    },
                                    modifier = Modifier.weight(1f).fillMaxWidth(),
                                    copyInspectionState = copyInspectionState, copyInspectionCallbacks = copyInspectionCallbacks,
                                    importActionLabel = importActionLabel, importCancelEnabled = importCancelEnabled,
                                    navigationRevision = navigationAtInput, queueEditorOwner = libraryCallbacks,
                                    queueEditor = playerState?.queueEditor ?: DesktopQueueEditorState(), textSearch = textSearch,
                                    searchScreen = section == DesktopSection.Search, metadata = metadataState, playlistTransfer = playlistTransfer, savedImports = if (showingImport) null else savedImportControls,
                                    onOpenDownloads = onOpenDownloads)
                                }
                            } else when (section) {
                                DesktopSection.Home -> Unit
                                DesktopSection.Player -> Unit
                                DesktopSection.Search, DesktopSection.Library -> {
                                    MutedText(DesktopText.LibrarySubtitle.inLanguage(language))
                                    EmptyCollection(
                                        language, DesktopText.EmptyLibrary, DesktopText.EmptyLibraryBody,
                                        DesktopText.Import,
                                    )
                                }
                                DesktopSection.Playlists -> EmptyCollection(
                                    language, DesktopText.EmptyPlaylists, DesktopText.EmptyPlaylistsBody,
                                    DesktopText.CreatePlaylist,
                                )
                                DesktopSection.Queue -> MutedText(DesktopText.EmptyQueue.inLanguage(language))
                                DesktopSection.History -> MutedText(DesktopText.EmptyHistory.inLanguage(language))
                                DesktopSection.Settings -> {
                                    val ru = language == DesktopLanguage.Russian
                                    var settingsPage by remember { mutableStateOf(0) }
                                    val pages = if (ru) listOf("Оформление", "Музыка", "Профиль", "Приложение")
                                        else listOf("Appearance", "Music", "Profile", "Application")
                                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        pages.forEachIndexed { index, label ->
                                            FilterChip(selected = settingsPage == index, onClick = { settingsPage = index }, label = { Text(label) })
                                        }
                                    }
                                    if (settingsPage == 2 && onOpenAccount != null) OutlinedButton(onClick = onOpenAccount) {
                                        Text(if (ru) "Открыть аккаунты" else "Open accounts")
                                    }
                                    if (settingsPage == 1 && onOpenDownloads != null) OutlinedButton(onClick = onOpenDownloads) {
                                        Text(if (ru) "Загрузки" else "Downloads")
                                    }
                                    if (settingsPage == 1 && sourceAccessState != null && sourceAccessActions != null) {
                                        if (sourceAccessState.fenced && !currentLifecycleState.closing && onResumeSourceAccess != null) {
                                            OutlinedButton(onClick = {
                                                if (isNavigationCurrent() && !currentLifecycleState.closing)
                                                    onResumeSourceAccess(sourceAccessState)
                                            }) {
                                                Text(if (language == DesktopLanguage.Russian) "Возобновить доступ к источникам" else "Resume source access")
                                            }
                                        }
                                        DesktopSourceAccessControls(sourceAccessState, sourceAccessActions, language,
                                            isNavigationCurrent = { isNavigationCurrent() && !currentLifecycleState.closing })
                                    }
                                    if (settingsPage == 3) DesktopLifecycleControls(currentLifecycleState, currentLifecycleCallbacks, language,
                                        navigationAtInput, isNavigationCurrent)
                                    if (settingsPage == 0 || settingsPage == 3) DesktopSettingsControls(language, themeMode, accentPalette, preferencesState,
                                        preferencesTransfer, preferencesCallbacks, onLanguageChange, onThemeChange,
                                        modifier = Modifier.weight(1f).fillMaxWidth(), transferOnly = settingsPage == 3)
                                }
                            }
                        }
                    }
                    if (section != DesktopSection.Player) {
                        HorizontalDivider()
                        PlaybackBar(transportInput, language, playbackCallbacks, transportCallbacks,
                            metadataState = metadataState,
                            smoothTrackTransitions = preferencesState?.value?.smoothTrackTransitions == true,
                            isNavigationCurrent = isNavigationCurrent,
                            onOpenPlayer = { if (isNavigationCurrent()) navigate(DesktopSection.Player) },
                            currentTrack = currentTrack,
                            canLike = !reopenRequired && !lifecycleState.closing,
                            onPreference = { track, value ->
                                if (isNavigationCurrent() && currentLibrary === library && !currentReopenRequired &&
                                    currentPlaybackLibrary === playbackLibrary && currentPlayerPreference === onPlayerPreference) {
                                    onPlayerPreference(track.id, value)
                                }
                            })
                    }
                }
            }
        }
        if (!importActive && metadataState.requestId == null && metadataState.preview == null) {
            DesktopTrackDetails(trackInput, trackView, presentation, playbackAdapterAvailable, reopenRequired,
                language, libraryCallbacks, onClose = {
                    if (trackInput.isSelectionCurrent()) {
                        trackSelectionRevision += 1
                        selectedTrackId = null
                    }
                }, metadataState = metadataState, onInspectMetadata = inspectMetadata,
                copyInspectionState = copyInspectionState, copyInspectionCallbacks = copyInspectionCallbacks, importing = importing)
        }
        if (!importActive) {
            DesktopMetadataDialog(metadataState, metadataCallbacks, trackInput, trackView, metadataTarget,
                reopenRequired, language, inspectMetadata)
        }
    }
}

@Composable
private fun CompactNavigation(section: DesktopSection, language: DesktopLanguage, onSectionChange: (DesktopSection) -> Unit) {
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).selectableGroup().padding(8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        DesktopSection.entries.forEach { destination ->
            val selected = section == destination
            Surface(color = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                shape = MaterialTheme.shapes.small) {
                Row(Modifier.selectable(selected = selected, role = Role.Tab, onClick = { onSectionChange(destination) })
                    .padding(12.dp)) { Text(destination.label(language)) }
            }
        }
    }
}

@Composable
private fun Sidebar(section: DesktopSection, language: DesktopLanguage, onSectionChange: (DesktopSection) -> Unit, connected: Boolean) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(
            Modifier.width(208.dp).fillMaxHeight().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Surface(color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(14.dp)) {
                Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) { DesktopIcon(DesktopGlyph.Wave, null) }
            }
            Text("AutPlay", style = MaterialTheme.typography.titleLarge)
            MutedText(DesktopText.LocalCollection.inLanguage(language))
            Spacer(Modifier.height(16.dp))
            Column(Modifier.selectableGroup(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DesktopSection.entries.forEach { destination ->
                    val selected = section == destination
                    Surface(
                        color = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                        shape = MaterialTheme.shapes.small,
                    ) {
                        Row(
                            Modifier.fillMaxWidth().selectable(
                                selected = selected, role = Role.Tab,
                                onClick = { onSectionChange(destination) },
                            ).padding(horizontal = 12.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            DesktopIcon(when (destination) {
                                DesktopSection.Home -> DesktopGlyph.Home
                                DesktopSection.Search -> DesktopGlyph.Search
                                DesktopSection.Library -> DesktopGlyph.Library
                                DesktopSection.Playlists, DesktopSection.Queue -> DesktopGlyph.List
                                DesktopSection.Player -> DesktopGlyph.Play
                                DesktopSection.History -> DesktopGlyph.History
                                DesktopSection.Settings -> DesktopGlyph.Settings
                            }, null, Modifier.size(20.dp))
                            Text(destination.label(language), style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            MutedText(if (connected) (if (language == DesktopLanguage.Russian) "Подключено к Sona" else "Connected to Sona") else DesktopText.LocalOnly.inLanguage(language))
        }
    }
}

@Composable
private fun EmptyCollection(language: DesktopLanguage, title: DesktopText, body: DesktopText, action: DesktopText) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        shape = MaterialTheme.shapes.large,
    ) {
        Column(
            Modifier.padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = MaterialTheme.shapes.large) {
                Box(Modifier.size(72.dp), contentAlignment = Alignment.Center) {
                    Text("♫", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.primary)
                }
            }
            Text(title.inLanguage(language), style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
            Text(
                body.inLanguage(language), modifier = Modifier.widthIn(max = 480.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyLarge,
            )
            OutlinedButton(onClick = {}, enabled = false) { Text(action.inLanguage(language)) }
        }
    }
}

@Composable
private fun PlaybackBar(
    input: DesktopTransportInput,
    language: DesktopLanguage,
    callbacks: DesktopPlaybackCallbacks,
    transportCallbacks: DesktopTransportCallbacks,
    metadataState: DesktopMetadataState,
    isNavigationCurrent: () -> Boolean,
    onOpenPlayer: () -> Unit,
    currentTrack: app.autplay.application.desktop.DesktopTrack?,
    canLike: Boolean,
    onPreference: (app.autplay.application.desktop.DesktopTrack, String) -> Unit,
    smoothTrackTransitions: Boolean = false,
) {
    if (input.playback.mediaId == null) return
    Surface(color = MaterialTheme.colorScheme.secondaryContainer, contentColor = MaterialTheme.colorScheme.onSecondaryContainer) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f).desktopSkipGesture(input, transportCallbacks, callbacks, true, isNavigationCurrent)
                .clickable(role = Role.Button, onClickLabel = DesktopText.OpenPlayer.inLanguage(language), onClick = onOpenPlayer)) {
                DesktopTrackVisuals(input, metadataState, language, compact = true, artworkSize = 44.dp,
                    navigationCurrent = isNavigationCurrent(), smoothTrackTransitions = smoothTrackTransitions)
            }
            if (currentTrack != null) {
                DesktopLikeButton(currentTrack, language, canLike) { value -> onPreference(currentTrack, value) }
            }
            DesktopTransportButtons(input, language, transportCallbacks, callbacks, true, isNavigationCurrent, scrollable = false)
        }
    }
}

@Composable
private fun MutedText(text: String) {
    Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
}

internal fun formatPlaybackTime(milliseconds: Long): String {
    val seconds = milliseconds.coerceAtLeast(0) / 1_000
    val minutes = seconds / 60
    return "$minutes:${(seconds % 60).toString().padStart(2, '0')}"
}
