package app.autplay

import app.autplay.application.library.CoreLibraryEntrySummary

/** Keep library order and select the tapped item without discarding its successors. */
internal fun continuationTrackIds(
    selectedTrackId: String,
    queueType: String,
    library: List<CoreLibraryEntrySummary>,
): List<String> {
    if (queueType !in setOf("USER", "LIBRARY")) return listOf(selectedTrackId)
    val ids = library.filterNot { it.removed }.map { it.localUserTrackRefId }.distinct()
    return if (selectedTrackId in ids) ids else listOf(selectedTrackId) + ids
}
