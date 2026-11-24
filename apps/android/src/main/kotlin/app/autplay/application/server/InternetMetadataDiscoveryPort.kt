package app.autplay.application.server

import androidx.room3.withWriteTransaction
import app.autplay.application.sync.remoteLocalId
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity

/** Private catalogue browsing and the existing, separately admitted audio-source search. */
interface InternetMetadataDiscoveryPort {
    suspend fun catalogueTrackReference(contextId: String): CatalogueTrackReference
    suspend fun discoverMusic(query: String, kind: InternetMetadataDiscoveryKind,
        limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun discoveryArtistReleases(id: String, limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun discoveryArtistTracks(id: String, limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun discoveryReleaseTracks(id: String, limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun createMusicCatalogueContext(selection: MusicCatalogueContextRequest): MusicCatalogueContext
    suspend fun searchInternetMusic(query: String, operationId: String, catalogueContextId: String? = null): InternetMusicSearch
}

data class CatalogueTrackReference(val id: String, val rowVersion: Long, val recordingId: String?,
    val title: String, val artist: String?, val album: String?, val durationMs: Long?, val resolutionStatus: String)

internal suspend fun CatalogueTrackReference.localTarget(database: AutPlayDatabase, profile: String): String =
    database.withWriteTransaction {
        val dao = database.libraryDao()
        val existing = dao.trackRefByServerId(profile, id)
        if (existing != null) {
            check(existing.deletedAtMs == null)
            return@withWriteTransaction existing.localUserTrackRefId
        }
        val localId = remoteLocalId(profile, id)
        check(dao.trackRef(localId) == null)
        val now = System.currentTimeMillis()
        dao.upsertTrackRef(UserTrackRefEntity(localId, id, null, recordingId, resolutionStatus, title, artist,
            album, durationMs, null, "CLEAN", rowVersion, 0, now, now, null, profile))
        localId
    }
