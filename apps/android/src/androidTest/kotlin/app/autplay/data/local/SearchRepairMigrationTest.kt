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

class SearchRepairMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "search-repair-migration.db"
    @After fun clean() { context.deleteDatabase(name) }

    @Test fun repairsDerivedIndexWithoutRewritingUserDataOrUnknownStates() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(15).use { db ->
            db.execSQL("INSERT INTO journal_lineage VALUES('lineage','user','device','epoch',9,1)")
            listOf("affected", "unaffected", "dirty", "future").forEach { profile ->
                val state = if (profile == "future") "FUTURE_STATE" else "READY"
                db.execSQL("INSERT INTO sync_cursor VALUES('$profile','lineage','device','epoch','opaque-$profile',6,5,'old-snapshot','$state',4,7)")
                db.execSQL("INSERT INTO sync_bootstrap_state VALUES('$profile','old-snapshot','old-page','old-final','$state',7)")
                val album = if (profile == "unaffected") "Album" else "null"
                val sync = if (profile == "dirty") "DIRTY" else "CLEAN"
                db.execSQL("""INSERT INTO user_track_ref(local_user_track_ref_id,server_user_track_ref_id,resolution_status,raw_title,raw_artist,raw_album,sync_state,last_local_sequence,created_at_ms,updated_at_ms,server_profile_id)
                    VALUES('$profile','server-$profile','FUTURE_STATUS','Title $profile','Artist','$album','$sync',8,1,2,'$profile')""")
            }
            db.execSQL("INSERT INTO track_search_content(rowid,local_user_track_ref_id,title,artist,album,aliases,transliterations) VALUES(77,'affected','Stale','Old',NULL,'Nickname','Alias')")
            db.execSQL("INSERT INTO user_track_preference VALUES('affected','LIKED',NULL,1,'DIRTY',8,2,'affected')")
            db.execSQL("INSERT INTO local_mutation_outbox VALUES('intent','FUTURE_EVENT',99,'FUTURE_AGGREGATE','affected','{\"future\":true}',2,'UNMATERIALIZED',NULL,NULL)")
        }
        helper.runMigrationsAndValidate(16, listOf(AutPlayDatabase.MIGRATION_15_16)).use { db ->
            assertEquals("4", value(db, "SELECT count(*) FROM track_search_content"))
            assertEquals("4", value(db, "SELECT count(*) FROM track_search_fts WHERE track_search_fts MATCH 'Title'"))
            assertEquals("0", value(db, "SELECT count(*) FROM track_search_fts WHERE track_search_fts MATCH 'Stale'"))
            assertEquals("1", value(db, "SELECT count(*) FROM track_search_fts WHERE track_search_fts MATCH 'Nickname'"))
            assertEquals("77", value(db, "SELECT rowid FROM track_search_content WHERE local_user_track_ref_id='affected'"))
            assertEquals("Alias", value(db, "SELECT transliterations FROM track_search_content WHERE rowid=77"))
            assertEquals("null", value(db, "SELECT raw_album FROM user_track_ref WHERE local_user_track_ref_id='affected'"))
            assertEquals("DIRTY", value(db, "SELECT sync_state FROM user_track_ref WHERE local_user_track_ref_id='dirty'"))
            assertEquals("FUTURE_STATUS", value(db, "SELECT resolution_status FROM user_track_ref WHERE local_user_track_ref_id='future'"))
            assertEquals("RESET_REQUIRED", value(db, "SELECT bootstrap_state FROM sync_cursor WHERE server_profile_id='affected'"))
            assertEquals("opaque-affected", value(db, "SELECT opaque_cursor FROM sync_cursor WHERE server_profile_id='affected'"))
            assertEquals("RESET_REQUIRED", value(db, "SELECT state FROM sync_bootstrap_state WHERE server_profile_id='affected'"))
            assertNull(value(db, "SELECT snapshot_id FROM sync_bootstrap_state WHERE server_profile_id='affected'"))
            listOf("unaffected", "dirty").forEach { assertEquals("READY", value(db, "SELECT bootstrap_state FROM sync_cursor WHERE server_profile_id='$it'")) }
            assertEquals("FUTURE_STATE", value(db, "SELECT bootstrap_state FROM sync_cursor WHERE server_profile_id='future'"))
            assertEquals("1", value(db, "SELECT excluded_from_taste FROM user_track_preference"))
            assertEquals("{\"future\":true}", value(db, "SELECT payload_json FROM local_mutation_outbox"))
            assertEquals("9", value(db, "SELECT next_device_sequence FROM journal_lineage"))
        }
    }

    private fun value(db: SQLiteConnection, sql: String): String? = db.prepare(sql).use {
        assertTrue(it.step())
        if (it.isNull(0)) null else it.getText(0)
    }
}
