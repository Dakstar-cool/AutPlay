package app.autplay.application.search

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity

/** Bounded product search projection; raw queries never reach SQL and empty input never scans. */
class LocalTrackSearchRepository(
    private val database: AutPlayDatabase,
    private val queryBuilder: SafeFtsQueryBuilder = SafeFtsQueryBuilder(),
) {
    suspend fun search(rawQuery: String, profileId: String? = null, limit: Int = DEFAULT_LIMIT): List<UserTrackRefEntity> {
        require(limit in 1..MAX_LIMIT)
        val match = queryBuilder.build(rawQuery) ?: return emptyList()
        val ids = profileId?.let { database.searchDao().searchForProfile(match, it, limit) }
            ?: database.searchDao().searchLegacy(match, limit)
        if (ids.isEmpty()) return emptyList()
        val records = database.libraryDao().trackRefs(ids, limit).associateBy { it.localUserTrackRefId }
        // FTS order is the ranking contract; an IN query must not be allowed to change it.
        return ids.mapNotNull(records::get)
    }

    private companion object {
        const val DEFAULT_LIMIT = 50
        const val MAX_LIMIT = 200
    }
}
