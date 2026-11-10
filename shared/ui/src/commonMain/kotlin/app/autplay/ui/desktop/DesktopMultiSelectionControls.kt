package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopLocalLimits

internal enum class DesktopMultiSelectionNotice { FILTER_CLEARED, LIMIT_REJECTED, SUBMITTED }

internal data class DesktopMultiSelectionFilter(
    val query: String,
    val collection: DesktopTrackCollectionFilter,
    val preference: DesktopTrackPreferenceFilter,
    val copy: DesktopCopyAvailabilityFilter,
)

@Composable
internal fun DesktopMultiSelectionControls(
    selection: DesktopMultiSelection,
    prepared: DesktopMultiSelectionView?,
    admission: DesktopMultiSelectionAdmission,
    language: DesktopLanguage,
    fresh: Boolean,
    notice: DesktopMultiSelectionNotice?,
    selectPage: () -> Unit,
    deselectPage: () -> Unit,
    clear: () -> Unit,
    deselectInvalid: (String) -> Unit,
    play: () -> Unit,
    enqueue: () -> Unit,
    playNext: () -> Unit,
    append: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(DesktopText.MultiSelectionCount.inLanguage(language).replace("{count}", selection.ids.size.toString())
            .replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString()),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Text(DesktopText.MultiSelectionOrder.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = selectPage, enabled = fresh && prepared?.activePageIds?.isNotEmpty() == true) {
                Text(DesktopText.SelectActivePage.inLanguage(language))
            }
            OutlinedButton(onClick = deselectPage, enabled = fresh && prepared?.shownIds?.isNotEmpty() == true && selection.ids.isNotEmpty()) {
                Text(DesktopText.DeselectPage.inLanguage(language))
            }
            OutlinedButton(onClick = clear, enabled = selection.ids.isNotEmpty()) { Text(DesktopText.ClearSelection.inLanguage(language)) }
        }
        if (prepared != null) {
            Text(DesktopText.MultiSelectionInvalidCount.inLanguage(language).replace("{count}", prepared.invalidIds.size.toString()),
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            if (prepared.invalidIds.isNotEmpty()) {
                Text(DesktopText.MultiSelectionInvalid.inLanguage(language), color = MaterialTheme.colorScheme.error)
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(prepared.invalidRows, key = { it.id }) { row ->
                        OutlinedButton(onClick = { deselectInvalid(row.id) }) {
                            Text("${DesktopText.DeselectTrack.inLanguage(language)}: ${row.label(language)}")
                        }
                    }
                }
            }
        }
        if (notice != null) Text(when (notice) {
            DesktopMultiSelectionNotice.FILTER_CLEARED -> DesktopText.MultiSelectionFilterCleared
            DesktopMultiSelectionNotice.LIMIT_REJECTED -> DesktopText.MultiSelectionLimitRejected
            DesktopMultiSelectionNotice.SUBMITTED -> DesktopText.MultiSelectionSubmitted
        }.inLanguage(language).replace("{limit}", DesktopLocalLimits.MAX_QUEUE_ENTRIES.toString()),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = play, enabled = admission.play) { Text(DesktopText.PlaySelected.inLanguage(language)) }
            OutlinedButton(onClick = enqueue, enabled = admission.enqueue) { Text(DesktopText.EnqueueSelected.inLanguage(language)) }
            OutlinedButton(onClick = playNext, enabled = admission.playNext) { Text(DesktopText.PlaySelectedNext.inLanguage(language)) }
            OutlinedButton(onClick = append, enabled = admission.append) { Text(DesktopText.AddSelectedToPlaylist.inLanguage(language)) }
        }
    }
}
