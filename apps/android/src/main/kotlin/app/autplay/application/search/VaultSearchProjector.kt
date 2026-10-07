package app.autplay.application.search

import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.data.local.AutPlayDatabase
import androidx.room3.withReadTransaction
import app.autplay.application.library.decoded

data class VaultSearchResult(
    val remoteLibraryEntryId: String,
    val remoteUserTrackRefId: String,
    val title: String?,
    val artist: String?,
    val source: String,
    val availability: String,
    val localUserTrackRefId: String?,
) {
    val playable: Boolean get() = localUserTrackRefId != null && availability in setOf("VAULT", "LOCAL")
}

/** Resolves bounded server identities against synced Room metadata without inventing remote fields. */
class VaultSearchProjector(private val database: AutPlayDatabase) {
    suspend fun project(profileId: String, remote: List<RemoteLibraryEntry>): List<VaultSearchResult> = database.withReadTransaction {
        val bounded = remote.take(MAX_RESULTS)
        if (bounded.isEmpty()) return@withReadTransaction emptyList()
        val localByServerId = database.libraryDao()
            .trackRefsByServerIds(profileId, bounded.map(RemoteLibraryEntry::userTrackRefId).distinct(), MAX_RESULTS)
            .associateBy { it.serverUserTrackRefId }
        val localIds = localByServerId.values.map { it.localUserTrackRefId }
        val metadata = if (localIds.isEmpty()) emptyMap() else database.trackMetadataDao().forTracks(profileId, localIds)
            .associateBy { it.localUserTrackRefId }
        bounded.map { row ->
            val local = localByServerId[row.userTrackRefId]
            val effective = local?.let { metadata[it.localUserTrackRefId]?.decoded() }
            VaultSearchResult(
                remoteLibraryEntryId = row.libraryEntryId,
                remoteUserTrackRefId = row.userTrackRefId,
                title = if (effective == null) local?.rawTitle else effective.text("title", local.rawTitle),
                artist = if (effective == null) local?.rawArtist else effective.text("artist", local.rawArtist),
                source = row.source,
                availability = row.availabilityStatus,
                localUserTrackRefId = local?.localUserTrackRefId,
            )
        }.sortedWith(compareBy<VaultSearchResult> { result ->
            when (result.availability) {
                "VAULT" -> 0
                "LOCAL" -> 1
                else -> 2
            }
        })
    }

    companion object {
        const val MAX_RESULTS: Int = 100
    }
}
