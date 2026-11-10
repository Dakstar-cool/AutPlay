package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopPlaylistEntry
import app.autplay.application.desktop.DesktopLocalLimits

internal const val DESKTOP_PLAYLIST_NAME_LIMIT: Int = 4096
internal const val DESKTOP_PLAYLIST_DESCRIPTION_LIMIT: Int = 500

/** Nullable storage value; only an exactly empty editor maps to null. */
internal fun desktopPlaylistDescriptionValue(text: String): String? = text.takeUnless { it.isEmpty() }

internal fun validDesktopPlaylistDescription(value: String?): Boolean {
    if (value == null) return true
    if (value.isEmpty() || value.length > DESKTOP_PLAYLIST_DESCRIPTION_LIMIT) return false
    var offset = 0
    while (offset < value.length) {
        val character = value[offset++]
        if (character == '\u0000') return false
        if (character.isHighSurrogate()) {
            if (offset >= value.length || !value[offset++].isLowSurrogate()) return false
        } else if (character.isLowSurrogate()) return false
    }
    return true
}

/** Immutable identities retire captured callbacks after every edit, submit, cancel or reopen. */
internal class DesktopPlaylistDescriptionDraft(
    val playlistId: String,
    val baselineDescription: String?,
    val text: String = baselineDescription.orEmpty(),
    val selectionRevision: Long = 0,
    val isNavigationCurrent: () -> Boolean = { true },
    val submittedInput: DesktopPlaylistInput? = null,
) {
    val description: String? get() = desktopPlaylistDescriptionValue(text)

    fun matches(playlist: DesktopPlaylist?): Boolean = playlist != null && playlist.id == playlistId &&
        playlist.description == baselineDescription

    fun canAct(current: DesktopPlaylistDescriptionDraft?, input: DesktopPlaylistInput,
        reopenRequired: Boolean, navigationCurrent: Boolean): Boolean =
        this === current && !reopenRequired && navigationCurrent && isNavigationCurrent() &&
            input.selectedId == playlistId && input.selectionRevision == selectionRevision

    /** Read-only dismissal remains available even when storage requires a restart. */
    fun canClose(current: DesktopPlaylistDescriptionDraft?, navigationCurrent: Boolean): Boolean =
        this === current && navigationCurrent

    fun canSubmit(playlist: DesktopPlaylist?): Boolean = submittedInput == null && matches(playlist) &&
        description != baselineDescription && validDesktopPlaylistDescription(description)

    fun withText(value: String): DesktopPlaylistDescriptionDraft = DesktopPlaylistDescriptionDraft(
        playlistId, baselineDescription, value, selectionRevision, isNavigationCurrent)

    fun submitted(input: DesktopPlaylistInput): DesktopPlaylistDescriptionDraft = DesktopPlaylistDescriptionDraft(
        playlistId, baselineDescription, text, selectionRevision, isNavigationCurrent, input)

    /** Only a newly published persisted snapshot can confirm the submitted value. */
    fun confirmed(input: DesktopPlaylistInput, playlist: DesktopPlaylist?): Boolean =
        submittedInput != null && submittedInput.library !== input.library && playlist != null && playlist.id == playlistId &&
            playlist.description == description

    /** Explicit review retains every draft character while accepting the currently displayed raw baseline. */
    fun reviewed(input: DesktopPlaylistInput, playlist: DesktopPlaylist,
        navigationCurrent: () -> Boolean): DesktopPlaylistDescriptionDraft = DesktopPlaylistDescriptionDraft(
        playlist.id, playlist.description, text, input.selectionRevision, navigationCurrent)
}

internal fun desktopPlaylistDescriptionErrorText(code: String?, reopenRequired: Boolean): DesktopText? = when {
    reopenRequired || desktopTasteRequiresReopen(code) -> DesktopText.LibraryReopenRequired
    code == null -> null
    code == "desktop_playlist_description_invalid" -> DesktopText.PlaylistDescriptionHint
    code == "desktop_playlist_description_conflict" -> DesktopText.PlaylistDescriptionConflict
    code == "desktop_playlist_missing" -> DesktopText.PlaylistMissing
    else -> DesktopText.PlaylistDescriptionUnconfirmed
}

/** Matches storage validation in UTF-16 code units without normalizing a name. */
internal fun validDesktopPlaylistName(value: String): Boolean {
    if (value.length > DESKTOP_PLAYLIST_NAME_LIMIT || value.isBlank()) return false
    var offset = 0
    while (offset < value.length) {
        val character = value[offset++]
        if (character == '\u0000') return false
        if (character.isHighSurrogate()) {
            if (offset >= value.length || !value[offset++].isLowSurrogate()) return false
        } else if (character.isLowSurrogate()) return false
    }
    return true
}

/** Identity distinguishes cancelled/reopened forms even when their names are identical. */
internal class DesktopPlaylistRenameDraft(
    val playlistId: String,
    val baselineName: String,
    val name: String = baselineName,
) {
    fun matches(playlist: DesktopPlaylist?): Boolean =
        playlist != null && playlist.id == playlistId && playlist.name == baselineName

    fun canSubmit(playlist: DesktopPlaylist?): Boolean =
        matches(playlist) && name != baselineName && validDesktopPlaylistName(name)
}

/** Confirmation covers the displayed aggregate, not media or an already selected queue. */
internal class DesktopPlaylistDeleteTarget(
    val playlistId: String,
    val baselineName: String,
    val baselineCount: Int,
) {
    fun matches(playlist: DesktopPlaylist?): Boolean = playlist != null && playlist.id == playlistId &&
        playlist.name == baselineName && playlist.entries.size == baselineCount
}

/** Full-count admission, independent of the bounded displayed rows; never truncates commands. */
internal class DesktopPlaylistBulkAdmission(
    val hasCapacity: Boolean,
    val enqueue: Boolean,
    val playNext: Boolean,
)

internal fun desktopPlaylistBulkAdmission(
    playlist: DesktopPlaylist?,
    queueCount: Int,
    engineAvailable: Boolean,
    queueSupported: Boolean,
    queueHasCurrent: Boolean,
    fresh: Boolean,
    reopenRequired: Boolean,
): DesktopPlaylistBulkAdmission {
    val count = playlist?.entries?.size ?: 0
    val hasCapacity = queueCount in 0..DesktopLocalLimits.MAX_QUEUE_ENTRIES &&
        count <= DesktopLocalLimits.MAX_QUEUE_ENTRIES - queueCount
    val enqueue = playlist != null && count > 0 && hasCapacity && engineAvailable && queueSupported && fresh && !reopenRequired
    return DesktopPlaylistBulkAdmission(hasCapacity, enqueue, enqueue && (queueCount == 0 || queueHasCurrent))
}

/** Freshness is O(1); revisions reject callbacks from an A/B/A selection or page navigation. */
internal class DesktopPlaylistInput(
    val library: DesktopLibrarySnapshot,
    val selectedId: String?,
    val selectionRevision: Long = 0,
    val query: String = "",
    val choicePage: Int = 0,
    val entryPage: Int = 0,
    val browseRevision: Long = 0,
) {
    fun isCurrent(snapshot: DesktopLibrarySnapshot, selectedId: String?, revision: Long,
        query: String = this.query, choicePage: Int = this.choicePage, entryPage: Int = this.entryPage,
        browseRevision: Long = this.browseRevision): Boolean =
        library === snapshot && this.selectedId == selectedId && selectionRevision == revision &&
            this.query == query && this.choicePage == choicePage && this.entryPage == entryPage &&
            this.browseRevision == browseRevision

    override fun equals(other: Any?): Boolean = other is DesktopPlaylistInput &&
        isCurrent(other.library, other.selectedId, other.selectionRevision, other.query, other.choicePage,
            other.entryPage, other.browseRevision)

    override fun hashCode(): Int {
        var result = selectedId?.hashCode() ?: 0
        result = 31 * result + selectionRevision.hashCode()
        result = 31 * result + query.hashCode()
        result = 31 * result + choicePage
        result = 31 * result + entryPage
        return 31 * result + browseRevision.hashCode()
    }
}

internal class DesktopPlaylistRow(
    val entry: DesktopPlaylistEntry,
    val position: Int,
    val occurrence: Int,
    val canMoveUp: Boolean,
    val canMoveDown: Boolean,
)

/** Lists/maps are constructed once on a background dispatcher and never mutated after publication. */
internal class DesktopPlaylistView(
    val input: DesktopPlaylistInput,
    val choices: List<DesktopPlaylist>,
    val playlist: DesktopPlaylist?,
    val rows: List<DesktopPlaylistRow>,
    val playIds: List<String>,
    val entryPositions: Map<String, Int>,
    val matchCount: Int = choices.size,
    val choicePage: DesktopBrowsePage = desktopBrowsePage(0, 0),
    val entryPage: DesktopBrowsePage = desktopBrowsePage(0, 0),
)

internal fun prepareDesktopPlaylistView(
    input: DesktopPlaylistInput,
    displayLimit: Int,
    checkpoint: () -> Unit = {},
): DesktopPlaylistView = prepareDesktopPlaylistViewInternal(input, displayLimit.coerceAtLeast(0), checkpoint)

/** Product display has independent choice/entry pages; row positions and occurrences stay global. */
internal fun prepareDesktopPagedPlaylistView(
    input: DesktopPlaylistInput,
    matchingIds: Set<String>? = null,
    checkpoint: () -> Unit = {},
): DesktopPlaylistView = prepareDesktopPlaylistViewInternal(input, null, checkpoint, matchingIds)

private fun prepareDesktopPlaylistViewInternal(
    input: DesktopPlaylistInput,
    legacyDisplayLimit: Int?,
    checkpoint: () -> Unit,
    matchingIds: Set<String>? = null,
): DesktopPlaylistView {
    checkpoint()
    val matches = mutableListOf<DesktopPlaylist>()
    val query = desktopSearchQuery(input.query)
    var selected: DesktopPlaylist? = null
    input.library.playlists.forEachIndexed { index, playlist ->
        if (index % 256 == 0) checkpoint()
        // Product search supplies the complete FTS set, including an empty set on failure.
        val matchesQuery = if (matchingIds != null) playlist.id in matchingIds else
            query.isBlank() || playlist.name.contains(query, true) || playlist.description?.contains(query, true) == true
        if (matchesQuery) matches += playlist
        if (playlist.id == input.selectedId) selected = playlist
    }
    var comparisons = 0
    matches.sortWith(Comparator { left, right ->
        if (comparisons++ % 256 == 0) checkpoint()
        val byName = left.name.compareTo(right.name, ignoreCase = true)
        if (byName != 0) byName else left.id.compareTo(right.id)
    })
    val choicePage = desktopBrowsePage(matches.size, input.choicePage)
    val choices = if (legacyDisplayLimit == null) desktopBrowseSlice(matches, choicePage) else matches.take(1_000)
    // Browsing the choice list never silently replaces an existing selection or its stable fallback.
    val playlist = selected ?: matches.firstOrNull()
    val rows = mutableListOf<DesktopPlaylistRow>()
    val playIds = mutableListOf<String>()
    val positions = mutableMapOf<String, Int>()
    val occurrences = mutableMapOf<String, Int>()
    val entries = playlist?.entries.orEmpty()
    val entryPage = desktopBrowsePage(entries.size, input.entryPage)
    val start = if (legacyDisplayLimit == null) entryPage.start else 0
    val end = if (legacyDisplayLimit == null) entryPage.end else minOf(entries.size, legacyDisplayLimit)
    // Count the preceding entries too, so duplicate labels remain global across page boundaries.
    for (position in 0 until end) {
        if (position % 256 == 0) checkpoint()
        val entry = entries[position]
        val occurrence = (occurrences[entry.trackId] ?: 0) + 1
        occurrences[entry.trackId] = occurrence
        if (position < start) continue
        rows += DesktopPlaylistRow(entry, position, occurrence, position > 0, position < entries.lastIndex)
        positions[entry.id] = playIds.size
        playIds += entry.trackId
    }
    checkpoint()
    return DesktopPlaylistView(input, choices.toList(), playlist, rows.toList(), playIds.toList(), positions.toMap(),
        matches.size, choicePage, entryPage)
}

/** Limit UTF-16 input without splitting a supplementary character at the boundary. */
internal fun desktopSearchQuery(value: String): String = value.take(256).let {
    if (it.lastOrNull()?.isHighSurrogate() == true) it.dropLast(1) else it
}
