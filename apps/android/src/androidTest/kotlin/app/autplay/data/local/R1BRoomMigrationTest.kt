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
class R1BRoomMigrationTest {
    @Test
    fun migration13To14PreservesP11RowsAndAddsCascadingDeltaTable() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val name = "r1b-v13-${System.nanoTime()}.db"
        val helper = MigrationTestHelper(
            InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath(name),
            BundledSQLiteDriver(),
            AutPlayDatabase::class,
        )
        helper.createDatabase(13).use { db ->
            db.execSQL(
                "INSERT INTO recommendation_pack(offline_pack_id,server_profile_id,owner_user_id,catalog_snapshot,model_bundle_version,payload_version,payload_encoding,payload,payload_sha256,created_at_ms,expires_at_ms) VALUES('pack','profile','owner',7,'cpu-v1',1,'RAW_JSON',X'0102',X'${"aa".repeat(32)}',1,9)",
            )
            db.execSQL(
                "INSERT INTO recommendation_presentation(server_profile_id,owner_user_id,presentation_id,recommendation_request_id,source_rank,impression_event_id,recording_id,offline_pack_id,source,surface,section_key,display_position,created_at_ms) VALUES('profile','owner','presentation','request',1,'event','recording','pack','offline_pack','home','for_you',1,2)",
            )
        }

        helper.runMigrationsAndValidate(14, listOf(AutPlayDatabase.MIGRATION_13_14)).use { db ->
            db.execSQL("PRAGMA foreign_keys=ON")
            db.prepare("SELECT hex(payload) FROM recommendation_pack WHERE offline_pack_id='pack'").use { statement ->
                assertTrue(statement.step())
                assertEquals("0102", statement.getText(0))
            }
            db.prepare("SELECT impression_event_id FROM recommendation_presentation WHERE presentation_id='presentation'").use { statement ->
                assertTrue(statement.step())
                assertEquals("event", statement.getText(0))
            }
            db.prepare("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='recommendation_temporal_delta'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
            db.execSQL(
                "INSERT INTO recommendation_temporal_delta(delta_id,server_profile_id,owner_user_id,device_id,offline_pack_id,recommendation_request_id,feature_policy_version,feature_policy_sha256,parent_pack_sha256,parent_items_sha256,payload_version,payload_encoding,payload,payload_sha256,cutoff_at_ms,created_at_ms,expires_at_ms) VALUES('delta','profile','owner','device','pack','request','1',X'${"bb".repeat(32)}',X'${"aa".repeat(32)}',X'${"cc".repeat(32)}',1,'RAW_JSON',X'03',X'${"dd".repeat(32)}',1,2,8)",
            )
            db.execSQL("DELETE FROM recommendation_pack WHERE offline_pack_id='pack'")
            db.prepare("SELECT count(*) FROM recommendation_temporal_delta").use { statement ->
                assertTrue(statement.step())
                assertEquals(0L, statement.getLong(0))
            }
        }
        context.deleteDatabase(name)
        Unit
    }
}
