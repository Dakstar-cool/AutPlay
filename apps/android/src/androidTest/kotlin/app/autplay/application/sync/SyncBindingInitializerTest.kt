package app.autplay.application.sync

import androidx.room3.Room
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.SyncBootstrapStateEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SyncBindingInitializerTest {
    @Test fun connectedDeviceGetsIdempotentCursorBeforeAnyLocalMutation() = runBlocking {
        val database = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(
            InstrumentationRegistry.getInstrumentation().targetContext,
        ).setDriver(BundledSQLiteDriver()).build()
        try {
            val binding = ClientEventBinding(
                UserId("11111111-1111-4111-8111-111111111111"),
                DeviceId("22222222-2222-4222-8222-222222222222"),
                ServerProfileId("33333333-3333-4333-8333-333333333333"),
            )
            val ids = ArrayDeque(
                listOf(
                    LocalId("44444444-4444-4444-8444-444444444444"),
                    LocalId("55555555-5555-4555-8555-555555555555"),
                ),
            )
            val initializer = SyncBindingInitializer(database, nowMs = { 123L }) {
                ids.removeFirst()
            }
            val first = initializer.ensure(binding)
            val second = initializer.ensure(binding)
            assertEquals("NOT_STARTED", first.bootstrapState)
            assertEquals(binding.deviceId.value, first.deviceId)
            assertEquals(first, second)
            assertEquals(
                database.journalDao().lineageById(first.journalLineageId),
                database.journalDao().lineageByDeviceId(binding.deviceId.value),
            )
        } finally {
            database.close()
        }
    }

    @Test fun fullSnapshotRequestPreservesCursorAndResetsSnapshotPaging() = runBlocking {
        val database = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(
            InstrumentationRegistry.getInstrumentation().targetContext,
        ).setDriver(BundledSQLiteDriver()).build()
        try {
            val binding = ClientEventBinding(
                UserId("11111111-1111-4111-8111-111111111111"),
                DeviceId("22222222-2222-4222-8222-222222222222"),
                ServerProfileId("33333333-3333-4333-8333-333333333333"),
            )
            val ids = ArrayDeque(
                listOf(
                    LocalId("44444444-4444-4444-8444-444444444444"),
                    LocalId("55555555-5555-4555-8555-555555555555"),
                ),
            )
            val initializer = SyncBindingInitializer(database, nowMs = { 456L }) {
                ids.removeFirst()
            }
            val initial = initializer.ensure(binding)
            database.syncDao().upsertCursor(
                initial.copy(
                    opaqueCursor = "incremental-cursor",
                    bootstrapSnapshotId = "66666666-6666-4666-8666-666666666666",
                    bootstrapState = "READY",
                ),
            )
            database.syncDao().upsertBootstrapState(
                SyncBootstrapStateEntity(
                    binding.serverProfileId.value,
                    "66666666-6666-4666-8666-666666666666",
                    "page-token",
                    "snapshot-cursor",
                    "READY",
                    123L,
                ),
            )

            val reset = initializer.requestFullSnapshot(binding)

            assertEquals("RESET_REQUIRED", reset.bootstrapState)
            assertEquals("incremental-cursor", reset.opaqueCursor)
            assertEquals(null, reset.bootstrapSnapshotId)
            val bootstrap = database.syncDao().bootstrapState(binding.serverProfileId.value)
            assertEquals("RESET_REQUIRED", bootstrap?.state)
            assertEquals(null, bootstrap?.snapshotId)
            assertEquals(null, bootstrap?.pageToken)
            assertEquals(null, bootstrap?.finalCursor)
        } finally {
            database.close()
        }
    }
}
