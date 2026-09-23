package app.autplay.data.local.dao

import androidx.room3.Dao
import androidx.room3.Query
import androidx.room3.Upsert
import app.autplay.data.local.entity.TrackMetadataEntity
import app.autplay.data.local.entity.MetadataArtworkEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface TrackMetadataDao {
    @Upsert suspend fun upsert(row: TrackMetadataEntity)
    @Upsert suspend fun upsertArtwork(row: MetadataArtworkEntity)
    @Query("SELECT * FROM track_metadata_projection WHERE server_profile_id=:profile AND local_user_track_ref_id=:track")
    suspend fun get(profile: String, track: String): TrackMetadataEntity?
    @Query("SELECT * FROM track_metadata_projection WHERE server_profile_id=:profile AND local_user_track_ref_id=:track")
    fun observe(profile: String, track: String): Flow<TrackMetadataEntity?>
    @Query("SELECT COALESCE(SUM(updated_at_ms),0) FROM track_metadata_projection WHERE server_profile_id=:profile")
    fun changes(profile: String): Flow<Long>
    @Query("SELECT * FROM track_metadata_projection WHERE server_profile_id=:profile AND local_user_track_ref_id IN (:tracks)")
    suspend fun forTracks(profile: String, tracks: List<String>): List<TrackMetadataEntity>
    @Query("SELECT * FROM metadata_artwork_cache WHERE server_profile_id=:profile AND sha256=:sha")
    suspend fun artwork(profile: String, sha: String): MetadataArtworkEntity?
    @Query("SELECT a.* FROM metadata_artwork_cache a JOIN track_metadata_projection m ON m.server_profile_id=a.server_profile_id AND m.artwork_sha256=a.sha256 WHERE m.server_profile_id=:profile AND m.local_user_track_ref_id=:track")
    fun observeArtwork(profile: String, track: String): Flow<MetadataArtworkEntity?>
    @Query("SELECT m.* FROM track_metadata_projection m JOIN user_track_ref u ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id LEFT JOIN metadata_artwork_cache a ON a.server_profile_id=m.server_profile_id AND a.sha256=m.artwork_sha256 WHERE m.server_profile_id=:profile AND m.artwork_sha256>=:lowerSha AND m.artwork_sha256<:upperSha AND a.sha256 IS NULL AND u.deleted_at_ms IS NULL AND EXISTS (SELECT 1 FROM library_entry l WHERE l.server_profile_id=m.server_profile_id AND l.local_user_track_ref_id=m.local_user_track_ref_id AND l.removed_at_ms IS NULL) ORDER BY m.local_user_track_ref_id ASC LIMIT :limit OFFSET :offset")
    suspend fun missingArtwork(profile: String, lowerSha: String, upperSha: String, limit: Int, offset: Int = 0): List<TrackMetadataEntity>
    @Query("SELECT COUNT(*) FROM track_metadata_projection m JOIN user_track_ref u ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id LEFT JOIN metadata_artwork_cache a ON a.server_profile_id=m.server_profile_id AND a.sha256=m.artwork_sha256 WHERE m.server_profile_id=:profile AND m.artwork_sha256>=:lowerSha AND m.artwork_sha256<:upperSha AND a.sha256 IS NULL AND u.deleted_at_ms IS NULL AND EXISTS (SELECT 1 FROM library_entry l WHERE l.server_profile_id=m.server_profile_id AND l.local_user_track_ref_id=m.local_user_track_ref_id AND l.removed_at_ms IS NULL)")
    suspend fun missingArtworkCount(profile: String, lowerSha: String, upperSha: String): Int
}
