package app.autplay

import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.playback.PlaybackPersistenceRepository.Companion.MAX_QUEUE_ENTRIES

/** Keep library order and select the tapped item without discarding its successors. */
internal fun continuationTrackIds(
    selectedTrackId: String,
    queueType: String,
    library: List<CoreLibraryEntrySummary>,
): List<String> {
    if (queueType !in setOf("USER", "LIBRARY")) return listOf(selectedTrackId)
    val ids = library.filterNot { it.removed }.map { it.localUserTrackRefId }.distinct()
    val selectedIndex = ids.indexOf(selectedTrackId)
    if (selectedIndex < 0) return listOf(selectedTrackId) + ids.take(MAX_QUEUE_ENTRIES - 1)
    if (ids.size <= MAX_QUEUE_ENTRIES) return ids
    val start = minOf(selectedIndex, ids.size - MAX_QUEUE_ENTRIES)
    return ids.subList(start, start + MAX_QUEUE_ENTRIES)
}
