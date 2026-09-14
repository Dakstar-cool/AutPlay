package app.autplay.application.history

import app.autplay.application.importing.LEGACY_PROFILE_ID
import app.autplay.data.local.AutPlayDatabase

data class HistoryCursor(val startedAtMs: Long, val listeningEventId: String)

data class HistoryItem(
    val listeningEventId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artist: String?,
    val startedAtMs: Long,
    val playedMs: Long,
    val trackDurationMs: Long?,
    val completionRatio: Double?,
    val excludedFromTaste: Boolean,
)

data class HistoryPage(
    val items: List<HistoryItem>,
    val nextCursor: HistoryCursor?,
)

/** Deterministic keyset pagination over presentation-safe Room history. */
class HistoryRepository(private val database: AutPlayDatabase) {
    suspend fun loadPage(
        profileId: String?,
        cursor: HistoryCursor? = null,
        pageSize: Int = DEFAULT_PAGE_SIZE,
    ): HistoryPage {
        val boundedSize = pageSize.coerceIn(1, MAX_PAGE_SIZE)
        val owner = profileId ?: LEGACY_PROFILE_ID
        val rows = if (cursor == null) {
            database.historyDao().presentationFirstPage(owner, boundedSize + 1)
        } else {
            database.historyDao().presentationNextPage(owner, cursor.startedAtMs, cursor.listeningEventId, boundedSize + 1)
        }
        val pageRows = rows.take(boundedSize)
        val next = pageRows.lastOrNull()?.takeIf { rows.size > boundedSize }?.let {
            HistoryCursor(it.startedAtMs, it.listeningEventId)
        }
        return HistoryPage(
            items = pageRows.map {
                HistoryItem(
                    listeningEventId = it.listeningEventId,
                    localUserTrackRefId = it.localUserTrackRefId,
                    title = it.title,
                    artist = it.artist,
                    startedAtMs = it.startedAtMs,
                    playedMs = it.playedMs,
                    trackDurationMs = it.trackDurationMs,
                    completionRatio = it.completionRatio,
                    excludedFromTaste = it.excludedFromTaste,
                )
            },
            nextCursor = next,
        )
    }

    companion object {
        const val DEFAULT_PAGE_SIZE: Int = 25
        const val MAX_PAGE_SIZE: Int = 100
    }
}
