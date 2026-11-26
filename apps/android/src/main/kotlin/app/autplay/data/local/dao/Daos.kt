package app.autplay.data.local.dao

import androidx.room3.Dao
import androidx.room3.Embedded
import androidx.room3.Delete
import androidx.room3.Insert
import androidx.room3.OnConflictStrategy
import androidx.room3.Query
import androidx.room3.Transaction
import androidx.room3.Upsert
import androidx.room3.Update
import app.autplay.data.local.entity.*
import kotlinx.coroutines.flow.Flow

data class ArtistAppearanceRow(@Embedded val link: CatalogArtistCreditLinkEntity, val plays: Int, val guest: Boolean)

data class TrackListenCount(val trackId: String, val plays: Int)
data class HeardCatalogueTrack(val mbid: String?, val trackId: String, val recordingId: String?,
    val title: String?, val artist: String?, val lastPlayedMs: Long)

data class LibraryReleaseRow(@Embedded val release: ReleaseProjectionEntity, val artworkTrackId: String?)
data class EditionCoverTrack(val releaseId: String, val trackRef: String?)
data class LibraryPlaylistRow(@Embedded val playlist: PlaylistEntity, val artworkTrackId: String?)

data class TaggedAlbumTrack(val trackId: String, val recording: String?, val album: String, val albumArtist: String,
    val artist: String?, val title: String?, val date: String?, val duration: Long?, val disc: Int?, val position: Int?,
    val hasArtwork: Boolean = false)

data class LibraryArtistTrack(val trackId: String, val recordingId: String?, val title: String?, val artist: String?,
    val nameInput: String? = title, val hasTitle: Boolean = false, val hasArtist: Boolean = false,
    val cleanSourceTitle: Boolean = false, val cleanSourceArtist: Boolean = false)

data class LibraryBrowseRow(
    val localLibraryEntryId: String?,
    val localUserTrackRefId: String,
    val title: String?,
    val artistName: String?,
    val addedAtMs: Long,
    val availabilityStatus: String,
    val removed: Boolean,
    val serverPlaybackCandidate: Boolean,
    val loved: Boolean = false,
    val onDevice: Boolean = false,
    val nameInput: String? = title, val hasTitle: Boolean = false, val hasArtist: Boolean = false,
    val cleanSourceTitle: Boolean = false, val cleanSourceArtist: Boolean = false,
)

// Every personal collection view uses the same three independent reasons for membership.
internal const val LIBRARY_MEMBERSHIP = """
    WITH playlist_saves AS (
        SELECT e.local_user_track_ref_id, MIN(e.added_at_ms) AS added_at_ms
        FROM playlist_entry e JOIN playlist p ON p.local_playlist_id = e.local_playlist_id
            AND p.server_profile_id = e.server_profile_id
        WHERE e.server_profile_id = :profileId AND p.playlist_type = 'MANUAL'
            AND e.removed_at_ms IS NULL AND p.deleted_at_ms IS NULL
        GROUP BY e.local_user_track_ref_id
    ), saved AS (
        SELECT local_user_track_ref_id, added_at_ms FROM library_entry
        WHERE server_profile_id = :profileId AND removed_at_ms IS NULL AND personal_save = 1
        UNION ALL
        SELECT local_user_track_ref_id, updated_at_ms FROM user_track_preference
        WHERE server_profile_id = :profileId AND preference = 'LIKED'
        UNION ALL
        SELECT local_user_track_ref_id, added_at_ms FROM playlist_saves
    ), members AS (
        SELECT local_user_track_ref_id, MIN(added_at_ms) AS added_at_ms FROM saved GROUP BY local_user_track_ref_id
    )
"""

@Dao
interface CatalogProjectionDao {
    @Query("""SELECT local_user_track_ref_id AS trackId, count(*) AS plays FROM listening_event
        WHERE server_profile_id = :profileId AND local_user_track_ref_id IN (:trackIds) AND played_ms > 0
        GROUP BY local_user_track_ref_id""")
    suspend fun trackListenCounts(profileId: String, trackIds: List<String>): List<TrackListenCount>

    @Query(LIBRARY_MEMBERSHIP + """
        SELECT u.local_user_track_ref_id AS trackId, u.server_recording_id AS recordingId,
               COALESCE(NULLIF(trim(u.raw_title), ''), r.title) AS nameInput, json_type(m.payload_json,'$.fields.title') IS NOT NULL AS hasTitle,
               json_type(m.payload_json,'$.fields.artist') IS NOT NULL AS hasArtist,
               CASE WHEN json_extract(m.payload_json,'$.provenance.title.source') IN ('EMBEDDED','SOURCE_NATIVE','FILENAME','SOURCE_LABEL')
                     AND coalesce(json_extract(m.payload_json,'$.provenance.title.normalization_version'),'') <> 'display-v9'
                     AND coalesce(json_extract(m.payload_json,'$.provenance.title.locked'),0)=0 THEN 1 ELSE 0 END AS cleanSourceTitle,
               CASE WHEN json_extract(m.payload_json,'$.provenance.artist.source') IN ('EMBEDDED','SOURCE_NATIVE','FILENAME','SOURCE_LABEL')
                     AND coalesce(json_extract(m.payload_json,'$.provenance.artist.normalization_version'),'') <> 'display-v9'
                     AND coalesce(json_extract(m.payload_json,'$.provenance.artist.locked'),0)=0 THEN 1 ELSE 0 END AS cleanSourceArtist,
               CASE WHEN json_type(m.payload_json, '$.fields.title') IS NULL THEN COALESCE(NULLIF(trim(u.raw_title), ''), r.title)
                    ELSE json_extract(m.payload_json, '$.fields.title') END AS title,
               CASE WHEN json_type(m.payload_json, '$.fields.artist') IS NULL THEN COALESCE(NULLIF(trim(u.raw_artist), ''), r.display_artist)
                    ELSE json_extract(m.payload_json, '$.fields.artist') END AS artist
        FROM user_track_ref u LEFT JOIN members USING (local_user_track_ref_id)
        LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id AND r.is_deleted = 0
        LEFT JOIN track_metadata_projection m ON m.server_profile_id = u.server_profile_id
          AND m.local_user_track_ref_id = u.local_user_track_ref_id
        WHERE u.server_profile_id = :profileId AND u.deleted_at_ms IS NULL
          AND (:personal = 0 OR members.local_user_track_ref_id IS NOT NULL)
          AND NOT EXISTS (
            SELECT 1 FROM catalog_artist_credit_link l
            JOIN artist_credit_name_projection n ON n.server_profile_id = l.server_profile_id
                AND n.server_artist_credit_id = l.server_artist_credit_id
            JOIN artist_projection a ON a.server_profile_id = n.server_profile_id
                AND a.server_artist_id = n.server_artist_id AND a.deleted_at_ms IS NULL
            JOIN catalog_artist_credit_link_owner o ON o.server_profile_id = l.server_profile_id
                AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id
                AND o.owner_scope_id = l.owner_scope_id
            JOIN user_track_ref owned ON owned.server_profile_id = o.server_profile_id
                AND owned.server_recording_id = o.owner_recording_id AND owned.deleted_at_ms IS NULL
            WHERE l.server_profile_id = :profileId AND l.subject_type = 'RECORDING'
              AND l.subject_server_id = u.server_recording_id AND l.owner_scope_complete = 1 AND l.deleted_at_ms IS NULL)
        ORDER BY members.added_at_ms DESC, u.local_user_track_ref_id
    """)
    fun libraryTracksWithoutArtistCredit(profileId: String, personal: Boolean = true): Flow<List<LibraryArtistTrack>>
    @Query("""SELECT u.* FROM user_track_ref u
        WHERE u.server_profile_id=:profile AND u.deleted_at_ms IS NULL AND EXISTS (
          SELECT 1 FROM catalog_artist_credit_link l JOIN artist_credit_name_projection n
            ON n.server_profile_id=l.server_profile_id AND n.server_artist_credit_id=l.server_artist_credit_id
          JOIN artist_projection a ON a.server_profile_id=n.server_profile_id AND a.server_artist_id=n.server_artist_id
          JOIN catalog_artist_credit_link_owner o ON o.server_profile_id=l.server_profile_id
            AND o.subject_type=l.subject_type AND o.subject_server_id=l.subject_server_id AND o.owner_scope_id=l.owner_scope_id
          WHERE l.server_profile_id=u.server_profile_id AND l.subject_type='RECORDING' AND l.owner_scope_complete=1
            AND l.deleted_at_ms IS NULL AND a.deleted_at_ms IS NULL AND l.subject_server_id=u.server_recording_id
            AND o.owner_recording_id=u.server_recording_id AND (a.name LIKE :pattern ESCAPE '!' OR n.credited_name LIKE :pattern ESCAPE '!'
              OR (a.identity_status='ACTIVE' AND EXISTS (SELECT 1 FROM artist_credit_name_projection other
                  WHERE other.server_profile_id=n.server_profile_id AND other.server_artist_id=n.server_artist_id
                    AND other.credited_name LIKE :pattern ESCAPE '!'))))
          AND u.local_user_track_ref_id=(SELECT c.local_user_track_ref_id FROM user_track_ref c
            WHERE c.server_profile_id=u.server_profile_id AND c.server_recording_id=u.server_recording_id AND c.deleted_at_ms IS NULL
            ORDER BY c.server_user_track_ref_id IS NULL, c.server_user_track_ref_id, c.created_at_ms, c.local_user_track_ref_id LIMIT 1)
        ORDER BY u.raw_title COLLATE NOCASE, u.server_recording_id LIMIT :limit""")
    suspend fun tracksForArtistName(profile: String, pattern: String, limit: Int): List<UserTrackRefEntity>

    @Query("UPDATE catalog_page SET observedAtMs=0 WHERE profile=:profile AND substr(pageKey,1,length(:prefix))=:prefix")
    suspend fun expireVaultPages(profile: String, prefix: String)
    @Query("SELECT observedAtMs FROM catalog_page WHERE profile=:profile AND pageKey=:key")
    fun catalogPageTimestamp(profile: String, key: String): kotlinx.coroutines.flow.Flow<Long?>

    @Upsert suspend fun putCatalogPage(row: CatalogPage)
    @Query("SELECT * FROM catalog_page WHERE profile=:profile AND pageKey=:key")
    suspend fun catalogPage(profile: String, key: String): CatalogPage?
    @Query("""DELETE FROM catalog_page WHERE profile=:profile AND pageKey NOT IN
        (SELECT pageKey FROM catalog_page WHERE profile=:profile ORDER BY observedAtMs DESC, pageKey LIMIT 256)""")
    suspend fun trimCatalogPages(profile: String)

    @Query("""SELECT DISTINCT e.* FROM catalog_edition_track e JOIN user_track_ref u ON u.server_profile_id=e.profile
        AND u.server_recording_id=e.recordingId AND u.deleted_at_ms IS NULL
        WHERE e.profile=:profile AND e.deleted=0 AND u.local_user_track_ref_id IN (:tracks)""")
    suspend fun editionsForTracks(profile: String, tracks: List<String>): List<CatalogEditionTrack>

    @Query(LIBRARY_MEMBERSHIP + """SELECT u.local_user_track_ref_id AS trackId, u.local_recording_id AS recording,
        coalesce(json_extract(m.payload_json,'$.fields.album'),u.raw_album,'') AS album,
        coalesce(json_extract(m.payload_json,'$.fields.album_artist'),json_extract(m.payload_json,'$.fields.artist'),u.raw_artist,'') AS albumArtist,
        coalesce(json_extract(m.payload_json,'$.fields.artist'),u.raw_artist) AS artist,
        coalesce(json_extract(m.payload_json,'$.fields.title'),u.raw_title) AS title,
        json_extract(m.payload_json,'$.fields.release_date') AS date, u.raw_duration_ms AS duration,
        CAST(json_extract(m.payload_json,'$.fields.disc_number') AS INTEGER) AS disc,
        CAST(json_extract(m.payload_json,'$.fields.track_number') AS INTEGER) AS position,
        m.artwork_sha256 IS NOT NULL AS hasArtwork
        FROM user_track_ref u LEFT JOIN track_metadata_projection m ON m.server_profile_id=u.server_profile_id
            AND m.local_user_track_ref_id=u.local_user_track_ref_id
        WHERE u.server_profile_id=:profileId AND u.deleted_at_ms IS NULL
            AND (:allTracks OR u.local_user_track_ref_id IN (:ids))
            AND (:personal=0 OR u.local_user_track_ref_id IN (SELECT local_user_track_ref_id FROM members))
            AND coalesce(json_extract(m.payload_json,'$.fields.album'),u.raw_album,'') != ''
            AND json_extract(m.payload_json,'$.album_group_v1.key') IS NULL
            AND NOT EXISTS (SELECT 1 FROM catalog_edition_track e WHERE e.profile=:profileId
                AND e.recordingId=u.server_recording_id AND e.deleted=0)
            AND NOT EXISTS (SELECT 1 FROM release_track_projection rt JOIN release_projection r USING(local_release_id)
                WHERE rt.local_recording_id=u.local_recording_id AND r.is_deleted=0)
        ORDER BY disc IS NULL,disc,position IS NULL,position,title,trackId""")
    fun taggedAlbumTracks(profileId: String, ids: List<String>, allTracks: Boolean, personal: Boolean): Flow<List<TaggedAlbumTrack>>

    @Query("""SELECT ids.releaseId, (
        SELECT u.local_user_track_ref_id FROM catalog_edition_track e JOIN user_track_ref u
            ON u.server_profile_id=e.profile AND u.server_recording_id=e.recordingId
        LEFT JOIN track_metadata_projection m USING(server_profile_id,local_user_track_ref_id)
        WHERE e.profile=:profile AND e.releaseId=ids.releaseId AND e.deleted=0 AND u.deleted_at_ms IS NULL
        ORDER BY m.artwork_sha256 IS NULL,e.disc,e.position,e.trackId,u.server_user_track_ref_id IS NULL,u.server_user_track_ref_id,
            u.created_at_ms,u.local_user_track_ref_id LIMIT 1) AS trackRef
        FROM (SELECT DISTINCT releaseId FROM catalog_edition_track WHERE profile=:profile AND releaseId IN (:ids)) ids""")
    suspend fun coverTracksForEditions(profile: String, ids: List<String>): List<EditionCoverTrack>

    @Upsert suspend fun putEditionTrack(track: CatalogEditionTrack)
    @Query("SELECT * FROM catalog_edition_track WHERE profile=:profile AND trackId=:id")
    suspend fun editionPosition(profile: String, id: String): CatalogEditionTrack?
    @Query("""SELECT e.* FROM catalog_edition_track e WHERE profile=:profile AND releaseId IN (:ids) AND deleted=0
        AND EXISTS (SELECT 1 FROM user_track_ref u WHERE u.server_profile_id=e.profile
                    AND u.server_recording_id=e.recordingId AND u.deleted_at_ms IS NULL)
        ORDER BY releaseId, disc, position, trackId""")
    suspend fun editionTracks(profile: String, ids: List<String>): List<CatalogEditionTrack>

    @Query(LIBRARY_MEMBERSHIP + """SELECT e.* FROM catalog_edition_track e WHERE profile=:profileId AND deleted=0
        AND EXISTS (SELECT 1 FROM user_track_ref u JOIN members USING(local_user_track_ref_id)
                    WHERE u.server_profile_id=e.profile AND u.server_recording_id=e.recordingId AND u.deleted_at_ms IS NULL)
        ORDER BY releaseId, disc, position, trackId""")
    fun libraryEditionTracks(profileId: String): Flow<List<CatalogEditionTrack>>

    @Upsert suspend fun upsertRecordings(rows: List<RecordingProjectionEntity>)
    @Upsert suspend fun upsertReleases(rows: List<ReleaseProjectionEntity>)
    @Upsert suspend fun upsertReleaseTracks(rows: List<ReleaseTrackProjectionEntity>)
    @Query("SELECT * FROM recording_projection WHERE local_recording_id = :localId") suspend fun recording(localId: String): RecordingProjectionEntity?
    @Query("SELECT * FROM recording_projection WHERE local_recording_id IN (:localIds) AND is_deleted = 0 LIMIT :limit") suspend fun recordings(localIds: List<String>, limit: Int): List<RecordingProjectionEntity>
    @Query("SELECT * FROM recording_projection WHERE server_recording_id IN (:serverIds) AND is_deleted = 0 LIMIT :limit") suspend fun recordingsByServerIds(serverIds: List<String>, limit: Int): List<RecordingProjectionEntity>
    @Query("SELECT * FROM release_projection WHERE local_release_id = :localId AND is_deleted = 0") suspend fun release(localId: String): ReleaseProjectionEntity?
    @Query("SELECT DISTINCT rel.* FROM release_projection rel JOIN release_track_projection rt ON rt.local_release_id = rel.local_release_id JOIN user_track_ref u ON u.local_recording_id = rt.local_recording_id WHERE rel.server_release_id IN (:serverIds) AND rel.is_deleted = 0 AND u.deleted_at_ms IS NULL AND u.server_profile_id = :profileId ORDER BY rel.projection_updated_at_ms DESC, rel.local_release_id ASC LIMIT :limit") suspend fun releasesByServerIdsForProfile(profileId: String, serverIds: List<String>, limit: Int): List<ReleaseProjectionEntity>
    @Query("SELECT DISTINCT rel.* FROM release_projection rel JOIN release_track_projection rt ON rt.local_release_id = rel.local_release_id JOIN user_track_ref u ON u.local_recording_id = rt.local_recording_id WHERE rel.local_release_id = :localId AND rel.is_deleted = 0 AND u.deleted_at_ms IS NULL AND u.server_profile_id = :profileId LIMIT 1") suspend fun releaseForProfile(localId: String, profileId: String): ReleaseProjectionEntity?
    @Query(LIBRARY_MEMBERSHIP + """
        SELECT rel.*, (SELECT ref.local_user_track_ref_id FROM release_track_projection pos
            JOIN user_track_ref ref ON ref.local_recording_id=pos.local_recording_id
            LEFT JOIN track_metadata_projection m ON m.server_profile_id=ref.server_profile_id AND m.local_user_track_ref_id=ref.local_user_track_ref_id
            WHERE pos.local_release_id=rel.local_release_id AND ref.server_profile_id=:profileId AND ref.deleted_at_ms IS NULL
            ORDER BY m.artwork_sha256 IS NULL,pos.medium_position,pos.sequence_no,pos.local_release_track_id,ref.server_user_track_ref_id IS NULL,
                ref.server_user_track_ref_id,ref.created_at_ms,ref.local_user_track_ref_id LIMIT 1) AS artworkTrackId
        FROM release_projection rel
        JOIN release_track_projection rt ON rt.local_release_id = rel.local_release_id
        JOIN user_track_ref u ON u.local_recording_id = rt.local_recording_id
        JOIN members ON members.local_user_track_ref_id = u.local_user_track_ref_id
        WHERE rel.is_deleted = 0 AND u.deleted_at_ms IS NULL AND u.server_profile_id = :profileId
        GROUP BY rel.local_release_id
        ORDER BY rel.release_date_text DESC, rel.title COLLATE NOCASE, rel.local_release_id ASC LIMIT :limit
    """)
    fun releasesForProfile(profileId: String, limit: Int): Flow<List<LibraryReleaseRow>>
    @Query("SELECT * FROM release_track_projection WHERE local_release_id = :localReleaseId ORDER BY medium_position ASC, sequence_no ASC, local_release_track_id ASC LIMIT :limit") suspend fun releaseTracks(localReleaseId: String, limit: Int): List<ReleaseTrackProjectionEntity>
    @Query("""
        SELECT a.* FROM user_track_ref playing
        JOIN catalog_artist_credit_link l ON l.server_profile_id = playing.server_profile_id
            AND l.subject_type = 'RECORDING' AND l.subject_server_id = playing.server_recording_id
        JOIN artist_credit_projection c ON c.server_profile_id = l.server_profile_id
            AND c.server_artist_credit_id = l.server_artist_credit_id AND c.deleted_at_ms IS NULL
        JOIN artist_credit_name_projection n ON n.server_profile_id = c.server_profile_id
            AND n.server_artist_credit_id = c.server_artist_credit_id
        JOIN artist_projection a ON a.server_profile_id = n.server_profile_id AND a.server_artist_id = n.server_artist_id
        WHERE playing.server_profile_id = :profile AND playing.local_user_track_ref_id = :trackId
          AND playing.deleted_at_ms IS NULL AND a.deleted_at_ms IS NULL
          AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (
            SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref owned
                ON owned.server_profile_id = o.server_profile_id AND owned.server_recording_id = o.owner_recording_id
            WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type
              AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id
              AND owned.deleted_at_ms IS NULL)
        GROUP BY a.local_artist_id ORDER BY MIN(n.position), a.server_artist_id
    """)
    fun observeTrackArtists(profile: String, trackId: String): Flow<List<ArtistProjectionEntity>>

    @Upsert suspend fun upsertArtists(rows: List<ArtistProjectionEntity>)
    @Upsert suspend fun upsertArtistCredits(rows: List<ArtistCreditProjectionEntity>)
    @Upsert suspend fun upsertArtistCreditNames(rows: List<ArtistCreditNameProjectionEntity>)
    @Upsert suspend fun upsertArtistCreditLinks(rows: List<CatalogArtistCreditLinkEntity>)
    @Upsert suspend fun upsertArtistCreditLinkOwners(rows: List<CatalogArtistCreditLinkOwnerEntity>)
    @Query("DELETE FROM catalog_artist_credit_link_owner WHERE server_profile_id = :profileId AND subject_type = :subjectType AND subject_server_id = :subjectId") suspend fun deleteArtistCreditLinkOwners(profileId: String, subjectType: String, subjectId: String)
    @Query("DELETE FROM artist_credit_name_projection WHERE server_profile_id = :profileId AND server_artist_credit_id = :creditId") suspend fun deleteArtistCreditNames(profileId: String, creditId: String)
    @Query("SELECT * FROM artist_credit_name_projection WHERE server_profile_id = :profileId AND server_artist_credit_id = :creditId ORDER BY position ASC LIMIT :limit") suspend fun namesForCredit(profileId: String, creditId: String, limit: Int): List<ArtistCreditNameProjectionEntity>
    @Query("SELECT n.* FROM artist_credit_name_projection n WHERE n.server_profile_id = :profileId AND n.server_artist_credit_id IN (:creditIds) AND (SELECT count(*) FROM artist_credit_name_projection earlier WHERE earlier.server_profile_id = n.server_profile_id AND earlier.server_artist_credit_id = n.server_artist_credit_id AND earlier.position <= n.position) <= :memberLimit ORDER BY n.server_artist_credit_id, n.position") suspend fun namesForCredits(profileId: String, creditIds: List<String>, memberLimit: Int): List<ArtistCreditNameProjectionEntity>
    @Query("SELECT * FROM artist_projection WHERE server_profile_id = :profileId AND server_artist_id = :artistId LIMIT 1") suspend fun artist(profileId: String, artistId: String): ArtistProjectionEntity?
    @Query("SELECT * FROM artist_credit_projection WHERE server_profile_id = :profileId AND server_artist_credit_id = :creditId LIMIT 1") suspend fun artistCredit(profileId: String, creditId: String): ArtistCreditProjectionEntity?
    @Query("SELECT * FROM catalog_artist_credit_link WHERE server_profile_id = :profileId AND subject_type = :subjectType AND subject_server_id = :subjectId LIMIT 1") suspend fun artistCreditLink(profileId: String, subjectType: String, subjectId: String): CatalogArtistCreditLinkEntity?
    @Query("SELECT DISTINCT a.* FROM artist_projection a JOIN artist_credit_name_projection n ON n.server_profile_id = a.server_profile_id AND n.server_artist_id = a.server_artist_id JOIN catalog_artist_credit_link l ON l.server_profile_id = n.server_profile_id AND l.server_artist_credit_id = n.server_artist_credit_id WHERE a.server_profile_id = :profileId AND a.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) ORDER BY COALESCE(a.sort_name, a.name), a.server_artist_id LIMIT :limit") suspend fun ownedArtists(profileId: String, limit: Int): List<ArtistProjectionEntity>
    @Query("SELECT DISTINCT a.* FROM artist_projection a JOIN artist_credit_name_projection n ON n.server_profile_id = a.server_profile_id AND n.server_artist_id = a.server_artist_id JOIN catalog_artist_credit_link l ON l.server_profile_id = n.server_profile_id AND l.server_artist_credit_id = n.server_artist_credit_id WHERE a.server_profile_id = :profileId AND a.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) ORDER BY COALESCE(a.sort_name, a.name), a.server_artist_id LIMIT :limit") fun observeAccessibleArtists(profileId: String, limit: Int): Flow<List<ArtistProjectionEntity>>
    @Query(LIBRARY_MEMBERSHIP + "SELECT DISTINCT a.* FROM artist_projection a JOIN artist_credit_name_projection n ON n.server_profile_id = a.server_profile_id AND n.server_artist_id = a.server_artist_id JOIN catalog_artist_credit_link l ON l.server_profile_id = n.server_profile_id AND l.server_artist_credit_id = n.server_artist_credit_id WHERE a.server_profile_id = :profileId AND a.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL JOIN members ON members.local_user_track_ref_id = u.local_user_track_ref_id WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) ORDER BY COALESCE(a.sort_name, a.name), a.server_artist_id LIMIT :limit") fun observeOwnedArtists(profileId: String, limit: Int): Flow<List<ArtistProjectionEntity>>
    @Query("SELECT DISTINCT a.* FROM artist_projection a JOIN artist_credit_name_projection n ON n.server_profile_id = a.server_profile_id AND n.server_artist_id = a.server_artist_id JOIN catalog_artist_credit_link l ON l.server_profile_id = n.server_profile_id AND l.server_artist_credit_id = n.server_artist_credit_id WHERE a.server_profile_id = :profileId AND a.server_artist_id = :artistId AND a.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) LIMIT 1") suspend fun ownedArtist(profileId: String, artistId: String): ArtistProjectionEntity?
    @Query("SELECT DISTINCT c.* FROM artist_credit_projection c JOIN artist_credit_name_projection n ON n.server_profile_id = c.server_profile_id AND n.server_artist_credit_id = c.server_artist_credit_id JOIN catalog_artist_credit_link l ON l.server_profile_id = c.server_profile_id AND l.server_artist_credit_id = c.server_artist_credit_id WHERE c.server_profile_id = :profileId AND n.server_artist_id IN (:artistIds) AND c.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) ORDER BY c.server_artist_credit_id LIMIT :limit") suspend fun ownedCreditsForArtist(profileId: String, artistIds: List<String>, limit: Int): List<ArtistCreditProjectionEntity>
    @Query("SELECT DISTINCT c.* FROM artist_credit_projection c JOIN catalog_artist_credit_link l ON l.server_profile_id = c.server_profile_id AND l.server_artist_credit_id = c.server_artist_credit_id WHERE c.server_profile_id = :profileId AND c.server_artist_credit_id = :creditId AND c.deleted_at_ms IS NULL AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) LIMIT 1") suspend fun ownedArtistCredit(profileId: String, creditId: String): ArtistCreditProjectionEntity?
    @Query("SELECT l.* FROM catalog_artist_credit_link l WHERE l.server_profile_id = :profileId AND l.subject_type = :subjectType AND l.subject_server_id = :subjectId AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id AND u.deleted_at_ms IS NULL WHERE o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id) LIMIT :limit") suspend fun creditLinksForSubject(profileId: String, subjectType: String, subjectId: String, limit: Int): List<CatalogArtistCreditLinkEntity>
    @Query(LIBRARY_MEMBERSHIP + """, owned AS (
            SELECT DISTINCT l.local_catalog_artist_credit_link_id AS linkId, l.subject_type AS kind,
                l.subject_server_id AS subjectId
            FROM catalog_artist_credit_link l JOIN artist_credit_name_projection n
              ON n.server_profile_id = l.server_profile_id AND n.server_artist_credit_id = l.server_artist_credit_id
            WHERE l.server_profile_id = :profileId AND n.server_artist_id IN (:artistIds)
              AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND EXISTS (
                SELECT 1 FROM catalog_artist_credit_link_owner o JOIN user_track_ref u
                  ON u.server_profile_id = o.server_profile_id AND u.server_recording_id = o.owner_recording_id
                WHERE (:personal = 0 OR u.local_user_track_ref_id IN (SELECT local_user_track_ref_id FROM members))
                  AND o.server_profile_id = l.server_profile_id AND o.subject_type = l.subject_type
                  AND o.subject_server_id = l.subject_server_id AND o.owner_scope_id = l.owner_scope_id
                  AND u.deleted_at_ms IS NULL)
        ), counted AS (
            SELECT owned.*, CASE WHEN kind = 'RECORDING' THEN (
                SELECT COUNT(*) FROM user_track_ref u JOIN listening_event e ON e.local_user_track_ref_id = u.local_user_track_ref_id
                WHERE u.server_profile_id = :profileId AND u.server_recording_id = owned.subjectId
                  AND e.server_profile_id = :profileId AND e.played_ms > 0
            ) ELSE 0 END AS plays FROM owned
        ), ranked AS (
            SELECT counted.*, ROW_NUMBER() OVER (PARTITION BY kind ORDER BY plays DESC, subjectId) AS sectionPosition
            FROM counted
        )
        SELECT l.*, ranked.plays, NOT EXISTS (SELECT 1 FROM artist_credit_name_projection n
            WHERE n.server_profile_id=:profileId AND n.server_artist_credit_id=l.server_artist_credit_id
              AND n.server_artist_id IN (:artistIds) AND upper(n.role) NOT IN ('GUEST','FEATURED','FEATURING'))
            AS guest FROM ranked JOIN catalog_artist_credit_link l
          ON l.local_catalog_artist_credit_link_id = ranked.linkId
        ORDER BY ranked.sectionPosition, ranked.kind, ranked.subjectId LIMIT :limit OFFSET :offset
    """)
    suspend fun ownedSubjectLinksForArtist(profileId: String, artistIds: List<String>, limit: Int, personal: Boolean, offset: Int = 0): List<ArtistAppearanceRow>
}

@Dao
interface LibraryDao {
    /** Likes and active manual playlists remain visible beyond the window of recent saves. */
    @Query(LIBRARY_MEMBERSHIP + """, recent AS (
            SELECT local_user_track_ref_id FROM library_entry
            WHERE server_profile_id = :profileId AND removed_at_ms IS NULL AND personal_save = 1
            ORDER BY added_at_ms DESC, local_user_track_ref_id LIMIT :limit
        )
        SELECT l.local_library_entry_id AS localLibraryEntryId,
               u.local_user_track_ref_id AS localUserTrackRefId, u.raw_title AS nameInput,
               json_type(m.payload_json,'$.fields.title') IS NOT NULL AS hasTitle,
               json_type(m.payload_json,'$.fields.artist') IS NOT NULL AS hasArtist,
               CASE WHEN json_extract(m.payload_json,'$.provenance.title.source') IN ('EMBEDDED','SOURCE_NATIVE','FILENAME','SOURCE_LABEL')
                     AND coalesce(json_extract(m.payload_json,'$.provenance.title.normalization_version'),'') <> 'display-v9'
                     AND coalesce(json_extract(m.payload_json,'$.provenance.title.locked'),0)=0 THEN 1 ELSE 0 END AS cleanSourceTitle,
               CASE WHEN json_extract(m.payload_json,'$.provenance.artist.source') IN ('EMBEDDED','SOURCE_NATIVE','FILENAME','SOURCE_LABEL')
                     AND coalesce(json_extract(m.payload_json,'$.provenance.artist.normalization_version'),'') <> 'display-v9'
                     AND coalesce(json_extract(m.payload_json,'$.provenance.artist.locked'),0)=0 THEN 1 ELSE 0 END AS cleanSourceArtist,
               CASE WHEN json_type(m.payload_json, '$.fields.title') IS NULL THEN u.raw_title
                    ELSE json_extract(m.payload_json, '$.fields.title') END AS title,
               CASE WHEN json_type(m.payload_json, '$.fields.artist') IS NULL THEN u.raw_artist
                    ELSE json_extract(m.payload_json, '$.fields.artist') END AS artistName,
               COALESCE(l.added_at_ms, members.added_at_ms) AS addedAtMs,
               COALESCE(l.availability_status, 'PENDING') AS availabilityStatus,
               0 AS removed,
               (u.server_user_track_ref_id IS NOT NULL AND u.server_recording_id IS NOT NULL
                    AND u.resolution_status = 'RESOLVED') AS serverPlaybackCandidate,
               COALESCE(p.preference = 'LIKED', 0) AS loved,
               EXISTS (SELECT 1 FROM local_audio_state a
                       WHERE a.local_user_track_ref_id = u.local_user_track_ref_id
                         AND a.storage_class = 'USER_IMPORT' AND a.status = 'AVAILABLE'
                         AND (a.persisted_uri_permission = 1 OR a.content_uri LIKE 'content://media/%')) AS onDevice
        FROM members JOIN user_track_ref u USING (local_user_track_ref_id)
        LEFT JOIN library_entry l ON l.local_user_track_ref_id = u.local_user_track_ref_id AND l.server_profile_id = :profileId AND l.removed_at_ms IS NULL AND l.personal_save = 1
        LEFT JOIN user_track_preference p ON p.local_user_track_ref_id = u.local_user_track_ref_id AND p.server_profile_id = :profileId
        LEFT JOIN track_metadata_projection m ON m.local_user_track_ref_id = u.local_user_track_ref_id AND m.server_profile_id = :profileId
        WHERE u.server_profile_id = :profileId AND u.deleted_at_ms IS NULL
          AND (:limit < 0 OR u.local_user_track_ref_id IN (SELECT local_user_track_ref_id FROM recent)
               OR p.preference = 'LIKED' OR u.local_user_track_ref_id IN (SELECT local_user_track_ref_id FROM playlist_saves))
        ORDER BY addedAtMs DESC, localUserTrackRefId
    """)
    fun browseLibrary(profileId: String, limit: Int): Flow<List<LibraryBrowseRow>>

    @Query(LIBRARY_MEMBERSHIP + """
        , playable AS (
            SELECT u.local_user_track_ref_id AS id, members.added_at_ms AS added
            FROM members JOIN user_track_ref u USING(local_user_track_ref_id)
            WHERE u.deleted_at_ms IS NULL AND u.server_profile_id=:profileId AND (
                (:server AND u.server_user_track_ref_id IS NOT NULL AND u.server_recording_id IS NOT NULL AND u.resolution_status='RESOLVED')
                OR EXISTS (SELECT 1 FROM local_audio_state a WHERE a.local_user_track_ref_id=u.local_user_track_ref_id
                    AND a.status='AVAILABLE' AND (a.storage_class!='USER_IMPORT' OR a.persisted_uri_permission=1 OR a.content_uri LIKE 'content://media/%'))
            )
        )
        SELECT p.id FROM playable p LEFT JOIN playable anchor ON anchor.id=:trackId
        WHERE p.id<>:trackId ORDER BY CASE WHEN anchor.id IS NULL OR p.added<anchor.added
            OR (p.added=anchor.added AND p.id>anchor.id) THEN 0 ELSE 1 END, p.added DESC, p.id LIMIT 1
    """)
    suspend fun nextPlayableLibraryTrack(profileId: String, trackId: String, server: Boolean): String?

    @Query(
        """
        SELECT u.local_user_track_ref_id
        FROM user_track_ref u
        WHERE u.server_profile_id = :profileId AND u.deleted_at_ms IS NULL
          AND (:onlyUnresolved = 0 OR u.server_recording_id IS NULL)
          AND (:excludeSnapshot IS NULL OR NOT EXISTS (
              SELECT 1 FROM queue_entry e JOIN user_track_ref queued
                ON queued.local_user_track_ref_id = e.local_user_track_ref_id
              WHERE e.queue_snapshot_id = :excludeSnapshot AND
                (queued.local_user_track_ref_id = u.local_user_track_ref_id
                 OR queued.server_recording_id = u.server_recording_id)
          ))
          AND NOT EXISTS (
              SELECT 1 FROM user_track_preference p
              WHERE p.local_user_track_ref_id = u.local_user_track_ref_id AND p.server_profile_id = :profileId
                AND (p.preference = 'DISLIKED' OR p.excluded_from_taste = 1)
          )
          AND EXISTS (
              SELECT 1 FROM library_entry l
              WHERE l.local_user_track_ref_id = u.local_user_track_ref_id
                AND l.server_profile_id = :profileId AND l.removed_at_ms IS NULL
          )
          AND (
              EXISTS (
                  SELECT 1 FROM local_audio_state a
                  WHERE a.local_user_track_ref_id = u.local_user_track_ref_id
                    AND a.status = 'AVAILABLE'
                    AND (a.persisted_uri_permission = 1 OR a.content_uri LIKE 'content://media/%')
              )
              OR EXISTS (
                  SELECT 1 FROM download_intent d
                  WHERE d.local_user_track_ref_id = u.local_user_track_ref_id
                    AND (d.server_profile_id = :profileId OR (:profileId = 'legacy-unscoped' AND d.server_profile_id IS NULL))
                    AND d.state = 'COMPLETED'
              )
              OR (:vaultAvailable = 1 AND u.server_user_track_ref_id IS NOT NULL
                  AND u.server_recording_id IS NOT NULL AND u.resolution_status = 'RESOLVED')
          )
        ORDER BY CASE WHEN :randomOrder THEN RANDOM() ELSE (
            SELECT MAX(l.added_at_ms) FROM library_entry l WHERE l.local_user_track_ref_id = u.local_user_track_ref_id
              AND l.server_profile_id = :profileId AND l.removed_at_ms IS NULL
        ) END DESC, u.local_user_track_ref_id
        LIMIT :limit
        """,
    )
    suspend fun playbackCandidates(
        profileId: String, vaultAvailable: Boolean, limit: Int, onlyUnresolved: Boolean = false,
        randomOrder: Boolean = true, excludeSnapshot: String? = null,
    ): List<String>

    @Query(WAVE_RECORDINGS + """
        WHERE u.server_profile_id = :profileId AND u.deleted_at_ms IS NULL
          AND u.local_user_track_ref_id IN (:refIds)
        """)
    suspend fun waveRecordings(profileId: String, refIds: List<String>): List<WaveRecordingRow>

    @Query("""
        SELECT local_user_track_ref_id AS refId, variation_kind AS kind,
               coalesce(variation_surface, CASE WHEN event_origin = 'RECOMMENDED' AND json_valid(recommendation_attribution_json)
                  THEN CASE json_extract(recommendation_attribution_json, '$.surface')
                    WHEN 'home' THEN 'home' WHEN 'offline_pack' THEN 'home' WHEN 'personal_wave' THEN 'personal_wave' END END) AS surface,
               count(*) AS plays, sum(CASE WHEN played_ms < 30000 OR completion_ratio <= 0.2 THEN 1 ELSE 0 END) AS skips
        FROM listening_event
        WHERE server_profile_id = :profile AND excluded_from_taste = 0 AND started_at_ms > :since AND started_at_ms <= :through
          AND (event_origin = 'RECOMMENDED' OR (event_origin = 'WAVE' AND variation_surface = 'personal_wave'))
          AND local_user_track_ref_id IN (:refs)
        GROUP BY local_user_track_ref_id, kind, surface
    """)
    suspend fun variationOutcomes(profile: String, since: Long, through: Long, refs: List<String>): List<VariationOutcomeRow>

    @Query(WAVE_RECORDINGS + """
        WHERE u.server_profile_id = :profile AND u.local_user_track_ref_id IN (
            SELECT local_user_track_ref_id FROM listening_event WHERE server_profile_id = :profile
            AND started_at_ms > :since AND started_at_ms <= :through AND excluded_from_taste = 0
            AND (event_origin = 'RECOMMENDED' OR (event_origin = 'WAVE' AND variation_surface = 'personal_wave')))
        AND u.local_user_track_ref_id > :after ORDER BY u.local_user_track_ref_id LIMIT 200
    """)
    suspend fun variationHistoryRefs(profile: String, since: Long, through: Long, after: String): List<WaveRecordingRow>

    @Query(WAVE_RECORDINGS + """
        JOIN user_track_preference p ON p.local_user_track_ref_id = u.local_user_track_ref_id
        WHERE u.server_profile_id = :profileId AND p.server_profile_id = :profileId
          AND p.preference = 'LIKED' AND p.excluded_from_taste = 0
          AND u.deleted_at_ms IS NULL AND COALESCE(r.is_deleted, 0) = 0
          AND r.redirect_server_recording_id IS NULL AND p.local_user_track_ref_id > :after
        ORDER BY p.local_user_track_ref_id LIMIT :limit
        """)
    suspend fun likedWaveRecordings(profileId: String, after: String, limit: Int): List<WaveRecordingRow>

    @Query(WAVE_RECORDINGS + """
        WHERE u.server_profile_id = :profileId AND u.local_user_track_ref_id > :after
          AND u.local_user_track_ref_id IN (
              SELECT local_user_track_ref_id FROM listening_event
              WHERE server_profile_id = :profileId AND excluded_from_taste = 0 AND played_ms > 0
                AND started_at_ms > :since AND started_at_ms <= :now
              UNION
              SELECT e.local_user_track_ref_id FROM queue_snapshot s
              JOIN queue_entry e ON e.queue_snapshot_id = s.queue_snapshot_id AND e.queue_entry_id = s.current_entry_id
              WHERE s.is_active = 1 AND COALESCE(s.server_profile_id, 'legacy-unscoped') = :profileId
                AND s.active_listening_event_id IS NOT NULL AND s.active_session_observed_played_ms > 0
                AND s.session_excluded_from_taste = 0 AND s.active_listen_excluded_from_taste = 0
                AND s.active_session_started_at_ms > :since AND s.active_session_started_at_ms <= :now
          )
        ORDER BY u.local_user_track_ref_id LIMIT :limit
    """)
    suspend fun recentlyPlayedWaveRecordings(profileId: String, since: Long, now: Long, after: String, limit: Int): List<WaveRecordingRow>

    @Upsert suspend fun upsertTrackRef(row: UserTrackRefEntity)
    @Upsert suspend fun upsertTrackRefs(rows: List<UserTrackRefEntity>)
    @Upsert suspend fun upsertEntry(row: LibraryEntryEntity)
    @Query("""SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND deleted_at_ms IS NULL
        AND raw_title COLLATE NOCASE IN (:titles)""")
    suspend fun tracksByImportTitles(profileId: String, titles: List<String>): List<UserTrackRefEntity>
    @Upsert suspend fun upsertEntries(rows: List<LibraryEntryEntity>)
    @Upsert suspend fun upsertPreference(row: UserTrackPreferenceEntity)
    @Query("SELECT * FROM user_track_preference WHERE local_user_track_ref_id = :trackRefId") suspend fun preference(trackRefId: String): UserTrackPreferenceEntity?
    @Query("SELECT * FROM user_track_preference WHERE local_user_track_ref_id = :trackRefId") fun observePreference(trackRefId: String): Flow<UserTrackPreferenceEntity?>
    @Query("SELECT * FROM user_track_preference WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC, local_user_track_ref_id ASC LIMIT :limit") fun preferencesForProfile(profileId: String, limit: Int): Flow<List<UserTrackPreferenceEntity>>
    @Query("SELECT * FROM library_entry WHERE removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry ORDER BY added_at_ms DESC LIMIT :limit") fun entries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId AND removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeEntriesForProfile(profileId: String, limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = 'legacy-unscoped' AND removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeLegacyEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId ORDER BY added_at_ms DESC LIMIT :limit") fun entriesForProfile(profileId: String, limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = 'legacy-unscoped' ORDER BY added_at_ms DESC LIMIT :limit") fun legacyEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM user_track_ref WHERE local_user_track_ref_id = :localId") suspend fun trackRef(localId: String): UserTrackRefEntity?
    @Query("SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND server_user_track_ref_id = :serverId LIMIT 1") suspend fun trackRefByServerId(profileId: String, serverId: String): UserTrackRefEntity?
    @Query("SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND server_user_track_ref_id IN (:serverIds) AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC, local_user_track_ref_id ASC LIMIT :limit") suspend fun trackRefsByServerIds(profileId: String, serverIds: List<String>, limit: Int): List<UserTrackRefEntity>
    @Query("SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND server_recording_id = :recordingId AND deleted_at_ms IS NULL ORDER BY server_user_track_ref_id IS NULL, server_user_track_ref_id, created_at_ms, local_user_track_ref_id LIMIT 1") suspend fun trackRefByRecording(profileId: String, recordingId: String): UserTrackRefEntity?
    @Query("SELECT u.* FROM user_track_ref u WHERE u.server_profile_id = :profileId AND u.server_recording_id IN (:recordingIds) AND u.deleted_at_ms IS NULL AND u.local_user_track_ref_id = (SELECT candidate.local_user_track_ref_id FROM user_track_ref candidate WHERE candidate.server_profile_id = u.server_profile_id AND candidate.server_recording_id = u.server_recording_id AND candidate.deleted_at_ms IS NULL ORDER BY candidate.server_user_track_ref_id IS NULL, candidate.server_user_track_ref_id, candidate.created_at_ms, candidate.local_user_track_ref_id LIMIT 1) ORDER BY u.updated_at_ms DESC, u.local_user_track_ref_id ASC LIMIT :limit") suspend fun trackRefsByServerRecordings(profileId: String, recordingIds: List<String>, limit: Int): List<UserTrackRefEntity>
    @Query("SELECT * FROM user_track_ref WHERE local_user_track_ref_id IN (:localIds) LIMIT :limit") suspend fun trackRefs(localIds: List<String>, limit: Int): List<UserTrackRefEntity>
    @Query("SELECT u.* FROM user_track_ref u WHERE u.server_profile_id = :profileId AND u.local_recording_id IN (:recordingIds) AND u.deleted_at_ms IS NULL AND u.local_user_track_ref_id = (SELECT candidate.local_user_track_ref_id FROM user_track_ref candidate WHERE candidate.server_profile_id = u.server_profile_id AND candidate.local_recording_id = u.local_recording_id AND candidate.deleted_at_ms IS NULL ORDER BY candidate.server_user_track_ref_id IS NULL, candidate.server_user_track_ref_id, candidate.created_at_ms, candidate.local_user_track_ref_id LIMIT 1) ORDER BY u.local_recording_id LIMIT :limit") suspend fun trackRefsByRecordings(profileId: String, recordingIds: List<String>, limit: Int): List<UserTrackRefEntity>
    @Query("SELECT * FROM library_entry WHERE local_library_entry_id = :localId") suspend fun entry(localId: String): LibraryEntryEntity?
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId AND server_library_entry_id = :serverId LIMIT 1") suspend fun entryByServerId(profileId: String, serverId: String): LibraryEntryEntity?
    @Query("SELECT * FROM library_entry WHERE local_user_track_ref_id = :trackRefId LIMIT 1") suspend fun entryForTrack(trackRefId: String): LibraryEntryEntity?
    @Query("SELECT count(*) FROM user_track_ref") suspend fun trackRefCount(): Int
    @Query("SELECT count(*) FROM library_entry") suspend fun entryCount(): Int
}

@Dao
interface ImportReviewDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertJob(row: LocalImportJobEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertEntries(rows: List<LocalImportEntryEntity>)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertDecision(row: LocalMatchDecisionEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertCandidates(rows: List<LocalMatchCandidateEntity>)

    @Query("SELECT * FROM local_import_job WHERE import_job_id = :jobId")
    suspend fun job(jobId: String): LocalImportJobEntity?

    @Query("SELECT * FROM local_import_job WHERE server_profile_id = :profileId AND adapter_id = :adapterId AND input_sha256 = :inputSha256 AND (source_uri = :sourceUri OR (source_uri IS NULL AND :sourceUri IS NULL)) LIMIT 1")
    suspend fun jobByIdentity(profileId: String, adapterId: String, inputSha256: String, sourceUri: String?): LocalImportJobEntity?

    @Query("SELECT * FROM local_import_job WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT 1")
    fun observeLatestJob(profileId: String): Flow<LocalImportJobEntity?>

    @Query("SELECT * FROM local_import_entry WHERE import_job_id = :jobId ORDER BY source_position ASC LIMIT :limit")
    suspend fun entries(jobId: String, limit: Int): List<LocalImportEntryEntity>

    @Query("SELECT * FROM local_import_entry WHERE import_job_id = :jobId ORDER BY source_position ASC LIMIT :limit")
    fun observeEntries(jobId: String, limit: Int): Flow<List<LocalImportEntryEntity>>

    @Query("SELECT * FROM local_import_entry WHERE import_entry_id = :entryId")
    suspend fun entry(entryId: String): LocalImportEntryEntity?

    @Query("SELECT * FROM match_decision WHERE decision_id = :decisionId")
    suspend fun decision(decisionId: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE import_entry_id = :entryId AND idempotency_key = :idempotencyKey LIMIT 1")
    suspend fun decisionByIdempotency(entryId: String, idempotencyKey: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE import_entry_id = :entryId AND decision_kind = 'EVALUATION' ORDER BY created_at_ms DESC, decision_id DESC LIMIT 1")
    suspend fun latestEvaluation(entryId: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE supersedes_decision_id = :decisionId LIMIT 1")
    suspend fun successor(decisionId: String): LocalMatchDecisionEntity?

    @Query("SELECT d.* FROM match_decision d JOIN local_import_entry e ON e.import_entry_id = d.import_entry_id WHERE e.import_job_id = :jobId ORDER BY d.created_at_ms ASC, d.decision_id ASC")
    fun observeDecisionsForJob(jobId: String): Flow<List<LocalMatchDecisionEntity>>

    @Query("SELECT * FROM match_candidate WHERE candidate_id = :candidateId")
    suspend fun candidate(candidateId: String): LocalMatchCandidateEntity?

    @Query("SELECT * FROM match_candidate WHERE decision_id = :decisionId ORDER BY rank ASC LIMIT :limit")
    suspend fun candidates(decisionId: String, limit: Int): List<LocalMatchCandidateEntity>

    @Query("SELECT * FROM match_candidate WHERE decision_id = :decisionId ORDER BY rank ASC LIMIT :limit")
    fun observeCandidates(decisionId: String, limit: Int): Flow<List<LocalMatchCandidateEntity>>

    @Query("UPDATE local_import_entry SET workflow_state = :workflowState, selected_local_recording_id = :recordingId, latest_decision_id = :decisionId, last_error_code = :errorCode, updated_at_ms = :nowMs WHERE import_entry_id = :entryId AND ((latest_decision_id IS NULL AND :expectedDecisionId IS NULL) OR latest_decision_id = :expectedDecisionId)")
    suspend fun advanceEntry(
        entryId: String,
        expectedDecisionId: String?,
        workflowState: String,
        recordingId: String?,
        decisionId: String,
        errorCode: String?,
        nowMs: Long,
    ): Int

    @Query("UPDATE local_import_entry SET source_availability = :availability, persisted_uri_permission = :persistedPermission, updated_at_ms = :nowMs WHERE import_entry_id = :entryId")
    suspend fun updateSourceAvailability(entryId: String, availability: String, persistedPermission: Boolean, nowMs: Long): Int

    @Query("UPDATE local_import_job SET state = :newState, updated_at_ms = :nowMs, completed_at_ms = :completedAtMs WHERE import_job_id = :jobId AND state = :expectedState")
    suspend fun transitionJobState(
        jobId: String,
        expectedState: String,
        newState: String,
        nowMs: Long,
        completedAtMs: Long?,
    ): Int

    @Query("UPDATE local_import_job SET state = :state, checkpoint_position = :checkpointPosition, review_required_count = :reviewRequiredCount, resolved_count = :resolvedCount, no_match_count = :noMatchCount, unresolved_count = :unresolvedCount, failed_count = :failedCount, report_json = :reportJson, updated_at_ms = :nowMs, completed_at_ms = :completedAtMs WHERE import_job_id = :jobId")
    suspend fun updateJobSummary(
        jobId: String,
        state: String,
        checkpointPosition: Int,
        reviewRequiredCount: Int,
        resolvedCount: Int,
        noMatchCount: Int,
        unresolvedCount: Int,
        failedCount: Int,
        reportJson: String,
        nowMs: Long,
        completedAtMs: Long?,
    ): Int
}

@Dao
interface ServerFeatureProjectionDao {
    @Upsert
    suspend fun upsertRemoteImportJob(row: RemoteImportJobProjectionEntity)

    @Query("SELECT * FROM remote_import_job_projection WHERE server_profile_id = :profileId AND import_job_id = :importJobId")
    suspend fun remoteImportJob(profileId: String, importJobId: String): RemoteImportJobProjectionEntity?

    @Query("SELECT * FROM remote_import_job_projection WHERE import_job_id = :importJobId LIMIT 1")
    suspend fun remoteImportJobById(importJobId: String): RemoteImportJobProjectionEntity?

    @Query("SELECT * FROM remote_import_job_projection WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT :limit")
    fun observeRemoteImportJobs(profileId: String, limit: Int): Flow<List<RemoteImportJobProjectionEntity>>

    @Upsert
    suspend fun upsertVaultUploadIntent(row: VaultUploadIntentEntity)

    @Query("SELECT * FROM vault_upload_intent WHERE upload_intent_id = :intentId")
    suspend fun vaultUploadIntent(intentId: String): VaultUploadIntentEntity?

    @Query("SELECT * FROM vault_upload_intent WHERE server_profile_id = :profileId AND state IN (:states) ORDER BY updated_at_ms ASC LIMIT :limit")
    suspend fun pendingVaultUploadIntents(profileId: String, states: List<String>, limit: Int): List<VaultUploadIntentEntity>

    @Query("SELECT * FROM vault_upload_intent WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT :limit")
    fun observeVaultUploadIntents(profileId: String, limit: Int): Flow<List<VaultUploadIntentEntity>>

    @Upsert
    suspend fun upsertRecommendationResponseSnapshot(row: RecommendationResponseSnapshotEntity)

    @Query("SELECT * FROM recommendation_response_snapshot WHERE server_profile_id = :profileId AND recommendation_request_id = :requestId")
    suspend fun recommendationResponseSnapshot(profileId: String, requestId: String): RecommendationResponseSnapshotEntity?

    @Query("SELECT * FROM recommendation_response_snapshot WHERE server_profile_id = :profileId ORDER BY received_at_ms DESC LIMIT :limit")
    fun observeRecommendationResponseSnapshots(profileId: String, limit: Int): Flow<List<RecommendationResponseSnapshotEntity>>
}

@Dao
interface PlaylistDao {
    @Upsert suspend fun upsertPlaylist(row: PlaylistEntity)
    @Upsert suspend fun upsertEntry(row: PlaylistEntryEntity)
    @Upsert suspend fun upsertEntries(rows: List<PlaylistEntryEntity>)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertPlaylist(row: PlaylistEntity)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertEntry(row: PlaylistEntryEntity)
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL ORDER BY active_position_key ASC LIMIT :limit") fun activeEntries(playlistId: String, limit: Int): Flow<List<PlaylistEntryEntity>>
    @Query("UPDATE playlist_entry SET position_key = :positionKey, active_position_key = :positionKey WHERE local_playlist_entry_id = :entryId AND removed_at_ms IS NULL") suspend fun moveEntry(entryId: String, positionKey: String): Int
    @Query("SELECT count(*) FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL") suspend fun activeEntryCount(playlistId: String): Int
    @Query("SELECT * FROM playlist WHERE local_playlist_id = :playlistId") suspend fun playlist(playlistId: String): PlaylistEntity?
    @Query("SELECT * FROM playlist WHERE server_profile_id = :profileId AND server_playlist_id = :serverId LIMIT 1") suspend fun playlistByServerId(profileId: String, serverId: String): PlaylistEntity?
    @Query("SELECT * FROM playlist WHERE deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activePlaylists(limit: Int): Flow<List<PlaylistEntity>>
    @Query("SELECT * FROM playlist WHERE server_profile_id = :profileId AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activePlaylistsForProfile(profileId: String, limit: Int): Flow<List<PlaylistEntity>>
    @Query("""
        SELECT p.*, (
            SELECT e.local_user_track_ref_id FROM playlist_entry e
            JOIN track_metadata_projection m ON m.server_profile_id = e.server_profile_id
                AND m.local_user_track_ref_id = e.local_user_track_ref_id AND m.artwork_sha256 IS NOT NULL
            JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id
                AND u.server_profile_id = p.server_profile_id AND u.deleted_at_ms IS NULL
            WHERE e.local_playlist_id = p.local_playlist_id AND e.server_profile_id = p.server_profile_id
                AND e.removed_at_ms IS NULL
            ORDER BY e.position_key, e.local_playlist_entry_id LIMIT 1
        ) AS artworkTrackId
        FROM playlist p WHERE p.server_profile_id = :profileId AND p.deleted_at_ms IS NULL
        ORDER BY p.updated_at_ms DESC, p.local_playlist_id LIMIT :limit
    """)
    fun libraryPlaylists(profileId: String, limit: Int): Flow<List<LibraryPlaylistRow>>

    @Query("SELECT * FROM playlist WHERE server_profile_id = 'legacy-unscoped' AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activeLegacyPlaylists(limit: Int): Flow<List<PlaylistEntity>>
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_entry_id = :entryId") suspend fun entry(entryId: String): PlaylistEntryEntity?
    @Query("SELECT * FROM playlist_entry WHERE server_profile_id = :profileId AND server_playlist_entry_id = :serverId LIMIT 1") suspend fun entryByServerId(profileId: String, serverId: String): PlaylistEntryEntity?
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL ORDER BY active_position_key ASC LIMIT :limit") suspend fun activeEntryList(playlistId: String, limit: Int): List<PlaylistEntryEntity>
}

@Dao
interface LocalAudioDao {
    @Upsert suspend fun upsertState(row: LocalAudioStateEntity)
    @Upsert suspend fun upsertDownloadIntent(row: DownloadIntentEntity)
    @Query("SELECT * FROM local_audio_state WHERE local_user_track_ref_id = :trackRefId ORDER BY updated_at_ms DESC LIMIT :limit") fun states(trackRefId: String, limit: Int): Flow<List<LocalAudioStateEntity>>
    @Query("SELECT * FROM local_audio_state WHERE local_audio_state_id = :localId") suspend fun state(localId: String): LocalAudioStateEntity?
    @Query("SELECT * FROM local_audio_state WHERE content_uri = :contentUri LIMIT 1") suspend fun stateByUri(contentUri: String): LocalAudioStateEntity?
    @Query("""SELECT a.* FROM local_audio_state a JOIN user_track_ref source ON source.local_user_track_ref_id=a.local_user_track_ref_id
        JOIN user_track_ref requested ON requested.local_user_track_ref_id=:trackRefId AND requested.server_profile_id=source.server_profile_id
        WHERE source.deleted_at_ms IS NULL AND requested.deleted_at_ms IS NULL AND source.local_user_track_ref_id != :trackRefId
          AND ((requested.server_recording_id IS NOT NULL AND requested.server_recording_id=source.server_recording_id)
            OR (requested.local_recording_id IS NOT NULL AND requested.local_recording_id=source.local_recording_id
              AND (source.server_recording_id IS NULL OR requested.server_recording_id IS NULL OR source.server_recording_id=requested.server_recording_id)))
        ORDER BY CASE a.status WHEN 'AVAILABLE' THEN 0 ELSE 1 END, a.updated_at_ms DESC LIMIT :limit""")
    suspend fun otherAudioForRecording(trackRefId: String, limit: Int): List<LocalAudioStateEntity>
    @Query("SELECT * FROM local_audio_state WHERE local_user_track_ref_id = :trackRefId ORDER BY CASE status WHEN 'AVAILABLE' THEN 0 ELSE 1 END, updated_at_ms DESC LIMIT :limit") suspend fun statesForPlayback(trackRefId: String, limit: Int): List<LocalAudioStateEntity>
    @Query("SELECT a.* FROM local_audio_state a JOIN user_track_ref u ON u.local_user_track_ref_id = a.local_user_track_ref_id WHERE u.server_profile_id = :profileId ORDER BY a.updated_at_ms DESC, a.local_audio_state_id ASC LIMIT :limit") fun statesForProfile(profileId: String, limit: Int): Flow<List<LocalAudioStateEntity>>
    @Query("SELECT * FROM download_intent WHERE download_intent_id = :intentId") suspend fun downloadIntent(intentId: String): DownloadIntentEntity?
    @Query("SELECT * FROM download_intent WHERE server_profile_id = :profileId AND download_intent_id IN (:ids) ORDER BY created_at_ms DESC, updated_at_ms DESC LIMIT 1")
    fun observeLatestDownloadIntent(profileId: String, ids: List<String>): Flow<DownloadIntentEntity?>
    @Query("SELECT * FROM download_intent WHERE server_profile_id = :profile AND local_user_track_ref_id IN (:refs) AND desired_storage_class IN ('USER_DOWNLOAD','PINNED') ORDER BY created_at_ms DESC, updated_at_ms DESC")
    fun collectionDownloads(profile: String, refs: List<String>): Flow<List<DownloadIntentEntity>>
    @Query("SELECT * FROM download_intent WHERE media3_download_id = :downloadId LIMIT 1") suspend fun downloadIntentByMedia3Id(downloadId: String): DownloadIntentEntity?
    @Query("SELECT * FROM download_intent ORDER BY created_at_ms ASC LIMIT :limit") suspend fun downloadIntents(limit: Int): List<DownloadIntentEntity>
    @Query("SELECT * FROM download_intent ORDER BY created_at_ms DESC LIMIT :limit") fun observeDownloadIntents(limit: Int): Flow<List<DownloadIntentEntity>>
    @Query("SELECT * FROM download_intent WHERE server_profile_id = :profileId AND state NOT IN ('CANCELLED','CANCELED') ORDER BY created_at_ms DESC LIMIT :limit") fun observeDownloadIntentsForProfile(profileId: String, limit: Int): Flow<List<DownloadIntentEntity>>
    @Query("SELECT * FROM download_intent WHERE server_profile_id IS NULL AND state NOT IN ('CANCELLED','CANCELED') ORDER BY created_at_ms DESC LIMIT :limit") fun observeStandaloneDownloadIntents(limit: Int): Flow<List<DownloadIntentEntity>>
    @Query("SELECT * FROM download_intent WHERE local_user_track_ref_id = :trackRefId ORDER BY created_at_ms DESC LIMIT :limit") suspend fun downloadIntentsForTrack(trackRefId: String, limit: Int): List<DownloadIntentEntity>
    @Query("SELECT * FROM local_audio_state WHERE storage_class IN (:storageClasses) AND status = 'AVAILABLE' ORDER BY last_accessed_at_ms ASC, created_at_ms ASC LIMIT :limit") suspend fun evictionCandidates(storageClasses: List<String>, limit: Int): List<LocalAudioStateEntity>
}

@Dao
interface QueueDao {
    @Upsert suspend fun upsertSnapshot(row: QueueSnapshotEntity)
    @Upsert suspend fun upsertEntries(rows: List<QueueEntryEntity>)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertSnapshot(row: QueueSnapshotEntity)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertEntries(rows: List<QueueEntryEntity>)
    @Query("SELECT * FROM queue_snapshot WHERE active_slot = 'ACTIVE' LIMIT 1") fun activeSnapshot(): Flow<QueueSnapshotEntity?>
    @Query("SELECT e.* FROM queue_entry e JOIN queue_snapshot s ON s.queue_snapshot_id = e.queue_snapshot_id WHERE s.active_slot = 'ACTIVE' ORDER BY e.position ASC LIMIT :limit") fun activeEntries(limit: Int): Flow<List<QueueEntryEntity>>
    @Query("SELECT * FROM queue_snapshot WHERE active_slot = 'ACTIVE' LIMIT 1") suspend fun activeSnapshotOnce(): QueueSnapshotEntity?
    @Query("SELECT * FROM queue_snapshot WHERE active_slot IS NULL AND active_listening_event_id IS NOT NULL ORDER BY updated_at_ms ASC LIMIT :limit") suspend fun inactiveSnapshotsWithActiveSessions(limit: Int): List<QueueSnapshotEntity>
    @Query("SELECT * FROM queue_snapshot WHERE queue_snapshot_id = :snapshotId") suspend fun snapshot(snapshotId: String): QueueSnapshotEntity?
    @Query("SELECT * FROM queue_entry WHERE queue_entry_id = :entryId") suspend fun entry(entryId: String): QueueEntryEntity?
    @Query("SELECT * FROM queue_entry WHERE queue_snapshot_id = :snapshotId ORDER BY position ASC LIMIT :limit") suspend fun entries(snapshotId: String, limit: Int): List<QueueEntryEntity>
    @Query("DELETE FROM queue_entry WHERE queue_snapshot_id = :snapshotId") suspend fun deleteEntriesForSnapshot(snapshotId: String): Int
    @Query("UPDATE queue_snapshot SET queue_type = :queueType, source_context_id = :sourceContextId, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_slot = 'ACTIVE'")
    suspend fun promoteActiveSnapshot(snapshotId: String, queueType: String, sourceContextId: String?, nowMs: Long): Int
    @Query("UPDATE queue_snapshot SET is_active = 0, active_slot = NULL, updated_at_ms = :nowMs WHERE active_slot = 'ACTIVE' AND queue_snapshot_id != :exceptSnapshotId") suspend fun deactivateOtherSnapshots(exceptSnapshotId: String, nowMs: Long): Int
    @Query("UPDATE queue_snapshot SET current_entry_id = :entryId, current_position_ms = :positionMs, shuffle_mode = :shuffleMode, repeat_mode = :repeatMode, seed = :seed, active_listening_event_id = :listeningEventId, active_session_started_at_ms = :startedAtMs, active_session_start_position_ms = :startPositionMs, active_session_observed_played_ms = :observedPlayedMs, active_session_user_id = :sessionUserId, active_session_device_id = :sessionDeviceId, active_session_server_profile_id = :sessionServerProfileId, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId")
    suspend fun checkpoint(
        snapshotId: String,
        entryId: String?,
        positionMs: Long,
        shuffleMode: String,
        repeatMode: String,
        seed: Long?,
        listeningEventId: String?,
        startedAtMs: Long?,
        startPositionMs: Long?,
        observedPlayedMs: Long?,
        sessionUserId: String?,
        sessionDeviceId: String?,
        sessionServerProfileId: String?,
        nowMs: Long,
    ): Int
    @Query("UPDATE queue_snapshot SET active_listen_excluded_from_taste = 0, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_slot = 'ACTIVE'")
    suspend fun beginActiveListenTasteExclusion(snapshotId: String, nowMs: Long): Int
    @Query("""
        UPDATE queue_snapshot
        SET current_entry_id = :entryId, current_position_ms = :positionMs, shuffle_mode = :shuffleMode,
            repeat_mode = :repeatMode, seed = :seed, updated_at_ms = :nowMs
        WHERE queue_snapshot_id = :snapshotId AND active_slot = 'ACTIVE' AND active_listening_event_id IS NULL
          AND EXISTS (SELECT 1 FROM queue_entry WHERE queue_snapshot_id = :snapshotId AND queue_entry_id = :entryId)
    """)
    suspend fun selectIdleEntry(
        snapshotId: String, entryId: String, positionMs: Long, shuffleMode: String,
        repeatMode: String, seed: Long?, nowMs: Long,
    )
    @Query("UPDATE queue_snapshot SET active_listen_excluded_from_taste = :excluded, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_slot = 'ACTIVE' AND current_entry_id = :entryId AND active_listening_event_id = :eventId")
    suspend fun setActiveListenTasteExclusion(snapshotId: String, entryId: String, eventId: String, excluded: Boolean, nowMs: Long): Int
    @Query("UPDATE queue_snapshot SET session_excluded_from_taste = :excluded, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_slot = 'ACTIVE'")
    suspend fun setSessionTasteExclusion(snapshotId: String, excluded: Boolean, nowMs: Long): Int
    @Query("UPDATE queue_snapshot SET current_entry_id = :entryId, current_position_ms = :positionMs, active_listening_event_id = NULL, active_session_started_at_ms = NULL, active_session_start_position_ms = NULL, active_session_observed_played_ms = NULL, active_session_user_id = NULL, active_session_device_id = NULL, active_session_server_profile_id = NULL, active_listen_excluded_from_taste = 0, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_listening_event_id = :eventId")
    suspend fun clearFinalizedSession(snapshotId: String, eventId: String, entryId: String, positionMs: Long, nowMs: Long): Int
}

@Dao
interface WaveDao {
    @Upsert suspend fun upsertRoom(row: app.autplay.data.local.entity.WaveRoomEntity)
    @Upsert suspend fun upsertPreflight(rows: List<app.autplay.data.local.entity.WavePreflightEntity>)
    @Upsert suspend fun upsertQueue(rows: List<app.autplay.data.local.entity.WaveQueueProjectionEntity>)
    @Query("SELECT * FROM wave_room WHERE room_id = :roomId") suspend fun room(roomId: String): app.autplay.data.local.entity.WaveRoomEntity?
    @Query("UPDATE wave_room SET last_sequence = :sequence, updated_at_ms = :nowMs WHERE room_id = :roomId AND last_sequence < :sequence") suspend fun advanceSequence(roomId: String, sequence: Long, nowMs: Long): Int
    @Query("SELECT * FROM wave_preflight WHERE room_id = :roomId ORDER BY queue_entry_id") suspend fun preflight(roomId: String): List<app.autplay.data.local.entity.WavePreflightEntity>
    @Query("SELECT * FROM wave_queue_projection WHERE room_id = :roomId AND sequence = :sequence ORDER BY position LIMIT :limit") suspend fun queue(roomId: String, sequence: Long, limit: Int): List<app.autplay.data.local.entity.WaveQueueProjectionEntity>
    @Query("DELETE FROM wave_queue_projection WHERE room_id = :roomId") suspend fun clearQueue(roomId: String): Int
    @Query("DELETE FROM wave_preflight WHERE room_id = :roomId") suspend fun clearPreflight(roomId: String): Int

    /** Snapshot replacement is atomic; no partial shared queue is observable. */
    @Transaction
    suspend fun replaceSnapshot(
        room: app.autplay.data.local.entity.WaveRoomEntity,
        preflight: List<app.autplay.data.local.entity.WavePreflightEntity>,
        queue: List<app.autplay.data.local.entity.WaveQueueProjectionEntity>,
    ) {
        clearQueue(room.roomId)
        clearPreflight(room.roomId)
        upsertRoom(room)
        upsertPreflight(preflight)
        upsertQueue(queue)
    }
}

@Dao
interface GuestRoomDao {
    @Upsert
    suspend fun upsert(row: app.autplay.data.local.entity.GuestRoomProjectionEntity)

    @Upsert
    suspend fun upsertPreflight(rows: List<app.autplay.data.local.entity.GuestWavePreflightEntity>)

    @Upsert
    suspend fun upsertQueue(rows: List<app.autplay.data.local.entity.GuestWaveQueueProjectionEntity>)

    @Query("SELECT * FROM guest_room_projection WHERE state = 'ACTIVE' ORDER BY updated_at_ms DESC LIMIT 1")
    fun observeActive(): Flow<app.autplay.data.local.entity.GuestRoomProjectionEntity?>

    @Query("SELECT * FROM guest_room_projection WHERE room_id = :roomId ORDER BY updated_at_ms DESC LIMIT 1")
    suspend fun room(roomId: String): app.autplay.data.local.entity.GuestRoomProjectionEntity?

    @Query("SELECT * FROM guest_room_projection WHERE guest_session_id = :guestSessionId")
    suspend fun session(guestSessionId: String): app.autplay.data.local.entity.GuestRoomProjectionEntity?

    @Query("UPDATE guest_room_projection SET state = :state, updated_at_ms = :nowMs WHERE guest_session_id = :guestSessionId")
    suspend fun markState(guestSessionId: String, state: String, nowMs: Long): Int

    @Query("UPDATE guest_room_projection SET last_sequence = :sequence, room_epoch = :roomEpoch, updated_at_ms = :nowMs WHERE guest_session_id = :guestSessionId AND last_sequence < :sequence")
    suspend fun advance(guestSessionId: String, roomEpoch: String, sequence: Long, nowMs: Long): Int

    @Query("SELECT * FROM guest_wave_preflight WHERE guest_session_id = :guestSessionId ORDER BY queue_entry_id")
    suspend fun preflight(guestSessionId: String): List<app.autplay.data.local.entity.GuestWavePreflightEntity>

    @Query("SELECT * FROM guest_wave_queue_projection WHERE guest_session_id = :guestSessionId AND sequence = :sequence ORDER BY position LIMIT :limit")
    suspend fun queue(guestSessionId: String, sequence: Long, limit: Int): List<app.autplay.data.local.entity.GuestWaveQueueProjectionEntity>

    @Query("DELETE FROM guest_wave_queue_projection WHERE guest_session_id = :guestSessionId")
    suspend fun clearQueue(guestSessionId: String): Int

    @Query("DELETE FROM guest_wave_preflight WHERE guest_session_id = :guestSessionId")
    suspend fun clearPreflight(guestSessionId: String): Int

    @Query("UPDATE guest_room_projection SET state = 'PROCESS_RESTART_REQUIRED', updated_at_ms = :nowMs WHERE state = 'ACTIVE'")
    suspend fun retireProcessLostAuthority(nowMs: Long): Int

    @Query("DELETE FROM guest_room_projection WHERE state != 'ACTIVE' AND updated_at_ms <= :cutoffMs")
    suspend fun deleteTerminalBefore(cutoffMs: Long): Int

    /** Guest snapshot replacement is atomic and never touches account-bound `wave_*` tables. */
    @Transaction
    suspend fun replaceSnapshot(
        room: app.autplay.data.local.entity.GuestRoomProjectionEntity,
        preflight: List<app.autplay.data.local.entity.GuestWavePreflightEntity>,
        queue: List<app.autplay.data.local.entity.GuestWaveQueueProjectionEntity>,
    ) {
        clearQueue(room.guestSessionId)
        clearPreflight(room.guestSessionId)
        upsert(room)
        upsertPreflight(preflight)
        upsertQueue(queue)
    }
}

@Dao
interface HistoryDao {

    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insert(event: ListeningEventEntity)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertOnce(event: ListeningEventEntity): Long
    @Upsert suspend fun upsert(event: ListeningEventEntity)
    @Query("SELECT * FROM listening_event WHERE listening_event_id = :eventId") suspend fun event(eventId: String): ListeningEventEntity?
    @Query("SELECT * FROM listening_event WHERE listening_event_id = :eventId AND server_profile_id = :profileId") suspend fun event(profileId: String, eventId: String): ListeningEventEntity?
    @Query("SELECT * FROM listening_event ORDER BY started_at_ms DESC LIMIT :limit") fun recent(limit: Int): Flow<List<ListeningEventEntity>>
    @Query("SELECT * FROM listening_event WHERE server_profile_id = :profileId ORDER BY started_at_ms DESC LIMIT :limit") fun recentForProfile(profileId: String, limit: Int): Flow<List<ListeningEventEntity>>
    @Query("SELECT * FROM listening_event WHERE server_profile_id = 'legacy-unscoped' ORDER BY started_at_ms DESC LIMIT :limit") fun recentLegacy(limit: Int): Flow<List<ListeningEventEntity>>
    @Query("SELECT e.listening_event_id AS listeningEventId, e.local_user_track_ref_id AS localUserTrackRefId, u.raw_title AS title, u.raw_artist AS artist, e.started_at_ms AS startedAtMs, e.played_ms AS playedMs, e.track_duration_ms AS trackDurationMs, e.completion_ratio AS completionRatio, e.event_origin AS eventOrigin, e.context AS context, e.excluded_from_taste AS excludedFromTaste FROM listening_event e JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id WHERE e.server_profile_id = :profileId ORDER BY e.started_at_ms DESC, e.listening_event_id DESC LIMIT :limit")
    suspend fun presentationFirstPage(profileId: String, limit: Int): List<HistoryPresentationRow>
    @Query("SELECT e.listening_event_id AS listeningEventId, e.local_user_track_ref_id AS localUserTrackRefId, u.raw_title AS title, u.raw_artist AS artist, e.started_at_ms AS startedAtMs, e.played_ms AS playedMs, e.track_duration_ms AS trackDurationMs, e.completion_ratio AS completionRatio, e.event_origin AS eventOrigin, e.context AS context, e.excluded_from_taste AS excludedFromTaste FROM listening_event e JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id WHERE e.server_profile_id = :profileId AND (e.started_at_ms < :beforeStartedAtMs OR (e.started_at_ms = :beforeStartedAtMs AND e.listening_event_id < :beforeEventId)) ORDER BY e.started_at_ms DESC, e.listening_event_id DESC LIMIT :limit")
    suspend fun presentationNextPage(profileId: String, beforeStartedAtMs: Long, beforeEventId: String, limit: Int): List<HistoryPresentationRow>

    /** Includes every retained playback outcome, even when its track is removed from the library. */
    @Query("SELECT COALESCE(SUM(played_ms), 0) FROM listening_event WHERE server_profile_id = :profileId AND started_at_ms <= :throughInclusiveMs AND played_ms > 0")
    suspend fun ownerListenedMs(profileId: String, throughInclusiveMs: Long): Long

    @Query(
        """
        SELECT CASE
                   WHEN u.server_recording_id IS NOT NULL THEN 'server:' || u.server_recording_id
                   WHEN e.server_recording_id IS NOT NULL THEN 'server:' || e.server_recording_id
                   WHEN u.local_recording_id IS NOT NULL THEN 'local:' || u.local_recording_id
                   ELSE 'track:' || e.local_user_track_ref_id
               END AS identity_key,
               MAX(NULLIF(TRIM(u.raw_title), '')) AS title,
               MAX(NULLIF(TRIM(u.raw_artist), '')) AS artist_name,
               COUNT(*) AS play_session_count,
               SUM(e.played_ms) AS listened_ms
        FROM listening_event e
        JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id
                             AND u.server_profile_id = e.server_profile_id
        WHERE e.server_profile_id = :profileId AND e.started_at_ms <= :throughInclusiveMs AND e.played_ms > 0
        GROUP BY identity_key
        ORDER BY listened_ms DESC, play_session_count DESC, identity_key ASC
        LIMIT :limit
        """,
    )
    suspend fun ownerTopTracksSnapshot(profileId: String, throughInclusiveMs: Long, limit: Int): List<OwnerTopTrackProjection>

    @Query(
        """
        SELECT TRIM(u.raw_artist) AS artist_name, COUNT(*) AS play_session_count, SUM(e.played_ms) AS listened_ms
        FROM listening_event e
        JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id
                             AND u.server_profile_id = e.server_profile_id
        WHERE e.server_profile_id = :profileId AND e.started_at_ms <= :throughInclusiveMs AND e.played_ms > 0
          AND NULLIF(TRIM(u.raw_artist), '') IS NOT NULL
        GROUP BY TRIM(u.raw_artist)
        ORDER BY listened_ms DESC, play_session_count DESC, artist_name ASC
        LIMIT :limit
        """,
    )
    suspend fun ownerTopArtistsSnapshot(profileId: String, throughInclusiveMs: Long, limit: Int): List<OwnerTopArtistProjection>

    /** Page by stable track identity, not event count; no archive horizon or top-track cutoff. */
    @Query(
        """
        SELECT e.local_user_track_ref_id AS local_track_ref_id, SUM(e.played_ms) AS listened_ms, m.payload_json
        FROM listening_event e
        JOIN track_metadata_projection m ON m.local_user_track_ref_id = e.local_user_track_ref_id
                                        AND m.server_profile_id = e.server_profile_id
        WHERE e.server_profile_id = :profileId AND e.started_at_ms <= :throughInclusiveMs AND e.played_ms > 0
          AND e.local_user_track_ref_id > :afterTrackId
        GROUP BY e.local_user_track_ref_id
        ORDER BY e.local_user_track_ref_id ASC
        LIMIT :limit
        """,
    )
    suspend fun ownerGenreSourcesPage(profileId: String, throughInclusiveMs: Long, afterTrackId: String, limit: Int): List<OwnerGenreSourceProjection>

}

data class HistoryPresentationRow(
    val listeningEventId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artist: String?,
    val startedAtMs: Long,
    val playedMs: Long,
    val trackDurationMs: Long?,
    val completionRatio: Double?,
    val eventOrigin: String,
    val context: String,
    val excludedFromTaste: Boolean,
)

data class OwnerGenreSourceProjection(
    @androidx.room3.ColumnInfo(name = "local_track_ref_id") val localTrackRefId: String,
    @androidx.room3.ColumnInfo(name = "listened_ms") val listenedMs: Long,
    @androidx.room3.ColumnInfo(name = "payload_json") val payloadJson: String,
)

data class OwnerTopTrackProjection(
    @androidx.room3.ColumnInfo(name = "identity_key") val identityKey: String,
    val title: String?,
    @androidx.room3.ColumnInfo(name = "artist_name") val artistName: String?,
    @androidx.room3.ColumnInfo(name = "play_session_count") val playSessionCount: Long,
    @androidx.room3.ColumnInfo(name = "listened_ms") val listenedMs: Long,
)

data class OwnerTopArtistProjection(
    @androidx.room3.ColumnInfo(name = "artist_name") val artistName: String?,
    @androidx.room3.ColumnInfo(name = "play_session_count") val playSessionCount: Long,
    @androidx.room3.ColumnInfo(name = "listened_ms") val listenedMs: Long,
)

@Dao
interface JournalDao {
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insert(event: OfflineJournalEventEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertLineage(lineage: JournalLineageEntity): Long

    @Query("SELECT * FROM journal_lineage WHERE lineage_id = :lineageId")
    suspend fun lineageById(lineageId: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE device_id = :deviceId")
    suspend fun lineageByDeviceId(deviceId: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE journal_epoch = :journalEpoch")
    suspend fun lineageByJournalEpoch(journalEpoch: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE user_id = :userId AND device_id = :deviceId AND journal_epoch = :journalEpoch")
    suspend fun lineageByBinding(userId: String, deviceId: String, journalEpoch: String): JournalLineageEntity?

    @Query("SELECT count(*) FROM journal_lineage")
    suspend fun countLineages(): Int

    @Query("UPDATE journal_lineage SET next_device_sequence = next_device_sequence + 1 WHERE lineage_id = :lineageId")
    suspend fun incrementSequence(lineageId: String): Int

    @Query("SELECT next_device_sequence - 1 FROM journal_lineage WHERE lineage_id = :lineageId")
    suspend fun allocatedSequence(lineageId: String): Long?

    @Transaction
    suspend fun allocateSequence(lineageId: String): Long {
        check(incrementSequence(lineageId) == 1) { "JOURNAL_LINEAGE_NOT_FOUND" }
        return checkNotNull(allocatedSequence(lineageId))
    }

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertOutbox(row: LocalMutationOutboxEntity)

    @Query("SELECT * FROM local_mutation_outbox WHERE local_change_id = :localChangeId")
    suspend fun outbox(localChangeId: String): LocalMutationOutboxEntity?

    @Query("SELECT * FROM local_mutation_outbox WHERE materialization_state = 'UNMATERIALIZED' AND materialized_event_id IS NULL ORDER BY occurred_at_ms ASC, local_change_id ASC LIMIT :limit")
    suspend fun pendingOutbox(limit: Int): List<LocalMutationOutboxEntity>

    @Query("SELECT count(*) FROM local_mutation_outbox")
    suspend fun outboxCount(): Int

    @Query("UPDATE local_mutation_outbox SET materialization_state = 'MATERIALIZED', materialized_event_id = :eventId, materialized_at_ms = :materializedAtMs WHERE local_change_id = :localChangeId AND materialization_state = 'UNMATERIALIZED' AND materialized_event_id IS NULL")
    suspend fun linkOutboxMaterialized(localChangeId: String, eventId: String, materializedAtMs: Long): Int

    @Query("SELECT * FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state = 'PENDING' AND (next_attempt_at_ms IS NULL OR next_attempt_at_ms <= :nowMs) ORDER BY device_sequence ASC LIMIT :limit")
    suspend fun nextPending(lineageId: String, nowMs: Long, limit: Int): List<OfflineJournalEventEntity>

    @Query("UPDATE offline_journal_event SET state = 'PENDING', lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND state = 'SENDING' AND lease_expires_at_ms <= :nowMs")
    suspend fun recoverExpiredLeases(lineageId: String, nowMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = 'PENDING', attempt_count = 0, next_attempt_at_ms = NULL, last_error_code = NULL, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND state = 'DEAD_LETTER' AND last_error_code = 'JOURNAL_RESET_REQUIRED'")
    suspend fun recoverUnboundJournal(lineageId: String): Int

    @Query("UPDATE offline_journal_event SET state = 'SENDING', lease_token = :leaseToken, lease_expires_at_ms = :leaseExpiresAtMs, attempt_count = attempt_count + 1 WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'PENDING'")
    suspend fun lease(lineageId: String, eventId: String, leaseToken: String, leaseExpiresAtMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = 'ACKED', acked_at_ms = :ackedAtMs, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun acknowledge(lineageId: String, eventId: String, leaseToken: String, ackedAtMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = :state, next_attempt_at_ms = :nextAttemptAtMs, last_error_code = :errorCode, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun finishAttempt(lineageId: String, eventId: String, leaseToken: String, state: String, nextAttemptAtMs: Long?, errorCode: String?): Int

    @Query("UPDATE offline_journal_event SET state = 'PENDING', attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END, next_attempt_at_ms = NULL, last_error_code = 'SESSION_REQUIRED', lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun releaseForSession(lineageId: String, eventId: String, leaseToken: String): Int

    @Query("UPDATE offline_journal_event SET state = 'PENDING', attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END, next_attempt_at_ms = NULL, last_error_code = NULL, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun releaseCancelledLease(lineageId: String, leaseToken: String): Int

    @Query("SELECT * FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND device_sequence >= :fromSequence AND state IN ('ACKED', 'DEAD_LETTER') ORDER BY device_sequence ASC LIMIT :limit")
    suspend fun compactable(lineageId: String, fromSequence: Long, limit: Int): List<OfflineJournalEventEntity>

    @Query("DELETE FROM offline_journal_event WHERE event_id IN (:eventIds) AND state IN ('ACKED', 'DEAD_LETTER')")
    suspend fun deleteTerminal(eventIds: List<String>): Int

    @Query("""SELECT EXISTS (SELECT 1 FROM offline_journal_event WHERE journal_lineage_id = :lineageId
        AND device_sequence = :sequence AND aggregate_local_id = :trackId
        AND event_type = 'USER_TRACK_PREFERENCE_SET' AND state = 'ACKED')""")
    suspend fun preferenceWasAcknowledged(lineageId: String, trackId: String, sequence: Long): Boolean

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state IN ('PENDING', 'SENDING')")
    fun observePendingCount(lineageId: String): Flow<Int>

    @Query("""SELECT EXISTS(SELECT 1 FROM offline_journal_event WHERE journal_lineage_id = :lineageId
        AND aggregate_local_id IN (:ids) AND aggregate_server_id IS NULL AND state IN ('PENDING', 'SENDING')
        AND event_type IN ('USER_TRACK_REF_CREATED','PLAYLIST_CREATED','PLAYLIST_ENTRY_UPSERTED','LIBRARY_ENTRY_UPSERTED'))""")
    suspend fun hasUnacknowledgedCreate(lineageId: String, ids: List<String>): Boolean

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state = 'DEAD_LETTER'")
    fun observeDeadLetterCount(lineageId: String): Flow<Int>

    @Query("SELECT * FROM offline_journal_event WHERE event_id = :eventId")
    suspend fun event(eventId: String): OfflineJournalEventEntity?

    @Query("SELECT count(*) FROM offline_journal_event")
    suspend fun eventCount(): Int

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId")
    suspend fun eventCountForLineage(lineageId: String): Int
}

@Dao
interface SyncDao {
    @Upsert suspend fun upsertCursor(cursor: SyncCursorEntity)
    @Upsert suspend fun upsertRuntimeStatus(status: SyncRuntimeStatusEntity)
    @Query("SELECT * FROM sync_runtime_status WHERE server_profile_id = :profileId") suspend fun runtimeStatus(profileId: String): SyncRuntimeStatusEntity?
    @Query("SELECT * FROM sync_runtime_status WHERE server_profile_id = :profileId") fun observeRuntimeStatus(profileId: String): Flow<SyncRuntimeStatusEntity?>
    @Upsert suspend fun upsertBootstrapState(state: SyncBootstrapStateEntity)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertInteractionFact(fact: RecommendationInteractionFactEntity): Long
    @Query("SELECT * FROM sync_bootstrap_state WHERE server_profile_id = :profileId") suspend fun bootstrapState(profileId: String): SyncBootstrapStateEntity?
    @Query("SELECT * FROM sync_cursor WHERE server_profile_id = :profileId") suspend fun cursor(profileId: String): SyncCursorEntity?
    @Query("SELECT * FROM sync_cursor WHERE server_profile_id = :profileId") fun observeCursor(profileId: String): Flow<SyncCursorEntity?>
    @Query("UPDATE sync_cursor SET last_acked_device_sequence = :sequence, updated_at_ms = :nowMs WHERE server_profile_id = :profileId AND last_acked_device_sequence < :sequence AND NOT EXISTS (SELECT 1 FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND device_sequence <= :sequence AND state NOT IN ('ACKED', 'DEAD_LETTER', 'CONFLICT'))") suspend fun advanceAck(profileId: String, lineageId: String, sequence: Long, nowMs: Long): Int
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun markApplied(event: AppliedServerEventEntity): Long
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun defer(event: DeferredServerEventEntity): Long
    @Query("SELECT EXISTS(SELECT 1 FROM applied_server_event WHERE server_profile_id = :profileId AND server_event_id = :eventId) OR EXISTS(SELECT 1 FROM deferred_server_event WHERE server_profile_id = :profileId AND server_event_id = :eventId)") suspend fun isServerEventKnown(profileId: String, eventId: String): Boolean
    @Upsert suspend fun upsertRedirect(redirect: AggregateRedirectEntity)
    @Query("SELECT * FROM aggregate_redirect WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND alias_local_id = :aliasLocalId") suspend fun redirect(profileId: String, aggregateType: String, aliasLocalId: String): AggregateRedirectEntity?
    @Query("SELECT * FROM aggregate_redirect WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND alias_server_id = :aliasServerId") suspend fun redirectByServerId(profileId: String, aggregateType: String, aliasServerId: String): AggregateRedirectEntity?
    @Upsert suspend fun upsertTombstone(row: TombstoneEntity)
    @Query("SELECT * FROM tombstone WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND aggregate_server_id = :serverId LIMIT 1") suspend fun tombstoneByServerId(profileId: String, aggregateType: String, serverId: String): TombstoneEntity?
    @Upsert suspend fun upsertConflict(row: SyncConflictEntity)
    @Query("SELECT count(*) FROM sync_conflict WHERE server_profile_id = :profileId AND status = 'OPEN'") fun observeOpenConflictCount(profileId: String): Flow<Int>
    @Query("DELETE FROM tombstone WHERE server_profile_id = :profileId AND server_acked = 1 AND retain_until_ms <= :nowMs") suspend fun compactTombstones(profileId: String, nowMs: Long): Int
}

data class LocalRecommendationCandidateRow(
    val localUserTrackRefId: String,
    val recordingId: String,
    val title: String,
    val artist: String,
    val isLocallyAvailable: Boolean,
    val preference: String,
    val excludedFromTaste: Boolean,
    val preferenceUpdatedAtMs: Long,
    val latestListenedAtMs: Long?,
    val latestSkipAtMs: Long?,
)

data class RecentRelevantReleaseRow(
    val profileId: String,
    val localReleaseId: String,
    val title: String,
    val artist: String,
    val releaseDateText: String?,
    val artworkRef: String?,
    val projectionUpdatedAtMs: Long,
)

/** Bounded raw source row used only to derive one immutable device-local R1B delta. */
data class LocalTemporalJournalRow(
    val sourceEventId: String,
    val ownerUserId: String,
    val serverProfileId: String,
    val deviceId: String,
    val deviceSequence: Long,
    val sourceEventType: String,
    val aggregateLocalId: String,
    val payloadJson: String,
    val sourceRequestSha256: ByteArray,
    val occurredAtMs: Long,
    val recordingId: String?,
)

@Dao
interface RecommendationPackDao {
    @Upsert suspend fun upsert(pack: RecommendationPackEntity)

    @Query("SELECT * FROM recommendation_pack WHERE server_profile_id = :profileId AND owner_user_id = :userId ORDER BY created_at_ms DESC, offline_pack_id DESC LIMIT :limit")
    suspend fun latest(profileId: String, userId: String, limit: Int): List<RecommendationPackEntity>

    @Query("SELECT * FROM recommendation_pack WHERE offline_pack_id = :packId AND server_profile_id = :profileId AND owner_user_id = :userId LIMIT 1")
    suspend fun pack(packId: String, profileId: String, userId: String): RecommendationPackEntity?

    @Query("SELECT * FROM recommendation_pack WHERE server_profile_id = :profileId AND owner_user_id = :userId AND expires_at_ms > :nowMs ORDER BY created_at_ms DESC, offline_pack_id DESC LIMIT 1")
    suspend fun active(profileId: String, userId: String, nowMs: Long): RecommendationPackEntity?

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertDelta(delta: RecommendationTemporalDeltaEntity)

    @Query("SELECT * FROM recommendation_temporal_delta WHERE server_profile_id = :profileId AND owner_user_id = :userId AND device_id = :deviceId AND offline_pack_id = :packId AND recommendation_request_id = :requestId LIMIT 1")
    suspend fun delta(profileId: String, userId: String, deviceId: String, packId: String, requestId: String): RecommendationTemporalDeltaEntity?

    @Query("DELETE FROM recommendation_temporal_delta WHERE expires_at_ms <= :nowMs")
    suspend fun deleteExpiredDeltas(nowMs: Long): Int

    @Query("DELETE FROM recommendation_presentation WHERE server_profile_id = :profileId")
    suspend fun deletePresentationsForProfile(profileId: String): Int

    @Query("DELETE FROM recommendation_temporal_delta WHERE server_profile_id = :profileId")
    suspend fun deleteDeltasForProfile(profileId: String): Int

    @Query("DELETE FROM recommendation_pack WHERE server_profile_id = :profileId")
    suspend fun deletePacksForProfile(profileId: String): Int

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertPresentation(row: RecommendationPresentationEntity)

    @Query("SELECT * FROM recommendation_presentation WHERE server_profile_id = :profileId AND owner_user_id = :userId AND presentation_id = :presentationId AND recommendation_request_id = :requestId AND source_rank = :sourceRank")
    suspend fun presentation(profileId: String, userId: String, presentationId: String, requestId: String, sourceRank: Int): RecommendationPresentationEntity?

    @Query("SELECT * FROM recommendation_presentation WHERE impression_event_id = :eventId")
    suspend fun presentationByEventId(eventId: String): RecommendationPresentationEntity?

    @Query("SELECT count(*) FROM recommendation_presentation WHERE server_profile_id = :profileId AND owner_user_id = :userId")
    suspend fun presentationCount(profileId: String, userId: String): Int

    @Query("SELECT count(*) FROM recommendation_temporal_delta WHERE server_profile_id = :profileId AND owner_user_id = :userId")
    suspend fun deltaCount(profileId: String, userId: String): Int

    @Query(
        """
        SELECT
            e.event_id AS sourceEventId,
            e.user_id AS ownerUserId,
            e.server_profile_id AS serverProfileId,
            e.device_id AS deviceId,
            e.device_sequence AS deviceSequence,
            e.event_type AS sourceEventType,
            e.aggregate_local_id AS aggregateLocalId,
            e.payload_json AS payloadJson,
            e.request_hash AS sourceRequestSha256,
            e.occurred_at_ms AS occurredAtMs,
            COALESCE(preference_track.server_recording_id, listening_track.server_recording_id) AS recordingId
        FROM offline_journal_event e
        LEFT JOIN user_track_ref preference_track
          ON e.event_type = 'USER_TRACK_PREFERENCE_SET'
         AND preference_track.local_user_track_ref_id = e.aggregate_local_id
         AND preference_track.server_profile_id = e.server_profile_id
         AND preference_track.deleted_at_ms IS NULL
        LEFT JOIN listening_event listening
          ON e.event_type = 'LISTENING_EVENT_RECORDED'
         AND listening.listening_event_id = e.aggregate_local_id
         AND listening.server_profile_id = e.server_profile_id
        LEFT JOIN user_track_ref listening_track
          ON listening_track.local_user_track_ref_id = listening.local_user_track_ref_id
         AND listening_track.server_profile_id = e.server_profile_id
         AND listening_track.deleted_at_ms IS NULL
        WHERE e.server_profile_id = :profileId
          AND e.user_id = :userId
          AND e.device_id = :deviceId
          AND e.event_type IN ('USER_TRACK_PREFERENCE_SET', 'LISTENING_EVENT_RECORDED')
          AND e.state IN ('PENDING', 'SENDING')
          AND e.occurred_at_ms > :cutoffAtMs
          AND e.occurred_at_ms <= :nowMs
        ORDER BY e.occurred_at_ms DESC, e.device_sequence DESC, e.event_id DESC
        LIMIT :limit
        """,
    )
    suspend fun localTemporalEvents(
        profileId: String,
        userId: String,
        deviceId: String,
        cutoffAtMs: Long,
        nowMs: Long,
        limit: Int,
    ): List<LocalTemporalJournalRow>

    @Query(
        """
        SELECT
            u.local_user_track_ref_id AS localUserTrackRefId,
            u.server_recording_id AS recordingId,
            COALESCE(r.title, u.raw_title, 'Unknown track') AS title,
            COALESCE(r.display_artist, u.raw_artist, 'Unknown artist') AS artist,
            CASE WHEN
                le.availability_status = 'LOCAL'
                OR EXISTS (
                    SELECT 1 FROM local_audio_state a
                    WHERE a.local_user_track_ref_id = u.local_user_track_ref_id
                      AND a.status = 'AVAILABLE'
                )
                OR EXISTS (
                    SELECT 1 FROM download_intent d
                    WHERE d.local_user_track_ref_id = u.local_user_track_ref_id
                      AND d.state = 'COMPLETED'
                )
            THEN 1 ELSE 0 END AS isLocallyAvailable,
            COALESCE(p.preference, 'NEUTRAL') AS preference,
            COALESCE(p.excluded_from_taste, 0) AS excludedFromTaste,
            COALESCE(p.updated_at_ms, 0) AS preferenceUpdatedAtMs,
            (
                SELECT MAX(h.started_at_ms) FROM listening_event h
                WHERE h.server_profile_id = :profileId
                  AND h.local_user_track_ref_id = u.local_user_track_ref_id
            ) AS latestListenedAtMs,
            (
                SELECT MAX(h.started_at_ms) FROM listening_event h
                WHERE h.server_profile_id = :profileId
                  AND h.local_user_track_ref_id = u.local_user_track_ref_id
                  AND (
                    (h.completion_ratio IS NOT NULL AND h.completion_ratio < 0.2)
                    OR (h.completion_ratio IS NULL AND h.played_ms < 30000)
                  )
            ) AS latestSkipAtMs
        FROM sync_cursor sc
        JOIN journal_lineage owner
          ON owner.lineage_id = sc.journal_lineage_id
         AND owner.user_id = :userId
        JOIN user_track_ref u ON u.server_profile_id = sc.server_profile_id
        LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id
        LEFT JOIN library_entry le
          ON le.local_user_track_ref_id = u.local_user_track_ref_id
         AND le.server_profile_id = :profileId
         AND le.removed_at_ms IS NULL
        LEFT JOIN user_track_preference p
          ON p.local_user_track_ref_id = u.local_user_track_ref_id
         AND p.server_profile_id = :profileId
        WHERE sc.server_profile_id = :profileId
          AND u.server_profile_id = :profileId
          AND u.server_recording_id IN (:recordingIds)
          AND u.deleted_at_ms IS NULL
          AND (
              (u.local_recording_id IS NULL AND u.resolution_status = 'RESOLVED')
              OR (r.is_deleted = 0 AND r.redirect_server_recording_id IS NULL
                  AND r.server_recording_id = u.server_recording_id)
          )
          AND NOT EXISTS (
              SELECT 1 FROM tombstone t
              WHERE t.server_profile_id = :profileId AND t.aggregate_type = 'RECORDING'
                AND t.aggregate_server_id = u.server_recording_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM aggregate_redirect redirect
              WHERE redirect.server_profile_id = :profileId AND redirect.aggregate_type = 'RECORDING'
                AND redirect.alias_server_id = u.server_recording_id
          )
        ORDER BY u.local_user_track_ref_id ASC
        LIMIT :limit
        """,
    )
    suspend fun localCandidates(profileId: String, userId: String, recordingIds: List<String>, limit: Int): List<LocalRecommendationCandidateRow>

    @Query(
        """
        SELECT DISTINCT
            sc.server_profile_id AS profileId,
            rel.local_release_id AS localReleaseId,
            rel.title AS title,
            rel.display_artist AS artist,
            rel.release_date_text AS releaseDateText,
            rel.artwork_ref AS artworkRef,
            rel.projection_updated_at_ms AS projectionUpdatedAtMs
        FROM sync_cursor sc
        JOIN journal_lineage owner
          ON owner.lineage_id = sc.journal_lineage_id
         AND owner.user_id = :userId
        CROSS JOIN release_projection rel
        WHERE sc.server_profile_id = :profileId
          AND rel.is_deleted = 0
          AND length(rel.release_date_text) = 10
          AND rel.release_date_text BETWEEN :firstDay AND :lastDay
          AND EXISTS (
            SELECT 1
            FROM user_track_ref u
            JOIN recording_projection owned ON owned.local_recording_id = u.local_recording_id
            JOIN library_entry le
              ON le.local_user_track_ref_id = u.local_user_track_ref_id
             AND le.server_profile_id = :profileId
             AND le.removed_at_ms IS NULL
            WHERE u.server_profile_id = :profileId
              AND u.deleted_at_ms IS NULL
              AND owned.is_deleted = 0
              AND owned.normalized_artist = LOWER(TRIM(rel.display_artist))
          )
        ORDER BY rel.release_date_text DESC, rel.projection_updated_at_ms DESC, rel.local_release_id ASC
        """,
    )
    suspend fun recentRelevantReleases(profileId: String, userId: String, firstDay: String, lastDay: String): List<RecentRelevantReleaseRow>

    @Query("""
        WITH evidence(ref, liked, day) AS (
            SELECT local_user_track_ref_id, 1, -1 FROM user_track_preference
            WHERE server_profile_id = :profile AND preference = 'LIKED' AND excluded_from_taste = 0
            UNION ALL
            SELECT DISTINCT e.local_user_track_ref_id, 0, e.started_at_ms / 86400000
            FROM listening_event e
            WHERE e.server_profile_id = :profile AND e.started_at_ms BETWEEN :since AND :through
              AND e.excluded_from_taste = 0 AND (e.played_ms >= 30000 OR e.completion_ratio >= 0.8)
        )
        SELECT DISTINCT coalesce(r.display_artist,u.raw_artist,'') AS artist,
            CASE WHEN liked = 1 THEN coalesce(u.server_recording_id,u.local_user_track_ref_id) END AS likedTrack,
            day FROM evidence JOIN user_track_ref u ON u.local_user_track_ref_id = ref
            LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id
            LEFT JOIN user_track_preference p ON p.local_user_track_ref_id = ref AND p.server_profile_id = :profile
        WHERE u.server_profile_id = :profile AND u.deleted_at_ms IS NULL AND coalesce(r.is_deleted,0) = 0
          AND coalesce(p.excluded_from_taste,0) = 0 AND coalesce(p.preference,'') != 'DISLIKED'
    """)
    suspend fun releaseArtistActivity(profile: String, since: Long, through: Long): List<ReleaseArtistActivity>

}

@Dao
interface SearchDao {
    @Query("""
        SELECT u.local_user_track_ref_id FROM user_track_ref u
        WHERE u.server_profile_id = :profileId AND u.deleted_at_ms IS NULL
          AND u.local_user_track_ref_id IN (:trackIds)
          AND (EXISTS (
              SELECT 1 FROM local_audio_state a WHERE a.local_user_track_ref_id = u.local_user_track_ref_id
                AND a.status = 'AVAILABLE' AND (a.persisted_uri_permission = 1 OR a.content_uri LIKE 'content://media/%')
          ) OR EXISTS (
              SELECT 1 FROM download_intent d WHERE d.local_user_track_ref_id = u.local_user_track_ref_id
                AND COALESCE(d.server_profile_id, 'legacy-unscoped') = u.server_profile_id
                AND d.state = 'COMPLETED' AND d.media3_download_id IS NOT NULL
                AND (:permanentOnly = 0 OR d.desired_storage_class IN ('PINNED','USER_DOWNLOAD'))
          ))
    """)
    fun offlineTrackIds(profileId: String, trackIds: List<String>, permanentOnly: Boolean = false): Flow<List<String>>

    @Query("""
        SELECT DISTINCT json_extract(m.payload_json, '$.fields.mb_recording_id')
        FROM track_metadata_projection m JOIN user_track_ref u
          ON u.local_user_track_ref_id=m.local_user_track_ref_id AND u.server_profile_id=m.server_profile_id
        WHERE u.server_profile_id=:profile AND u.deleted_at_ms IS NULL
          AND json_extract(m.payload_json, '$.fields.mb_recording_id') IN (:mbids)
          AND (EXISTS (SELECT 1 FROM local_audio_state a WHERE a.local_user_track_ref_id=u.local_user_track_ref_id
               AND a.status='AVAILABLE' AND (a.persisted_uri_permission=1 OR a.content_uri LIKE 'content://media/%'))
            OR EXISTS (SELECT 1 FROM download_intent d WHERE d.local_user_track_ref_id=u.local_user_track_ref_id
               AND d.server_profile_id=u.server_profile_id AND d.state='COMPLETED' AND d.media3_download_id IS NOT NULL))
    """)
    fun offlineCatalogueRecordings(profile: String, mbids: List<String>): Flow<List<String>>

    @Query("""SELECT json_extract(m.payload_json, '$.fields.mb_recording_id') AS mbid,
        u.local_user_track_ref_id AS trackId, u.server_recording_id AS recordingId,
        CASE WHEN json_type(m.payload_json,'$.fields.title') IS NULL THEN coalesce(u.raw_title, r.title)
            ELSE json_extract(m.payload_json,'$.fields.title') END AS title,
        CASE WHEN json_type(m.payload_json,'$.fields.artist') IS NULL THEN coalesce(u.raw_artist, r.display_artist)
            ELSE json_extract(m.payload_json,'$.fields.artist') END AS artist,
        max(e.started_at_ms) AS lastPlayedMs
        FROM listening_event e JOIN user_track_ref u ON u.local_user_track_ref_id = e.local_user_track_ref_id
            AND u.server_profile_id = e.server_profile_id AND u.deleted_at_ms IS NULL
        LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id AND r.is_deleted = 0
        LEFT JOIN track_metadata_projection m ON m.server_profile_id = u.server_profile_id
            AND m.local_user_track_ref_id = u.local_user_track_ref_id
        WHERE e.server_profile_id = :profile AND e.played_ms > 0
            AND (json_extract(m.payload_json, '$.fields.mb_recording_id') IN (:mbids)
                OR (:artistId IS NOT NULL AND EXISTS (
                    SELECT 1 FROM catalog_artist_credit_link l
                    JOIN artist_credit_name_projection n ON n.server_profile_id = l.server_profile_id
                        AND n.server_artist_credit_id = l.server_artist_credit_id
                    JOIN catalog_artist_credit_link_owner o ON o.server_profile_id = l.server_profile_id
                        AND o.subject_type = l.subject_type AND o.subject_server_id = l.subject_server_id
                        AND o.owner_scope_id = l.owner_scope_id
                    WHERE l.server_profile_id = u.server_profile_id AND l.subject_type = 'RECORDING'
                        AND l.subject_server_id = u.server_recording_id AND o.owner_recording_id = u.server_recording_id
                        AND l.deleted_at_ms IS NULL AND l.owner_scope_complete = 1 AND n.server_artist_id = :artistId)))
        GROUP BY u.local_user_track_ref_id""")
    fun heardCatalogueRecordings(profile: String, artistId: String?, mbids: List<String>): Flow<List<HeardCatalogueTrack>>

    @Query("""
        SELECT local_user_track_ref_id FROM user_track_ref
        WHERE server_profile_id=:profile AND deleted_at_ms IS NULL AND local_user_track_ref_id>:after
            AND (raw_artist IS NOT NULL OR instr(raw_title, '.')>0 OR instr(raw_title, '_')>0 OR instr(raw_title, '|')>0 OR instr(raw_title, ' ')>0
            OR instr(raw_title, ' - ')>0 OR instr(raw_title, ' – ')>0 OR instr(raw_title, ' — ')>0
            OR instr(raw_title, '(')>0 OR instr(raw_title, '[')>0
            OR EXISTS (SELECT 1 FROM track_metadata_projection m
              WHERE m.server_profile_id=:profile AND m.local_user_track_ref_id=user_track_ref.local_user_track_ref_id
                  AND json_type(m.payload_json,'$.fields.title')='text'))
        ORDER BY local_user_track_ref_id LIMIT :limit
    """)
    suspend fun filenameIndexBatch(profile: String, after: String, limit: Int): List<String>

    @Query("SELECT * FROM track_search_content WHERE local_user_track_ref_id = :trackRefId LIMIT 1") suspend fun contentForTrack(trackRefId: String): TrackSearchContentEntity?
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertContent(row: TrackSearchContentEntity): Long
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertContents(rows: List<TrackSearchContentEntity>): List<Long>
    @Update suspend fun updateContent(row: TrackSearchContentEntity): Int
    @Delete suspend fun deleteContent(row: TrackSearchContentEntity): Int
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid WHERE track_search_fts MATCH :query ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun search(query: String, limit: Int): List<String>
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid JOIN user_track_ref u ON u.local_user_track_ref_id = c.local_user_track_ref_id WHERE track_search_fts MATCH :query AND u.deleted_at_ms IS NULL AND u.server_profile_id = :profileId ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun searchForProfile(query: String, profileId: String, limit: Int): List<String>
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid JOIN user_track_ref u ON u.local_user_track_ref_id = c.local_user_track_ref_id WHERE track_search_fts MATCH :query AND u.deleted_at_ms IS NULL AND u.server_profile_id = 'legacy-unscoped' ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun searchLegacy(query: String, limit: Int): List<String>
    @Query("DELETE FROM track_search_content") suspend fun clearContent(): Int
}

data class ReleaseArtistActivity(val artist: String, val likedTrack: String?, val day: Long)
