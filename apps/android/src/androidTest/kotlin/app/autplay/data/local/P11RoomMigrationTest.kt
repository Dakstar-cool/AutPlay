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

/** Room v9 preserves v8 bytes but never assigns an unverifiable legacy pack to a user. */
@RunWith(AndroidJUnit4::class)
class P11RoomMigrationTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-p11-migration.db"

    @After
    fun clean() {
        context.deleteDatabase(name)
    }

    @Test
    fun v8ToV9PreservesLegacyPackAndAddsOwnerScopedPresentationMapping() = runBlocking {
        val helper = MigrationTestHelper(
            InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath(name),
            BundledSQLiteDriver(),
            AutPlayDatabase::class,
        )
        helper.createDatabase(8).use { db ->
            db.execSQL(
                "INSERT INTO recommendation_pack(offline_pack_id,server_profile_id,catalog_snapshot,model_bundle_version,payload_version,payload_encoding,payload,payload_sha256,created_at_ms,expires_at_ms) " +
                    "VALUES ('$PACK','$PROFILE',7,'legacy',1,'RAW_JSON',X'7B7D',X'00',1,999)",
            )
            db.execSQL(
                "INSERT INTO local_import_job(import_job_id,server_profile_id,adapter_id,adapter_version,envelope_version,input_sha256,input_digest_verified,source_uri,persisted_uri_permission,source_availability,state,checkpoint_position,total_entries,review_required_count,resolved_count,no_match_count,unresolved_count,failed_count,report_json,created_at_ms,updated_at_ms,completed_at_ms) " +
                    "VALUES ('$JOB','$PROFILE','fixture','1',1,'${"a".repeat(64)}',1,NULL,0,'AVAILABLE','COMPLETED',1,1,0,1,0,0,0,'{}',1,1,1)",
            )
        }

        helper.runMigrationsAndValidate(9, listOf(AutPlayDatabase.MIGRATION_8_9)).use { db ->
            db.prepare("SELECT owner_user_id, payload FROM recommendation_pack WHERE offline_pack_id = '$PACK'").use { statement ->
                assertTrue(statement.step())
                assertTrue(statement.isNull(0))
                assertEquals("{}", statement.getBlob(1).toString(Charsets.UTF_8))
            }
            db.prepare("SELECT count(*) FROM local_import_job WHERE import_job_id = '$JOB'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
            db.prepare("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='recommendation_presentation'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
        }
    }

    @Test
    fun freshV9CreatesBothRecommendationTables() = runBlocking {
        val database = AutPlayDatabase.open(context, name)
        try {
            assertEquals(0, database.recommendationPackDao().latest(PROFILE, USER, 5).size)
            assertEquals(0, database.recommendationPackDao().presentationCount(PROFILE, USER))
        } finally {
            database.close()
        }
    }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val PACK = "33333333-3333-4333-8333-333333333333"
        const val JOB = "44444444-4444-4444-8444-444444444444"
    }
}
