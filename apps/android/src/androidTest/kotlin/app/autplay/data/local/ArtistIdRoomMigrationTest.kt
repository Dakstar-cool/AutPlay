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
    @Test fun personalCollectionMigrationPreservesExistingLibraryAndStartsWithNoCollectionLikes() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath("personal-library-" + System.nanoTime() + ".db"), BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(20).use { db ->
            db.execSQL("""INSERT INTO user_track_ref(local_user_track_ref_id, server_profile_id,
                raw_title, resolution_status, sync_state, last_local_sequence, created_at_ms, updated_at_ms)
                VALUES('track','profile','My track','UNRESOLVED','DIRTY',1,1,1)""")
            db.execSQL("""INSERT INTO library_entry(local_library_entry_id, local_user_track_ref_id,
                added_at_ms, source, availability_status, sync_state, last_local_sequence, updated_at_ms, server_profile_id)
                VALUES('entry','track',1,'LOCAL','LOCAL','DIRTY',1,1,'profile')""")
        }
        helper.runMigrationsAndValidate(21, listOf(AutPlayDatabase.MIGRATION_20_21)).use { db ->
            db.prepare("SELECT personal_save FROM library_entry WHERE local_library_entry_id='entry'").use {
                assertTrue(it.step())
                assertEquals(1L, it.getLong(0))
            }
            db.prepare("SELECT count(*) FROM collection_preference_event").use {
                assertTrue(it.step())
                assertEquals(0L, it.getLong(0))
            }
        }
    }

    @Test fun editionPagesPreserveExistingOwnerReferences() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val file = context.getDatabasePath("artist-editions-${System.nanoTime()}.db")
        val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), file,
            BundledSQLiteDriver(), AutPlayDatabase::class)
        helper.createDatabase(19).use { db ->
            db.execSQL("""INSERT INTO user_track_ref(local_user_track_ref_id, server_profile_id,
                raw_title, resolution_status, sync_state, last_local_sequence, created_at_ms, updated_at_ms)
                VALUES('old-ref','profile','Same recording','PENDING','DIRTY',1,1,1)""")
        }
        helper.runMigrationsAndValidate(20, listOf(AutPlayDatabase.MIGRATION_19_20)).use { db ->
            db.prepare("SELECT raw_title, sync_state FROM user_track_ref WHERE local_user_track_ref_id='old-ref'").use {
                assertTrue(it.step())
                assertEquals("Same recording", it.getText(0))
                assertEquals("DIRTY", it.getText(1))
            }
            db.execSQL("INSERT INTO catalog_page VALUES('profile','release-page','{}',1)")
            db.execSQL("INSERT INTO catalog_page VALUES('second','release-page','{}',2)")
            db.prepare("SELECT count(*) FROM catalog_page").use { assertTrue(it.step()); assertEquals(2L, it.getLong(0)) }
        }
    }

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
