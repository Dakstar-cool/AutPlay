package app.autplay.application.search

import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.data.local.AutPlayDatabase
import androidx.room3.withReadTransaction
import androidx.room3.withWriteTransaction
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.application.sync.remoteLocalId
import app.autplay.application.library.decoded

data class VaultSearchResult(
    val remoteLibraryEntryId: String,
    val remoteUserTrackRefId: String,
    val title: String?,
    val artist: String?,
    val source: String,
    val availability: String,
    val localUserTrackRefId: String?,
    val catalogId: String? = null,
    val recordingId: String? = null,
    val durationMs: Long? = null,
) {
    val playable: Boolean get() = availability == "VAULT" || (localUserTrackRefId != null && availability == "LOCAL")
}

/** Prefer effective metadata from this profile and fall back to the authenticated search response. */
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
                title = if (effective == null) local?.rawTitle ?: row.title else effective.text("title", local.rawTitle),
                artist = if (effective == null) local?.rawArtist ?: row.artist else effective.text("artist", local.rawArtist),
                catalogId = row.catalogId,
                recordingId = row.recordingId,
                durationMs = local?.rawDurationMs ?: row.durationMs,
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

    /** Store just the selected queue's metadata. Collection membership and downloads have separate lifecycles. */
    suspend fun playbackRows(profile: String, rows: List<VaultSearchResult>, granted: Set<String>): List<Pair<String, String>> =
        database.withWriteTransaction {
            val library = database.libraryDao()
            val now = System.currentTimeMillis()
            rows.mapNotNull { row ->
                val serverId = row.remoteUserTrackRefId
                val stored = library.trackRefByServerId(profile, serverId)
                if (stored != null) {
                    return@mapNotNull if (stored.deletedAtMs == null) row.remoteLibraryEntryId to stored.localUserTrackRefId else null
                }
                if (row.availability != "VAULT" || (row.catalogId != null && serverId !in granted)) return@mapNotNull null
                val localId = remoteLocalId(profile, serverId)
                check(library.trackRef(localId) == null)
                library.upsertTrackRef(UserTrackRefEntity(
                    localUserTrackRefId = localId, serverUserTrackRefId = serverId, serverProfileId = profile,
                    localRecordingId = null, serverRecordingId = row.recordingId,
                    resolutionStatus = if (row.recordingId == null) "UNRESOLVED" else "RESOLVED",
                    rawTitle = row.title, rawArtist = row.artist, rawAlbum = null, rawDurationMs = row.durationMs,
                    resolutionConfidence = null, syncState = "CLEAN", serverRowVersion = null, lastLocalSequence = 0,
                    createdAtMs = now, updatedAtMs = now, deletedAtMs = null,
                ))
                row.remoteLibraryEntryId to localId
            }
        }

    companion object {
        const val MAX_RESULTS: Int = 100
    }
}
