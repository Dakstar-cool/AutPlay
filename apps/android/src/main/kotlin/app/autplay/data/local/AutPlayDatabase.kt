package app.autplay.data.local

import android.content.Context
import androidx.room3.Database
import androidx.room3.Room
import androidx.room3.RoomDatabase
import androidx.room3.migration.Migration
import app.autplay.data.local.dao.CatalogProjectionDao
import app.autplay.data.local.dao.HistoryDao
import app.autplay.data.local.dao.JournalDao
import app.autplay.data.local.dao.LibraryDao
import app.autplay.data.local.dao.LocalAudioDao
import app.autplay.data.local.dao.ImportReviewDao
import app.autplay.data.local.dao.PlaylistDao
import app.autplay.data.local.dao.QueueDao
import app.autplay.data.local.dao.RecommendationPackDao
import app.autplay.data.local.dao.SearchDao
import app.autplay.data.local.dao.SyncDao
import app.autplay.data.local.dao.WaveDao
import app.autplay.data.local.entity.AggregateRedirectEntity
import app.autplay.data.local.entity.AppliedServerEventEntity
import app.autplay.data.local.entity.DeferredServerEventEntity
import app.autplay.data.local.entity.DownloadIntentEntity
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.ListeningEventEntity
import app.autplay.data.local.entity.LocalImportEntryEntity
import app.autplay.data.local.entity.LocalImportJobEntity
import app.autplay.data.local.entity.LocalMatchCandidateEntity
import app.autplay.data.local.entity.LocalMatchDecisionEntity
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.PlaylistEntryEntity
import app.autplay.data.local.entity.QueueEntryEntity
import app.autplay.data.local.entity.QueueSnapshotEntity
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.RecommendationPresentationEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.data.local.entity.SyncConflictEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.SyncRuntimeStatusEntity
import app.autplay.data.local.entity.SyncBootstrapStateEntity
import app.autplay.data.local.entity.RecommendationInteractionFactEntity
import app.autplay.data.local.entity.TombstoneEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.data.local.entity.TrackSearchFtsEntity
import app.autplay.data.local.entity.UserTrackExternalRefEntity
import app.autplay.data.local.entity.UserTrackPreferenceEntity
import app.autplay.data.local.entity.WavePreflightEntity
import app.autplay.data.local.entity.WaveRoomEntity
import app.autplay.data.local.entity.WaveQueueProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL
import kotlinx.coroutines.Dispatchers

@Database(
    entities = [
        RecordingProjectionEntity::class,
        ReleaseProjectionEntity::class,
        ReleaseTrackProjectionEntity::class,
        UserTrackRefEntity::class,
        UserTrackExternalRefEntity::class,
        LibraryEntryEntity::class,
        UserTrackPreferenceEntity::class,
        PlaylistEntity::class,
        PlaylistEntryEntity::class,
        LocalAudioStateEntity::class,
        DownloadIntentEntity::class,
        QueueSnapshotEntity::class,
        QueueEntryEntity::class,
        ListeningEventEntity::class,
        JournalLineageEntity::class,
        OfflineJournalEventEntity::class,
        LocalMutationOutboxEntity::class,
        SyncCursorEntity::class,
        SyncRuntimeStatusEntity::class,
        SyncBootstrapStateEntity::class,
        RecommendationInteractionFactEntity::class,
        TombstoneEntity::class,
        SyncConflictEntity::class,
        RecommendationPackEntity::class,
        RecommendationPresentationEntity::class,
        TrackSearchContentEntity::class,
        TrackSearchFtsEntity::class,
        AppliedServerEventEntity::class,
        DeferredServerEventEntity::class,
        AggregateRedirectEntity::class,
        LocalImportJobEntity::class,
        LocalImportEntryEntity::class,
        LocalMatchDecisionEntity::class,
        LocalMatchCandidateEntity::class,
        WaveRoomEntity::class,
        WavePreflightEntity::class,
        WaveQueueProjectionEntity::class,
    ],
    version = 10,
    exportSchema = true,
)
abstract class AutPlayDatabase : RoomDatabase() {
    abstract fun catalogProjectionDao(): CatalogProjectionDao

    abstract fun libraryDao(): LibraryDao

    abstract fun playlistDao(): PlaylistDao

    abstract fun localAudioDao(): LocalAudioDao

    abstract fun queueDao(): QueueDao

    abstract fun historyDao(): HistoryDao

    abstract fun journalDao(): JournalDao

    abstract fun syncDao(): SyncDao

    abstract fun recommendationPackDao(): RecommendationPackDao

    abstract fun searchDao(): SearchDao

    abstract fun importReviewDao(): ImportReviewDao
    abstract fun waveDao(): WaveDao

    companion object {
        const val DATABASE_NAME = "autplay.db"

        /** Opens the non-destructive database with bundled SQLite, WAL, and all migrations. */
        fun open(context: Context, name: String = DATABASE_NAME): AutPlayDatabase =
            Room.databaseBuilder<AutPlayDatabase>(
                context = context.applicationContext,
                name = context.getDatabasePath(name).absolutePath,
            ).setDriver(BundledSQLiteDriver())
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING)
                .setQueryCoroutineContext(Dispatchers.IO)
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5, MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9, MIGRATION_9_10)
                .build()

        /** P08-only additive state required to restore attribution and one logical play session. */
        val MIGRATION_1_2: Migration = object : Migration(1, 2) {
            override suspend fun migrate(connection: SQLiteConnection) {
                listOf(
                    "ALTER TABLE download_intent ADD COLUMN server_profile_id TEXT",
                    "ALTER TABLE download_intent ADD COLUMN last_accessed_at_ms INTEGER",
                    "ALTER TABLE queue_snapshot ADD COLUMN server_profile_id TEXT",
                    "ALTER TABLE queue_snapshot ADD COLUMN listening_context TEXT NOT NULL DEFAULT 'GENERAL'",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_listening_event_id TEXT",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_started_at_ms INTEGER",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_start_position_ms INTEGER",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_observed_played_ms INTEGER",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_user_id TEXT",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_device_id TEXT",
                    "ALTER TABLE queue_snapshot ADD COLUMN active_session_server_profile_id TEXT",
                    "ALTER TABLE queue_entry ADD COLUMN recommendation_attribution_json TEXT",
                    "ALTER TABLE listening_event ADD COLUMN recommendation_attribution_json TEXT",
                    "ALTER TABLE listening_event ADD COLUMN session_start_position_ms INTEGER",
                    "ALTER TABLE listening_event ADD COLUMN session_end_position_ms INTEGER",
                ).forEach(connection::execSQL)
            }
        }

        /** P09 additive profile-scoped runtime diagnostics; journal and tombstones are preserved. */
        val MIGRATION_2_3: Migration = object : Migration(2, 3) {
            override suspend fun migrate(connection: SQLiteConnection) {
                connection.execSQL("CREATE TABLE IF NOT EXISTS sync_runtime_status (server_profile_id TEXT NOT NULL, last_error_code TEXT, last_attempt_at_ms INTEGER, last_success_at_ms INTEGER, PRIMARY KEY(server_profile_id))")
            }
        }
        val MIGRATION_3_4: Migration = object : Migration(3, 4) {
            override suspend fun migrate(connection: SQLiteConnection) {
                connection.execSQL("CREATE TABLE IF NOT EXISTS sync_bootstrap_state (server_profile_id TEXT NOT NULL, snapshot_id TEXT, page_token TEXT, final_cursor TEXT, state TEXT NOT NULL, updated_at_ms INTEGER NOT NULL, PRIMARY KEY(server_profile_id))")
            }
        }
        val MIGRATION_4_5: Migration = object : Migration(4, 5) {
            override suspend fun migrate(connection: SQLiteConnection) {
                // SQLite cannot remove a DEFAULT or replace the v4 global unique index in place.
                // Copying is non-destructive and assigns a visible legacy sentinel rather than an
                // ambiguous empty profile to pre-P09 rows.
                connection.execSQL("CREATE TABLE tombstone_v5 (tombstone_id TEXT NOT NULL, server_profile_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, aggregate_local_id TEXT NOT NULL, aggregate_server_id TEXT, deleted_by_event_id TEXT NOT NULL, deleted_at_ms INTEGER NOT NULL, retain_until_ms INTEGER NOT NULL, server_acked INTEGER NOT NULL, PRIMARY KEY(tombstone_id))")
                connection.execSQL("INSERT INTO tombstone_v5(tombstone_id,server_profile_id,aggregate_type,aggregate_local_id,aggregate_server_id,deleted_by_event_id,deleted_at_ms,retain_until_ms,server_acked) SELECT tombstone_id,'legacy-unscoped',aggregate_type,aggregate_local_id,aggregate_server_id,deleted_by_event_id,deleted_at_ms,retain_until_ms,server_acked FROM tombstone")
                connection.execSQL("DROP TABLE tombstone")
                connection.execSQL("ALTER TABLE tombstone_v5 RENAME TO tombstone")
                connection.execSQL("CREATE UNIQUE INDEX index_tombstone_server_profile_id_aggregate_type_aggregate_local_id ON tombstone(server_profile_id, aggregate_type, aggregate_local_id)")
                connection.execSQL("CREATE TABLE sync_conflict_v5 (sync_conflict_id TEXT NOT NULL, server_profile_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, aggregate_local_id TEXT NOT NULL, local_event_id TEXT, server_event_id TEXT, reason_code TEXT NOT NULL, local_snapshot_json TEXT, server_snapshot_json TEXT, status TEXT NOT NULL, resolution_json TEXT, created_at_ms INTEGER NOT NULL, resolved_at_ms INTEGER, PRIMARY KEY(sync_conflict_id))")
                connection.execSQL("INSERT INTO sync_conflict_v5(sync_conflict_id,server_profile_id,aggregate_type,aggregate_local_id,local_event_id,server_event_id,reason_code,local_snapshot_json,server_snapshot_json,status,resolution_json,created_at_ms,resolved_at_ms) SELECT sync_conflict_id,'legacy-unscoped',aggregate_type,aggregate_local_id,local_event_id,server_event_id,reason_code,local_snapshot_json,server_snapshot_json,status,resolution_json,created_at_ms,resolved_at_ms FROM sync_conflict")
                connection.execSQL("DROP TABLE sync_conflict")
                connection.execSQL("ALTER TABLE sync_conflict_v5 RENAME TO sync_conflict")
                connection.execSQL("CREATE INDEX index_sync_conflict_server_profile_id_aggregate_type_aggregate_local_id ON sync_conflict(server_profile_id, aggregate_type, aggregate_local_id)")
                connection.execSQL("CREATE INDEX index_sync_conflict_server_profile_id_status_created_at_ms ON sync_conflict(server_profile_id, status, created_at_ms)")
            }
        }
        val MIGRATION_5_6: Migration = object : Migration(5, 6) {
            override suspend fun migrate(connection: SQLiteConnection) {
                connection.execSQL("CREATE TABLE IF NOT EXISTS recommendation_interaction_fact (server_profile_id TEXT NOT NULL, event_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL, PRIMARY KEY(server_profile_id, event_id))")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_recommendation_interaction_fact_server_profile_id_event_type_created_at_ms ON recommendation_interaction_fact(server_profile_id, event_type, created_at_ms)")
            }
        }
        val MIGRATION_6_7: Migration = object : Migration(6, 7) {
            override suspend fun migrate(connection: SQLiteConnection) {
                // Rebuild rather than ALTER: old globally-unique server IDs would make equal
                // UUIDs from two profiles collide. Existing local-only rows remain visible under
                // the explicit legacy sentinel and retain every local primary key/reference.
                connection.execSQL("PRAGMA defer_foreign_keys = TRUE")
                connection.execSQL("CREATE TABLE user_track_ref_v7 (local_user_track_ref_id TEXT NOT NULL, server_user_track_ref_id TEXT, local_recording_id TEXT, server_recording_id TEXT, resolution_status TEXT NOT NULL, raw_title TEXT, raw_artist TEXT, raw_album TEXT, raw_duration_ms INTEGER, resolution_confidence REAL, sync_state TEXT NOT NULL, server_row_version INTEGER, last_local_sequence INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, deleted_at_ms INTEGER, server_profile_id TEXT NOT NULL, PRIMARY KEY(local_user_track_ref_id), FOREIGN KEY(local_recording_id) REFERENCES recording_projection(local_recording_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("INSERT INTO user_track_ref_v7 SELECT local_user_track_ref_id,server_user_track_ref_id,local_recording_id,server_recording_id,resolution_status,raw_title,raw_artist,raw_album,raw_duration_ms,resolution_confidence,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms,'legacy-unscoped' FROM user_track_ref")
                connection.execSQL("CREATE TABLE library_entry_v7 (local_library_entry_id TEXT NOT NULL, server_library_entry_id TEXT, local_user_track_ref_id TEXT NOT NULL, added_at_ms INTEGER NOT NULL, source TEXT NOT NULL, availability_status TEXT NOT NULL, sync_state TEXT NOT NULL, server_row_version INTEGER, last_local_sequence INTEGER NOT NULL, removed_at_ms INTEGER, updated_at_ms INTEGER NOT NULL, server_profile_id TEXT NOT NULL, PRIMARY KEY(local_library_entry_id), FOREIGN KEY(local_user_track_ref_id) REFERENCES user_track_ref(local_user_track_ref_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("INSERT INTO library_entry_v7 SELECT local_library_entry_id,server_library_entry_id,local_user_track_ref_id,added_at_ms,source,availability_status,sync_state,server_row_version,last_local_sequence,removed_at_ms,updated_at_ms,'legacy-unscoped' FROM library_entry")
                connection.execSQL("CREATE TABLE user_track_preference_v7 (local_user_track_ref_id TEXT NOT NULL, preference TEXT NOT NULL, rating INTEGER, excluded_from_taste INTEGER NOT NULL, sync_state TEXT NOT NULL, last_local_sequence INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, server_profile_id TEXT NOT NULL, PRIMARY KEY(local_user_track_ref_id), FOREIGN KEY(local_user_track_ref_id) REFERENCES user_track_ref(local_user_track_ref_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("INSERT INTO user_track_preference_v7 SELECT local_user_track_ref_id,preference,rating,excluded_from_taste,sync_state,last_local_sequence,updated_at_ms,'legacy-unscoped' FROM user_track_preference")
                connection.execSQL("CREATE TABLE playlist_v7 (local_playlist_id TEXT NOT NULL, server_playlist_id TEXT, name TEXT NOT NULL, description TEXT, visibility TEXT NOT NULL, playlist_type TEXT NOT NULL, smart_rule_version INTEGER, smart_rule_json TEXT, sync_state TEXT NOT NULL, server_row_version INTEGER, last_local_sequence INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, deleted_at_ms INTEGER, server_profile_id TEXT NOT NULL, PRIMARY KEY(local_playlist_id))")
                connection.execSQL("INSERT INTO playlist_v7 SELECT local_playlist_id,server_playlist_id,name,description,visibility,playlist_type,smart_rule_version,smart_rule_json,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms,'legacy-unscoped' FROM playlist")
                connection.execSQL("CREATE TABLE playlist_entry_v7 (local_playlist_entry_id TEXT NOT NULL, server_playlist_entry_id TEXT, local_playlist_id TEXT NOT NULL, local_user_track_ref_id TEXT NOT NULL, position_key TEXT NOT NULL, active_position_key TEXT, source_position INTEGER, added_at_ms INTEGER NOT NULL, sync_state TEXT NOT NULL, server_row_version INTEGER, last_local_sequence INTEGER NOT NULL, removed_at_ms INTEGER, server_profile_id TEXT NOT NULL, PRIMARY KEY(local_playlist_entry_id), FOREIGN KEY(local_playlist_id) REFERENCES playlist(local_playlist_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(local_user_track_ref_id) REFERENCES user_track_ref(local_user_track_ref_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("INSERT INTO playlist_entry_v7 SELECT local_playlist_entry_id,server_playlist_entry_id,local_playlist_id,local_user_track_ref_id,position_key,active_position_key,source_position,added_at_ms,sync_state,server_row_version,last_local_sequence,removed_at_ms,'legacy-unscoped' FROM playlist_entry")
                connection.execSQL("CREATE TABLE listening_event_v7 (listening_event_id TEXT NOT NULL, local_user_track_ref_id TEXT NOT NULL, server_recording_id TEXT, started_at_ms INTEGER NOT NULL, played_ms INTEGER NOT NULL, track_duration_ms INTEGER, completion_ratio REAL, event_origin TEXT NOT NULL, context TEXT NOT NULL, recommendation_request_id TEXT, explicit_feedback TEXT NOT NULL, excluded_from_taste INTEGER NOT NULL, sync_state TEXT NOT NULL, created_at_ms INTEGER NOT NULL, recommendation_attribution_json TEXT, session_start_position_ms INTEGER, session_end_position_ms INTEGER, server_profile_id TEXT NOT NULL, PRIMARY KEY(listening_event_id), FOREIGN KEY(local_user_track_ref_id) REFERENCES user_track_ref(local_user_track_ref_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("INSERT INTO listening_event_v7 SELECT listening_event_id,local_user_track_ref_id,server_recording_id,started_at_ms,played_ms,track_duration_ms,completion_ratio,event_origin,context,recommendation_request_id,explicit_feedback,excluded_from_taste,sync_state,created_at_ms,recommendation_attribution_json,session_start_position_ms,session_end_position_ms,'legacy-unscoped' FROM listening_event")
                connection.execSQL("DROP TABLE library_entry")
                connection.execSQL("DROP TABLE user_track_preference")
                connection.execSQL("DROP TABLE playlist_entry")
                connection.execSQL("DROP TABLE listening_event")
                connection.execSQL("DROP TABLE user_track_ref")
                connection.execSQL("DROP TABLE playlist")
                connection.execSQL("ALTER TABLE user_track_ref_v7 RENAME TO user_track_ref")
                connection.execSQL("ALTER TABLE library_entry_v7 RENAME TO library_entry")
                connection.execSQL("ALTER TABLE user_track_preference_v7 RENAME TO user_track_preference")
                connection.execSQL("ALTER TABLE playlist_v7 RENAME TO playlist")
                connection.execSQL("ALTER TABLE playlist_entry_v7 RENAME TO playlist_entry")
                connection.execSQL("ALTER TABLE listening_event_v7 RENAME TO listening_event")
                connection.execSQL("CREATE UNIQUE INDEX index_user_track_ref_server_profile_id_server_user_track_ref_id ON user_track_ref(server_profile_id,server_user_track_ref_id)")
                connection.execSQL("CREATE INDEX index_user_track_ref_server_recording_id ON user_track_ref(server_recording_id)")
                connection.execSQL("CREATE INDEX index_user_track_ref_local_recording_id ON user_track_ref(local_recording_id)")
                connection.execSQL("CREATE INDEX index_user_track_ref_resolution_status_updated_at_ms ON user_track_ref(resolution_status,updated_at_ms)")
                connection.execSQL("CREATE INDEX index_user_track_ref_sync_state_updated_at_ms ON user_track_ref(sync_state,updated_at_ms)")
                connection.execSQL("CREATE INDEX index_user_track_ref_server_profile_id ON user_track_ref(server_profile_id)")
                connection.execSQL("CREATE UNIQUE INDEX index_library_entry_server_profile_id_server_library_entry_id ON library_entry(server_profile_id,server_library_entry_id)")
                connection.execSQL("CREATE UNIQUE INDEX index_library_entry_local_user_track_ref_id ON library_entry(local_user_track_ref_id)")
                connection.execSQL("CREATE INDEX index_library_entry_server_profile_id ON library_entry(server_profile_id)")
                connection.execSQL("CREATE INDEX index_user_track_preference_server_profile_id ON user_track_preference(server_profile_id)")
                connection.execSQL("CREATE UNIQUE INDEX index_playlist_server_profile_id_server_playlist_id ON playlist(server_profile_id,server_playlist_id)")
                connection.execSQL("CREATE INDEX index_playlist_server_profile_id ON playlist(server_profile_id)")
                connection.execSQL("CREATE UNIQUE INDEX index_playlist_entry_server_profile_id_server_playlist_entry_id ON playlist_entry(server_profile_id,server_playlist_entry_id)")
                connection.execSQL("CREATE UNIQUE INDEX index_playlist_entry_local_playlist_id_active_position_key ON playlist_entry(local_playlist_id,active_position_key)")
                connection.execSQL("CREATE INDEX index_playlist_entry_local_user_track_ref_id ON playlist_entry(local_user_track_ref_id)")
                connection.execSQL("CREATE INDEX index_playlist_entry_server_profile_id ON playlist_entry(server_profile_id)")
                connection.execSQL("CREATE INDEX index_listening_event_started_at_ms ON listening_event(started_at_ms)")
                connection.execSQL("CREATE INDEX index_listening_event_sync_state_created_at_ms ON listening_event(sync_state,created_at_ms)")
                connection.execSQL("CREATE INDEX index_listening_event_local_user_track_ref_id ON listening_event(local_user_track_ref_id)")
                connection.execSQL("CREATE INDEX index_listening_event_server_profile_id ON listening_event(server_profile_id)")
            }
        }

        /** P10 additive import/review projection; every P09 row and journal lineage is untouched. */
        val MIGRATION_7_8: Migration = object : Migration(7, 8) {
            override suspend fun migrate(connection: SQLiteConnection) {
                connection.execSQL("CREATE TABLE IF NOT EXISTS local_import_job (import_job_id TEXT NOT NULL, server_profile_id TEXT NOT NULL, adapter_id TEXT NOT NULL, adapter_version TEXT NOT NULL, envelope_version INTEGER NOT NULL, input_sha256 TEXT NOT NULL, input_digest_verified INTEGER NOT NULL, source_uri TEXT, persisted_uri_permission INTEGER NOT NULL, source_availability TEXT NOT NULL, state TEXT NOT NULL, checkpoint_position INTEGER NOT NULL, total_entries INTEGER NOT NULL, review_required_count INTEGER NOT NULL, resolved_count INTEGER NOT NULL, no_match_count INTEGER NOT NULL, unresolved_count INTEGER NOT NULL, failed_count INTEGER NOT NULL, report_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, completed_at_ms INTEGER, PRIMARY KEY(import_job_id))")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_local_import_job_server_profile_id_adapter_id_input_sha256_source_uri ON local_import_job(server_profile_id, adapter_id, input_sha256, source_uri)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_local_import_job_server_profile_id_updated_at_ms ON local_import_job(server_profile_id, updated_at_ms)")
                connection.execSQL("CREATE TABLE IF NOT EXISTS local_import_entry (import_entry_id TEXT NOT NULL, import_job_id TEXT NOT NULL, source_row_key TEXT NOT NULL, source_position INTEGER NOT NULL, row_sha256 TEXT NOT NULL, raw_title TEXT NOT NULL, raw_artist TEXT NOT NULL, raw_album TEXT, raw_duration_ms INTEGER, raw_provenance_json TEXT NOT NULL, content_uri TEXT, persisted_uri_permission INTEGER NOT NULL, source_availability TEXT NOT NULL, fingerprint_algorithm TEXT, fingerprint_version TEXT, local_user_track_ref_id TEXT NOT NULL, workflow_state TEXT NOT NULL, selected_local_recording_id TEXT, latest_decision_id TEXT, last_error_code TEXT, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, PRIMARY KEY(import_entry_id), FOREIGN KEY(import_job_id) REFERENCES local_import_job(import_job_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(local_user_track_ref_id) REFERENCES user_track_ref(local_user_track_ref_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(selected_local_recording_id) REFERENCES recording_projection(local_recording_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_local_import_entry_import_job_id_source_row_key ON local_import_entry(import_job_id, source_row_key)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_local_import_entry_import_job_id_source_position ON local_import_entry(import_job_id, source_position)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_local_import_entry_import_job_id_workflow_state ON local_import_entry(import_job_id, workflow_state)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_local_import_entry_local_user_track_ref_id ON local_import_entry(local_user_track_ref_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_local_import_entry_selected_local_recording_id ON local_import_entry(selected_local_recording_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_local_import_entry_latest_decision_id ON local_import_entry(latest_decision_id)")
                connection.execSQL("CREATE TABLE IF NOT EXISTS match_decision (decision_id TEXT NOT NULL, import_entry_id TEXT NOT NULL, decision_kind TEXT NOT NULL, execution_mode TEXT NOT NULL, resolver_state TEXT NOT NULL, review_action TEXT, selected_local_recording_id TEXT, reviewed_candidate_id TEXT, supersedes_decision_id TEXT, evidence_decision_id TEXT NOT NULL, candidate_count INTEGER NOT NULL, top_confidence REAL, top_two_margin REAL, evidence_mode TEXT NOT NULL, matcher_version TEXT NOT NULL, fingerprint_algorithm TEXT, fingerprint_version TEXT, explanation_json TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, created_at_ms INTEGER NOT NULL, PRIMARY KEY(decision_id), FOREIGN KEY(import_entry_id) REFERENCES local_import_entry(import_entry_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(selected_local_recording_id) REFERENCES recording_projection(local_recording_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(supersedes_decision_id) REFERENCES match_decision(decision_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_match_decision_import_entry_id_idempotency_key ON match_decision(import_entry_id, idempotency_key)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_match_decision_supersedes_decision_id ON match_decision(supersedes_decision_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_match_decision_selected_local_recording_id ON match_decision(selected_local_recording_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_match_decision_evidence_decision_id ON match_decision(evidence_decision_id)")
                connection.execSQL("CREATE TABLE IF NOT EXISTS match_candidate (candidate_id TEXT NOT NULL, decision_id TEXT NOT NULL, local_recording_id TEXT NOT NULL, rank INTEGER NOT NULL, raw_score REAL, confidence REAL, evidence_tier TEXT NOT NULL, title_snapshot TEXT NOT NULL, artist_snapshot TEXT NOT NULL, version_snapshot TEXT, duration_ms INTEGER, feature_evidence_json TEXT NOT NULL, hard_conflicts_json TEXT NOT NULL, candidate_origins_json TEXT NOT NULL, extractor_versions_json TEXT NOT NULL, fingerprint_algorithm TEXT, fingerprint_version TEXT, created_at_ms INTEGER NOT NULL, PRIMARY KEY(candidate_id), FOREIGN KEY(decision_id) REFERENCES match_decision(decision_id) ON UPDATE NO ACTION ON DELETE RESTRICT, FOREIGN KEY(local_recording_id) REFERENCES recording_projection(local_recording_id) ON UPDATE NO ACTION ON DELETE RESTRICT)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_match_candidate_decision_id_rank ON match_candidate(decision_id, rank)")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_match_candidate_decision_id_local_recording_id ON match_candidate(decision_id, local_recording_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_match_candidate_local_recording_id ON match_candidate(local_recording_id)")
            }
        }

        /** P11 owner-scoped offline packs and durable actual-presentation idempotency. */
        val MIGRATION_8_9: Migration = object : Migration(8, 9) {
            override suspend fun migrate(connection: SQLiteConnection) {
                // A legacy pack has no authenticated owner proof. Preserve its bytes for audit,
                // but leave owner_user_id null so every v9 verifier fails closed.
                connection.execSQL("ALTER TABLE recommendation_pack ADD COLUMN owner_user_id TEXT")
                connection.execSQL("DROP INDEX IF EXISTS index_recommendation_pack_server_profile_id_expires_at_ms")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_recommendation_pack_server_profile_id_owner_user_id_expires_at_ms ON recommendation_pack(server_profile_id, owner_user_id, expires_at_ms)")
                connection.execSQL("CREATE TABLE IF NOT EXISTS recommendation_presentation (server_profile_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, presentation_id TEXT NOT NULL, recommendation_request_id TEXT NOT NULL, source_rank INTEGER NOT NULL, impression_event_id TEXT NOT NULL, recording_id TEXT NOT NULL, offline_pack_id TEXT, source TEXT NOT NULL, surface TEXT NOT NULL, section_key TEXT, display_position INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, PRIMARY KEY(server_profile_id, owner_user_id, presentation_id, recommendation_request_id, source_rank))")
                connection.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_recommendation_presentation_impression_event_id ON recommendation_presentation(impression_event_id)")
                connection.execSQL("CREATE INDEX IF NOT EXISTS index_recommendation_presentation_server_profile_id_owner_user_id_offline_pack_id ON recommendation_presentation(server_profile_id, owner_user_id, offline_pack_id)")
            }
        }

        /** P13 additive cache. Commands/tokens/URLs and clock samples are deliberately absent. */
        val MIGRATION_9_10: Migration = object : Migration(9, 10) {
            override suspend fun migrate(connection: SQLiteConnection) {
                connection.execSQL("CREATE TABLE wave_room (room_id TEXT NOT NULL, server_profile_id TEXT NOT NULL, room_epoch TEXT NOT NULL, queue_version INTEGER NOT NULL, role TEXT NOT NULL, state TEXT NOT NULL, last_sequence INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, PRIMARY KEY(room_id))")
                connection.execSQL("CREATE INDEX index_wave_room_server_profile_id ON wave_room(server_profile_id)")
                connection.execSQL("CREATE TABLE wave_preflight (room_id TEXT NOT NULL, queue_entry_id TEXT NOT NULL, server_recording_id TEXT NOT NULL, local_user_track_ref_id TEXT, queue_version INTEGER NOT NULL, availability TEXT NOT NULL, final_ready INTEGER NOT NULL, checked_at_ms INTEGER NOT NULL, PRIMARY KEY(room_id, queue_entry_id))")
                connection.execSQL("CREATE TABLE wave_queue_projection (room_id TEXT NOT NULL, sequence INTEGER NOT NULL, position INTEGER NOT NULL, queue_entry_id TEXT NOT NULL, server_recording_id TEXT NOT NULL, local_user_track_ref_id TEXT, ready INTEGER NOT NULL, PRIMARY KEY(room_id, sequence, position))")
            }
        }
    }
}
