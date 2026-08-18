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

/** API26 migration evidence: v9 playback data survives; Wave cache is additive. */
@RunWith(AndroidJUnit4::class)
class P13RoomMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-p13-migration.db"
    @After fun clean() { context.deleteDatabase(name) }

    @Test fun v9ToV10PreservesQueueAndCreatesWaveTables() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(9).use { db ->
            db.execSQL("INSERT INTO queue_snapshot(queue_snapshot_id,queue_type,source_context_id,current_entry_id,current_position_ms,shuffle_mode,repeat_mode,seed,generation_version,is_active,active_slot,created_at_ms,updated_at_ms,server_profile_id,listening_context) VALUES('queue','USER',NULL,NULL,0,'OFF','OFF',NULL,NULL,1,'ACTIVE',1,1,NULL,'GENERAL')")
        }
        helper.runMigrationsAndValidate(10, listOf(AutPlayDatabase.MIGRATION_9_10)).use { db ->
            db.prepare("SELECT count(*) FROM queue_snapshot WHERE queue_snapshot_id='queue'").use { statement -> assertTrue(statement.step()); assertEquals(1L, statement.getLong(0)) }
            listOf("wave_room", "wave_preflight", "wave_queue_projection").forEach { table ->
                db.prepare("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='$table'").use { statement -> assertTrue(statement.step()); assertEquals(1L, statement.getLong(0)) }
            }
        }
    }
}
