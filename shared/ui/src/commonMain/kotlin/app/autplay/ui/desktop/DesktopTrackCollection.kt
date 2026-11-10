package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopCopyInspectionLimits
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.matches

/** Explicit host commands contain track IDs or an exact host-issued request ID, never paths. */
public data class DesktopCopyInspectionCallbacks(
    val onInspect: (List<String>) -> Unit = {},
    val onCancel: (String) -> Unit = {},
)

internal enum class DesktopCopyAvailability { PRESENT, UNAVAILABLE, UNKNOWN }

/** Evidence is advisory and process-local; it never grants or refuses playback capability. */
internal fun desktopCopyAvailability(track: DesktopTrack, results: Map<String, DesktopCopyInspection>): DesktopCopyAvailability =
    when (results[track.id]?.takeIf { it.matches(track) }?.status) {
        "PRESENT" -> DesktopCopyAvailability.PRESENT
        "UNAVAILABLE" -> DesktopCopyAvailability.UNAVAILABLE
        else -> DesktopCopyAvailability.UNKNOWN
    }

internal enum class DesktopCopyAvailabilityFilter {
    ALL, PRESENT, UNAVAILABLE, UNKNOWN;

    fun matches(value: DesktopCopyAvailability): Boolean = when (this) {
        ALL -> true
        PRESENT -> value == DesktopCopyAvailability.PRESENT
        UNAVAILABLE -> value == DesktopCopyAvailability.UNAVAILABLE
        UNKNOWN -> value == DesktopCopyAvailability.UNKNOWN
    }

    fun label(language: DesktopLanguage): String = when (this) {
        ALL -> DesktopCopyText.All
        PRESENT -> DesktopCopyText.Present
        UNAVAILABLE -> DesktopCopyText.Unavailable
        UNKNOWN -> DesktopCopyText.Unknown
    }.label(language)
}

internal fun desktopCopyStatus(track: DesktopTrack, results: Map<String, DesktopCopyInspection>, language: DesktopLanguage): String =
    when (desktopCopyAvailability(track, results)) {
        DesktopCopyAvailability.PRESENT -> DesktopCopyText.Present
        DesktopCopyAvailability.UNAVAILABLE -> DesktopCopyText.Unavailable
        DesktopCopyAvailability.UNKNOWN -> DesktopCopyText.Unknown
    }.label(language)

/** Only the first bounded window of the prepared, currently displayed ordering is inspected. */
internal fun prepareDesktopCopyInspectionIds(tracks: List<DesktopTrack>, checkpoint: () -> Unit = {}): List<String> {
    val ids = mutableListOf<String>()
    tracks.take(DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS).forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        ids += track.id
    }
    checkpoint()
    return ids.distinct()
}

internal fun desktopCopyCanInspect(state: DesktopCopyInspectionState, fresh: Boolean, reopenRequired: Boolean,
    hasTracks: Boolean): Boolean = fresh && !reopenRequired && !state.busy && hasTracks

internal fun desktopCopyCanCancel(requestId: String?, state: DesktopCopyInspectionState, current: Boolean): Boolean =
    current && requestId != null && state.busy && state.requestId == requestId

internal fun desktopCopyProgress(state: DesktopCopyInspectionState, language: DesktopLanguage): String {
    val requested = state.requested.coerceIn(0, DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS)
    return DesktopCopyText.Progress.label(language).replace("{completed}", state.completed.coerceIn(0, requested).toString())
        .replace("{requested}", requested.toString())
}

/** Cancellation survives progress updates, but cannot target a replacement request or old navigation. */
@Composable
internal fun DesktopCopyInspectionProgress(state: DesktopCopyInspectionState, callbacks: DesktopCopyInspectionCallbacks,
    language: DesktopLanguage, isCurrent: () -> Boolean) {
    val currentState by rememberUpdatedState(state)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val requestId = state.requestId
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (state.busy || requestId != null || state.requested > 0 || state.completed > 0) Text(desktopCopyProgress(state, language),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        if (desktopCopyCanCancel(requestId, state, isCurrent())) {
            OutlinedButton(onClick = {
                if (desktopCopyCanCancel(requestId, currentState, isCurrent())) requestId?.let(currentCallbacks.onCancel)
            }) { Text(DesktopCopyText.Cancel.label(language)) }
        }
        state.errorCode?.let {
            // Never render arbitrary adapter messages, paths or future error strings.
            Text(DesktopCopyText.Failed.label(language), color = MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
    }
}

internal enum class DesktopCopyText(val russian: String, val english: String) {
    Observation("Последнее наблюдение управляемой копии", "Last managed-copy observation"),
    All("Все наблюдения", "All observations"),
    Present("Копия наблюдалась доступной", "Copy observed present"),
    Unavailable("Копия наблюдалась недоступной", "Copy observed unavailable"),
    Unknown("Неизвестно / не проверено", "Unknown / not inspected"),
    Hint("Последняя явная проверка атрибутов и размера — только подсказка этого запуска. Она не проверяет хеш или декодирование; при воспроизведении доступность проверяется заново.",
        "The last explicit attribute/size observation is only a hint for this process. It proves neither hash integrity nor decoding; playback checks actual availability again."),
    InspectShown("Проверить показанные треки по порядку (до {limit})", "Inspect shown tracks in order (up to {limit})"),
    InspectTrack("Проверить управляемую копию", "Inspect managed copy"),
    Progress("Проверка копий: {completed} из {requested} запрошенных", "Copy inspection: {completed} of {requested} requested"),
    Cancel("Отменить проверку копий", "Cancel copy inspection"),
    Failed("Проверка не завершена. Последние наблюдения могут быть устаревшими; повторите проверку явно.",
        "Inspection did not complete. Last observations may be stale; explicitly inspect again."),
    Reimport("Для восстановления используйте «Импорт» и явно выберите файл с теми же байтами. Проверка не удаляет треки и не восстанавливает файлы.",
        "To restore the copy, use Import and explicitly choose a file with the same bytes. Inspection does not remove tracks or repair files."),
    ;

    fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.Russian -> russian
        DesktopLanguage.English -> english
    }
}

/** Membership is exact; unsupported persisted strings never admit a new playback selection. */
internal fun desktopTrackIsActive(track: DesktopTrack?): Boolean = track?.collectionState == "ACTIVE"

/** Process-local view state; only ALL includes unsupported future membership values. */
internal enum class DesktopTrackCollectionFilter {
    ACTIVE, REMOVED, ALL;

    fun matches(value: String): Boolean = when (this) {
        ACTIVE -> value == "ACTIVE"
        REMOVED -> value == "REMOVED"
        ALL -> true
    }

    fun label(language: DesktopLanguage): String = when (this) {
        ACTIVE -> DesktopText.CollectionActive
        REMOVED -> DesktopText.CollectionRemoved
        ALL -> DesktopText.CollectionAll
    }.inLanguage(language)
}

/** Raw membership strings never enter visible copy or accessibility labels. */
internal fun desktopTrackCollectionStatus(value: String, language: DesktopLanguage): String = when (value) {
    "ACTIVE" -> DesktopText.CollectionActive
    "REMOVED" -> DesktopText.CollectionRemoved
    else -> DesktopText.CollectionUnknown
}.inLanguage(language)

internal fun desktopTrackCollectionCanRestore(
    input: DesktopTrackInput,
    prepared: DesktopTrackView,
    reopenRequired: Boolean,
): Boolean = desktopTrackPreferenceCanWrite(input, prepared, reopenRequired) &&
    !desktopTrackIsActive(prepared.selectedTrack)

/** A confirmation belongs to one confirmed snapshot and one opening of the details dialog. */
internal class DesktopTrackCollectionRemoval(
    val input: DesktopTrackInput,
    val track: DesktopTrack,
) {
    fun canConfirm(current: DesktopTrackInput, prepared: DesktopTrackView, reopenRequired: Boolean): Boolean =
        input == current && desktopTrackPreferenceCanWrite(current, prepared, reopenRequired) &&
            prepared.selectedTrack?.id == track.id && desktopTrackIsActive(track) &&
            desktopTrackIsActive(prepared.selectedTrack)
}

internal class DesktopPlaylistCollectionSelection(
    val allActive: Boolean = false,
    val playIds: List<String> = emptyList(),
)

internal class DesktopLibraryCollectionSelection(
    val playIds: List<String>,
    val positions: Map<String, Int>,
    val overflow: Boolean = false,
)

/** Whole-result selection stores at most the queue limit; overflow refuses the entire command. */
internal fun prepareDesktopLibraryResultSelection(
    tracks: List<DesktopTrack>,
    checkpoint: () -> Unit = {},
): DesktopLibraryCollectionSelection {
    checkpoint()
    val ids = mutableListOf<String>()
    val positions = mutableMapOf<String, Int>()
    tracks.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        if (desktopTrackIsActive(track)) {
            if (ids.size == DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
                checkpoint()
                return DesktopLibraryCollectionSelection(emptyList(), emptyMap(), overflow = true)
            }
            positions[track.id] = ids.size
            ids += track.id
        }
    }
    checkpoint()
    return DesktopLibraryCollectionSelection(ids.toList(), positions.toMap())
}

/** Visible retained rows stay readable; only active rows receive positions in a fresh selection. */
internal fun prepareDesktopLibraryCollectionSelection(
    tracks: List<DesktopTrack>,
    checkpoint: () -> Unit = {},
): DesktopLibraryCollectionSelection {
    val ids = mutableListOf<String>()
    val positions = mutableMapOf<String, Int>()
    tracks.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        if (desktopTrackIsActive(track)) {
            positions[track.id] = ids.size
            ids += track.id
        }
    }
    checkpoint()
    return DesktopLibraryCollectionSelection(ids.toList(), positions.toMap())
}

/** Exact current page or suffix only; a missing/non-active reference refuses the entire remainder. */
internal fun prepareDesktopPlaylistPageCollectionSelection(
    playIds: List<String>,
    tracksById: Map<String, DesktopTrack>,
    startIndex: Int = 0,
    checkpoint: () -> Unit = {},
): DesktopPlaylistCollectionSelection {
    checkpoint()
    if (playIds.size > DESKTOP_BROWSE_PAGE_SIZE || startIndex !in playIds.indices) {
        return DesktopPlaylistCollectionSelection()
    }
    val ids = mutableListOf<String>()
    var allActive = true
    for (index in startIndex until playIds.size) {
        if (index % 256 == 0) checkpoint()
        val id = playIds[index]
        if (!desktopTrackIsActive(tracksById[id])) allActive = false
        ids += id
    }
    checkpoint()
    return DesktopPlaylistCollectionSelection(allActive, if (allActive) ids.toList() else emptyList())
}

/** Inspect every reference before the row cap. Reject the whole request, preserving duplicates. */
internal fun prepareDesktopPlaylistCollectionSelection(
    playlist: DesktopPlaylist?,
    tracksById: Map<String, DesktopTrack>,
    checkpoint: () -> Unit = {},
): DesktopPlaylistCollectionSelection {
    checkpoint()
    val entries = playlist?.entries ?: return DesktopPlaylistCollectionSelection()
    var allActive = entries.isNotEmpty()
    val ids = mutableListOf<String>()
    val fits = entries.size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES
    entries.forEachIndexed { index, entry ->
        if (index % 256 == 0) checkpoint()
        if (!desktopTrackIsActive(tracksById[entry.trackId])) allActive = false
        if (fits) ids += entry.trackId
    }
    checkpoint()
    return DesktopPlaylistCollectionSelection(allActive, if (allActive && fits) ids.toList() else emptyList())
}
