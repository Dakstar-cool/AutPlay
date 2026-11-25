package app.autplay.application.search

import app.autplay.data.local.AutPlayDatabase
import androidx.room3.withReadTransaction
import app.autplay.application.library.names
import app.autplay.application.library.decoded
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf

data class LocalTrackSearchResult(
    val localUserTrackRefId: String,
    val rawTitle: String?,
    val rawArtist: String?,
    val serverUserTrackRefId: String? = null,
)

/** Bounded product search projection; raw queries never reach SQL and empty input never scans. */
class LocalTrackSearchRepository(
    private val database: AutPlayDatabase,
    private val queryBuilder: SafeFtsQueryBuilder = SafeFtsQueryBuilder(),
) {
    fun offlineTrackIds(profileId: String?, trackIds: List<String>): Flow<List<String>> =
        if (trackIds.isEmpty()) flowOf(emptyList())
        else {
            val pages = trackIds.chunked(500).map { database.searchDao().offlineTrackIds(profileId ?: "legacy-unscoped", it) }
            if (pages.size == 1) pages.single() else kotlinx.coroutines.flow.combine(pages) { it.flatMap { page -> page } }
        }

    suspend fun search(rawQuery: String, profileId: String? = null, limit: Int = DEFAULT_LIMIT,
        kind: LibrarySearchKind = LibrarySearchKind.All): List<LocalTrackSearchResult> = database.withReadTransaction {
        require(limit in 1..MAX_LIMIT)
        val match = queryBuilder.build(rawQuery, kind) ?: return@withReadTransaction emptyList()
        val ids = profileId?.let { database.searchDao().searchForProfile(match, it, limit) }
            ?: database.searchDao().searchLegacy(match, limit)
        val records = database.libraryDao().trackRefs(ids, limit).associateBy { it.localUserTrackRefId }
        val profile = profileId ?: "legacy-unscoped"
        val credited = if (kind == LibrarySearchKind.Artist || kind == LibrarySearchKind.All) {
            val prefix = rawQuery.take(256).trim().replace("!", "!!").replace("%", "!%").replace("_", "!_")
            database.catalogProjectionDao().tracksForArtistName(profile, "$prefix%", limit)
        } else emptyList()
        val ordered = (ids.mapNotNull(records::get) + credited)
            .distinctBy { it.serverRecordingId ?: it.localUserTrackRefId }.take(limit)
        if (ordered.isEmpty()) return@withReadTransaction emptyList()
        val metadata = database.trackMetadataDao().forTracks(profile, ordered.map { it.localUserTrackRefId })
            .associateBy { it.localUserTrackRefId }
        return@withReadTransaction ordered.map { track ->
            val effective = metadata[track.localUserTrackRefId]?.decoded()
            val names = effective.names(track.rawTitle, track.rawArtist)
            LocalTrackSearchResult(
                localUserTrackRefId = track.localUserTrackRefId,
                serverUserTrackRefId = track.serverUserTrackRefId,
                rawTitle = names.title,
                rawArtist = names.artist,
            )
        }
    }

    private companion object {
        const val DEFAULT_LIMIT = 50
        const val MAX_LIMIT = 200
    }
}
