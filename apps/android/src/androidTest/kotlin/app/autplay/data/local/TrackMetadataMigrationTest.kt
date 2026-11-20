package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Test

class TrackMetadataMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "track-metadata-migration.db"
    @After fun clean() { context.deleteDatabase(name) }

    @Test fun oldListeningEventsRemainAvailableForVersionStatistics() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(18).use { db ->
            db.execSQL("""INSERT INTO user_track_ref(local_user_track_ref_id,resolution_status,raw_title,sync_state,last_local_sequence,created_at_ms,updated_at_ms,server_profile_id) VALUES('track','UNRESOLVED','Song','CLEAN',0,1,1,'profile')""")
            db.execSQL("""INSERT INTO listening_event(listening_event_id,local_user_track_ref_id,started_at_ms,played_ms,event_origin,context,explicit_feedback,excluded_from_taste,sync_state,created_at_ms,server_profile_id,recommendation_attribution_json) VALUES('play','track',1,1000,'RECOMMENDED','GENERAL','NONE',0,'CLEAN',2,'profile','{"surface":"home"}')""")
        }
        helper.runMigrationsAndValidate(19, listOf(AutPlayDatabase.MIGRATION_18_19)).use { db ->
            assertEquals("home", value(db, "SELECT json_extract(recommendation_attribution_json,'$.surface') FROM listening_event WHERE variation_kind IS NULL AND variation_surface IS NULL"))
        }
    }

    @Test fun addsMetadataWithoutChangingTracksHistoryOrPendingIntents() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(16).use { db ->
            db.execSQL("INSERT INTO journal_lineage VALUES('lineage','user','device','epoch',9,1)")
            db.execSQL("""INSERT INTO user_track_ref(local_user_track_ref_id,server_user_track_ref_id,resolution_status,raw_title,raw_artist,sync_state,last_local_sequence,created_at_ms,updated_at_ms,server_profile_id) VALUES('track','server-track','UNRESOLVED','Original','Artist','DIRTY',8,1,2,'profile')""")
            db.execSQL("INSERT INTO track_search_content(rowid,local_user_track_ref_id,title,artist,aliases) VALUES(77,'track','Original','Artist','Keep alias')")
            db.execSQL("INSERT INTO local_mutation_outbox VALUES('intent','FUTURE_EVENT',99,'FUTURE_AGGREGATE','track','{}',2,'UNMATERIALIZED',NULL,NULL)")
        }
        helper.runMigrationsAndValidate(17, listOf(AutPlayDatabase.MIGRATION_16_17)).use { db ->
            assertEquals("Original", value(db, "SELECT raw_title FROM user_track_ref"))
            assertEquals("DIRTY", value(db, "SELECT sync_state FROM user_track_ref"))
            assertEquals("77", value(db, "SELECT rowid FROM track_search_content"))
            assertEquals("Keep alias", value(db, "SELECT aliases FROM track_search_content"))
            assertEquals("9", value(db, "SELECT next_device_sequence FROM journal_lineage"))
            assertEquals("FUTURE_EVENT", value(db, "SELECT event_type FROM local_mutation_outbox"))
            assertEquals("0", value(db, "SELECT count(*) FROM track_metadata_projection"))
            assertEquals("0", value(db, "SELECT count(*) FROM metadata_artwork_cache"))
        }
    }
    private fun value(db: SQLiteConnection, sql: String): String? = db.prepare(sql).use {
        assertTrue(it.step()); if (it.isNull(0)) null else it.getText(0)
    }
}
