package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopTrack
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** Keys compare only IDs and snapshot identity, never the snapshot's full lists. */
internal class DesktopTrackInput(
    val library: DesktopLibrarySnapshot?,
    val selectedId: String?,
    val currentId: String?,
    val selectionRevision: Long = 0,
    val isSelectionCurrent: () -> Boolean = { true },
) {
    override fun equals(other: Any?): Boolean = other is DesktopTrackInput &&
        library === other.library && selectedId == other.selectedId && currentId == other.currentId &&
        selectionRevision == other.selectionRevision

    override fun hashCode(): Int = 31 * (31 * (selectedId?.hashCode() ?: 0) + (currentId?.hashCode() ?: 0)) + selectionRevision.hashCode()
}

/** Reference equality avoids deep publication comparisons; no media capabilities are retained. */
internal class DesktopTrackView(
    val input: DesktopTrackInput? = null,
    val selectedTrack: DesktopTrack? = null,
    val currentTrack: DesktopTrack? = null,
    val queueHasCurrent: Boolean = false,
)

@Composable
internal fun rememberDesktopTrackView(input: DesktopTrackInput): DesktopTrackView {
    val view by produceState(DesktopTrackView(), input) {
        value = withContext(Dispatchers.Default) {
            var selected: DesktopTrack? = null
            var current: DesktopTrack? = null
            if (input.selectedId != null || input.currentId != null) {
                val tracks = input.library?.tracks.orEmpty()
                for (index in tracks.indices) {
                    if (index % 256 == 0) currentCoroutineContext().ensureActive()
                    val track = tracks[index]
                    if (track.id == input.selectedId) selected = track
                    if (track.id == input.currentId) current = track
                    if ((input.selectedId == null || selected != null) && (input.currentId == null || current != null)) break
                }
            }
            currentCoroutineContext().ensureActive()
            val queue = input.library?.queue
            val queueHasCurrent = if (queue != null && queue.currentEntryId != null) {
                val entryId = queue.currentEntryId
                if (queue.shuffleEnabled) queue.shuffleOrder.any { it == entryId } && queue.entries.any { it.id == entryId }
                else queue.entries.any { it.id == entryId }
            } else false
            currentCoroutineContext().ensureActive()
            DesktopTrackView(input, selected, current, queueHasCurrent)
        }
    }
    return view
}

@Composable
internal fun DesktopTrackDetails(
    input: DesktopTrackInput,
    prepared: DesktopTrackView,
    playback: PlaybackPresentationState,
    adapterAvailable: Boolean,
    reopenRequired: Boolean,
    language: DesktopLanguage,
    callbacks: DesktopLibraryCallbacks,
    onClose: () -> Unit,
    metadataState: DesktopMetadataState = DesktopMetadataState(),
    onInspectMetadata: () -> Unit = {},
    copyInspectionState: DesktopCopyInspectionState = DesktopCopyInspectionState(),
    copyInspectionCallbacks: DesktopCopyInspectionCallbacks = DesktopCopyInspectionCallbacks(),
    importing: Boolean = false,
) {
    val currentInput by rememberUpdatedState(input)
    val currentPrepared by rememberUpdatedState(prepared)
    val currentReopenRequired by rememberUpdatedState(reopenRequired)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val currentMetadataState by rememberUpdatedState(metadataState)
    val currentInspectMetadata by rememberUpdatedState(onInspectMetadata)
    val currentCopyState by rememberUpdatedState(copyInspectionState)
    val currentCopyCallbacks by rememberUpdatedState(copyInspectionCallbacks)
    val currentImporting by rememberUpdatedState(importing)
    var inspectionSubmitted by remember(input, copyInspectionState.requestId, copyInspectionState.busy) { mutableStateOf(false) }
    var editDraft by remember(input.selectedId, input.selectionRevision) { mutableStateOf<DesktopTrackTextDraft?>(null) }
    var removal by remember(input.selectedId, input.selectionRevision) { mutableStateOf<DesktopTrackCollectionRemoval?>(null) }
    var removalSubmitted by remember(input.selectedId, input.selectionRevision) { mutableStateOf(false) }
    if (input.selectedId == null) return
    val fresh = prepared.input == input
    val track = if (fresh) prepared.selectedTrack else null
    val draft = editDraft?.takeIf { it.matches(track) }
    val isViewCurrent: () -> Boolean = {
        input.isSelectionCurrent() && currentPrepared === prepared && prepared.input == currentInput
    }
    val editEnabled = track != null && !reopenRequired
    val currentEditEnabled by rememberUpdatedState(editEnabled)
    val preferenceEnabled = desktopTrackPreferenceCanWrite(input, prepared, reopenRequired)
    val setPreference: (DesktopTrackPreference) -> Unit = { preference ->
        if (isViewCurrent() && desktopTrackPreferenceCanWrite(currentInput, currentPrepared, currentReopenRequired)) {
            track?.let { currentCallbacks.onSetTrackPreference(it.id, preference.storedValue) }
        }
    }
    SideEffect {
        if (fresh && isViewCurrent() && editDraft?.matches(track) == false) editDraft = null
        if (fresh && isViewCurrent() && track?.collectionState == "REMOVED") {
            removal = null
            removalSubmitted = false
        }
    }
    val isCurrent = track != null && track.id == input.currentId && prepared.currentTrack?.id == track.id
    val engineAvailable = adapterAvailable && playback.connection == PlaybackConnectionState.Connected
    // Replacing a queue can start another track even when the existing queue has no playable source.
    val playEnabled = desktopTrackIsActive(track) && engineAvailable && !reopenRequired &&
        (!isCurrent || playback.controls == PlaybackControlGate.Allowed)
    val queue = input.library?.queue
    val queueSupported = queue == null || (playback.shuffleEnabled && playback.repeatEnabled)
    val queueHasSpace = (queue?.entries?.size ?: 0) < DesktopLocalLimits.MAX_QUEUE_ENTRIES
    val enqueueEnabled = desktopTrackIsActive(track) && engineAvailable && !reopenRequired && queueSupported && queueHasSpace
    val playNextEnabled = enqueueEnabled && (queue == null || queue.entries.isEmpty() || prepared.queueHasCurrent)
    val currentPlayEnabled by rememberUpdatedState(playEnabled)
    val currentEnqueueEnabled by rememberUpdatedState(enqueueEnabled)
    val currentPlayNextEnabled by rememberUpdatedState(playNextEnabled)
    val duration = if (isCurrent) playback.durationMs?.takeIf { it >= 0 } ?: track?.durationMs else track?.durationMs
    val title = DesktopText.TrackDetails.inLanguage(language)
    removal?.let { target ->
        val canConfirm = !removalSubmitted && target.canConfirm(input, prepared, reopenRequired)
        val dialogTitle = DesktopText.RemoveTrackConfirmation.inLanguage(language)
        AlertDialog(
            onDismissRequest = { if (removal === target && input.isSelectionCurrent()) removal = null },
            modifier = Modifier.semantics { paneTitle = dialogTitle },
            title = { Text(dialogTitle) },
            text = {
                Column(Modifier.heightIn(max = 300.dp).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(target.track.title)
                    Text(DesktopText.RemoveTrackRetained.inLanguage(language))
                    when {
                        reopenRequired -> Text(DesktopText.LibraryReopenRequired.inLanguage(language), color = MaterialTheme.colorScheme.error)
                        !target.canConfirm(input, prepared, false) -> Text(DesktopText.CollectionConfirmationChanged.inLanguage(language))
                        removalSubmitted -> Text(DesktopText.CollectionRequestPending.inLanguage(language),
                            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (removal === target && !removalSubmitted && isViewCurrent() &&
                        target.canConfirm(currentInput, currentPrepared, currentReopenRequired)) {
                        removalSubmitted = true
                        currentCallbacks.onRemoveTrackFromCollection(target.track.id)
                    }
                }, enabled = canConfirm, modifier = Modifier.detailAction(DesktopText.ConfirmRemoveTrack, target.track, language)) {
                    Text(DesktopText.ConfirmRemoveTrack.inLanguage(language))
                }
            },
            dismissButton = {
                TextButton(onClick = {
                    if (removal === target && input.isSelectionCurrent()) removal = null
                }) { Text(DesktopText.CancelRemoveTrack.inLanguage(language)) }
            },
        )
        return
    }
    AlertDialog(
        onDismissRequest = { if (input.isSelectionCurrent()) onClose() },
        modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) },
        text = {
            Column(
                Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                when {
                    !fresh -> Text(DesktopText.UpdatingTrack.inLanguage(language),
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    track == null -> Text(DesktopText.TrackUnavailable.inLanguage(language))
                    else -> {
                        DesktopArtworkView(track.id, metadataState.artworks[track.id], language, 128.dp)
                        Text(DesktopMetadataText.CoverHint.label(language), style = MaterialTheme.typography.bodySmall)
                        Text(DesktopMetadataText.ProvenanceHint.label(language), style = MaterialTheme.typography.bodySmall)
                        OutlinedButton(onClick = {
                            if (isViewCurrent() && desktopMetadataCanInspect(currentInput, currentPrepared,
                                currentMetadataState, currentReopenRequired)) currentInspectMetadata()
                        }, enabled = desktopMetadataCanInspect(input, prepared, metadataState, reopenRequired),
                            modifier = Modifier.semantics {
                                contentDescription = "${DesktopMetadataText.Inspect.label(language)}: ${track.title}"
                            }) { Text(DesktopMetadataText.Inspect.label(language)) }
                        if (draft == null) {
                            TrackDetailField(DesktopText.TrackTitle, track.title.ifBlank { DesktopText.Unknown.inLanguage(language) }, language)
                            TrackDetailField(DesktopText.Artist, track.artist.ifBlank { DesktopText.Unknown.inLanguage(language) }, language)
                            OutlinedButton(onClick = {
                                if (editDraft == null && isViewCurrent() && currentEditEnabled && !currentReopenRequired) {
                                    editDraft = DesktopTrackTextDraft(track.id, track.title, track.artist)
                                }
                            }, enabled = editEnabled, modifier = Modifier.detailAction(DesktopText.EditTrackMetadata, track, language)) {
                                Text(DesktopText.EditTrackMetadata.inLanguage(language))
                            }
                        } else {
                            OutlinedTextField(draft.title, { value ->
                                if (value.length <= DESKTOP_TRACK_TEXT_LIMIT && editDraft === draft && isViewCurrent() &&
                                    currentEditEnabled && !currentReopenRequired) {
                                    editDraft = DesktopTrackTextDraft(draft.trackId, draft.baselineTitle, draft.baselineArtist, value, draft.artist)
                                }
                            }, singleLine = true, enabled = editEnabled,
                                label = { Text(DesktopText.TrackTitle.inLanguage(language)) },
                                isError = !validDesktopTrackText(draft.title, title = true), modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(draft.artist, { value ->
                                if (value.length <= DESKTOP_TRACK_TEXT_LIMIT && editDraft === draft && isViewCurrent() &&
                                    currentEditEnabled && !currentReopenRequired) {
                                    editDraft = DesktopTrackTextDraft(draft.trackId, draft.baselineTitle, draft.baselineArtist, draft.title, value)
                                }
                            }, singleLine = true, enabled = editEnabled,
                                label = { Text(DesktopText.Artist.inLanguage(language)) },
                                isError = !validDesktopTrackText(draft.artist, title = false), modifier = Modifier.fillMaxWidth())
                            Text(DesktopText.TrackMetadataHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = {
                                    if (editDraft === draft && isViewCurrent() && currentEditEnabled && !currentReopenRequired && draft.canSubmit(track)) {
                                        callbacks.onUpdateTrackMetadata(draft.trackId, draft.title, draft.artist)
                                        editDraft = null
                                    }
                                }, enabled = editEnabled && draft.canSubmit(track),
                                    modifier = Modifier.detailAction(DesktopText.ApplyTrackMetadata, track, language)) { Text(DesktopText.ApplyTrackMetadata.inLanguage(language)) }
                                OutlinedButton(onClick = { if (editDraft === draft && input.isSelectionCurrent()) editDraft = null },
                                    modifier = Modifier.detailAction(DesktopText.CancelTrackMetadata, track, language)) { Text(DesktopText.CancelTrackMetadata.inLanguage(language)) }
                            }
                        }
                        TrackDetailField(DesktopText.Duration,
                            duration?.takeIf { it >= 0 }?.let(::formatPlaybackTime) ?: DesktopText.Unknown.inLanguage(language), language)
                        TrackDetailField(DesktopText.ByteSize,
                            track.byteSize.takeIf { it >= 0 }?.toString() ?: DesktopText.Unknown.inLanguage(language), language)
                        Text(DesktopCopyText.Observation.label(language), style = MaterialTheme.typography.labelMedium)
                        Text(desktopCopyStatus(track, copyInspectionState.results, language))
                        Text(DesktopCopyText.Hint.label(language), style = MaterialTheme.typography.bodySmall)
                        OutlinedButton(onClick = {
                            if (!inspectionSubmitted && isViewCurrent() && desktopCopyCanInspect(currentCopyState,
                                    desktopTrackPreferenceCanWrite(currentInput, currentPrepared, false),
                                    currentReopenRequired, true)) {
                                inspectionSubmitted = true
                                currentCopyCallbacks.onInspect(listOf(track.id))
                            }
                        }, enabled = !inspectionSubmitted && desktopCopyCanInspect(copyInspectionState,
                            desktopTrackPreferenceCanWrite(input, prepared, false), reopenRequired, true),
                            modifier = Modifier.semantics {
                                contentDescription = "${DesktopCopyText.InspectTrack.label(language)}: ${track.title}"
                            }) { Text(DesktopCopyText.InspectTrack.label(language)) }
                        DesktopCopyInspectionProgress(copyInspectionState, copyInspectionCallbacks, language, input.isSelectionCurrent)
                        if (desktopCopyAvailability(track, copyInspectionState.results) == DesktopCopyAvailability.UNAVAILABLE) {
                            Text(DesktopCopyText.Reimport.label(language), style = MaterialTheme.typography.bodySmall)
                            OutlinedButton(onClick = {
                                if (isViewCurrent() && !currentImporting && !currentReopenRequired &&
                                    desktopCopyAvailability(track, currentCopyState.results) == DesktopCopyAvailability.UNAVAILABLE) {
                                    currentCallbacks.onImport()
                                }
                            }, enabled = !importing && !reopenRequired,
                                modifier = Modifier.detailAction(DesktopText.Import, track, language)) {
                                Text(DesktopText.Import.inLanguage(language))
                            }
                        }
                        TrackDetailField(DesktopText.CollectionFilter, desktopTrackCollectionStatus(track.collectionState, language), language)
                        if (desktopTrackIsActive(track)) {
                            OutlinedButton(onClick = {
                                if (removal == null && isViewCurrent() &&
                                    desktopTrackPreferenceCanWrite(currentInput, currentPrepared, currentReopenRequired) &&
                                    desktopTrackIsActive(currentPrepared.selectedTrack)) {
                                    removalSubmitted = false
                                    removal = DesktopTrackCollectionRemoval(input, track)
                                }
                            }, enabled = preferenceEnabled && removal == null,
                                modifier = Modifier.detailAction(DesktopText.RemoveTrackFromCollection, track, language)) {
                                Text(DesktopText.RemoveTrackFromCollection.inLanguage(language))
                            }
                        } else {
                            if (track.collectionState != "REMOVED") Text(DesktopText.CollectionUnknownHint.inLanguage(language),
                                style = MaterialTheme.typography.bodySmall)
                            OutlinedButton(onClick = {
                                if (isViewCurrent() && desktopTrackCollectionCanRestore(currentInput, currentPrepared, currentReopenRequired)) {
                                    currentCallbacks.onRestoreTrackToCollection(track.id)
                                }
                            }, enabled = desktopTrackCollectionCanRestore(input, prepared, reopenRequired),
                                modifier = Modifier.detailAction(DesktopText.RestoreTrackToCollection, track, language)) {
                                Text(DesktopText.RestoreTrackToCollection.inLanguage(language))
                            }
                        }
                        TrackDetailField(DesktopText.TrackPreference, desktopTrackPreferenceStatus(track.preference, language), language)
                        val knownPreference = desktopTrackPreference(track.preference)
                        if (knownPreference == null) {
                            Text(DesktopText.TrackPreferenceUnknownHint.inLanguage(language),
                                style = MaterialTheme.typography.bodySmall)
                        }
                        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            DesktopTrackPreference.entries.forEach { preference ->
                                val label = preference.label(language)
                                FilterChip(selected = knownPreference == preference, enabled = preferenceEnabled,
                                    onClick = { setPreference(preference) }, modifier = Modifier.semantics {
                                        contentDescription = "${DesktopText.TrackPreference.inLanguage(language)}: $label; ${track.title}"
                                    }, label = { Text(label) })
                            }
                            OutlinedButton(onClick = { setPreference(DesktopTrackPreference.NEUTRAL) }, enabled = preferenceEnabled,
                                modifier = Modifier.detailAction(DesktopText.ClearTrackPreference, track, language)) {
                                Text(DesktopText.ClearTrackPreference.inLanguage(language))
                            }
                        }
                        Text(DesktopText.TrackPreferenceHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (reopenRequired) {
                    Text(DesktopText.LibraryReopenRequired.inLanguage(language), color = MaterialTheme.colorScheme.error)
                } else if (track != null) {
                    val availability = when {
                        !desktopTrackIsActive(track) -> DesktopText.CollectionNewSelectionUnavailable
                        !engineAvailable -> DesktopText.PlaybackUnavailable
                        isCurrent && (playback.controls as? PlaybackControlGate.Locked)?.reason == PlaybackControlLockReason.SOURCE_UNAVAILABLE -> DesktopText.SourceMissing
                        isCurrent && !playEnabled -> DesktopText.ControlsUnavailable
                        else -> DesktopText.SourceCheckedOnPlay
                    }
                    Text(availability.inLanguage(language))
                    TrackDetailField(DesktopText.Play, (if (playEnabled) DesktopText.ActionAvailable else DesktopText.ActionUnavailable).inLanguage(language), language)
                    TrackDetailField(DesktopText.Enqueue, (if (enqueueEnabled) DesktopText.ActionAvailable else DesktopText.ActionUnavailable).inLanguage(language), language)
                    TrackDetailField(DesktopText.PlayNext, (if (playNextEnabled) DesktopText.ActionAvailable else DesktopText.ActionUnavailable).inLanguage(language), language)
                    if (!queueSupported) Text(DesktopText.UnsupportedMode.inLanguage(language))
                    else if (!queueHasSpace) Text(DesktopText.QueueLimit.inLanguage(language)
                        .replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString()))
                    else if (queue != null && queue.entries.isNotEmpty() && !prepared.queueHasCurrent) Text(DesktopText.QueueEditUnavailable.inLanguage(language))
                }
                // Cancellation remains possible while a new snapshot is being prepared.
                if (!fresh && editDraft != null) {
                    val pendingDraft = editDraft
                    OutlinedButton(onClick = {
                        if (editDraft === pendingDraft && input.isSelectionCurrent()) editDraft = null
                    }) { Text(DesktopText.CancelTrackMetadata.inLanguage(language)) }
                }
                if (track != null) {
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = {
                            if (isViewCurrent() && currentPlayEnabled && !currentReopenRequired) callbacks.onPlayTracks(listOf(track.id), "LIBRARY")
                        }, enabled = playEnabled, modifier = Modifier.detailAction(DesktopText.Play, track, language)) { Text(DesktopText.Play.inLanguage(language)) }
                        OutlinedButton(onClick = {
                            if (isViewCurrent() && currentEnqueueEnabled && !currentReopenRequired) callbacks.onEnqueue(track.id)
                        }, enabled = enqueueEnabled, modifier = Modifier.detailAction(DesktopText.Enqueue, track, language)) { Text(DesktopText.Enqueue.inLanguage(language)) }
                        OutlinedButton(onClick = {
                            if (isViewCurrent() && currentPlayNextEnabled && !currentReopenRequired) callbacks.onPlayNext(track.id)
                        }, enabled = playNextEnabled, modifier = Modifier.detailAction(DesktopText.PlayNext, track, language)) { Text(DesktopText.PlayNext.inLanguage(language)) }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { if (input.isSelectionCurrent()) onClose() }) { Text(DesktopText.Close.inLanguage(language)) } },
    )
}

private fun Modifier.detailAction(action: DesktopText, track: DesktopTrack, language: DesktopLanguage): Modifier = semantics {
    contentDescription = "${action.inLanguage(language)}: ${track.title}"
}

@Composable
private fun TrackDetailField(label: DesktopText, value: String, language: DesktopLanguage) {
    Column {
        Text(label.inLanguage(language), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}
