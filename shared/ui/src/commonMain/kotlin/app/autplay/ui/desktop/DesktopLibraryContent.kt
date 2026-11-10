package app.autplay.ui.desktop

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopCopyInspectionLimits
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTextSearchPort
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** Commands carry stable IDs, never filesystem capabilities. Selection and search are view state. */
public data class DesktopLibraryCallbacks(
    val onImport: () -> Unit = {},
    val onPlayTracks: (List<String>, String) -> Unit = { _, _ -> },
    val onEnqueue: (String) -> Unit = {},
    val onSelectEntry: (String) -> Unit = {},
    val onRemoveEntry: (String) -> Unit = {},
    val onCreatePlaylist: (String) -> Unit = {},
    val onAddToPlaylist: (String, String) -> Unit = { _, _ -> },
    val onRemovePlaylistEntry: (String, String) -> Unit = { _, _ -> },
    val onShuffle: (Boolean) -> Unit = {},
    val onRepeat: (String) -> Unit = {},
    val onCancelImport: () -> Unit = {},
    val onPlayNext: (String) -> Unit = {},
    val onMoveUpcoming: (String, Int) -> Unit = { _, _ -> },
    val onClearUpcoming: () -> Unit = {},
    val onRenamePlaylist: (String, String) -> Unit = { _, _ -> },
    val onMovePlaylistEntry: (String, String, Int) -> Unit = { _, _, _ -> },
    val onUpdateTrackMetadata: (String, String, String) -> Unit = { _, _, _ -> },
    val onDeletePlaylist: (String) -> Unit = {},
    val onEnqueuePlaylist: (String) -> Unit = {},
    val onPlayPlaylistNext: (String) -> Unit = {},
    val onSetTrackPreference: (String, String) -> Unit = { _, _ -> },
    val onRemoveTrackFromCollection: (String) -> Unit = {},
    val onRestoreTrackToCollection: (String) -> Unit = {},
    val onUpdatePlaylistDescription: (String, String?, String?) -> Unit = { _, _, _ -> },
    val onContinueQueue: (String, String, String?) -> Unit = { _, _, _ -> },
    val onEnqueueTracks: (List<String>) -> Unit = {},
    val onPlayTracksNext: (List<String>) -> Unit = {},
    val onAddTracksToPlaylist: (String, List<String>) -> Unit = { _, _ -> },
    val onEditUpcoming: (DesktopQueueEditTarget, DesktopQueueGroupAction, List<String>) -> Unit = { _, _, _ -> },
)

@Composable
@NonSkippableComposable
internal fun DesktopLibraryContent(
    section: DesktopSection,
    library: DesktopLibrarySnapshot,
    language: DesktopLanguage,
    importing: Boolean,
    importResult: DesktopImportResult?,
    errorCode: String?,
    callbacks: DesktopLibraryCallbacks,
    queueControlsEnabled: Boolean,
    queueInsertionEnabled: Boolean,
    reopenRequired: Boolean,
    onTrackDetails: (String) -> Unit,
    sortState: MutableState<DesktopLibrarySort>,
    directionState: MutableState<DesktopSortDirection>,
    collectionFilterState: MutableState<DesktopTrackCollectionFilter>,
    isNavigationCurrent: () -> Boolean,
    modifier: Modifier = Modifier,
    copyInspectionState: DesktopCopyInspectionState = DesktopCopyInspectionState(),
    copyInspectionCallbacks: DesktopCopyInspectionCallbacks = DesktopCopyInspectionCallbacks(),
    importActionLabel: String? = null,
    importCancelEnabled: Boolean = importing,
    navigationRevision: Long = 0L,
    queueEditorOwner: DesktopLibraryCallbacks = callbacks,
    queueEditor: DesktopQueueEditorState = DesktopQueueEditorState(),
    textSearch: DesktopTextSearchPort? = null,
) {
    val browseLifetime = remember(section, DesktopSearchOwnerKey(textSearch)) { DesktopBrowseLifetime() }
    val currentCallbacks by rememberDesktopBrowseReference(callbacks)
    val currentCopyCallbacks by rememberDesktopBrowseReference(copyInspectionCallbacks)
    val currentTextSearch by rememberDesktopBrowseReference(textSearch)
    DisposableEffect(browseLifetime) { onDispose { browseLifetime.retire() } }
    val isCopyOwnerCurrent = desktopBrowseIdentityGuard(browseLifetime, copyInspectionCallbacks,
        { currentCopyCallbacks }, isNavigationCurrent)
    val isCallbackOwnerCurrent = desktopBrowseIdentityGuard(browseLifetime, callbacks,
        { currentCallbacks }, isCopyOwnerCurrent)
    val isOwnerCurrent = desktopSearchOwnerGuard(textSearch, { currentTextSearch }, isCallbackOwnerCurrent)
    var search by remember { mutableStateOf("") }
    var playlistSearch by remember { mutableStateOf("") }
    var sort by sortState
    var direction by directionState
    var preferenceFilter by remember { mutableStateOf(DesktopTrackPreferenceFilter.ALL) }
    var collectionFilter by collectionFilterState
    var copyFilter by remember { mutableStateOf(DesktopCopyAvailabilityFilter.ALL) }
    val multiScope = DesktopMultiSelectionScope(section, navigationRevision)
    var trackSelection by remember(multiScope) { mutableStateOf(DesktopMultiSelection()) }
    var multiNotice by remember(multiScope) { mutableStateOf<DesktopMultiSelectionNotice?>(null) }
    val selectionFilter = DesktopMultiSelectionFilter(search, collectionFilter, preferenceFilter, copyFilter)
    var lastSelectionFilter by remember(multiScope) { mutableStateOf(selectionFilter) }
    val selectionAtInput = trackSelection
    var viewRevision by remember { mutableStateOf(0L) }
    var trackPage by remember { mutableStateOf(0) }
    var choicePage by remember { mutableStateOf(0) }
    var entryPage by remember { mutableStateOf(0) }
    var browseRevision by remember { mutableStateOf(0L) }
    var playlistName by remember { mutableStateOf("") }
    var selectedPlaylistId by remember { mutableStateOf<String?>(null) }
    var selectionRevision by remember { mutableStateOf(0L) }
    var renameDraft by remember { mutableStateOf<DesktopPlaylistRenameDraft?>(null) }
    var deleteTarget by remember { mutableStateOf<DesktopPlaylistDeleteTarget?>(null) }
    var descriptionDraft by remember { mutableStateOf<DesktopPlaylistDescriptionDraft?>(null) }
    val browseAtInput = browseRevision
    val playlistInput = DesktopPlaylistInput(library, selectedPlaylistId, selectionRevision, playlistSearch,
        choicePage = choicePage, entryPage = entryPage, browseRevision = browseAtInput)
    val input = DesktopLibraryInput(library, search, sort, direction, playlistInput, preferenceFilter, viewRevision,
        collectionFilter, copyFilter, copyInspectionState.results, trackPage, browseAtInput, textSearch, section, navigationRevision)
    val currentInput by rememberDesktopBrowseReference(input)
    val currentReopenRequired by rememberUpdatedState(reopenRequired)
    val currentImporting by rememberUpdatedState(importing)
    val currentImportCancelEnabled by rememberUpdatedState(importCancelEnabled)
    val currentCopyState by rememberUpdatedState(copyInspectionState)
    var inspectionSubmitted by remember(input, copyInspectionState.requestId, copyInspectionState.busy) { mutableStateOf(false) }
    val view by produceState(DesktopLibraryView(), input, selectionAtInput) {
        value = withContext(Dispatchers.Default) {
            val context = currentCoroutineContext()
            // Hidden queries from another route cannot open SQLite during blank browsing.
            val trackQuery = desktopActiveSearchQuery(input.section, playlists = false, query = input.query)
            val playlistQuery = desktopActiveSearchQuery(input.section, playlists = true, query = input.playlistInput.query)
            val trackSearch = loadDesktopSearchMatches(input.textSearch, input.library, trackQuery) { context.ensureActive() }
            val playlistSearchResult = if (playlistQuery == trackQuery) trackSearch else
                loadDesktopSearchMatches(input.textSearch, input.library, playlistQuery) { context.ensureActive() }
            context.ensureActive()
            val byId = mutableMapOf<String, DesktopTrack>()
            input.library.tracks.forEachIndexed { index, track ->
                if (index % 256 == 0) context.ensureActive()
                byId[track.id] = track
            }
            // Text matching is exclusively supplied by the port. Existing sort/filter policy sees
            // only its complete candidate set, with no second substring pass or partial cap.
            val searchedTracks = desktopSearchTracks(input.library.tracks, trackSearch) { context.ensureActive() }
            val candidates = prepareDesktopLibraryMatches(searchedTracks, "", input.preferenceFilter,
                input.sort, input.direction, input.collectionFilter, input.copyFilter, input.copyResults) { context.ensureActive() }
            val page = desktopBrowsePage(candidates.size, input.page)
            val visible = desktopBrowseSlice(candidates, page)
            val inspectionIds = prepareDesktopCopyInspectionIds(visible) { context.ensureActive() }
            val selection = prepareDesktopLibraryCollectionSelection(visible) { context.ensureActive() }
            val allSelection = prepareDesktopLibraryResultSelection(candidates) { context.ensureActive() }
            val multiSelection = prepareDesktopMultiSelection(selectionAtInput, byId, visible) { context.ensureActive() }
            context.ensureActive()
            val queueView = prepareDesktopQueueView(input.library.queue)
            val matchingPlaylists = desktopSearchPlaylistIds(input.library.playlists, playlistSearchResult) { context.ensureActive() }
            val playlistView = prepareDesktopPagedPlaylistView(input.playlistInput, matchingPlaylists) { context.ensureActive() }
            val playlistSelection = prepareDesktopPlaylistCollectionSelection(playlistView.playlist, byId) { context.ensureActive() }
            val playlistPageSelection = prepareDesktopPlaylistPageCollectionSelection(playlistView.playIds, byId) { context.ensureActive() }
            val playlistSuffixSelections = playlistView.entryPositions.mapValues { (_, position) ->
                prepareDesktopPlaylistPageCollectionSelection(playlistView.playIds, byId, position) { context.ensureActive() }
            }
            context.ensureActive()
            DesktopLibraryView(visible, candidates.size, byId, input, queueView, selection.playIds,
                selection.positions, playlistView, playlistSelection, inspectionIds, page,
                allSelection.playIds, allSelection.overflow, playlistPageSelection, playlistSuffixSelections, multiSelection,
                trackSearch.errorCode, playlistSearchResult.errorCode)
        }
    }
    val prepared = view
    val currentPrepared by rememberDesktopBrowseReference(prepared)
    val fresh = prepared.input == input
    val selectedPlaylist = if (fresh) prepared.playlist?.playlist else null
    val tracksById = if (fresh) prepared.tracksById else emptyMap()
    val queue = library.queue
    val enqueueEnabled = fresh && queueInsertionEnabled && !reopenRequired &&
        (queue == null || queueControlsEnabled) &&
        (queue?.entries?.size ?: 0) < DesktopLocalLimits.MAX_QUEUE_ENTRIES
    val playNextEnabled = enqueueEnabled && (queue == null || queue.entries.isEmpty() || prepared.queue.hasCurrent)
    val upcomingEnabled = fresh && queueControlsEnabled && !reopenRequired && prepared.queue.hasCurrent
    val currentEnqueueEnabled by rememberUpdatedState(enqueueEnabled)
    val currentPlayNextEnabled by rememberUpdatedState(playNextEnabled)
    val currentUpcomingEnabled by rememberUpdatedState(upcomingEnabled)
    val selectionPlayEnabled = fresh && queueInsertionEnabled && !reopenRequired
    val currentSelectionPlayEnabled by rememberUpdatedState(selectionPlayEnabled)
    val bulkAdmission = desktopPlaylistBulkAdmission(selectedPlaylist, queue?.entries?.size ?: 0,
        queueInsertionEnabled && prepared.playlistSelection.allActive, queue == null || queueControlsEnabled,
        prepared.queue.hasCurrent, fresh, reopenRequired)
    val playlistPlayEnabled = selectionPlayEnabled && prepared.playlistSelection.playIds.isNotEmpty()
    val currentPlaylistPlayEnabled by rememberUpdatedState(playlistPlayEnabled)
    val currentBulkEnqueueEnabled by rememberUpdatedState(bulkAdmission.enqueue)
    val currentBulkPlayNextEnabled by rememberUpdatedState(bulkAdmission.playNext)
    val multiPrepared = prepared.multiSelection
    val multiFresh = fresh && prepared.searchError == null && multiPrepared?.selection === trackSelection && lastSelectionFilter == selectionFilter
    val multiAdmission = desktopMultiSelectionAdmission(multiPrepared, queue?.entries?.size ?: 0,
        queueInsertionEnabled, queue == null || queueControlsEnabled, prepared.queue.hasCurrent,
        selectedPlaylist, multiFresh, reopenRequired)
    val currentMultiAdmission by rememberDesktopBrowseReference(multiAdmission)
    val multiTarget = DesktopMultiSelectionActionTarget(selectionAtInput, input, prepared, selectedPlaylist)
    val currentMultiTarget by rememberDesktopBrowseReference(multiTarget)
    val isInputCurrent: () -> Boolean = {
        isOwnerCurrent() && input == currentInput && input.isCurrent(currentInput.library, search, sort, direction,
            preferenceFilter, viewRevision, selectedPlaylistId, selectionRevision, collectionFilter, copyFilter,
            currentCopyState.results, trackPage, browseRevision) && playlistInput.query == playlistSearch &&
            playlistInput.choicePage == choicePage && playlistInput.entryPage == entryPage && browseAtInput == browseRevision
    }
    val isViewCurrent: () -> Boolean = {
        currentPrepared === prepared && prepared.input == currentInput && isInputCurrent()
    }
    val actOnView: (() -> Unit) -> Unit = { action ->
        if (isViewCurrent() && !currentReopenRequired) action()
    }
    val clearMultiSelectionForFilter: () -> Unit = {
        if (trackSelection.ids.isNotEmpty()) multiNotice = DesktopMultiSelectionNotice.FILTER_CLEARED
        trackSelection = trackSelection.clear()
        lastSelectionFilter = DesktopMultiSelectionFilter(search, collectionFilter, preferenceFilter, copyFilter)
    }
    val multiCommand: (DesktopMultiSelectionCommand) -> (() -> Unit) = { command ->
        desktopMultiSelectionCommand(multiTarget, command, claim = {
            multiTarget.claim(trackSelection, currentInput, currentPrepared, currentPrepared.playlist?.playlist, currentMultiTarget,
                currentMultiAdmission.allows(command) && !currentReopenRequired, isViewCurrent())
        }, retire = {
            trackSelection = trackSelection.retireIntent()
            multiNotice = DesktopMultiSelectionNotice.SUBMITTED
        }, callbacks = callbacks, queueType = if (input.query.isBlank()) "LIBRARY" else "SEARCH")
    }
    val selectPlaylist: (String) -> Unit = { id ->
        if (isViewCurrent()) {
            selectedPlaylistId = id
            selectionRevision += 1
            entryPage = 0
            browseRevision += 1
            renameDraft = null
            deleteTarget = null
        }
    }
    SideEffect {
        if (lastSelectionFilter != selectionFilter && isOwnerCurrent()) {
            clearMultiSelectionForFilter()
        }
        if (isViewCurrent()) {
            if (renameDraft?.matches(selectedPlaylist) == false) renameDraft = null
            if (deleteTarget?.matches(selectedPlaylist) == false) deleteTarget = null
            descriptionDraft?.let { draft ->
                if (draft.canAct(descriptionDraft, playlistInput, currentReopenRequired, isNavigationCurrent()) &&
                    draft.confirmed(playlistInput, selectedPlaylist)) descriptionDraft = null
            }
            // Pin the effective fallback before name-sorted storage snapshots can move it.
            if (selectedPlaylistId != selectedPlaylist?.id) {
                selectedPlaylistId = selectedPlaylist?.id
                selectionRevision += 1
                entryPage = 0
                browseRevision += 1
            } else {
                val nextTrackPage = prepared.page.index
                val nextChoicePage = prepared.playlist?.choicePage?.index ?: 0
                val nextEntryPage = prepared.playlist?.entryPage?.index ?: 0
                if (trackPage != nextTrackPage || choicePage != nextChoicePage || entryPage != nextEntryPage) {
                    trackPage = nextTrackPage
                    choicePage = nextChoicePage
                    entryPage = nextEntryPage
                    browseRevision += 1
                }
            }
        }
    }
    // Keep Cancel available during refresh; Confirm only applies to a fresh matching baseline.
    deleteTarget?.let { target ->
        val confirmEnabled = fresh && target.matches(selectedPlaylist) && !reopenRequired
        val dialogTitle = DesktopText.DeletePlaylistConfirmation.inLanguage(language)
        AlertDialog(
            onDismissRequest = { if (isInputCurrent() && deleteTarget === target) deleteTarget = null },
            modifier = Modifier.semantics { paneTitle = dialogTitle },
            title = { Text(dialogTitle) },
            text = {
                Column(Modifier.heightIn(max = 280.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(target.baselineName)
                    Text(DesktopText.DeletePlaylistCount.inLanguage(language).replace("{count}", target.baselineCount.toString()))
                    Text(DesktopText.DeletePlaylistRetained.inLanguage(language))
                    if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language))
                    if (reopenRequired) Text(DesktopText.LibraryReopenRequired.inLanguage(language), color = MaterialTheme.colorScheme.error)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (deleteTarget === target && target.matches(selectedPlaylist)) actOnView {
                        callbacks.onDeletePlaylist(target.playlistId)
                        deleteTarget = null
                    }
                }, enabled = confirmEnabled,
                    modifier = Modifier.playlistAction(DesktopText.ConfirmDeletePlaylist, target.baselineName, language)) {
                    Text(DesktopText.ConfirmDeletePlaylist.inLanguage(language))
                }
            },
            dismissButton = {
                TextButton(onClick = { if (isInputCurrent() && deleteTarget === target) deleteTarget = null }) { Text(DesktopText.CancelDeletePlaylist.inLanguage(language)) }
            },
        )
    }
    // Keep raw baselines and drafts across refresh, errors and target disappearance.
    if (section == DesktopSection.Playlists) descriptionDraft?.let { draft ->
        val target = selectedPlaylist?.takeIf { it.id == draft.playlistId }
        val canAct = fresh && draft.canAct(descriptionDraft, playlistInput, reopenRequired, isNavigationCurrent())
        val pending = draft.submittedInput != null
        val canReview = fresh && target != null && !reopenRequired &&
            (pending || !draft.matches(target) || !canAct)
        val title = DesktopText.EditPlaylistDescription.inLanguage(language)
        val close: () -> Unit = {
            if (isInputCurrent() && draft.canClose(descriptionDraft, isNavigationCurrent())) descriptionDraft = null
        }
        AlertDialog(
            onDismissRequest = close,
            modifier = Modifier.semantics { paneTitle = title },
            title = { Text(title) },
            text = {
                Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (target != null) {
                        Text(target.name)
                        Text(DesktopText.PlaylistDescriptionSaved.inLanguage(language), style = MaterialTheme.typography.labelMedium)
                        Text(target.description ?: DesktopText.PlaylistDescriptionEmpty.inLanguage(language))
                    }
                    OutlinedTextField(draft.text, { value ->
                        if (descriptionDraft === draft && draft.submittedInput == null && isViewCurrent() &&
                            draft.canAct(descriptionDraft, currentInput.playlistInput, currentReopenRequired, isNavigationCurrent()) &&
                            target != null) {
                            // Preserve invalid/legacy text for explicit correction; never truncate or trim it.
                            descriptionDraft = draft.withText(value)
                        }
                    }, enabled = canAct && !pending && target != null,
                        label = { Text(DesktopText.PlaylistDescription.inLanguage(language)) },
                        supportingText = { Text(DesktopText.PlaylistDescriptionHint.inLanguage(language)) },
                        isError = !validDesktopPlaylistDescription(draft.description),
                        modifier = Modifier.fillMaxWidth())
                    val status = when {
                        reopenRequired -> DesktopText.LibraryReopenRequired
                        !fresh -> DesktopText.UpdatingLibrary
                        target == null -> DesktopText.PlaylistMissing
                        !draft.matches(target) || !canAct -> DesktopText.PlaylistDescriptionConflict
                        errorCode != null -> desktopPlaylistDescriptionErrorText(errorCode, false)
                        pending -> DesktopText.PlaylistDescriptionUnconfirmed
                        else -> null
                    }
                    status?.let {
                        Text(it.inLanguage(language), color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    }
                    if (canReview) {
                        OutlinedButton(onClick = {
                            if (descriptionDraft === draft && isViewCurrent() && !currentReopenRequired) {
                                target?.let { descriptionDraft = draft.reviewed(currentInput.playlistInput, it, isNavigationCurrent) }
                            }
                        }, enabled = canReview) { Text(DesktopText.ReviewPlaylistDescription.inLanguage(language)) }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (isViewCurrent() && draft.canAct(descriptionDraft, currentInput.playlistInput,
                            currentReopenRequired, isNavigationCurrent()) && draft.canSubmit(target)) {
                        // Retire this callback before dispatch; Unit callbacks provide no command acknowledgment.
                        descriptionDraft = draft.submitted(currentInput.playlistInput)
                        callbacks.onUpdatePlaylistDescription(draft.playlistId, draft.baselineDescription, draft.description)
                    }
                }, enabled = canAct && draft.canSubmit(target)) { Text(DesktopText.ApplyPlaylistDescription.inLanguage(language)) }
            },
            dismissButton = {
                TextButton(onClick = close) { Text(DesktopText.CancelPlaylistDescription.inLanguage(language)) }
            },
        )
    }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        errorCode?.let {
            if (!reopenRequired) Text(desktopErrorMessage(it, language),
                color = if (it == "IMPORT_CANCELLED") MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        if (section == DesktopSection.Library) {
            Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(onClick = { if (isInputCurrent() && !currentImporting && !currentReopenRequired) callbacks.onImport() }, enabled = !importing && !reopenRequired) {
                        Text(importActionLabel ?: (if (importing) DesktopText.Importing else DesktopText.Import).inLanguage(language))
                    }
                    if (importing) {
                        OutlinedButton(onClick = { if (isInputCurrent() && currentImporting && currentImportCancelEnabled) callbacks.onCancelImport() },
                            enabled = importing && importCancelEnabled) {
                            Text(DesktopText.CancelImport.inLanguage(language))
                        }
                    }
                    Text(DesktopText.LibrarySubtitle.inLanguage(language), modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(DesktopText.DropFiles.inLanguage(language), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                importResult?.let {
                    if (!importing && (!reopenRequired || desktopImportWasCancelled(it, errorCode))) {
                        Text(desktopImportResultSummary(it, errorCode, language),
                            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                        it.errorCode?.let { code ->
                            if (code != errorCode) Text(desktopErrorMessage(code, language),
                                color = if (code == "IMPORT_CANCELLED") MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                        }
                    }
                }
                if (library.tracks.isEmpty()) Text(DesktopText.EmptyLibraryBody.inLanguage(language))
                OutlinedTextField(value = search, onValueChange = {
                    val next = desktopSearchQuery(it)
                    // Accept consecutive input events before recomposition; retire this field closure afterwards.
                    if (isOwnerCurrent() && input == currentInput && browseAtInput == browseRevision && search != next) {
                        search = next; trackPage = 0; viewRevision += 1
                        clearMultiSelectionForFilter()
                    }
                }, singleLine = true,
                    label = { Text(DesktopText.Search.inLanguage(language)) }, modifier = Modifier.fillMaxWidth())
                Text(DesktopText.LocalSearchHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                DesktopSearchFailure(prepared.searchError.takeIf { fresh }, language, enabled = !reopenRequired,
                    onRetry = { if (isViewCurrent() && !currentReopenRequired) viewRevision += 1 },
                    onReset = {
                        if (isInputCurrent()) {
                            search = ""; trackPage = 0; viewRevision += 1
                            clearMultiSelectionForFilter()
                        }
                    })
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(DesktopText.CollectionFilter.inLanguage(language), style = MaterialTheme.typography.labelMedium)
                    DesktopTrackCollectionFilter.entries.forEach { filter ->
                        val label = filter.label(language)
                        FilterChip(selected = collectionFilter == filter, onClick = {
                            if (isInputCurrent() && collectionFilter != filter) {
                                collectionFilter = filter; trackPage = 0; viewRevision += 1
                                clearMultiSelectionForFilter()
                            }
                        }, modifier = Modifier.semantics {
                            contentDescription = "${DesktopText.CollectionFilter.inLanguage(language)}: $label"
                        }, label = { Text(label) })
                    }
                }
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(DesktopText.TrackPreferenceFilter.inLanguage(language), style = MaterialTheme.typography.labelMedium)
                    DesktopTrackPreferenceFilter.entries.forEach { filter ->
                        val label = filter.label(language)
                        FilterChip(selected = preferenceFilter == filter, onClick = {
                            if (isInputCurrent() && preferenceFilter != filter) {
                                preferenceFilter = filter; trackPage = 0; viewRevision += 1
                                clearMultiSelectionForFilter()
                            }
                        }, modifier = Modifier.semantics {
                            contentDescription = "${DesktopText.TrackPreferenceFilter.inLanguage(language)}: $label"
                        }, label = { Text(label) })
                    }
                }
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(DesktopText.SortBy.inLanguage(language), style = MaterialTheme.typography.labelMedium)
                    DesktopLibrarySort.entries.forEach { criterion ->
                        val label = criterion.label(language)
                        FilterChip(selected = sort == criterion, onClick = {
                            if (isInputCurrent() && sort != criterion) { sort = criterion; trackPage = 0; viewRevision += 1 }
                        },
                            modifier = Modifier.semantics { contentDescription = "${DesktopText.SortBy.inLanguage(language)}: $label" },
                            label = { Text(label) })
                    }
                    DesktopSortDirection.entries.forEach { order ->
                        val label = order.label(language)
                        FilterChip(selected = direction == order, onClick = {
                            if (isInputCurrent() && direction != order) { direction = order; trackPage = 0; viewRevision += 1 }
                        },
                            modifier = Modifier.semantics { contentDescription = "${sort.label(language)}: $label" },
                            label = { Text(label) })
                    }
                }
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(DesktopCopyText.Observation.label(language), style = MaterialTheme.typography.labelMedium)
                    DesktopCopyAvailabilityFilter.entries.forEach { filter ->
                        val label = filter.label(language)
                        FilterChip(selected = copyFilter == filter, onClick = {
                            if (isInputCurrent() && copyFilter != filter) {
                                copyFilter = filter; trackPage = 0; viewRevision += 1
                                clearMultiSelectionForFilter()
                            }
                        }, modifier = Modifier.semantics {
                            contentDescription = "${DesktopCopyText.Observation.label(language)}: $label"
                        }, label = { Text(label) })
                    }
                }
                Text(DesktopCopyText.Hint.label(language), style = MaterialTheme.typography.bodySmall)
                OutlinedButton(onClick = {
                    if (!inspectionSubmitted && isViewCurrent() && desktopCopyCanInspect(currentCopyState, true,
                            currentReopenRequired, prepared.inspectionIds.isNotEmpty())) {
                        inspectionSubmitted = true
                        copyInspectionCallbacks.onInspect(prepared.inspectionIds)
                    }
                }, enabled = !inspectionSubmitted && desktopCopyCanInspect(copyInspectionState, fresh,
                    reopenRequired, prepared.inspectionIds.isNotEmpty())) {
                    Text(DesktopCopyText.InspectShown.label(language)
                        .replace("{limit}", DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS.toString()))
                }
                DesktopCopyInspectionProgress(copyInspectionState, copyInspectionCallbacks, language, isViewCurrent)
                val visible = if (fresh) prepared.matches else emptyList()
                val queueType = if (input.query.isBlank()) "LIBRARY" else "SEARCH"
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(onClick = {
                        if (currentSelectionPlayEnabled && !prepared.allPlayOverflow && prepared.allPlayIds.isNotEmpty()) {
                            actOnView { callbacks.onPlayTracks(prepared.allPlayIds, queueType) }
                        }
                    }, enabled = prepared.allPlayIds.isNotEmpty() && !prepared.allPlayOverflow && selectionPlayEnabled) {
                        Text(DesktopText.PlayAll.inLanguage(language))
                    }
                    OutlinedButton(onClick = {
                        if (currentSelectionPlayEnabled && prepared.playIds.isNotEmpty()) actOnView { callbacks.onPlayTracks(prepared.playIds, queueType) }
                    }, enabled = prepared.playIds.isNotEmpty() && selectionPlayEnabled) { Text(DesktopText.PlayPage.inLanguage(language)) }
                    selectedPlaylist?.let { Text("${DesktopText.AddToPlaylist.inLanguage(language)}: ${it.name}", modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis) }
                }
                PlaylistSelector(if (fresh) prepared.playlist?.choices.orEmpty() else emptyList(), selectedPlaylist?.id, language, selectPlaylist)
                prepared.playlist?.let { playlistView ->
                    DesktopBrowsePager(playlistView.choicePage, DesktopText.SelectPlaylist.inLanguage(language), language, fresh) { next ->
                        if (isViewCurrent() && next != choicePage) { choicePage = next; browseRevision += 1 }
                    }
                }
                DesktopMultiSelectionControls(trackSelection, multiPrepared.takeIf { multiFresh }, multiAdmission,
                    language, fresh, multiNotice,
                    selectPage = {
                        if (isViewCurrent() && lastSelectionFilter == selectionFilter) {
                            val activeIds = multiPrepared?.activePageIds.orEmpty()
                            if (trackSelection.pageFits(activeIds)) {
                                trackSelection = trackSelection.selectPage(activeIds)
                                multiNotice = null
                            } else multiNotice = DesktopMultiSelectionNotice.LIMIT_REJECTED
                        }
                    }, deselectPage = {
                        if (isViewCurrent()) {
                            trackSelection = trackSelection.deselectPage(multiPrepared?.shownIds.orEmpty())
                            multiNotice = null
                        }
                    }, clear = {
                        if (isInputCurrent()) { trackSelection = trackSelection.clear(); multiNotice = null }
                    }, deselectInvalid = { id ->
                        if (isViewCurrent()) {
                            trackSelection = trackSelection.setSelected(id, false, false)
                            multiNotice = null
                        }
                    }, play = multiCommand(DesktopMultiSelectionCommand.PLAY),
                    enqueue = multiCommand(DesktopMultiSelectionCommand.ENQUEUE),
                    playNext = multiCommand(DesktopMultiSelectionCommand.PLAY_NEXT),
                    append = multiCommand(DesktopMultiSelectionCommand.APPEND))
                if (fresh && prepared.allPlayOverflow) Text(DesktopText.WholeResultQueueLimit.inLanguage(language)
                    .replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString()))
                Text(DesktopText.PagePlaybackBoundary.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                DesktopBrowsePager(prepared.page, DesktopText.Library.inLanguage(language), language, fresh) { next ->
                    if (isViewCurrent() && next != trackPage) { trackPage = next; browseRevision += 1 }
                }
                if (fresh && prepared.searchError == null && visible.isEmpty() && library.tracks.isNotEmpty()) Text(DesktopText.NoResults.inLanguage(language))
                LazyColumn(Modifier.fillMaxWidth().heightIn(min = 160.dp, max = 480.dp)) {
                    items(visible, key = { it.id }) { track ->
                        TrackRow(track, language, enabled = selectionPlayEnabled && desktopTrackIsActive(track), onPlay = {
                            if (currentSelectionPlayEnabled && desktopTrackIsActive(track)) actOnView {
                                prepared.trackPositions[track.id]?.let { position ->
                                    callbacks.onPlayTracks(prepared.playIds.subList(position, prepared.playIds.size), queueType)
                                }
                            }
                        }, copyInspectionState = copyInspectionState) {
                            Checkbox(checked = multiPrepared?.selectedIds?.contains(track.id) == true,
                                onCheckedChange = desktopMultiSelectionCheckbox(track.id, desktopTrackIsActive(track),
                                    isPageCurrent = { isViewCurrent() && lastSelectionFilter == selectionFilter },
                                    latest = { trackSelection }, update = { next -> trackSelection = next; multiNotice = null },
                                    onLimit = { multiNotice = DesktopMultiSelectionNotice.LIMIT_REJECTED }),
                                enabled = desktopTrackIsActive(track) || multiPrepared?.selectedIds?.contains(track.id) == true,
                                modifier = Modifier.trackAction(DesktopText.SelectTrack, track, language))
                            TrackDetailsButton(track, language) {
                                if (isViewCurrent()) onTrackDetails(track.id)
                            }
                            OutlinedButton(onClick = { if (currentEnqueueEnabled && desktopTrackIsActive(track)) actOnView { callbacks.onEnqueue(track.id) } }, enabled = enqueueEnabled && desktopTrackIsActive(track),
                                modifier = Modifier.trackAction(DesktopText.Enqueue, track, language)) { Text(DesktopText.Enqueue.inLanguage(language)) }
                            OutlinedButton(onClick = { if (currentPlayNextEnabled && desktopTrackIsActive(track)) actOnView { callbacks.onPlayNext(track.id) } }, enabled = playNextEnabled && desktopTrackIsActive(track),
                                modifier = Modifier.trackAction(DesktopText.PlayNext, track, language)) { Text(DesktopText.PlayNext.inLanguage(language)) }
                            selectedPlaylist?.let { playlist ->
                                OutlinedButton(onClick = { if (desktopTrackIsActive(track)) actOnView { callbacks.onAddToPlaylist(playlist.id, track.id) } }, enabled = !reopenRequired && desktopTrackIsActive(track),
                                    modifier = Modifier.trackAction(DesktopText.AddToPlaylist, track, language)) { Text(DesktopText.AddToPlaylist.inLanguage(language)) }
                            }
                        }
                    }
                }
            }
        }
        if (section == DesktopSection.Playlists) {
            Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(playlistName, {
                        if (isInputCurrent() && it.length <= DESKTOP_PLAYLIST_NAME_LIMIT) playlistName = it
                    }, singleLine = true,
                        label = { Text(DesktopText.PlaylistName.inLanguage(language)) }, modifier = Modifier.weight(1f))
                    Button(onClick = {
                        if (isViewCurrent() && !currentReopenRequired && validDesktopPlaylistName(playlistName)) {
                            callbacks.onCreatePlaylist(playlistName)
                            playlistName = ""
                        }
                    }, enabled = validDesktopPlaylistName(playlistName) && !reopenRequired) {
                        Text(DesktopText.CreatePlaylist.inLanguage(language))
                    }
                }
                OutlinedTextField(playlistSearch, {
                    val next = desktopSearchQuery(it)
                    if (isOwnerCurrent() && input == currentInput && browseAtInput == browseRevision && playlistSearch != next) {
                        playlistSearch = next; choicePage = 0; viewRevision += 1
                    }
                }, singleLine = true, label = { Text(DesktopText.SearchPlaylists.inLanguage(language)) }, modifier = Modifier.fillMaxWidth())
                Text(DesktopText.LocalSearchHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                DesktopSearchFailure(prepared.playlistSearchError.takeIf { fresh }, language, enabled = !reopenRequired,
                    onRetry = { if (isViewCurrent() && !currentReopenRequired) viewRevision += 1 },
                    onReset = {
                        if (isInputCurrent()) { playlistSearch = ""; choicePage = 0; viewRevision += 1 }
                    })
                if (fresh && prepared.playlistSearchError == null) {
                    val count = prepared.playlist?.matchCount ?: 0
                    Text(DesktopText.SearchMatchCount.inLanguage(language).replace("{count}", count.toString()),
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    if (count == 0 && library.playlists.isNotEmpty()) Text(DesktopText.NoResults.inLanguage(language))
                }
                PlaylistSelector(if (fresh) prepared.playlist?.choices.orEmpty() else emptyList(), selectedPlaylist?.id, language, selectPlaylist)
                prepared.playlist?.let { playlistView ->
                    DesktopBrowsePager(playlistView.choicePage, DesktopText.SelectPlaylist.inLanguage(language), language, fresh) { next ->
                        if (isViewCurrent() && next != choicePage) { choicePage = next; browseRevision += 1 }
                    }
                }
                if (fresh && prepared.playlistSearchError == null && selectedPlaylist == null) Text(DesktopText.EmptyPlaylistsBody.inLanguage(language))
                selectedPlaylist?.let { playlist ->
                    Text("${DesktopText.SelectedPlaylist.inLanguage(language)}: ${playlist.name}", style = MaterialTheme.typography.titleMedium)
                    val playlistView = requireNotNull(prepared.playlist)
                    Text(DesktopText.PlaylistDescription.inLanguage(language), style = MaterialTheme.typography.labelMedium)
                    Text(playlist.description ?: DesktopText.PlaylistDescriptionEmpty.inLanguage(language),
                        modifier = Modifier.fillMaxWidth().heightIn(max = 120.dp).verticalScroll(rememberScrollState()))
                    OutlinedButton(onClick = {
                        if (descriptionDraft == null) actOnView {
                            descriptionDraft = DesktopPlaylistDescriptionDraft(playlist.id, playlist.description,
                                selectionRevision = selectionRevision, isNavigationCurrent = isNavigationCurrent)
                        }
                    }, enabled = !reopenRequired && descriptionDraft == null,
                        modifier = Modifier.playlistAction(DesktopText.EditPlaylistDescription, playlist.name, language)) {
                        Text(DesktopText.EditPlaylistDescription.inLanguage(language))
                    }
                    val draft = renameDraft?.takeIf { it.matches(playlist) }
                    if (draft == null) {
                        OutlinedButton(onClick = {
                            if (renameDraft == null) actOnView { renameDraft = DesktopPlaylistRenameDraft(playlist.id, playlist.name) }
                        }, enabled = !reopenRequired, modifier = Modifier.playlistAction(DesktopText.RenamePlaylist, playlist.name, language)) {
                            Text(DesktopText.RenamePlaylist.inLanguage(language))
                        }
                    } else {
                        OutlinedTextField(draft.name, { name ->
                            if (name.length <= DESKTOP_PLAYLIST_NAME_LIMIT && renameDraft === draft && isViewCurrent() && !currentReopenRequired) {
                                renameDraft = DesktopPlaylistRenameDraft(draft.playlistId, draft.baselineName, name)
                            }
                        }, singleLine = true, enabled = !reopenRequired,
                            label = { Text(DesktopText.PlaylistName.inLanguage(language)) },
                            supportingText = { Text(DesktopText.PlaylistNameHint.inLanguage(language)) },
                            isError = !validDesktopPlaylistName(draft.name),
                            modifier = Modifier.fillMaxWidth().playlistAction(DesktopText.RenamePlaylist, playlist.name, language))
                        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = {
                                if (renameDraft === draft && draft.canSubmit(playlist)) actOnView {
                                    callbacks.onRenamePlaylist(draft.playlistId, draft.name)
                                    renameDraft = null
                                }
                            }, enabled = draft.canSubmit(playlist) && !reopenRequired,
                                modifier = Modifier.playlistAction(DesktopText.ApplyPlaylistName, playlist.name, language)) {
                                Text(DesktopText.ApplyPlaylistName.inLanguage(language))
                            }
                            OutlinedButton(onClick = { if (isInputCurrent() && renameDraft === draft) renameDraft = null },
                                modifier = Modifier.playlistAction(DesktopText.CancelPlaylistRename, playlist.name, language)) {
                                Text(DesktopText.CancelPlaylistRename.inLanguage(language))
                            }
                        }
                    }
                    OutlinedButton(onClick = {
                        if (deleteTarget == null) actOnView {
                            renameDraft = null
                            deleteTarget = DesktopPlaylistDeleteTarget(playlist.id, playlist.name, playlist.entries.size)
                        }
                    }, enabled = !reopenRequired,
                        modifier = Modifier.playlistAction(DesktopText.DeletePlaylist, playlist.name, language)) { Text(DesktopText.DeletePlaylist.inLanguage(language)) }
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = {
                            if (currentBulkEnqueueEnabled) actOnView { callbacks.onEnqueuePlaylist(playlist.id) }
                        }, enabled = bulkAdmission.enqueue,
                            modifier = Modifier.playlistAction(DesktopText.EnqueuePlaylist, playlist.name, language)) { Text(DesktopText.EnqueuePlaylist.inLanguage(language)) }
                        OutlinedButton(onClick = {
                            if (currentBulkPlayNextEnabled) actOnView { callbacks.onPlayPlaylistNext(playlist.id) }
                        }, enabled = bulkAdmission.playNext,
                            modifier = Modifier.playlistAction(DesktopText.PlayPlaylistNext, playlist.name, language)) { Text(DesktopText.PlayPlaylistNext.inLanguage(language)) }
                    }
                    if (playlist.entries.isNotEmpty()) {
                        when {
                            !prepared.playlistSelection.allActive -> Text(DesktopText.PlaylistCollectionUnavailable.inLanguage(language))
                            !bulkAdmission.hasCapacity -> Text(DesktopText.PlaylistQueueCapacity.inLanguage(language)
                                .replace("{count}", playlist.entries.size.toString())
                                .replace("{remaining}", (DesktopLocalLimits.MAX_QUEUE_ENTRIES - (queue?.entries?.size ?: 0)).coerceAtLeast(0).toString()))
                            !queueInsertionEnabled -> Text(DesktopText.PlaybackUnavailable.inLanguage(language))
                            queue != null && !queueControlsEnabled -> Text(DesktopText.UnsupportedMode.inLanguage(language))
                            queue != null && queue.entries.isNotEmpty() && !prepared.queue.hasCurrent -> Text(DesktopText.QueueEditUnavailable.inLanguage(language))
                        }
                    }
                    OutlinedButton(onClick = {
                        if (currentPlaylistPlayEnabled) actOnView { callbacks.onPlayTracks(prepared.playlistSelection.playIds, "PLAYLIST") }
                    }, enabled = playlistPlayEnabled,
                        modifier = Modifier.playlistAction(DesktopText.PlayAll, playlist.name, language)) {
                        Text(DesktopText.PlayAll.inLanguage(language))
                    }
                    if (playlist.entries.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
                        Text(DesktopText.WholePlaylistQueueLimit.inLanguage(language)
                            .replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString()))
                    }
                    OutlinedButton(onClick = {
                        if (currentSelectionPlayEnabled && prepared.playlistPageSelection.allActive &&
                            prepared.playlistPageSelection.playIds.isNotEmpty()) actOnView {
                            callbacks.onPlayTracks(prepared.playlistPageSelection.playIds, "PLAYLIST")
                        }
                    }, enabled = selectionPlayEnabled && prepared.playlistPageSelection.allActive &&
                        prepared.playlistPageSelection.playIds.isNotEmpty(),
                        modifier = Modifier.playlistAction(DesktopText.PlayPage, playlist.name, language)) {
                        Text(DesktopText.PlayPage.inLanguage(language))
                    }
                    Text(DesktopText.PagePlaybackBoundary.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                    if (!prepared.playlistPageSelection.allActive && playlistView.rows.isNotEmpty()) {
                        Text(DesktopText.PlaylistPageUnavailable.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                    }
                    DesktopBrowsePager(playlistView.entryPage, DesktopText.PlaylistEntries.inLanguage(language), language, fresh) { next ->
                        if (isViewCurrent() && next != entryPage) { entryPage = next; browseRevision += 1 }
                    }
                    LazyColumn(Modifier.fillMaxWidth().heightIn(min = 160.dp, max = 480.dp)) {
                        items(playlistView.rows, key = { it.entry.id }) { row ->
                            val entry = row.entry
                            val track = tracksById[entry.trackId]
                            val suffix = prepared.playlistSuffixSelections[entry.id]
                            if (track != null) {
                                TrackRow(track, language, enabled = selectionPlayEnabled && suffix?.allActive == true, onPlay = {
                                    if (currentSelectionPlayEnabled && suffix?.allActive == true && suffix.playIds.isNotEmpty()) actOnView {
                                        callbacks.onPlayTracks(suffix.playIds, "PLAYLIST")
                                    }
                                }, playModifier = Modifier.playlistEntryAction(DesktopText.Play, playlist.name, track, row, language)) {
                                    OutlinedButton(onClick = {
                                        if (isViewCurrent()) onTrackDetails(track.id)
                                    }, modifier = Modifier.playlistEntryAction(DesktopText.TrackDetails, playlist.name, track, row, language)) { Text(DesktopText.TrackDetails.inLanguage(language)) }
                                    OutlinedButton(onClick = { if (currentEnqueueEnabled && desktopTrackIsActive(track)) actOnView { callbacks.onEnqueue(track.id) } }, enabled = enqueueEnabled && desktopTrackIsActive(track),
                                        modifier = Modifier.playlistEntryAction(DesktopText.Enqueue, playlist.name, track, row, language)) { Text(DesktopText.Enqueue.inLanguage(language)) }
                                    OutlinedButton(onClick = { if (currentPlayNextEnabled && desktopTrackIsActive(track)) actOnView { callbacks.onPlayNext(track.id) } }, enabled = playNextEnabled && desktopTrackIsActive(track),
                                        modifier = Modifier.playlistEntryAction(DesktopText.PlayNext, playlist.name, track, row, language)) { Text(DesktopText.PlayNext.inLanguage(language)) }
                                    OutlinedButton(onClick = { if (row.canMoveUp) actOnView { callbacks.onMovePlaylistEntry(playlist.id, entry.id, -1) } },
                                        enabled = row.canMoveUp && !reopenRequired,
                                        modifier = Modifier.playlistEntryAction(DesktopText.MoveUp, playlist.name, track, row, language)) { Text(DesktopText.MoveUp.inLanguage(language)) }
                                    OutlinedButton(onClick = { if (row.canMoveDown) actOnView { callbacks.onMovePlaylistEntry(playlist.id, entry.id, 1) } },
                                        enabled = row.canMoveDown && !reopenRequired,
                                        modifier = Modifier.playlistEntryAction(DesktopText.MoveDown, playlist.name, track, row, language)) { Text(DesktopText.MoveDown.inLanguage(language)) }
                                    OutlinedButton(onClick = { actOnView { callbacks.onRemovePlaylistEntry(playlist.id, entry.id) } }, enabled = !reopenRequired,
                                        modifier = Modifier.playlistEntryAction(DesktopText.Remove, playlist.name, track, row, language)) { Text(DesktopText.Remove.inLanguage(language)) }
                                }
                            } else {
                                Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(DesktopText.SourceMissing.inLanguage(language), style = MaterialTheme.typography.titleMedium)
                                    Text(desktopPlaylistEntryLabel(row, language), style = MaterialTheme.typography.bodySmall)
                                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        OutlinedButton(onClick = { if (row.canMoveUp) actOnView { callbacks.onMovePlaylistEntry(playlist.id, entry.id, -1) } },
                                            enabled = row.canMoveUp && !reopenRequired,
                                            modifier = Modifier.playlistEntryAction(DesktopText.MoveUp, playlist.name, null, row, language)) { Text(DesktopText.MoveUp.inLanguage(language)) }
                                        OutlinedButton(onClick = { if (row.canMoveDown) actOnView { callbacks.onMovePlaylistEntry(playlist.id, entry.id, 1) } },
                                            enabled = row.canMoveDown && !reopenRequired,
                                            modifier = Modifier.playlistEntryAction(DesktopText.MoveDown, playlist.name, null, row, language)) { Text(DesktopText.MoveDown.inLanguage(language)) }
                                        OutlinedButton(onClick = { actOnView { callbacks.onRemovePlaylistEntry(playlist.id, entry.id) } }, enabled = !reopenRequired,
                                            modifier = Modifier.playlistEntryAction(DesktopText.Remove, playlist.name, null, row, language)) { Text(DesktopText.Remove.inLanguage(language)) }
                                    }
                                }
                                HorizontalDivider()
                            }
                        }
                    }
                }
            }
        }
        if (section == DesktopSection.Queue) {
            val queueActionsEnabled = fresh && queueControlsEnabled && !reopenRequired
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FilterChip(selected = queue?.shuffleEnabled == true, onClick = { actOnView { callbacks.onShuffle(queue?.shuffleEnabled != true) } },
                    enabled = queueActionsEnabled, label = { Text(DesktopText.Shuffle.inLanguage(language)) })
                listOf("OFF" to DesktopText.RepeatOff, "ONE" to DesktopText.RepeatOne, "ALL" to DesktopText.RepeatAll).forEach { (mode, label) ->
                    FilterChip(selected = queue?.repeatMode == mode, onClick = { actOnView { callbacks.onRepeat(mode) } }, enabled = queueActionsEnabled,
                        label = { Text(label.inLanguage(language)) })
                }
            }
            if (queue != null && !queueControlsEnabled && !reopenRequired) Text(DesktopText.UnsupportedMode.inLanguage(language))
            val rows = if (fresh) prepared.queue.rows else emptyList()
            if (fresh) {
                OutlinedButton(onClick = {
                    if (currentUpcomingEnabled && prepared.queue.upcomingCount > 0) actOnView { callbacks.onClearUpcoming() }
                }, enabled = upcomingEnabled && prepared.queue.upcomingCount > 0) {
                    Text(DesktopText.ClearUpcoming.inLanguage(language))
                }
                if (queueEditor.target == null) when {
                    rows.isEmpty() -> Text(DesktopText.EmptyQueue.inLanguage(language))
                    !prepared.queue.hasCurrent -> Text(DesktopText.QueueEditUnavailable.inLanguage(language))
                    prepared.queue.upcomingCount == 0 -> Text(DesktopText.NoUpcoming.inLanguage(language))
                }
            }
            if (queueEditor.target != null) {
                DesktopUpcomingQueueEditor(queueEditor, tracksById, callbacks, queueEditorOwner, language,
                    enabled = queueControlsEnabled, reopenRequired = reopenRequired,
                    navigationRevision = navigationRevision, isOwnerCurrent = isOwnerCurrent,
                    legacyQueue = queue, legacyEnabled = queueActionsEnabled, actOnLegacyView = actOnView,
                    modifier = Modifier.weight(1f).fillMaxWidth(), metadataUpdating = !fresh)
            } else LazyColumn(Modifier.weight(1f)) {
                items(rows, key = { it.entry.id }) { row ->
                    val entry = row.entry
                    val track = tracksById[entry.trackId]
                    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(Modifier.fillMaxWidth().semantics { selected = entry.id == queue?.currentEntryId }
                            .clickable(enabled = queueActionsEnabled, role = Role.Button,
                                onClickLabel = DesktopText.Play.inLanguage(language)) { actOnView { callbacks.onSelectEntry(entry.id) } }.padding(vertical = 8.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(if (entry.id == queue?.currentEntryId) "▶" else "♫", color = MaterialTheme.colorScheme.primary)
                            Column(Modifier.weight(1f)) {
                                Text(track?.title ?: DesktopText.SourceMissing.inLanguage(language), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(track?.artist.orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (row.upcoming) {
                                OutlinedButton(onClick = {
                                    if (currentUpcomingEnabled && row.canMoveUp) actOnView { callbacks.onMoveUpcoming(entry.id, -1) }
                                }, enabled = upcomingEnabled && row.canMoveUp,
                                    modifier = Modifier.queueAction(DesktopText.MoveUp, track, row.position, language)) { Text(DesktopText.MoveUp.inLanguage(language)) }
                                OutlinedButton(onClick = {
                                    if (currentUpcomingEnabled && row.canMoveDown) actOnView { callbacks.onMoveUpcoming(entry.id, 1) }
                                }, enabled = upcomingEnabled && row.canMoveDown,
                                    modifier = Modifier.queueAction(DesktopText.MoveDown, track, row.position, language)) { Text(DesktopText.MoveDown.inLanguage(language)) }
                            }
                            OutlinedButton(onClick = { actOnView { callbacks.onRemoveEntry(entry.id) } }, enabled = queueActionsEnabled,
                                modifier = Modifier.queueAction(DesktopText.Remove, track, row.position, language)) { Text(DesktopText.Remove.inLanguage(language)) }
                        }
                    }
                    HorizontalDivider()
                }
            }
        }

    }
}

@Composable
private fun PlaylistSelector(playlists: List<DesktopPlaylist>, selectedId: String?, language: DesktopLanguage, onSelect: (String) -> Unit) {
    if (playlists.isEmpty()) return
    Text(DesktopText.SelectPlaylist.inLanguage(language), style = MaterialTheme.typography.labelMedium)
    LazyRow(Modifier.fillMaxWidth().heightIn(max = 64.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(playlists, key = { it.id }) { playlist ->
            FilterChip(selected = playlist.id == selectedId, onClick = { onSelect(playlist.id) },
                label = { Text(playlist.name, maxLines = 1, overflow = TextOverflow.Ellipsis) })
        }
    }
}

@Composable
private fun TrackCollectionBadge(track: DesktopTrack, language: DesktopLanguage) {
    if (!desktopTrackIsActive(track)) {
        Text(desktopTrackCollectionStatus(track.collectionState, language),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun TrackRow(track: DesktopTrack, language: DesktopLanguage, enabled: Boolean, onPlay: () -> Unit,
    playModifier: Modifier = Modifier, copyInspectionState: DesktopCopyInspectionState? = null, actions: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(playModifier.fillMaxWidth().clickable(enabled = enabled, role = Role.Button,
            onClickLabel = DesktopText.PlayPage.inLanguage(language) + ": " + DesktopText.PagePlaybackBoundary.inLanguage(language),
            onClick = onPlay).padding(vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                Text(track.artist, maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TrackCollectionBadge(track, language)
                copyInspectionState?.let { state ->
                    Text("${DesktopCopyText.Observation.label(language)}: ${desktopCopyStatus(track, state.results, language)}",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text("${DesktopText.TrackPreference.inLanguage(language)}: ${desktopTrackPreferenceStatus(track.preference, language)}",
                    maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(track.durationMs?.let(::formatPlaybackTime) ?: "--:--", style = MaterialTheme.typography.labelMedium)
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            actions()
        }
    }
    HorizontalDivider()
}

private const val LIST_LIMIT: Int = 10_000

/** Compose keys and freshness compare snapshot identity without walking lists on the UI thread. */
internal class DesktopLibraryInput(
    val library: DesktopLibrarySnapshot,
    val query: String,
    val sort: DesktopLibrarySort,
    val direction: DesktopSortDirection,
    val playlistInput: DesktopPlaylistInput,
    val preferenceFilter: DesktopTrackPreferenceFilter = DesktopTrackPreferenceFilter.ALL,
    val viewRevision: Long = 0,
    val collectionFilter: DesktopTrackCollectionFilter = DesktopTrackCollectionFilter.ACTIVE,
    val copyFilter: DesktopCopyAvailabilityFilter = DesktopCopyAvailabilityFilter.ALL,
    val copyResults: Map<String, DesktopCopyInspection> = emptyMap(),
    val page: Int = 0,
    val browseRevision: Long = 0,
    val textSearch: DesktopTextSearchPort? = null,
    val section: DesktopSection = DesktopSection.Library,
    val navigationRevision: Long = 0,
) {
    override fun equals(other: Any?): Boolean = other is DesktopLibraryInput && library === other.library &&
        query == other.query && sort == other.sort && direction == other.direction && playlistInput == other.playlistInput &&
        preferenceFilter == other.preferenceFilter && viewRevision == other.viewRevision && collectionFilter == other.collectionFilter &&
        copyFilter == other.copyFilter && copyResults === other.copyResults && page == other.page &&
        browseRevision == other.browseRevision && textSearch === other.textSearch &&
        section == other.section && navigationRevision == other.navigationRevision
    override fun hashCode(): Int = 31 * (31 * (31 * (31 * query.hashCode() + sort.hashCode()) + direction.hashCode()) +
        preferenceFilter.hashCode()) + viewRevision.hashCode() + collectionFilter.hashCode() + copyFilter.hashCode() +
        page.hashCode() + browseRevision.hashCode()

    fun isCurrent(
        snapshot: DesktopLibrarySnapshot,
        query: String,
        sort: DesktopLibrarySort,
        direction: DesktopSortDirection,
        preferenceFilter: DesktopTrackPreferenceFilter,
        viewRevision: Long,
        selectedPlaylistId: String?,
        selectionRevision: Long,
        collectionFilter: DesktopTrackCollectionFilter = DesktopTrackCollectionFilter.ACTIVE,
        copyFilter: DesktopCopyAvailabilityFilter = DesktopCopyAvailabilityFilter.ALL,
        copyResults: Map<String, DesktopCopyInspection> = emptyMap(),
        page: Int = 0,
        browseRevision: Long = 0,
    ): Boolean = library === snapshot && this.query == query && this.sort == sort && this.direction == direction &&
        this.preferenceFilter == preferenceFilter && this.viewRevision == viewRevision && this.collectionFilter == collectionFilter &&
        this.copyFilter == copyFilter && this.copyResults === copyResults && this.page == page &&
        this.browseRevision == browseRevision &&
        playlistInput.isCurrent(snapshot, selectedPlaylistId, selectionRevision)
}

/** Identity equality also keeps produceState publication from comparing all prepared rows. */
private class DesktopLibraryView(
    val matches: List<DesktopTrack> = emptyList(),
    val matchCount: Int = 0,
    val tracksById: Map<String, DesktopTrack> = emptyMap(),
    val input: DesktopLibraryInput? = null,
    val queue: DesktopQueueView = DesktopQueueView(),
    val playIds: List<String> = emptyList(),
    val trackPositions: Map<String, Int> = emptyMap(),
    val playlist: DesktopPlaylistView? = null,
    val playlistSelection: DesktopPlaylistCollectionSelection = DesktopPlaylistCollectionSelection(),
    val inspectionIds: List<String> = emptyList(),
    val page: DesktopBrowsePage = desktopBrowsePage(0, 0),
    val allPlayIds: List<String> = emptyList(),
    val allPlayOverflow: Boolean = false,
    val playlistPageSelection: DesktopPlaylistCollectionSelection = DesktopPlaylistCollectionSelection(),
    val playlistSuffixSelections: Map<String, DesktopPlaylistCollectionSelection> = emptyMap(),
    val multiSelection: DesktopMultiSelectionView? = null,
    val searchError: String? = null,
    val playlistSearchError: String? = null,
)

private class DesktopQueueRow(
    val entry: DesktopQueueEntry,
    val position: Int,
    val upcoming: Boolean,
    val canMoveUp: Boolean,
    val canMoveDown: Boolean,
)

private class DesktopQueueView(
    val rows: List<DesktopQueueRow> = emptyList(),
    val hasCurrent: Boolean = false,
    val upcomingCount: Int = 0,
)

/** Called only by background preparation; rows carry positions in the effective persisted order. */
private suspend fun prepareDesktopQueueView(queue: DesktopQueue?): DesktopQueueView {
    if (queue == null) return DesktopQueueView()
    val entries = if (queue.shuffleEnabled) {
        val byId = queue.entries.associateBy { it.id }
        queue.shuffleOrder.mapNotNull { byId[it] }
    } else queue.entries
    val currentPosition = entries.indexOfFirst { it.id == queue.currentEntryId }
    val rows = entries.take(LIST_LIMIT).mapIndexed { position, entry ->
        if (position % 256 == 0) currentCoroutineContext().ensureActive()
        val upcoming = currentPosition >= 0 && position > currentPosition
        DesktopQueueRow(entry, position, upcoming,
            canMoveUp = upcoming && position > currentPosition + 1,
            canMoveDown = upcoming && position < entries.lastIndex)
    }
    currentCoroutineContext().ensureActive()
    return DesktopQueueView(rows, currentPosition >= 0,
        if (currentPosition >= 0) entries.size - currentPosition - 1 else 0)
}

private fun Modifier.queueAction(action: DesktopText, track: DesktopTrack?, position: Int, language: DesktopLanguage): Modifier = semantics {
    val entryLabel = DesktopText.QueueEntryPosition.inLanguage(language).replace("{position}", (position + 1).toString())
    contentDescription = "${action.inLanguage(language)}: ${track?.title ?: DesktopText.Unknown.inLanguage(language)}; $entryLabel"
}

private fun Modifier.trackAction(action: DesktopText, track: DesktopTrack, language: DesktopLanguage): Modifier = semantics {
    contentDescription = "${action.inLanguage(language)}: ${track.title}"
}

private fun Modifier.playlistAction(action: DesktopText, playlistName: String, language: DesktopLanguage): Modifier = semantics {
    contentDescription = "${action.inLanguage(language)}: $playlistName"
}

private fun desktopPlaylistEntryLabel(row: DesktopPlaylistRow, language: DesktopLanguage): String =
    DesktopText.PlaylistEntryPosition.inLanguage(language)
        .replace("{position}", (row.position + 1).toString()).replace("{occurrence}", row.occurrence.toString())

private fun Modifier.playlistEntryAction(action: DesktopText, playlistName: String, track: DesktopTrack?, row: DesktopPlaylistRow, language: DesktopLanguage): Modifier = semantics {
    val entryLabel = desktopPlaylistEntryLabel(row, language)
    contentDescription = "${action.inLanguage(language)}: $playlistName; ${track?.title ?: DesktopText.SourceMissing.inLanguage(language)}; $entryLabel"
}


internal fun DesktopLibrarySort.label(language: DesktopLanguage): String = when (this) {
    DesktopLibrarySort.Title -> DesktopText.TrackTitle
    DesktopLibrarySort.Artist -> DesktopText.Artist
    DesktopLibrarySort.Duration -> DesktopText.Duration
}.inLanguage(language)

internal fun DesktopSortDirection.label(language: DesktopLanguage): String = when (this) {
    DesktopSortDirection.Ascending -> DesktopText.SortAscending
    DesktopSortDirection.Descending -> DesktopText.SortDescending
}.inLanguage(language)

@Composable
private fun TrackDetailsButton(track: DesktopTrack, language: DesktopLanguage, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = Modifier.trackAction(DesktopText.TrackDetails, track, language)) {
        Text(DesktopText.TrackDetails.inLanguage(language))
    }
}

internal fun desktopErrorMessage(code: String, language: DesktopLanguage): String = when (code) {
    "IMPORT_CANCELLED" -> DesktopText.ImportCancelled.inLanguage(language)
    "desktop_snapshot_unavailable", "import_library_reopen_required" -> DesktopText.LibraryReopenRequired.inLanguage(language)
    "SOURCE_UNAVAILABLE" -> DesktopText.SourceMissing.inLanguage(language)
    "AUDIO_ERROR" -> DesktopText.AudioError.inLanguage(language)
    "AUDIO_UNAVAILABLE" -> DesktopText.PlaybackUnavailable.inLanguage(language)
    "UNSUPPORTED_QUEUE_MODE", "QUEUE_LIMIT_OR_MODE" -> DesktopText.UnsupportedMode.inLanguage(language)
    "QUEUE_LIMIT" -> DesktopText.QueueLimit.inLanguage(language).replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString())
    "QUEUE_EDIT_UNAVAILABLE", "QUEUE_CURRENT_MISSING" -> DesktopText.QueueEditUnavailable.inLanguage(language)
    "desktop_playlist_name_invalid" -> DesktopText.PlaylistNameHint.inLanguage(language)
    "desktop_playlist_description_invalid" -> DesktopText.PlaylistDescriptionHint.inLanguage(language)
    "desktop_playlist_description_conflict" -> DesktopText.PlaylistDescriptionConflict.inLanguage(language)
    "desktop_playlist_missing" -> DesktopText.ReferencedPlaylistMissing.inLanguage(language)
    "desktop_playlist_batch_limit" -> DesktopText.PlaylistBatchLimit.inLanguage(language)
        .replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString())
    "desktop_playlist_limit" -> DesktopText.PlaylistAppendLimit.inLanguage(language)
    "desktop_playlist_order_invalid" -> DesktopText.PlaylistOrderUnsupported.inLanguage(language)
    "desktop_track_missing" -> DesktopText.ReferencedTrackMissing.inLanguage(language)
    "desktop_identity_invalid" -> DesktopText.CommandIdentityInvalid.inLanguage(language)
    "desktop_metadata_invalid" -> DesktopText.TrackMetadataHint.inLanguage(language)
    "desktop_track_not_in_collection" -> DesktopText.TrackNotInCollection.inLanguage(language)
    "desktop_collection_state_invalid" -> DesktopText.CollectionStateInvalid.inLanguage(language)
    "import_selection_limit" -> DesktopText.ImportSelectionLimit.inLanguage(language).replace("{limit}", DesktopLocalLimits.MAX_SELECTION_ROOTS.toString())
    "import_preview_expired", "import_source_changed", "import_busy", "import_file_limit", "import_entry_limit",
    "import_depth_limit", "import_file_size_limit", "import_batch_size_limit", "import_time_limit",
    "import_source_unreadable", "import_access_denied", "import_source_not_regular", "import_symlink_rejected",
    "import_invalid_selection", "import_empty_file", "import_unsupported_format", "import_insufficient_space",
    "import_io_failed", "import_store_failed", "import_managed_directory_unavailable", "import_managed_directory_unsafe",
    "import_managed_integrity_failed", "import_atomic_publish_unavailable", "import_publish_sync_failed",
    "IMPORT_FAILED" -> desktopImportErrorText(code).label(language)
    "IMPORT_DROP_INVALID" -> DesktopText.InvalidDrop.inLanguage(language)
    "LIBRARY_COMMAND_BUSY", "PLAYBACK_COMMAND_BUSY" -> DesktopText.CommandBusy.inLanguage(language)
    else -> DesktopText.OperationFailed.inLanguage(language)
}
