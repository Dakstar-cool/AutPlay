package app.autplay.data.local

import androidx.room3.Dao
import androidx.room3.Entity
import androidx.room3.Index
import androidx.room3.Insert
import androidx.room3.PrimaryKey
import androidx.room3.Query
import app.autplay.application.artist.ArtistSummary
import app.autplay.application.artist.displayArtists
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import app.autplay.application.library.albumKey
import java.util.Locale

/** An append records both the preference change and its local ordering, including unlikes. */
@Entity(tableName = "collection_preference_event", indices = [Index(value = ["owner", "kind", "target"])])
data class CollectionPreferenceEvent(@PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val owner: String, val kind: String, val target: String, val liked: Boolean, val route: String? = null, val label: String? = null)

data class PreferenceCandidate(val ref: String, val kind: String, val target: String)

fun artistPreferenceTarget(artist: ArtistSummary): String =
    if (artist.preferenceByName)
        "tag:" + artist.name.trim().lowercase(Locale.ROOT)
    else artist.key.artistId.value

@Dao
interface CollectionPreferenceDao {
    @Insert suspend fun record(event: CollectionPreferenceEvent)
    @Query("SELECT liked FROM collection_preference_event WHERE owner=:owner AND kind=:kind AND target=:target ORDER BY sequence DESC LIMIT 1")
    fun observe(owner: String, kind: String, target: String): Flow<Boolean?>

    @Query("""SELECT e.* FROM collection_preference_event e WHERE owner=:owner AND liked=1 AND
        sequence=(SELECT max(sequence) FROM collection_preference_event n
            WHERE n.owner=e.owner AND n.kind=e.kind AND n.target=e.target)""")
    fun observeFavorites(owner: String): Flow<List<CollectionPreferenceEvent>>

    @Query("""WITH candidates AS (
        SELECT u.* FROM user_track_ref u WHERE u.server_profile_id=:profile AND u.deleted_at_ms IS NULL
            AND u.local_user_track_ref_id IN (:ids)
    )
    SELECT u.local_user_track_ref_id AS ref,'artist' AS kind,n.server_artist_id AS target
    FROM candidates u JOIN catalog_artist_credit_link l ON l.server_profile_id=u.server_profile_id
        AND l.subject_type='RECORDING' AND l.subject_server_id=u.server_recording_id AND l.deleted_at_ms IS NULL
        AND l.owner_scope_complete=1
    JOIN artist_credit_name_projection n ON n.server_profile_id=l.server_profile_id AND n.server_artist_credit_id=l.server_artist_credit_id
    UNION ALL SELECT u.local_user_track_ref_id,'artist','mb:' || json_extract(c.document,'$.artist_mbid')
        FROM candidates u JOIN catalog_artist_credit_link l ON l.server_profile_id=u.server_profile_id
            AND l.subject_type='RECORDING' AND l.subject_server_id=u.server_recording_id
            AND l.owner_scope_complete=1 AND l.deleted_at_ms IS NULL
        JOIN artist_credit_name_projection n ON n.server_profile_id=l.server_profile_id
            AND n.server_artist_credit_id=l.server_artist_credit_id
        JOIN catalog_page c ON c.profile=:profile AND c.pageKey='ranking-v2:' || :owner || ':' || n.server_artist_id
        WHERE json_extract(c.document,'$.artist_mbid') IS NOT NULL
    UNION ALL SELECT u.local_user_track_ref_id,'credit',coalesce(json_extract(m.payload_json,'$.fields.artist'),u.raw_artist,'')
        FROM candidates u LEFT JOIN track_metadata_projection m ON m.server_profile_id=u.server_profile_id AND m.local_user_track_ref_id=u.local_user_track_ref_id
    UNION ALL SELECT u.local_user_track_ref_id,'album',rt.local_release_id
        FROM candidates u JOIN release_track_projection rt ON rt.local_recording_id=u.local_recording_id
    UNION ALL SELECT u.local_user_track_ref_id,'album',coalesce('mb:' || e.releaseMbid, 'catalog-edition:' || e.releaseId)
        FROM candidates u JOIN catalog_edition_track e ON e.profile=:profile AND e.recordingId=u.server_recording_id AND e.deleted=0
    UNION ALL SELECT u.local_user_track_ref_id,'album',CASE WHEN json_extract(m.payload_json,'$.album_group_v1.provider')='MUSICBRAINZ'
        THEN 'mb:' || json_extract(m.payload_json,'$.album_group_v1.release_id')
        ELSE 'metadata-album:' || json_extract(m.payload_json,'$.album_group_v1.key') END
        FROM candidates u JOIN track_metadata_projection m ON m.server_profile_id=u.server_profile_id
        AND m.local_user_track_ref_id=u.local_user_track_ref_id WHERE json_extract(m.payload_json,'$.album_group_v1.key') IS NOT NULL""")
    suspend fun candidates(owner: String, profile: String, ids: List<String>): List<PreferenceCandidate>
}

/** A collection like can move a candidate up two positions without adding tracks to the queue. */
internal suspend fun AutPlayDatabase.preferCollections(owner: String, profile: String, ids: List<String>): List<String> {
    val liked = buildSet {
        collectionPreferences().observeFavorites(owner).first().forEach { favorite ->
            add(favorite.kind to favorite.target)
            favorite.route?.let { add(favorite.kind to it) }
            if (favorite.kind == "artist" && favorite.target == "mb:" + app.autplay.application.artist.ARTIST_25_17_MBID) {
                add("artist" to "tag:25/17")
            }
        }
    }
    if (liked.isEmpty() || ids.size < 2) return ids
    val boosted = ids.chunked(500).flatMap { collectionPreferences().candidates(owner, profile, it) }.filter { row ->
        if (row.kind == "credit") displayArtists(row.target).any { ("artist" to "tag:${it.lowercase(Locale.ROOT)}") in liked }
        else (row.kind to row.target) in liked
    }.mapTo(hashSetOf()) { it.ref }
    if (liked.any { (kind, target) -> kind == "album" && target.startsWith("tag-album:") }) {
        ids.chunked(500).forEach { page ->
            val rows = catalogProjectionDao().taggedAlbumTracks(profile, page, false, false).first()
            rows.filter { ("album" to it.albumKey()) in liked }.mapTo(boosted) { it.trackId }
        }
    }
    return ids.withIndex().sortedBy { it.index - if (it.value in boosted) 2 else 0 }.map { it.value }
}
