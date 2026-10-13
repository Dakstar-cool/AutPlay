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

class PortableAccountDataMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "portable-data-migration.db"
    @After fun cleanup() { context.deleteDatabase(name) }

    @Test fun migrationPreservesExistingDataAndEnforcesAccountAliasIsolation() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(17).use { db ->
            db.execSQL("INSERT INTO journal_lineage VALUES('lineage','user','device','epoch',9,1)")
            db.execSQL("INSERT INTO user_track_ref(local_user_track_ref_id,resolution_status,raw_title,sync_state,last_local_sequence,created_at_ms,updated_at_ms,server_profile_id) VALUES('track','FUTURE_STATUS','Preserved','DIRTY',8,1,2,'profile')")
            db.execSQL("INSERT INTO local_mutation_outbox VALUES('intent','FUTURE_EVENT',99,'FUTURE_AGGREGATE','track','{}',2,'UNMATERIALIZED',NULL,NULL)")
            db.execSQL("INSERT INTO listening_event(listening_event_id,local_user_track_ref_id,started_at_ms,played_ms,event_origin,context,explicit_feedback,excluded_from_taste,sync_state,created_at_ms,server_profile_id) VALUES('event','track',1,30000,'FUTURE_ORIGIN','GENERAL','NONE',0,'DIRTY',2,'profile')")
        }
        helper.runMigrationsAndValidate(18, listOf(AutPlayDatabase.MIGRATION_17_18)).use { db ->
            // MigrationTestHelper's TestOpenDelegate omits the generated Room onOpen FK pragma.
            // Configure the returned raw connection after the migration transaction has ended.
            db.execSQL("PRAGMA foreign_keys = ON")
            assertEquals("1", value(db, "PRAGMA foreign_keys"))
            db.prepare("PRAGMA foreign_key_list('portable_listening_alias')").use { fk ->
                val columns = mutableSetOf<Pair<String, String>>()
                while (fk.step()) {
                    assertEquals("portable_account_record", fk.getText(2))
                    assertEquals("NO ACTION", fk.getText(5))
                    assertEquals("CASCADE", fk.getText(6))
                    columns.add(fk.getText(3) to fk.getText(4))
                }
                assertEquals(setOf("account_id" to "account_id", "record_id" to "record_id"), columns)
            }
            assertEquals("Preserved", value(db, "SELECT raw_title FROM user_track_ref"))
            assertEquals("FUTURE_STATUS", value(db, "SELECT resolution_status FROM user_track_ref"))
            assertEquals("FUTURE_EVENT", value(db, "SELECT event_type FROM local_mutation_outbox"))
            assertEquals("30000", value(db, "SELECT played_ms FROM listening_event"))
            assertEquals("9", value(db, "SELECT next_device_sequence FROM journal_lineage"))
            assertEquals("0", value(db, "SELECT count(*) FROM portable_account_scope"))
            db.execSQL("INSERT INTO portable_account_record VALUES('owner-a','record','LISTENING','{}','hash',1)")
            db.execSQL("INSERT INTO portable_account_record VALUES('owner-b','record','LISTENING','{}','hash',1)")
            db.execSQL("INSERT INTO portable_listening_alias VALUES('owner-a','alias','record')")
            db.execSQL("INSERT INTO portable_listening_alias VALUES('owner-b','alias','record')")
            db.execSQL("DELETE FROM portable_account_record WHERE account_id='owner-a'")
            assertEquals("0", value(db, "SELECT count(*) FROM portable_listening_alias WHERE account_id='owner-a'"))
            assertEquals("1", value(db, "SELECT count(*) FROM portable_listening_alias"))
            assertEquals("owner-b", value(db, "SELECT account_id FROM portable_listening_alias"))
            assertEquals("1", value(db, "SELECT count(*) FROM portable_account_record WHERE account_id='owner-b'"))
            assertEquals("1", value(db, "SELECT count(*) FROM listening_event"))
            db.prepare("PRAGMA foreign_key_check").use { assertFalse(it.step()) }
        }
    }

    private fun value(db: SQLiteConnection, sql: String): String? = db.prepare(sql).use {
        assertTrue(it.step()); if (it.isNull(0)) null else it.getText(0)
    }
}
