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

/** Room v8 is additive and cannot discard any P09 owner intent, journal, or recovery state. */
@RunWith(AndroidJUnit4::class)
class P10RoomMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-p10-migration.db"

    @After
    fun clean() {
        context.deleteDatabase(name)
    }

    @Test
    fun v7ToV8PreservesP09JournalProfileTombstoneAndConflictRows() = runBlocking {
        val helper = MigrationTestHelper(
            InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath(name),
            BundledSQLiteDriver(),
            AutPlayDatabase::class,
        )
        helper.createDatabase(7).use { db ->
            db.execSQL("INSERT INTO journal_lineage(lineage_id,user_id,device_id,journal_epoch,next_device_sequence,created_at_ms) VALUES ('$LINEAGE','$USER','$DEVICE','$EPOCH',2,1)")
            db.execSQL("INSERT INTO offline_journal_event(event_id,journal_lineage_id,idempotency_key,user_id,device_id,server_profile_id,device_sequence,event_type,schema_version,aggregate_type,aggregate_local_id,aggregate_server_id,base_server_row_version,payload_json,request_hash,occurred_at_ms,state,attempt_count,next_attempt_at_ms,lease_token,lease_expires_at_ms,last_error_code,acked_at_ms) VALUES ('$EVENT','$LINEAGE','$EVENT','$USER','$DEVICE','$PROFILE',1,'USER_TRACK_REF_CREATED',1,'USER_TRACK_REF','$TRACK',NULL,NULL,'{}',X'00',1,'PENDING',0,NULL,NULL,NULL,NULL,NULL)")
            db.execSQL("INSERT INTO user_track_ref(local_user_track_ref_id,server_user_track_ref_id,local_recording_id,server_recording_id,resolution_status,raw_title,raw_artist,raw_album,raw_duration_ms,resolution_confidence,sync_state,server_row_version,last_local_sequence,created_at_ms,updated_at_ms,deleted_at_ms,server_profile_id) VALUES ('$TRACK',NULL,NULL,NULL,'UNRESOLVED','raw','artist',NULL,NULL,NULL,'DIRTY',NULL,1,1,1,NULL,'$PROFILE')")
            db.execSQL("INSERT INTO sync_runtime_status(server_profile_id,last_error_code,last_attempt_at_ms,last_success_at_ms) VALUES ('$PROFILE','OFFLINE',1,NULL)")
            db.execSQL("INSERT INTO sync_bootstrap_state(server_profile_id,snapshot_id,page_token,final_cursor,state,updated_at_ms) VALUES ('$PROFILE','snapshot','page',NULL,'IN_PROGRESS',1)")
            db.execSQL("INSERT INTO recommendation_interaction_fact(server_profile_id,event_id,event_type,payload_json,created_at_ms) VALUES ('$PROFILE','$FACT','LISTENING_RECORDED','{}',1)")
            db.execSQL("INSERT INTO tombstone(tombstone_id,server_profile_id,aggregate_type,aggregate_local_id,aggregate_server_id,deleted_by_event_id,deleted_at_ms,retain_until_ms,server_acked) VALUES ('$TOMBSTONE','$PROFILE','PLAYLIST','$PLAYLIST',NULL,'$EVENT',1,999,0)")
            db.execSQL("INSERT INTO sync_conflict(sync_conflict_id,server_profile_id,aggregate_type,aggregate_local_id,local_event_id,server_event_id,reason_code,local_snapshot_json,server_snapshot_json,status,resolution_json,created_at_ms,resolved_at_ms) VALUES ('$CONFLICT','$PROFILE','USER_TRACK_REF','$TRACK','$EVENT',NULL,'DIRTY_REMOTE_DELETE','{}',NULL,'OPEN',NULL,1,NULL)")
        }

        helper.runMigrationsAndValidate(8, listOf(AutPlayDatabase.MIGRATION_7_8)).use { db ->
            db.prepare("SELECT state FROM offline_journal_event WHERE event_id = '$EVENT'").use { statement ->
                assertTrue(statement.step())
                assertEquals("PENDING", statement.getText(0))
            }
            db.prepare("SELECT server_profile_id, sync_state FROM user_track_ref WHERE local_user_track_ref_id = '$TRACK'").use { statement ->
                assertTrue(statement.step())
                assertEquals(PROFILE, statement.getText(0))
                assertEquals("DIRTY", statement.getText(1))
            }
            db.prepare("SELECT count(*) FROM tombstone WHERE tombstone_id = '$TOMBSTONE'").use { statement -> assertTrue(statement.step()); assertEquals(1L, statement.getLong(0)) }
            db.prepare("SELECT status FROM sync_conflict WHERE sync_conflict_id = '$CONFLICT'").use { statement -> assertTrue(statement.step()); assertEquals("OPEN", statement.getText(0)) }
            db.prepare("SELECT last_error_code FROM sync_runtime_status WHERE server_profile_id = '$PROFILE'").use { statement -> assertTrue(statement.step()); assertEquals("OFFLINE", statement.getText(0)) }
            db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('local_import_job','local_import_entry','match_decision','match_candidate') ORDER BY name").use { statement ->
                var count = 0
                while (statement.step()) count += 1
                assertEquals(4, count)
            }
        }
    }

    private companion object {
        const val LINEAGE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val DEVICE = "33333333-3333-4333-8333-333333333333"
        const val EPOCH = "44444444-4444-4444-8444-444444444444"
        const val EVENT = "55555555-5555-4555-8555-555555555555"
        const val PROFILE = "66666666-6666-4666-8666-666666666666"
        const val TRACK = "77777777-7777-4777-8777-777777777777"
        const val FACT = "88888888-8888-4888-8888-888888888888"
        const val TOMBSTONE = "99999999-9999-4999-8999-999999999999"
        const val PLAYLIST = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        const val CONFLICT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    }
}
