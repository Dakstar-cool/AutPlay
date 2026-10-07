package app.autplay.application.search

import app.autplay.data.local.AutPlayDatabase
import androidx.room3.withReadTransaction
import app.autplay.application.library.decoded

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
    suspend fun search(rawQuery: String, profileId: String? = null, limit: Int = DEFAULT_LIMIT,
        kind: LibrarySearchKind = LibrarySearchKind.All): List<LocalTrackSearchResult> = database.withReadTransaction {
        require(limit in 1..MAX_LIMIT)
        val match = queryBuilder.build(rawQuery, kind) ?: return@withReadTransaction emptyList()
        val ids = profileId?.let { database.searchDao().searchForProfile(match, it, limit) }
            ?: database.searchDao().searchLegacy(match, limit)
        if (ids.isEmpty()) return@withReadTransaction emptyList()
        val records = database.libraryDao().trackRefs(ids, limit).associateBy { it.localUserTrackRefId }
        val metadata = database.trackMetadataDao().forTracks(profileId ?: "legacy-unscoped", ids)
            .associateBy { it.localUserTrackRefId }
        // FTS order is the ranking contract; an IN query must not be allowed to change it.
        ids.mapNotNull(records::get).map { track ->
            val effective = metadata[track.localUserTrackRefId]?.decoded()
            LocalTrackSearchResult(
                localUserTrackRefId = track.localUserTrackRefId,
                rawTitle = if (effective == null) track.rawTitle else effective.text("title", track.rawTitle),
                rawArtist = if (effective == null) track.rawArtist else effective.text("artist", track.rawArtist),
            )
        }
    }

    private companion object {
        const val DEFAULT_LIMIT = 50
        const val MAX_LIMIT = 200
    }
}
