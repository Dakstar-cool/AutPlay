package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopMetadataState

/** Visibility and completion belong to the host. Callbacks never optimistically remove this dialog. */
@Composable
internal fun DesktopMetadataDialog(state: DesktopMetadataState, callbacks: DesktopMetadataCallbacks,
    input: DesktopTrackInput, view: DesktopTrackView, target: DesktopMetadataTarget?, reopenRequired: Boolean,
    language: DesktopLanguage, onInspect: () -> Unit) {
    if (state.requestId == null && state.preview == null) return
    val currentState by rememberUpdatedState(state)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val currentInput by rememberUpdatedState(input)
    val currentView by rememberUpdatedState(view)
    val currentTarget by rememberUpdatedState(target)
    val currentReopen by rememberUpdatedState(reopenRequired)
    val currentInspect by rememberUpdatedState(onInspect)
    val preview = state.preview
    var applyAdmitted by remember(state.requestId, preview, state.errorCode) { mutableStateOf(false) }
    // Hand the local admission fence over only after the host publishes its applying state.
    SideEffect { if (state.applying) applyAdmitted = false }
    val locked = applyAdmitted || state.applying
    val canApply = !locked && preview != null && target?.canApply(input, view, state, preview, reopenRequired) == true
    val dismiss: () -> Unit = {
        if (!applyAdmitted && !currentState.applying && currentState === state) {
            desktopMetadataCancelId(currentState)?.let { currentCallbacks.onCancel(it) }
                ?: desktopMetadataDismissId(currentState)?.let { currentCallbacks.onDismiss(it) }
        }
    }
    val title = DesktopMetadataText.Preview.label(language)
    AlertDialog(onDismissRequest = dismiss, modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                when {
                    locked -> Text(DesktopMetadataText.Applying.label(language),
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    state.busy -> Text(DesktopMetadataText.Reading.label(language),
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                }
                if (state.errorCode != null) Text((if (state.errorCode == "desktop_metadata_conflict")
                    DesktopMetadataText.Changed else DesktopMetadataText.Failed).label(language), color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                if (preview != null) {
                    Text(desktopMetadataStatus(preview.embedded.status, language))
                    MetadataField(DesktopMetadataText.Format, desktopMetadataFormat(preview.embedded.format, language), language)
                    Text(DesktopMetadataText.Current.label(language), style = MaterialTheme.typography.titleSmall)
                    val track = view.selectedTrack?.takeIf { view.input == input && input.isSelectionCurrent() &&
                        it.id == preview.baseline.trackId && input.selectedId == it.id }
                    val provenance = preview.provenance?.takeIf { it.baseline == preview.baseline }
                    MetadataField(DesktopMetadataText.Title, desktopMetadataSafeText(preview.baseline.title, language), language)
                    MetadataField(DesktopMetadataText.TitleOrigin, desktopMetadataProvenance(provenance?.title, language), language)
                    MetadataField(DesktopMetadataText.Artist, desktopMetadataSafeText(preview.baseline.artist, language), language)
                    MetadataField(DesktopMetadataText.ArtistOrigin, desktopMetadataProvenance(provenance?.artist, language), language)
                    Text(DesktopMetadataText.Embedded.label(language), style = MaterialTheme.typography.titleSmall)
                    MetadataField(DesktopMetadataText.Title, desktopMetadataSafeText(preview.embedded.title, language), language)
                    MetadataField(DesktopMetadataText.Artist, desktopMetadataSafeText(preview.embedded.artist, language), language)
                    MetadataField(DesktopMetadataText.Album, desktopMetadataSafeText(preview.embedded.album, language), language)
                    DesktopArtworkView(preview.baseline.trackId, preview.embedded.artwork, language, 128.dp)
                    Text(DesktopMetadataText.Proposed.label(language), style = MaterialTheme.typography.titleSmall)
                    MetadataField(DesktopMetadataText.Title, desktopMetadataSafeText(preview.proposedTitle, language), language)
                    MetadataField(DesktopMetadataText.TitleOrigin, desktopMetadataProvenance(preview.proposedTitleProvenance, language), language)
                    MetadataField(DesktopMetadataText.Artist, desktopMetadataSafeText(preview.proposedArtist, language), language)
                    MetadataField(DesktopMetadataText.ArtistOrigin, desktopMetadataProvenance(preview.proposedArtistProvenance, language), language)
                    Text(DesktopMetadataText.Hint.label(language), style = MaterialTheme.typography.bodySmall)
                    if (target?.isCurrent(input, view) != true || !desktopMetadataBaselineMatches(preview, track)) {
                        Text(DesktopMetadataText.Changed.label(language))
                    }
                } else if (!state.busy) Text(DesktopMetadataText.Unavailable.label(language))
                if (reopenRequired) Text(DesktopMetadataText.Reopen.label(language), color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = {
                    if (!applyAdmitted && currentState === state &&
                        desktopMetadataCanInspect(currentInput, currentView, currentState, currentReopen)) currentInspect()
                }, enabled = !locked && desktopMetadataCanInspect(input, view, state, reopenRequired)) {
                    Text(DesktopMetadataText.Inspect.label(language))
                }
            }
        },
        confirmButton = {
            if (preview != null) TextButton(onClick = {
                if (!applyAdmitted && currentState === state && currentTarget === target &&
                    currentTarget?.canApply(currentInput, currentView, currentState, preview, currentReopen) == true) {
                    applyAdmitted = true
                    currentCallbacks.onApply(preview.id)
                }
            }, enabled = canApply) { Text(DesktopMetadataText.Apply.label(language)) }
        },
        dismissButton = {
            TextButton(onClick = dismiss, enabled = !locked &&
                (desktopMetadataCancelId(state) != null || desktopMetadataDismissId(state) != null)) {
                Text((if (state.busy) DesktopMetadataText.Cancel else DesktopMetadataText.Dismiss).label(language))
            }
        })
}

@Composable
private fun MetadataField(label: DesktopMetadataText, value: String, language: DesktopLanguage) {
    Column {
        Text(label.label(language), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        // Text has no HTML, URI, Markdown, link annotation or instruction execution semantics.
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}
