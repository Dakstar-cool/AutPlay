package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class P08RoomMigrationTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val databaseName = "autplay-p08-migration.db"

    @After fun tearDown() { context.deleteDatabase(databaseName) }

    @Test
    fun v1ToV2PreservesQueueAndAddsNullablePlaybackState() = runBlocking {
        val file = context.getDatabasePath(databaseName)
        val helper = MigrationTestHelper(
            instrumentation = instrumentation,
            file = file,
            driver = BundledSQLiteDriver(),
            databaseClass = AutPlayDatabase::class,
        )
        helper.createDatabase(1).use { connection ->
            connection.execSQL(
                """INSERT INTO queue_snapshot(
                    queue_snapshot_id, queue_type, source_context_id, current_entry_id,
                    current_position_ms, shuffle_mode, repeat_mode, seed, generation_version,
                    is_active, active_slot, created_at_ms, updated_at_ms
                ) VALUES ('${uuid(1)}', 'USER', NULL, NULL, 42, 'OFF', 'OFF', NULL, 'p07', 1, 'ACTIVE', 1, 1)""".trimIndent(),
            )
        }
        helper.runMigrationsAndValidate(2, listOf(AutPlayDatabase.MIGRATION_1_2)).use { connection ->
            connection.prepare(
                "SELECT current_position_ms, listening_context, active_listening_event_id, active_session_user_id, active_session_device_id, active_session_server_profile_id FROM queue_snapshot",
            ).use { statement ->
                assertEquals(true, statement.step())
                assertEquals(42, statement.getLong(0))
                assertEquals("GENERAL", statement.getText(1))
                assertEquals(true, statement.isNull(2))
                assertEquals(true, statement.isNull(3))
                assertEquals(true, statement.isNull(4))
                assertEquals(true, statement.isNull(5))
            }
        }
        Unit
    }

    private fun uuid(seed: Int): String = UUID(0, seed.toLong()).toString()
}
