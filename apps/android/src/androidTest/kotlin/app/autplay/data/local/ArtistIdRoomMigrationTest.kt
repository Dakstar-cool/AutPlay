package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import kotlinx.coroutines.runBlocking

@RunWith(AndroidJUnit4::class)
class ArtistIdRoomMigrationTest {
    @Test fun migration11To12PreservesExistingServerFeatureProjection() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val name = "artist-v11-${System.nanoTime()}.db"
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), context.getDatabasePath(name), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(11).use { db ->
            db.execSQL("INSERT INTO remote_import_job_projection(server_profile_id,import_job_id,state,progress_current,progress_total,review_required_count,resolved_count,no_match_count,unresolved_count,failed_count,updated_at_ms) VALUES ('profile-a','job-a','RUNNING',1,2,0,0,0,0,0,1)")
        }
        helper.runMigrationsAndValidate(12, listOf(AutPlayDatabase.MIGRATION_11_12)).use { db ->
            db.prepare("SELECT count(*) FROM remote_import_job_projection WHERE server_profile_id = 'profile-a'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
            db.prepare("SELECT count(*) FROM artist_projection").use { statement ->
                assertTrue(statement.step())
                assertEquals(0L, statement.getLong(0))
            }
        }
    }
}
