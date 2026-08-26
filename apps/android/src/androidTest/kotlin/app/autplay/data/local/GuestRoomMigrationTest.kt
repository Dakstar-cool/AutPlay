package app.autplay.data.local

import androidx.room3.testing.MigrationTestHelper
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.entity.GuestRoomProjectionEntity
import app.autplay.data.local.entity.GuestWavePreflightEntity
import app.autplay.data.local.entity.GuestWaveQueueProjectionEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class GuestRoomMigrationTest {
    @Test
    fun migration12To13PreservesExistingWaveAndStoresNoGuestBearerColumn() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val name = "guest-v12-${System.nanoTime()}.db"
        val helper = MigrationTestHelper(
            InstrumentationRegistry.getInstrumentation(),
            context.getDatabasePath(name),
            BundledSQLiteDriver(),
            AutPlayDatabase::class,
        )
        helper.createDatabase(12).use { database ->
            database.execSQL(
                "INSERT INTO wave_room(room_id,server_profile_id,room_epoch,queue_version,role,state,last_sequence,updated_at_ms) VALUES ('room-a','profile-a','1',1,'HOST','OPEN',0,1)",
            )
            database.execSQL(
                "INSERT INTO wave_preflight(room_id,queue_entry_id,server_recording_id,local_user_track_ref_id,queue_version,availability,final_ready,checked_at_ms) VALUES ('room-a','normal-entry','normal-recording',NULL,1,'LOCAL_READABLE',1,1)",
            )
            database.execSQL(
                "INSERT INTO wave_queue_projection(room_id,sequence,position,queue_entry_id,server_recording_id,local_user_track_ref_id,ready) VALUES ('room-a',0,0,'normal-entry','normal-recording',NULL,1)",
            )
        }
        helper.runMigrationsAndValidate(13, listOf(AutPlayDatabase.MIGRATION_12_13)).use { database ->
            database.prepare("SELECT count(*) FROM wave_room WHERE room_id = 'room-a'").use {
                assertTrue(it.step())
                assertEquals(1L, it.getLong(0))
            }
            val columns = mutableSetOf<String>()
            database.prepare("PRAGMA table_info(guest_room_projection)").use { statement ->
                while (statement.step()) columns += statement.getText(1)
            }
            assertTrue("guest_session_id" in columns)
            assertTrue(columns.none { it.contains("bearer") || it.contains("secret") || it.contains("token") })
        }

        val roomDatabase = AutPlayDatabase.open(context, name)
        try {
            val guestRoom = GuestRoomProjectionEntity(
                guestSessionId = "guest-session",
                invitationId = "invitation",
                roomId = "room-a",
                serverInstanceId = "server-instance",
                identityEpoch = 1,
                localMediaProfileId = "profile-a",
                roomEpoch = "2",
                queueVersion = 2,
                roomState = "OPEN",
                displayName = "Guest",
                state = "ACTIVE",
                expiresAtMs = 10_000,
                lastSequence = 3,
                updatedAtMs = 2,
            )
            roomDatabase.guestRoomDao().upsert(guestRoom)
            roomDatabase.guestRoomDao().replaceSnapshot(
                guestRoom,
                listOf(
                    GuestWavePreflightEntity(
                        "guest-session",
                        "guest-entry",
                        "guest-recording",
                        null,
                        2,
                        "UNAVAILABLE",
                        false,
                        2,
                    ),
                ),
                listOf(
                    GuestWaveQueueProjectionEntity(
                        "guest-session",
                        3,
                        0,
                        "guest-entry",
                        "guest-recording",
                        null,
                        false,
                    ),
                ),
            )

            assertEquals("HOST", roomDatabase.waveDao().room("room-a")?.role)
            assertEquals(
                listOf("normal-entry"),
                roomDatabase.waveDao().queue("room-a", 0, 10).map { it.queueEntryId },
            )
            assertEquals(
                listOf("normal-entry"),
                roomDatabase.waveDao().preflight("room-a").map { it.queueEntryId },
            )
            assertEquals(
                listOf("guest-entry"),
                roomDatabase.guestRoomDao().queue("guest-session", 3, 10)
                    .map { it.queueEntryId },
            )
        } finally {
            roomDatabase.close()
        }
    }
}
