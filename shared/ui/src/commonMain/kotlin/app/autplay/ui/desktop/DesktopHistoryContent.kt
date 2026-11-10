package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryState
import app.autplay.application.desktop.DesktopLocalStatisticsState
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopTrack
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** The host owns page requests and rejects retired revisions. No history writes are exposed. */
public data class DesktopHistoryCallbacks(
    val onRefresh: () -> Unit = {},
    val onLoadMore: (Long) -> Unit = {},
    val onRetry: (Long) -> Unit = {},
)

/** O(1) keys: page state and snapshot references, never list equality. */
internal class DesktopHistoryInput(val state: DesktopHistoryState, val library: DesktopLibrarySnapshot?) {
    override fun equals(other: Any?): Boolean = other is DesktopHistoryInput &&
        state === other.state && library === other.library

    override fun hashCode(): Int = state.revision.hashCode()

    fun canRequest(current: DesktopHistoryInput, navigationCurrent: Boolean, reopenRequired: Boolean): Boolean =
        this == current && navigationCurrent && !reopenRequired && !current.state.loading

    fun canLoadMore(current: DesktopHistoryInput, navigationCurrent: Boolean, reopenRequired: Boolean): Boolean =
        canRequest(current, navigationCurrent, reopenRequired) && state.initialized && state.errorCode == null && state.nextCursor != null

    fun canRetry(current: DesktopHistoryInput, navigationCurrent: Boolean, reopenRequired: Boolean): Boolean =
        canRequest(current, navigationCurrent, reopenRequired) && state.errorCode != null
}

internal class DesktopHistoryRow(val event: DesktopListeningEvent, val track: DesktopTrack?, val position: Int)

internal class DesktopHistoryView(val input: DesktopHistoryInput? = null, val rows: List<DesktopHistoryRow> = emptyList())

internal fun desktopHistoryStatus(state: DesktopHistoryState, fresh: Boolean, reopenRequired: Boolean, empty: Boolean): DesktopText? = when {
    reopenRequired -> null // The shell's sticky restart message has priority.
    state.loading || !fresh -> DesktopText.HistoryLoading
    state.errorCode != null -> DesktopText.HistoryFailure
    !state.initialized -> DesktopText.HistoryNotLoaded
    empty -> DesktopText.EmptyHistory
    else -> null
}

internal fun prepareDesktopHistoryView(input: DesktopHistoryInput, checkpoint: () -> Unit = {}): DesktopHistoryView {
    val tracks = mutableMapOf<String, DesktopTrack>()
    input.library?.tracks?.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        tracks[track.id] = track
    }
    val ids = mutableSetOf<String>()
    val rows = mutableListOf<DesktopHistoryRow>()
    input.state.events.take(DesktopHistoryLimits.MAX_VISIBLE_EVENTS).forEachIndexed { index, event ->
        if (index % 256 == 0) checkpoint()
        if (ids.add(event.id)) rows += DesktopHistoryRow(event, tracks[event.trackId], index)
    }
    checkpoint()
    return DesktopHistoryView(input, rows)
}

@Composable
@NonSkippableComposable
internal fun DesktopHistoryContent(
    state: DesktopHistoryState,
    library: DesktopLibrarySnapshot?,
    language: DesktopLanguage,
    callbacks: DesktopHistoryCallbacks,
    libraryCallbacks: DesktopLibraryCallbacks,
    newPlaybackAvailable: Boolean,
    reopenRequired: Boolean,
    isNavigationCurrent: () -> Boolean,
    onTrackDetails: (String) -> Unit,
    errorCode: String? = null,
    modifier: Modifier = Modifier,
    localStatisticsState: DesktopLocalStatisticsState = DesktopLocalStatisticsState(),
    localStatisticsCallbacks: DesktopLocalStatisticsCallbacks = DesktopLocalStatisticsCallbacks(),
) {
    val input = DesktopHistoryInput(state, library)
    val currentInput by rememberUpdatedState(input)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val currentLibraryCallbacks by rememberUpdatedState(libraryCallbacks)
    val currentReopen by rememberUpdatedState(reopenRequired)
    val currentPlaybackAvailable by rememberUpdatedState(newPlaybackAvailable)
    val view by produceState(DesktopHistoryView(), input) {
        value = withContext(Dispatchers.Default) {
            val context = currentCoroutineContext()
            prepareDesktopHistoryView(input) { context.ensureActive() }
        }
    }
    val prepared = view
    val currentPrepared by rememberUpdatedState(prepared)
    val fresh = prepared.input == input
    val isViewCurrent: () -> Boolean = {
        isNavigationCurrent() && currentPrepared === prepared && prepared.input == currentInput
    }
    LazyColumn(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item(key = "local-statistics") {
            DesktopLocalStatisticsControls(localStatisticsState, localStatisticsCallbacks, language,
                blocked = reopenRequired || library == null, isNavigationCurrent = isNavigationCurrent)
        }
        item(key = "history-controls") {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = {
                        if (input.canRequest(currentInput, isNavigationCurrent(), currentReopen)) currentCallbacks.onRefresh()
                    }, enabled = !state.loading && !reopenRequired) { Text(DesktopText.HistoryRefresh.inLanguage(language)) }
                    OutlinedButton(onClick = {
                        if (input.canLoadMore(currentInput, isNavigationCurrent(), currentReopen)) currentCallbacks.onLoadMore(input.state.revision)
                    }, enabled = input.canLoadMore(input, true, reopenRequired)) { Text(DesktopText.HistoryLoadMore.inLanguage(language)) }
                    if (state.errorCode != null) OutlinedButton(onClick = {
                        if (input.canRetry(currentInput, isNavigationCurrent(), currentReopen)) currentCallbacks.onRetry(input.state.revision)
                    }, enabled = input.canRetry(input, true, reopenRequired)) { Text(DesktopText.HistoryRetry.inLanguage(language)) }
                }
                if (!reopenRequired) errorCode?.let {
                    Text(desktopErrorMessage(it, language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                }
                val status = desktopHistoryStatus(state, fresh, reopenRequired, prepared.rows.isEmpty())
                status?.let { Text(it.inLanguage(language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) }
                Text(DesktopText.HistoryWindow.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                if (state.newerItemsHidden) Text(DesktopText.HistoryNewerHidden.inLanguage(language))
                Text(DesktopText.HistoryPlayHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                Text(DesktopText.TasteHistoryRetained.inLanguage(language), style = MaterialTheme.typography.bodySmall)
            }
        }
        items(if (fresh) prepared.rows else emptyList(), key = { "history-event:${it.event.id}" }) { row ->
            val track = row.track
            Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(track?.title ?: DesktopText.TrackUnavailable.inLanguage(language), maxLines = 1, overflow = TextOverflow.Ellipsis)
                track?.let {
                    Text(it.artist, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (!desktopTrackIsActive(it)) Text(desktopTrackCollectionStatus(it.collectionState, language))
                }
                Text("${DesktopText.Played.inLanguage(language)}: ${formatPlaybackTime(row.event.playedMs)}" +
                    if (!row.event.finalized) " · ${DesktopText.ActiveSession.inLanguage(language)}" else "")
                Text(desktopHistoryTasteAnnotation(row.event, language), style = MaterialTheme.typography.bodySmall)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { if (isViewCurrent()) onTrackDetails(row.event.trackId) },
                        modifier = Modifier.historyAction(DesktopText.TrackDetails, row, language)) { Text(DesktopText.TrackDetails.inLanguage(language)) }
                    OutlinedButton(onClick = {
                        if (isViewCurrent() && !currentReopen && currentPlaybackAvailable && desktopTrackIsActive(track)) {
                            currentLibraryCallbacks.onPlayTracks(listOf(row.event.trackId), "LIBRARY")
                        }
                    }, enabled = !reopenRequired && newPlaybackAvailable && desktopTrackIsActive(track),
                        modifier = Modifier.historyAction(DesktopText.Play, row, language)) { Text(DesktopText.Play.inLanguage(language)) }
                }
            }
            HorizontalDivider()
        }
    }
}

private fun Modifier.historyAction(action: DesktopText, row: DesktopHistoryRow, language: DesktopLanguage): Modifier = semantics {
    val eventLabel = DesktopText.HistoryEventPosition.inLanguage(language).replace("{position}", (row.position + 1).toString())
    contentDescription = "${action.inLanguage(language)}: ${row.track?.title ?: DesktopText.Unknown.inLanguage(language)}; $eventLabel"
}
