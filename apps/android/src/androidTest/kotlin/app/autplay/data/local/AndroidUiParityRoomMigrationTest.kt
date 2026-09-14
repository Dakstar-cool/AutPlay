package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AndroidUiParityRoomMigrationTest {
    @Test fun migration14To15PreservesQueueAndAddsIndependentTasteFlags() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val name = "ui-parity-v14-${System.nanoTime()}.db"
        val helper = MigrationTestHelper(
            InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath(name),
            BundledSQLiteDriver(),
            AutPlayDatabase::class,
        )
        helper.createDatabase(14).use { db ->
            db.execSQL("INSERT INTO queue_snapshot(queue_snapshot_id,queue_type,source_context_id,current_entry_id,current_position_ms,shuffle_mode,repeat_mode,seed,generation_version,is_active,active_slot,created_at_ms,updated_at_ms,server_profile_id,listening_context,active_listening_event_id,active_session_started_at_ms,active_session_start_position_ms,active_session_observed_played_ms,active_session_user_id,active_session_device_id,active_session_server_profile_id) VALUES('queue','USER',NULL,NULL,0,'OFF','OFF',NULL,NULL,1,'ACTIVE',1,1,NULL,'GENERAL',NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
        }

        helper.runMigrationsAndValidate(15, listOf(AutPlayDatabase.MIGRATION_14_15)).use { db ->
            db.prepare("SELECT session_excluded_from_taste,active_listen_excluded_from_taste FROM queue_snapshot WHERE queue_snapshot_id='queue'").use { statement ->
                assertTrue(statement.step())
                assertEquals(0L, statement.getLong(0))
                assertEquals(0L, statement.getLong(1))
            }
            db.execSQL("UPDATE queue_snapshot SET session_excluded_from_taste=1,active_listen_excluded_from_taste=0 WHERE queue_snapshot_id='queue'")
            db.prepare("SELECT session_excluded_from_taste,active_listen_excluded_from_taste FROM queue_snapshot WHERE queue_snapshot_id='queue'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
                assertEquals(0L, statement.getLong(1))
            }
        }
        context.deleteDatabase(name)
        Unit
    }
}
