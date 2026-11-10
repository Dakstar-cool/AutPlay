package app.autplay.ui.desktop

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
) {
    // Read these holders inside content lambdas, so strong skipping cannot retain an equal old owner.
    val currentLifecycleState by rememberDesktopBrowseReference(lifecycleState)
    val currentLifecycleCallbacks by rememberDesktopBrowseReference(lifecycleCallbacks)
    val currentOpenAccount by rememberUpdatedState(onOpenAccount)
    val browseLifetime = remember { DesktopBrowseLifetime() }
    val currentLibrary by rememberDesktopBrowseReference(library)
    DisposableEffect(browseLifetime) { onDispose { browseLifetime.retire() } }
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
    val tasteInput = DesktopTasteInput(tasteState, library, section, navigationAtInput, revisionAtInput, reopenRequired)
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
    val currentTrack = if (trackView.input == trackInput) trackView.currentTrack else null
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
                    DesktopSavedImportsControls(DesktopSavedImportsInput(savedImportsState, importMapping,
                        savedImportsCallbacks, library, section, navigationAtInput, revisionAtInput,
                        reopenRequired || lifecycleState.closing, importActive || importReconciliationRequired), importJobProgress,
                        importReviewState.phase, importActions, language,
                        isNavigationCurrent = { isNavigationCurrent() && trackSelectionRevision == revisionAtInput })
                    Row(Modifier.weight(1f).fillMaxWidth()) {
                        if (!compact) Sidebar(section, language, navigate)
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
                                    DesktopLibraryKind.entries.forEach { kind ->
                                        OutlinedButton(enabled = !currentLifecycleState.closing, onClick = {
                                            if (isNavigationCurrent() && !currentLifecycleState.closing && libraryKind != kind) {
                                                selectedTrackId = null; trackSelectionRevision += 1; navigationRevision += 1
                                                onLibraryKindChange(kind)
                                            }
                                        }) {
                                            val catalogKind = when (kind) {
                                                DesktopLibraryKind.Track -> DesktopCatalogKind.Track
                                                DesktopLibraryKind.Playlist -> DesktopCatalogKind.Playlist
                                                DesktopLibraryKind.Artist -> DesktopCatalogKind.Artist
                                                DesktopLibraryKind.Album -> DesktopCatalogKind.Album
                                            }
                                            Text((if (kind == libraryKind) "\u2022 " else "") + desktopCatalogKindText(catalogKind, language))
                                        }
                                    }
                                }
                            }
                            if (section == DesktopSection.Player) {
                                DesktopPlayerContent(library, transportInput, language, transportCallbacks, playbackCallbacks,
                                    metadataState, sleepTimer, sleepTimerCallbacks, tasteInput, tasteCallbacks,
                                    isNavigationCurrent, isTasteNavigationCurrent,
                                    onTrackDetails = {
                                        if (isNavigationCurrent()) {
                                            trackSelectionRevision += 1
                                            selectedTrackId = it
                                        }
                                    }, onQueue = { if (isNavigationCurrent()) navigate(DesktopSection.Queue) },
                                    modifier = Modifier.weight(1f).fillMaxWidth(),
                                    smoothTrackTransitions = preferencesState?.value?.smoothTrackTransitions == true)
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
                                    accountContent = homeAccountContent)
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
                                val sourceSection = if (section == DesktopSection.Library && libraryKind == DesktopLibraryKind.Playlist)
                                    DesktopSection.Playlists else section
                                if (section == DesktopSection.Library) Text(if (language == DesktopLanguage.Russian)
                                    "Источник: отдельная локальная библиотека; текстовый поиск FTS."
                                    else "Source: standalone local library; FTS text search.")
                                key(sourceSection, libraryKind) {
                                DesktopLibraryContent(sourceSection, library, language, importActive, importResult,
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
                                    queueEditor = playerState?.queueEditor ?: DesktopQueueEditorState(), textSearch = textSearch)
                                }
                            } else when (section) {
                                DesktopSection.Home -> Unit
                                DesktopSection.Player -> Unit
                                DesktopSection.Library -> {
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
                                    MutedText(DesktopText.SettingsSubtitle.inLanguage(language))
                                    if (sourceAccessState != null && sourceAccessActions != null) {
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
                                    DesktopLifecycleControls(currentLifecycleState, currentLifecycleCallbacks, language,
                                        navigationAtInput, isNavigationCurrent)
                                    DesktopSettingsControls(language, themeMode, accentPalette, preferencesState,
                                        preferencesTransfer, preferencesCallbacks, onLanguageChange, onThemeChange,
                                        modifier = Modifier.weight(1f).fillMaxWidth())
                                }
                            }
                        }
                    }
                    if (section != DesktopSection.Player) {
                        HorizontalDivider()
                        PlaybackBar(transportInput, language, playbackCallbacks, transportCallbacks,
                            metadataState = metadataState,
                            smoothTrackTransitions = preferencesState?.value?.smoothTrackTransitions == true,
                            sleepTimer = sleepTimer, sleepTimerCallbacks = sleepTimerCallbacks,
                            tasteInput = tasteInput,
                            tasteCallbacks = tasteCallbacks,
                            isNavigationCurrent = isNavigationCurrent,
                            isTasteNavigationCurrent = isTasteNavigationCurrent,
                            onOpenPlayer = { if (isNavigationCurrent()) navigate(DesktopSection.Player) },
                            onTrackDetails = currentTrack?.let { track ->
                                {
                                    if (trackView.input == currentTrackInput && trackInput.isSelectionCurrent()) {
                                        trackSelectionRevision += 1
                                        selectedTrackId = track.id
                                    }
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
private fun Sidebar(section: DesktopSection, language: DesktopLanguage, onSectionChange: (DesktopSection) -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(
            Modifier.width(208.dp).fillMaxHeight().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Surface(color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(14.dp)) {
                Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                    Text("AP", style = MaterialTheme.typography.titleLarge)
                }
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
                            ).padding(horizontal = 12.dp, vertical = 16.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Box(
                                Modifier.size(6.dp).background(
                                    if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
                                    RoundedCornerShape(3.dp),
                                ),
                            )
                            Text(destination.label(language), style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            MutedText(DesktopText.LocalOnly.inLanguage(language))
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
    sleepTimer: DesktopSleepTimerState,
    sleepTimerCallbacks: DesktopSleepTimerCallbacks,
    tasteInput: DesktopTasteInput,
    tasteCallbacks: DesktopTasteCallbacks,
    isNavigationCurrent: () -> Boolean,
    isTasteNavigationCurrent: () -> Boolean,
    onOpenPlayer: () -> Unit,
    onTrackDetails: (() -> Unit)?,
    smoothTrackTransitions: Boolean = false,
) {
    val state = input.playback
    val controls = input.controls(allowLegacy = true)
    Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    DesktopTrackVisuals(input, metadataState, language, compact = true,
                        navigationCurrent = isNavigationCurrent(), smoothTrackTransitions = smoothTrackTransitions)
                    val subtitle = when {
                        !input.commandsEnabled -> DesktopText.ControlsUnavailable.inLanguage(language)
                        !input.adapterAvailable || state.connection == PlaybackConnectionState.Disconnected -> DesktopText.PlaybackUnavailable.inLanguage(language)
                        !controls.playPause -> DesktopText.ControlsUnavailable.inLanguage(language)
                        else -> null
                    }
                    subtitle?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    if (onTrackDetails != null) {
                        OutlinedButton(onClick = onTrackDetails) { Text(DesktopText.TrackDetails.inLanguage(language)) }
                    }
                    OutlinedButton(onClick = onOpenPlayer) { Text(DesktopText.OpenPlayer.inLanguage(language)) }
                }
                Row(Modifier.weight(1.5f).horizontalScroll(rememberScrollState()),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    DesktopTransportButtons(input, language, transportCallbacks, callbacks, true, isNavigationCurrent, scrollable = false)
                    DesktopSleepTimerControls(sleepTimer, !input.commandsEnabled, language, sleepTimerCallbacks)
                }
            }
            DesktopTransportSeek(input, language, transportCallbacks, callbacks, true, isNavigationCurrent)
            DesktopTasteControls(tasteInput, isTasteNavigationCurrent, language, tasteCallbacks)
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
