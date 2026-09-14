package app.autplay.application.search

import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.data.local.AutPlayDatabase

data class VaultSearchResult(
    val remoteLibraryEntryId: String,
    val remoteUserTrackRefId: String,
    val title: String?,
    val artist: String?,
    val source: String,
    val availability: String,
    val localUserTrackRefId: String?,
) {
    val playable: Boolean get() = localUserTrackRefId != null
}

/** Resolves bounded server identities against synced Room metadata without inventing remote fields. */
class VaultSearchProjector(private val database: AutPlayDatabase) {
    suspend fun project(profileId: String, remote: List<RemoteLibraryEntry>): List<VaultSearchResult> {
        val bounded = remote.take(MAX_RESULTS)
        if (bounded.isEmpty()) return emptyList()
        val localByServerId = database.libraryDao()
            .trackRefsByServerIds(profileId, bounded.map(RemoteLibraryEntry::userTrackRefId).distinct(), MAX_RESULTS)
            .associateBy { it.serverUserTrackRefId }
        return bounded.map { row ->
            val local = localByServerId[row.userTrackRefId]
            VaultSearchResult(
                remoteLibraryEntryId = row.libraryEntryId,
                remoteUserTrackRefId = row.userTrackRefId,
                title = local?.rawTitle,
                artist = local?.rawArtist,
                source = row.source,
                availability = row.availabilityStatus,
                localUserTrackRefId = local?.localUserTrackRefId,
            )
        }
    }

    companion object {
        const val MAX_RESULTS: Int = 100
    }
}
