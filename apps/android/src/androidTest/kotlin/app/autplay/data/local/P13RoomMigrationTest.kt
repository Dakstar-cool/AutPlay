package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.entity.RecommendationResponseSnapshotEntity
import app.autplay.data.local.entity.RemoteImportJobProjectionEntity
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

    @Test fun v10ToV11PreservesWaveAndCreatesServerFeatureMetadataTables() = runBlocking {
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(10).use { db ->
            db.execSQL("INSERT INTO wave_room(room_id,server_profile_id,room_epoch,queue_version,role,state,last_sequence,updated_at_ms) VALUES('room','profile','epoch',1,'HOST','ACTIVE',2,3)")
        }
        helper.runMigrationsAndValidate(11, listOf(AutPlayDatabase.MIGRATION_10_11)).use { db ->
            db.prepare("SELECT count(*) FROM wave_room WHERE room_id='room'").use { statement -> assertTrue(statement.step()); assertEquals(1L, statement.getLong(0)) }
            listOf("remote_import_job_projection", "vault_upload_intent", "recommendation_response_snapshot").forEach { table ->
                db.prepare("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='$table'").use { statement -> assertTrue(statement.step()); assertEquals(1L, statement.getLong(0)) }
            }
        }
    }

    @Test fun v11ServerFeatureProjectionDaoRoundTripsMetadataOnlyRows() = runBlocking {
        val database = AutPlayDatabase.open(context, name)
        try {
            val dao = database.serverFeatureProjectionDao()
            val import = RemoteImportJobProjectionEntity(
                serverProfileId = "profile",
                importJobId = "import-job",
                deliveryJobId = "delivery-job",
                state = "RUNNING",
                progressCurrent = 4,
                progressTotal = 8,
                reviewRequiredCount = 1,
                resolvedCount = 2,
                noMatchCount = 0,
                unresolvedCount = 1,
                failedCount = 0,
                lastErrorCode = null,
                updatedAtMs = 9,
            )
            val response = RecommendationResponseSnapshotEntity("profile", "request", "served", 3, "a".repeat(64), 10)
            dao.upsertRemoteImportJob(import)
            dao.upsertRecommendationResponseSnapshot(response)

            assertEquals(import, dao.remoteImportJob("profile", "import-job"))
            assertEquals(response, dao.recommendationResponseSnapshot("profile", "request"))
        } finally {
            database.close()
        }
    }
}
