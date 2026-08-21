package app.autplay.application.search

import app.autplay.data.local.AutPlayDatabase

data class LocalTrackSearchResult(
    val localUserTrackRefId: String,
    val rawTitle: String?,
    val rawArtist: String?,
)

/** Bounded product search projection; raw queries never reach SQL and empty input never scans. */
class LocalTrackSearchRepository(
    private val database: AutPlayDatabase,
    private val queryBuilder: SafeFtsQueryBuilder = SafeFtsQueryBuilder(),
) {
    suspend fun search(rawQuery: String, profileId: String? = null, limit: Int = DEFAULT_LIMIT): List<LocalTrackSearchResult> {
        require(limit in 1..MAX_LIMIT)
        val match = queryBuilder.build(rawQuery) ?: return emptyList()
        val ids = profileId?.let { database.searchDao().searchForProfile(match, it, limit) }
            ?: database.searchDao().searchLegacy(match, limit)
        if (ids.isEmpty()) return emptyList()
        val records = database.libraryDao().trackRefs(ids, limit).associateBy { it.localUserTrackRefId }
        // FTS order is the ranking contract; an IN query must not be allowed to change it.
        return ids.mapNotNull(records::get).map { track ->
            LocalTrackSearchResult(
                localUserTrackRefId = track.localUserTrackRefId,
                rawTitle = track.rawTitle,
                rawArtist = track.rawArtist,
            )
        }
    }

    private companion object {
        const val DEFAULT_LIMIT = 50
        const val MAX_LIMIT = 200
    }
}
