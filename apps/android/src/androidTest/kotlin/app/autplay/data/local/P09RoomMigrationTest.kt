package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** P09 migrations are additive: a pending immutable journal event survives v2→v7. */
@RunWith(AndroidJUnit4::class)
class P09RoomMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-p09-migration.db"
    @After fun clean() { context.deleteDatabase(name) }

    @Test fun v2ToV7PreservesPendingJournalAndAddsProfileState() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(2).use { db ->
            db.execSQL("INSERT INTO journal_lineage(lineage_id,user_id,device_id,journal_epoch,next_device_sequence,created_at_ms) VALUES ('11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444',2,1)")
            db.execSQL("INSERT INTO offline_journal_event(event_id,journal_lineage_id,idempotency_key,user_id,device_id,server_profile_id,device_sequence,event_type,schema_version,aggregate_type,aggregate_local_id,aggregate_server_id,base_server_row_version,payload_json,request_hash,occurred_at_ms,state,attempt_count,next_attempt_at_ms,lease_token,lease_expires_at_ms,last_error_code,acked_at_ms) VALUES ('55555555-5555-4555-8555-555555555555','11111111-1111-4111-8111-111111111111','55555555-5555-4555-8555-555555555555','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333','66666666-6666-4666-8666-666666666666',1,'USER_TRACK_REF_CREATED',1,'USER_TRACK_REF','77777777-7777-4777-8777-777777777777',NULL,NULL,'{}',X'00',1,'PENDING',0,NULL,NULL,NULL,NULL,NULL)")
        }
        helper.runMigrationsAndValidate(7, listOf(AutPlayDatabase.MIGRATION_2_3, AutPlayDatabase.MIGRATION_3_4, AutPlayDatabase.MIGRATION_4_5, AutPlayDatabase.MIGRATION_5_6, AutPlayDatabase.MIGRATION_6_7)).use { db ->
            db.prepare("SELECT state FROM offline_journal_event WHERE event_id = '55555555-5555-4555-8555-555555555555'").use { s -> assertTrue(s.step()); assertEquals("PENDING", s.getText(0)) }
            db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_bootstrap_state'").use { s -> assertTrue(s.step()) }
        }
    }

    @Test fun v6ToV7PreservesLegacyProjectionRowsAndReplacesGlobalServerIdentity() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(6).use { db ->
            db.execSQL("INSERT INTO user_track_ref(local_user_track_ref_id,server_user_track_ref_id,local_recording_id,server_recording_id,resolution_status,raw_title,raw_artist,raw_album,raw_duration_ms,resolution_confidence,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms) VALUES ('track-local','same-server',NULL,NULL,'UNRESOLVED','title',NULL,NULL,NULL,NULL,'CLEAN',1,0,1,1,NULL)")
            db.execSQL("INSERT INTO library_entry(local_library_entry_id,server_library_entry_id,local_user_track_ref_id,added_at_ms,source,availability_status,sync_state,server_row_version,last_local_sequence,removed_at_ms,updated_at_ms) VALUES ('library-local','same-library','track-local',1,'IMPORT','AVAILABLE','CLEAN',1,0,NULL,1)")
            db.execSQL("INSERT INTO user_track_preference(local_user_track_ref_id,preference,rating,excluded_from_taste,sync_state,last_local_sequence,updated_at_ms) VALUES ('track-local','LIKE',NULL,0,'CLEAN',0,1)")
            db.execSQL("INSERT INTO playlist(local_playlist_id,server_playlist_id,name,description,visibility,playlist_type,smart_rule_version,smart_rule_json,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms) VALUES ('playlist-local','same-playlist','name',NULL,'PRIVATE','MANUAL',NULL,NULL,'CLEAN',1,0,1,1,NULL)")
            db.execSQL("INSERT INTO playlist_entry(local_playlist_entry_id,server_playlist_entry_id,local_playlist_id,local_user_track_ref_id,position_key,active_position_key,source_position,added_at_ms,sync_state,server_row_version,last_local_sequence,removed_at_ms) VALUES ('entry-local','same-entry','playlist-local','track-local','U1','U1',NULL,1,'CLEAN',1,0,NULL)")
            db.execSQL("INSERT INTO listening_event(listening_event_id,local_user_track_ref_id,server_recording_id,started_at_ms,played_ms,track_duration_ms,completion_ratio,event_origin,context,recommendation_request_id,explicit_feedback,excluded_from_taste,sync_state,created_at_ms,recommendation_attribution_json,session_start_position_ms,session_end_position_ms) VALUES ('listen-local','track-local',NULL,1,2,NULL,NULL,'ORGANIC','GENERAL',NULL,'NONE',0,'CLEAN',1,NULL,NULL,NULL)")
        }
        helper.runMigrationsAndValidate(7, listOf(AutPlayDatabase.MIGRATION_6_7)).use { db ->
            listOf("user_track_ref", "library_entry", "user_track_preference", "playlist", "playlist_entry", "listening_event").forEach { table ->
                db.prepare("SELECT server_profile_id FROM $table LIMIT 1").use { statement -> assertTrue(statement.step()); assertEquals("legacy-unscoped", statement.getText(0)) }
            }
            db.execSQL("INSERT INTO user_track_ref(local_user_track_ref_id,server_user_track_ref_id,local_recording_id,server_recording_id,resolution_status,raw_title,raw_artist,raw_album,raw_duration_ms,resolution_confidence,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms,server_profile_id) VALUES ('track-profile-b','same-server',NULL,NULL,'UNRESOLVED',NULL,NULL,NULL,NULL,NULL,'CLEAN',1,0,1,1,NULL,'profile-b')")
        }
    }
}
