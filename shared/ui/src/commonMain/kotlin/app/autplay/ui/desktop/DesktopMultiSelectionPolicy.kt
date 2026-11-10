package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopTrack

/** Navigation events retire selection even when an intermediate route was never composed. */
internal data class DesktopMultiSelectionScope(val section: DesktopSection, val navigationRevision: Long)

/** Process-local ordered IDs. Every change creates a new identity, including clear and submission. */
internal class DesktopMultiSelection private constructor(val ids: List<String>, val revision: Long) {
    constructor() : this(emptyList(), 0)

    fun setSelected(id: String, selected: Boolean, active: Boolean): DesktopMultiSelection {
        if (!selected) return if (id in ids) DesktopMultiSelection(ids.filterNot { it == id }, revision + 1) else this
        if (id in ids || !active || ids.size == DesktopLocalLimits.MAX_QUEUE_ENTRIES) return this
        return DesktopMultiSelection(ids + id, revision + 1)
    }

    /** Page IDs are the bounded ACTIVE rows, in displayed order. Overflow rejects the entire page. */
    fun selectPage(activeIds: List<String>): DesktopMultiSelection {
        if (activeIds.size > DESKTOP_BROWSE_PAGE_SIZE) return this
        val additions = activeIds.distinct().filterNot { it in ids }
        if (additions.isEmpty() || additions.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES - ids.size) return this
        return DesktopMultiSelection(ids + additions, revision + 1)
    }

    fun pageFits(activeIds: List<String>): Boolean = activeIds.size <= DESKTOP_BROWSE_PAGE_SIZE &&
        activeIds.distinct().count { it !in ids } <= DesktopLocalLimits.MAX_QUEUE_ENTRIES - ids.size

    fun deselectPage(shownIds: List<String>): DesktopMultiSelection {
        if (shownIds.size > DESKTOP_BROWSE_PAGE_SIZE) return this
        val remaining = ids.filterNot { it in shownIds }
        return if (remaining.size == ids.size) this else DesktopMultiSelection(remaining, revision + 1)
    }

    fun clear(): DesktopMultiSelection = DesktopMultiSelection(emptyList(), revision + 1)

    /** Retain IDs after a Unit callback; a new explicit command requires a freshly prepared revision. */
    fun retireIntent(): DesktopMultiSelection = DesktopMultiSelection(ids.toList(), revision + 1)
}

internal class DesktopMultiSelectionView(
    val selection: DesktopMultiSelection,
    val selectedIds: Set<String>,
    val invalidIds: List<String>,
    val shownIds: List<String>,
    val activePageIds: List<String>,
    val pageFits: Boolean,
    val invalidRows: List<DesktopMultiSelectionInvalidRow>,
) {
    val allActive: Boolean get() = selection.ids.isNotEmpty() && invalidIds.isEmpty()
}

/** IDs are command identities only; display text is bounded and prepared for both languages. */
internal class DesktopMultiSelectionInvalidRow(val id: String, val russian: String, val english: String) {
    fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.Russian -> russian
        DesktopLanguage.English -> english
    }
}

private fun desktopMultiSelectionSafeTitle(title: String?): String? = title?.takeIf { value ->
    value.length <= 512 && validDesktopTrackText(value, title = true) && value.none {
        it.isISOControl() || it == '/' || it == '\\' || it == ':' ||
            it in '\u202A'..'\u202E' || it in '\u2066'..'\u2069' ||
            it == '\u200E' || it == '\u200F' || it == '\u061C' || it == '\uFEFF'
    }
}

/** Run with the existing background map/page preparation. Never drop a missing or unknown selected ID. */
internal fun prepareDesktopMultiSelection(
    selection: DesktopMultiSelection,
    byId: Map<String, DesktopTrack>,
    shown: List<DesktopTrack>,
    checkpoint: () -> Unit = {},
): DesktopMultiSelectionView {
    val invalidRows = selection.ids.mapIndexedNotNull { index, id ->
        if (index % 256 == 0) checkpoint()
        val track = byId[id]
        if (track?.let(::desktopTrackIsActive) == true) null else {
            val title = desktopMultiSelectionSafeTitle(track?.title)
            fun fallback(language: DesktopLanguage): String = DesktopText.MultiSelectionUnavailableTrack.inLanguage(language)
                .replace("{position}", (index + 1).toString())
            DesktopMultiSelectionInvalidRow(id, title ?: fallback(DesktopLanguage.Russian),
                title ?: fallback(DesktopLanguage.English))
        }
    }
    val page = shown.take(DESKTOP_BROWSE_PAGE_SIZE)
    val active = page.filter(::desktopTrackIsActive).map { it.id }.distinct()
    checkpoint()
    return DesktopMultiSelectionView(selection, selection.ids.toSet(), invalidRows.map { it.id }, page.map { it.id }, active,
        selection.pageFits(active), invalidRows)
}

internal enum class DesktopMultiSelectionCommand { PLAY, ENQUEUE, PLAY_NEXT, APPEND }

/** The page guard is frozen; the selection is read at each explicit event, before recomposition. */
internal fun desktopMultiSelectionCheckbox(
    id: String,
    active: Boolean,
    isPageCurrent: () -> Boolean,
    latest: () -> DesktopMultiSelection,
    update: (DesktopMultiSelection) -> Unit,
    onLimit: () -> Unit,
): (Boolean) -> Unit = { selected ->
    if (isPageCurrent()) {
        val selection = latest()
        if (selected && active && id !in selection.ids && selection.ids.size == DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
            onLimit()
        } else update(selection.setSelected(id, selected, active))
    }
}

internal class DesktopMultiSelectionAdmission(
    val play: Boolean,
    val enqueue: Boolean,
    val playNext: Boolean,
    val append: Boolean,
) {
    fun allows(command: DesktopMultiSelectionCommand): Boolean = when (command) {
        DesktopMultiSelectionCommand.PLAY -> play
        DesktopMultiSelectionCommand.ENQUEUE -> enqueue
        DesktopMultiSelectionCommand.PLAY_NEXT -> playNext
        DesktopMultiSelectionCommand.APPEND -> append
    }
}

internal fun desktopMultiSelectionAdmission(
    selection: DesktopMultiSelectionView?,
    queueSize: Int,
    queueInsertionEnabled: Boolean,
    queueControlsEnabled: Boolean,
    hasCurrent: Boolean,
    playlist: DesktopPlaylist?,
    fresh: Boolean,
    reopenRequired: Boolean,
): DesktopMultiSelectionAdmission {
    val valid = fresh && !reopenRequired && selection?.allActive == true
    val size = selection?.selection?.ids?.size ?: 0
    val fits = queueSize in 0..DesktopLocalLimits.MAX_QUEUE_ENTRIES &&
        size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES - queueSize
    val enqueue = valid && queueInsertionEnabled && queueControlsEnabled && fits
    return DesktopMultiSelectionAdmission(valid && queueInsertionEnabled, enqueue,
        enqueue && (queueSize == 0 || hasCurrent), valid && playlist != null)
}

/** Single-use captured command input. Identity checks reject equal replacement DTOs and A-B-A revisions. */
internal class DesktopMultiSelectionActionTarget(
    val selection: DesktopMultiSelection,
    private val input: DesktopLibraryInput,
    private val view: Any,
    private val playlist: DesktopPlaylist?,
) {
    private var retired: Boolean = false
    val playlistId: String? get() = playlist?.id

    fun claim(currentSelection: DesktopMultiSelection, currentInput: DesktopLibraryInput, currentView: Any,
        currentPlaylist: DesktopPlaylist?, currentTarget: DesktopMultiSelectionActionTarget?,
        enabled: Boolean, isCurrent: Boolean): Boolean {
        if (retired || currentTarget !== this || currentSelection !== selection || currentInput != input ||
            currentView !== view || currentPlaylist !== playlist || !enabled || !isCurrent) return false
        retired = true
        return true
    }
}

/** Non-composable factory prevents memoization of a closure that captured an equal older owner. */
internal fun desktopMultiSelectionCommand(
    target: DesktopMultiSelectionActionTarget,
    command: DesktopMultiSelectionCommand,
    claim: () -> Boolean,
    retire: () -> Unit,
    callbacks: DesktopLibraryCallbacks,
    queueType: String,
): () -> Unit = {
    if (claim()) {
        // Copy the bounded list before crossing the host's asynchronous boundary.
        val ids = target.selection.ids.toList()
        retire()
        when (command) {
            DesktopMultiSelectionCommand.PLAY -> callbacks.onPlayTracks(ids, queueType)
            DesktopMultiSelectionCommand.ENQUEUE -> callbacks.onEnqueueTracks(ids)
            DesktopMultiSelectionCommand.PLAY_NEXT -> callbacks.onPlayTracksNext(ids)
            DesktopMultiSelectionCommand.APPEND -> target.playlistId?.let { callbacks.onAddTracksToPlaylist(it, ids) }
        }
    }
}
