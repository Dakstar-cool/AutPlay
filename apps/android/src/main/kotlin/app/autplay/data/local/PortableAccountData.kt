package app.autplay.data.local

import androidx.room3.ColumnInfo
import androidx.room3.Dao
import androidx.room3.Entity
import androidx.room3.ForeignKey
import androidx.room3.Index
import androidx.room3.Insert
import androidx.room3.OnConflictStrategy
import androidx.room3.PrimaryKey
import androidx.room3.Query
import androidx.room3.Upsert
import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.RecommendationInteractionFactEntity

/** Ownership aliases are immutable; disconnecting a server never removes an account's records. */
@Entity(tableName = "portable_account_scope", indices = [Index(value = ["account_id"])])
data class PortableAccountScopeEntity(@PrimaryKey @ColumnInfo(name = "profile_id") val profileId: String,
    @ColumnInfo(name = "account_id") val accountId: String,
    @ColumnInfo(name = "owner_since_ms", defaultValue = "0") val ownerSinceMs: Long = 0)

@Entity(tableName = "portable_account_record", primaryKeys = ["account_id", "record_id"],
    indices = [Index(value = ["account_id", "revision"]), Index(value = ["account_id", "kind", "record_id"])])
data class PortableAccountRecordEntity(@ColumnInfo(name = "account_id") val accountId: String,
    @ColumnInfo(name = "record_id") val recordId: String, val kind: String,
    @ColumnInfo(name = "document_json") val documentJson: String,
    @ColumnInfo(name = "content_hash") val contentHash: String, val revision: Long)

@Entity(tableName = "portable_listening_alias", primaryKeys = ["account_id", "event_id"],
    foreignKeys = [ForeignKey(entity = PortableAccountRecordEntity::class, parentColumns = ["account_id", "record_id"],
        childColumns = ["account_id", "record_id"], onDelete = ForeignKey.CASCADE)],
    indices = [Index(value = ["account_id", "record_id"])])
data class PortableListeningAliasEntity(@ColumnInfo(name = "account_id") val accountId: String,
    @ColumnInfo(name = "event_id") val eventId: String, @ColumnInfo(name = "record_id") val recordId: String)

@Entity(tableName = "portable_account_cursor", primaryKeys = ["account_id", "profile_id", "source"])
data class PortableAccountCursorEntity(@ColumnInfo(name = "account_id") val accountId: String,
    @ColumnInfo(name = "profile_id") val profileId: String, val source: String,
    @ColumnInfo(name = "cursor_time") val cursorTime: String = "1970-01-01T00:00:00Z",
    @ColumnInfo(name = "cursor_id") val cursorId: String = "00000000-0000-0000-0000-000000000000",
    @ColumnInfo(name = "uploaded_revision") val uploadedRevision: Long = 0, val complete: Boolean = false)

@Entity(tableName = "portable_account_statistics", primaryKeys = ["account_id", "profile_id"])
data class PortableAccountStatisticsEntity(@ColumnInfo(name = "account_id") val accountId: String,
    @ColumnInfo(name = "profile_id") val profileId: String,
    @ColumnInfo(name = "document_json") val documentJson: String,
    @ColumnInfo(name = "through_ms") val throughMs: Long)

data class PortableLocalListeningRow(val eventId: String, val materializedEventId: String?, val trackKey: String,
    val title: String?, val artist: String?, val metadata: String?, val startedAtMs: Long, val playedMs: Long,
    val durationMs: Long?, val excluded: Boolean, val origin: String, val context: String, val updatedAtMs: Long)

data class PortableLocalPreferenceRow(val id: String, val trackKey: String, val title: String?, val artist: String?,
    val preference: String, val rating: Int?, val excluded: Boolean, val updatedAtMs: Long)
data class PortableLocalEntryRow(val id: String, val recordId: String, val playlistId: String, val trackKey: String, val title: String?,
    val artist: String?, val positionKey: String, val deleted: Boolean, val updatedAtMs: Long)

@Dao
interface PortableAccountDao {
    @Query("SELECT * FROM portable_account_statistics WHERE account_id=:account AND profile_id=:profile")
    suspend fun statistics(account: String, profile: String): PortableAccountStatisticsEntity?
    @Upsert suspend fun putStatistics(row: PortableAccountStatisticsEntity)
    @Query("SELECT * FROM portable_account_statistics WHERE account_id=:account ORDER BY through_ms DESC,profile_id LIMIT 32")
    suspend fun allStatistics(account: String): List<PortableAccountStatisticsEntity>
    @Query("SELECT * FROM portable_account_scope WHERE profile_id=:profile") suspend fun scope(profile: String): PortableAccountScopeEntity?
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun addScope(row: PortableAccountScopeEntity): Long
    @Query("SELECT * FROM portable_account_scope WHERE account_id=:account") suspend fun scopes(account: String): List<PortableAccountScopeEntity>
    @Query("SELECT * FROM portable_account_record WHERE account_id=:account AND record_id=:record") suspend fun record(account: String, record: String): PortableAccountRecordEntity?
    @Query("SELECT * FROM portable_account_record WHERE account_id=:account AND record_id IN (SELECT record_id FROM portable_listening_alias WHERE account_id=:account AND event_id IN (:events))")
    suspend fun listeningMatches(account: String, events: List<String>): List<PortableAccountRecordEntity>
    @Query("SELECT coalesce(max(revision),0) FROM portable_account_record WHERE account_id=:account") suspend fun latestRevision(account: String): Long
    @Query("SELECT coalesce(max(revision),0) FROM portable_account_record WHERE account_id=:account")
    fun observeRevision(account: String): kotlinx.coroutines.flow.Flow<Long>
    @Upsert suspend fun putRecord(row: PortableAccountRecordEntity)
    @Upsert suspend fun putAliases(rows: List<PortableListeningAliasEntity>)
    @Query("DELETE FROM portable_account_record WHERE account_id=:account AND record_id IN (:records)") suspend fun removeMergedCopies(account: String, records: List<String>)
    @Query("SELECT * FROM portable_account_record WHERE account_id=:account AND revision>:after ORDER BY revision,record_id LIMIT :limit")
    suspend fun uploadPage(account: String, after: Long, limit: Int): List<PortableAccountRecordEntity>
    @Query("SELECT * FROM portable_account_record WHERE account_id=:account AND kind=:kind AND record_id>:after ORDER BY record_id LIMIT :limit")
    suspend fun kindPage(account: String, kind: String, after: String, limit: Int): List<PortableAccountRecordEntity>
    @Query("SELECT * FROM portable_account_cursor WHERE account_id=:account AND profile_id=:profile AND source=:source")
    suspend fun cursor(account: String, profile: String, source: String): PortableAccountCursorEntity?
    @Upsert suspend fun putCursor(row: PortableAccountCursorEntity)
    @Query("SELECT * FROM portable_account_cursor WHERE account_id=:account AND profile_id=:profile")
    suspend fun cursors(account: String, profile: String): List<PortableAccountCursorEntity>
    @Query("SELECT * FROM portable_account_record WHERE account_id=:account AND kind='LISTENING' ORDER BY record_id")
    fun observeListening(account: String): kotlinx.coroutines.flow.Flow<List<PortableAccountRecordEntity>>
    @Query("""SELECT e.listening_event_id AS eventId,o.materialized_event_id AS materializedEventId,
        coalesce(u.server_recording_id,u.local_recording_id,u.local_user_track_ref_id) AS trackKey,
        u.raw_title AS title,u.raw_artist AS artist,m.payload_json AS metadata,
        e.started_at_ms AS startedAtMs,e.played_ms AS playedMs,e.track_duration_ms AS durationMs,
        e.excluded_from_taste AS excluded,e.event_origin AS origin,e.context AS context,e.created_at_ms AS updatedAtMs
        FROM listening_event e JOIN user_track_ref u ON u.local_user_track_ref_id=e.local_user_track_ref_id
        LEFT JOIN local_mutation_outbox o ON o.local_change_id=e.listening_event_id
        LEFT JOIN track_metadata_projection m ON m.local_user_track_ref_id=u.local_user_track_ref_id AND m.server_profile_id=u.server_profile_id
        WHERE e.server_profile_id=:profile AND u.server_profile_id=:profile AND e.created_at_ms>=:since AND e.listening_event_id>:after
        ORDER BY e.listening_event_id LIMIT :limit""")
    suspend fun localListeningPage(profile: String, since: Long, after: String, limit: Int): List<PortableLocalListeningRow>
    @Query("""SELECT p.local_user_track_ref_id AS id,coalesce(u.server_recording_id,u.local_recording_id,u.local_user_track_ref_id) AS trackKey,
        u.raw_title AS title,u.raw_artist AS artist,p.preference,p.rating,p.excluded_from_taste AS excluded,p.updated_at_ms AS updatedAtMs
        FROM user_track_preference p JOIN user_track_ref u ON u.local_user_track_ref_id=p.local_user_track_ref_id
        WHERE p.server_profile_id=:profile AND u.server_profile_id=:profile AND p.updated_at_ms>=:since
        AND p.local_user_track_ref_id>:after ORDER BY p.local_user_track_ref_id LIMIT :limit""")
    suspend fun localPreferencePage(profile: String, since: Long, after: String, limit: Int): List<PortableLocalPreferenceRow>
    @Query("SELECT * FROM playlist WHERE server_profile_id=:profile AND created_at_ms>=:since AND local_playlist_id>:after ORDER BY local_playlist_id LIMIT :limit")
    suspend fun localPlaylistPage(profile: String, since: Long, after: String, limit: Int): List<PlaylistEntity>
    @Query("""SELECT e.local_playlist_entry_id AS id,coalesce(e.server_playlist_entry_id,e.local_playlist_entry_id) AS recordId,coalesce(p.server_playlist_id,p.local_playlist_id) AS playlistId,
        coalesce(u.server_recording_id,u.local_recording_id,u.local_user_track_ref_id) AS trackKey,u.raw_title AS title,u.raw_artist AS artist,
        e.position_key AS positionKey,(e.removed_at_ms IS NOT NULL) AS deleted,coalesce(e.removed_at_ms,e.added_at_ms) AS updatedAtMs
        FROM playlist_entry e JOIN playlist p ON p.local_playlist_id=e.local_playlist_id
        JOIN user_track_ref u ON u.local_user_track_ref_id=e.local_user_track_ref_id
        WHERE e.server_profile_id=:profile AND p.server_profile_id=:profile AND u.server_profile_id=:profile
        AND p.created_at_ms>=:since AND e.added_at_ms>=:since AND e.local_playlist_entry_id>:after
        ORDER BY e.local_playlist_entry_id LIMIT :limit""")
    suspend fun localEntryPage(profile: String, since: Long, after: String, limit: Int): List<PortableLocalEntryRow>
    @Query("SELECT * FROM recommendation_interaction_fact WHERE server_profile_id=:profile AND created_at_ms>=:since AND event_id>:after ORDER BY event_id LIMIT :limit")
    suspend fun localTastePage(profile: String, since: Long, after: String, limit: Int): List<RecommendationInteractionFactEntity>
}

object PortableAccountDataMigration : Migration(17, 18) {
    override suspend fun migrate(connection: SQLiteConnection) {
        listOf(
            "CREATE TABLE IF NOT EXISTS portable_account_scope (profile_id TEXT NOT NULL PRIMARY KEY,account_id TEXT NOT NULL,owner_since_ms INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS index_portable_account_scope_account_id ON portable_account_scope(account_id)",
            "CREATE TABLE IF NOT EXISTS portable_account_record (account_id TEXT NOT NULL,record_id TEXT NOT NULL,kind TEXT NOT NULL,document_json TEXT NOT NULL,content_hash TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(account_id,record_id))",
            "CREATE INDEX IF NOT EXISTS index_portable_account_record_account_id_revision ON portable_account_record(account_id,revision)",
            "CREATE INDEX IF NOT EXISTS index_portable_account_record_account_id_kind_record_id ON portable_account_record(account_id,kind,record_id)",
            "CREATE TABLE IF NOT EXISTS portable_listening_alias (account_id TEXT NOT NULL,event_id TEXT NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(account_id,event_id),FOREIGN KEY(account_id,record_id) REFERENCES portable_account_record(account_id,record_id) ON UPDATE NO ACTION ON DELETE CASCADE)",
            "CREATE INDEX IF NOT EXISTS index_portable_listening_alias_account_id_record_id ON portable_listening_alias(account_id,record_id)",
            "CREATE TABLE IF NOT EXISTS portable_account_cursor (account_id TEXT NOT NULL,profile_id TEXT NOT NULL,source TEXT NOT NULL,cursor_time TEXT NOT NULL,cursor_id TEXT NOT NULL,uploaded_revision INTEGER NOT NULL,complete INTEGER NOT NULL,PRIMARY KEY(account_id,profile_id,source))",
            "CREATE TABLE IF NOT EXISTS portable_account_statistics (account_id TEXT NOT NULL,profile_id TEXT NOT NULL,document_json TEXT NOT NULL,through_ms INTEGER NOT NULL,PRIMARY KEY(account_id,profile_id))",
        ).forEach(connection::execSQL)
    }
}
