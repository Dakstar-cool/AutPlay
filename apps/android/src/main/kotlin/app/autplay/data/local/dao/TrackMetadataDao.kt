package app.autplay.data.local.dao

import androidx.room3.Dao
import androidx.room3.Query
import androidx.room3.Upsert
import app.autplay.data.local.entity.TrackMetadataEntity
import app.autplay.data.local.entity.MetadataArtworkEntity
import kotlinx.coroutines.flow.Flow

data class CoverSource(val sha: String?, val ref: String?, val path: String?, val releaseMbid: String?)

data class ArtistPortraitSource(val ref: String, val image: String)

@Dao
interface TrackMetadataDao {
    @Query("""SELECT local_user_track_ref_id FROM track_metadata_projection WHERE server_profile_id=:profile
        AND local_user_track_ref_id IN (:ids) AND artwork_sha256 IS NOT NULL""")
    suspend fun tracksWithArtwork(profile: String, ids: List<String>): List<String>

    @Query("""SELECT u.server_user_track_ref_id AS ref, json_extract(m.payload_json,'$.catalog_artist_image_v1') AS image
        FROM track_metadata_projection m JOIN user_track_ref u USING(server_profile_id,local_user_track_ref_id)
        WHERE m.server_profile_id=:profile AND u.deleted_at_ms IS NULL AND u.server_user_track_ref_id IS NOT NULL
          AND json_type(m.payload_json,'$.catalog_artist_image_v1')='object'
          AND (m.local_user_track_ref_id IN (:tracks) OR u.server_recording_id IN (
            SELECT l.subject_server_id FROM catalog_artist_credit_link l JOIN artist_credit_name_projection n
              USING(server_profile_id,server_artist_credit_id)
            WHERE l.server_profile_id=:profile AND l.subject_type='RECORDING' AND l.deleted_at_ms IS NULL
              AND l.owner_scope_complete=1 AND n.server_artist_id IN (:artists)
              AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o WHERE o.server_profile_id=l.server_profile_id
                AND o.subject_type=l.subject_type AND o.subject_server_id=l.subject_server_id
                AND o.owner_scope_id=l.owner_scope_id AND o.owner_recording_id=u.server_recording_id)))
        ORDER BY u.server_user_track_ref_id""")
    fun portraitSources(profile: String, tracks: List<String>, artists: List<String>): Flow<List<ArtistPortraitSource>>

    @Query("""SELECT m.artwork_sha256 AS sha, u.server_user_track_ref_id AS ref, a.file_path AS path,
            json_extract(m.payload_json,'$.fields.mb_release_id') AS releaseMbid
        FROM track_metadata_projection m JOIN user_track_ref u USING(server_profile_id,local_user_track_ref_id)
        LEFT JOIN metadata_artwork_cache a ON a.server_profile_id=m.server_profile_id AND a.sha256=m.artwork_sha256
        WHERE m.server_profile_id=:profile AND m.local_user_track_ref_id=:track AND u.deleted_at_ms IS NULL
          AND (m.artwork_sha256 IS NOT NULL OR json_type(m.payload_json,'$.fields.mb_release_id')='text')""")
    fun coverSource(profile: String, track: String): Flow<CoverSource?>

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
    @Query(LIBRARY_MEMBERSHIP + """
        SELECT m.local_user_track_ref_id AS localUserTrackRefId, m.payload_json AS payloadJson,
               u.local_recording_id AS localRecordingId, u.raw_title AS rawTitle,
               u.raw_artist AS rawArtist, u.raw_duration_ms AS rawDurationMs,
               COALESCE(members.added_at_ms, u.created_at_ms) AS addedAtMs,
               m.updated_at_ms AS metadataUpdatedAtMs
        FROM track_metadata_projection m
        JOIN user_track_ref u ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id
        LEFT JOIN members ON members.local_user_track_ref_id = u.local_user_track_ref_id
        WHERE (:personal=0 OR members.local_user_track_ref_id IS NOT NULL) AND m.server_profile_id=:profileId AND m.local_user_track_ref_id>:afterTrackId AND u.deleted_at_ms IS NULL
        ORDER BY m.local_user_track_ref_id ASC LIMIT :limit
    """)
    suspend fun albumSourcesPage(profileId: String, afterTrackId: String, limit: Int, personal: Boolean = true): List<MetadataAlbumSourceRow>
    @Query("""SELECT u.local_user_track_ref_id AS localUserTrackRefId, m.payload_json AS payloadJson,
        u.local_recording_id AS localRecordingId, u.raw_title AS rawTitle, u.raw_artist AS rawArtist,
        u.raw_duration_ms AS rawDurationMs, u.created_at_ms AS addedAtMs, m.updated_at_ms AS metadataUpdatedAtMs
        FROM user_track_ref u JOIN track_metadata_projection m USING(server_profile_id,local_user_track_ref_id)
        WHERE u.server_profile_id=:profile AND u.deleted_at_ms IS NULL AND u.local_user_track_ref_id IN (:ids)
            AND json_type(m.payload_json,'$.album_group_v1')='object'""")
    suspend fun artistAlbumSources(profile: String, ids: List<String>): List<MetadataAlbumSourceRow>

    @Query("SELECT * FROM metadata_artwork_cache WHERE server_profile_id=:profile AND sha256=:sha")
    suspend fun artwork(profile: String, sha: String): MetadataArtworkEntity?
    @Query("SELECT a.* FROM metadata_artwork_cache a JOIN track_metadata_projection m ON m.server_profile_id=a.server_profile_id AND m.artwork_sha256=a.sha256 WHERE m.server_profile_id=:profile AND m.local_user_track_ref_id=:track")
    fun observeArtwork(profile: String, track: String): Flow<MetadataArtworkEntity?>
    @Query("SELECT m.* FROM track_metadata_projection m JOIN user_track_ref u ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id LEFT JOIN metadata_artwork_cache a ON a.server_profile_id=m.server_profile_id AND a.sha256=m.artwork_sha256 WHERE m.server_profile_id=:profile AND m.artwork_sha256>=:lowerSha AND m.artwork_sha256<:upperSha AND a.sha256 IS NULL AND u.deleted_at_ms IS NULL AND EXISTS (SELECT 1 FROM library_entry l WHERE l.server_profile_id=m.server_profile_id AND l.local_user_track_ref_id=m.local_user_track_ref_id AND l.removed_at_ms IS NULL) ORDER BY m.local_user_track_ref_id ASC LIMIT :limit OFFSET :offset")
    suspend fun missingArtwork(profile: String, lowerSha: String, upperSha: String, limit: Int, offset: Int = 0): List<TrackMetadataEntity>
    @Query("SELECT COUNT(*) FROM track_metadata_projection m JOIN user_track_ref u ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id LEFT JOIN metadata_artwork_cache a ON a.server_profile_id=m.server_profile_id AND a.sha256=m.artwork_sha256 WHERE m.server_profile_id=:profile AND m.artwork_sha256>=:lowerSha AND m.artwork_sha256<:upperSha AND a.sha256 IS NULL AND u.deleted_at_ms IS NULL AND EXISTS (SELECT 1 FROM library_entry l WHERE l.server_profile_id=m.server_profile_id AND l.local_user_track_ref_id=m.local_user_track_ref_id AND l.removed_at_ms IS NULL)")
    suspend fun missingArtworkCount(profile: String, lowerSha: String, upperSha: String): Int
}
