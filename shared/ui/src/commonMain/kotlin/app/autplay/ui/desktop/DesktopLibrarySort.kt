package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopTrack

internal enum class DesktopLibrarySort { Title, Artist, Duration }

internal enum class DesktopSortDirection { Ascending, Descending }

/** Common case-insensitive string ordering, without locale/ICU collation or metadata writes. */
internal fun compareDesktopLibraryTracks(
    left: DesktopTrack,
    right: DesktopTrack,
    sort: DesktopLibrarySort,
    direction: DesktopSortDirection,
): Int {
    val leftMissing = when (sort) {
        DesktopLibrarySort.Title -> false
        DesktopLibrarySort.Artist -> left.artist.isBlank()
        DesktopLibrarySort.Duration -> left.durationMs?.let { it < 0 } ?: true
    }
    val rightMissing = when (sort) {
        DesktopLibrarySort.Title -> false
        DesktopLibrarySort.Artist -> right.artist.isBlank()
        DesktopLibrarySort.Duration -> right.durationMs?.let { it < 0 } ?: true
    }
    // Missing values stay last in either direction; equal values always use ascending stable IDs.
    if (leftMissing != rightMissing) return if (leftMissing) 1 else -1
    val first = if (direction == DesktopSortDirection.Ascending) left else right
    val second = if (direction == DesktopSortDirection.Ascending) right else left
    val comparison = if (leftMissing) 0 else when (sort) {
        DesktopLibrarySort.Title -> first.title.compareTo(second.title, ignoreCase = true)
        DesktopLibrarySort.Artist -> first.artist.compareTo(second.artist, ignoreCase = true)
        DesktopLibrarySort.Duration -> requireNotNull(first.durationMs).compareTo(requireNotNull(second.durationMs))
    }
    return if (comparison != 0) comparison else left.id.compareTo(right.id)
}

/** Returns a new ordering of every candidate before the caller applies its display limit. */
internal fun sortDesktopLibraryTracks(
    candidates: List<DesktopTrack>,
    sort: DesktopLibrarySort,
    direction: DesktopSortDirection,
    checkpoint: () -> Unit = {},
): List<DesktopTrack> {
    checkpoint()
    val sorted = mutableListOf<DesktopTrack>()
    candidates.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        sorted += track
    }
    var comparisons = 0
    sorted.sortWith(Comparator { left, right ->
        if (comparisons++ % 256 == 0) checkpoint()
        compareDesktopLibraryTracks(left, right, sort, direction)
    })
    checkpoint()
    return sorted
}
