package app.autplay.application.wave

import androidx.room3.Room
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.data.local.AutPlayDatabase
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class WaveCoordinatorHostTransferTest {
    private lateinit var database: AutPlayDatabase

    @Before fun setUp() {
        database = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(
            ApplicationProvider.getApplicationContext<android.content.Context>(),
        ).setDriver(BundledSQLiteDriver()).build()
    }

    @After fun tearDown() {
        database.close()
    }

    @Test fun successfulTransferUsesFreshSnapshotBeforeChangingRole() = runBlocking {
        val target = WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room")
        val transport = FakeTransport(hostSnapshot(target), memberSnapshot())
        val coordinator = coordinator(transport)
        coordinator.joinByCode("01ABCDEFGH")
        assertTrue(coordinator.uiState.value.isHost)

        coordinator.transferHost(TARGET_DEVICE_ID)

        assertEquals(TARGET_DEVICE_ID, transport.transferredDeviceId)
        assertEquals(2, transport.snapshotCalls.get())
        assertFalse(coordinator.uiState.value.isHost)
        assertEquals(emptyList<WaveHostTransferTarget>(), coordinator.uiState.value.hostTransferTargets)
        coordinator.close()
    }

    @Test fun staleTargetIsRejectedBeforeTransportAndKeepsHostState() = runBlocking {
        val target = WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room")
        val transport = FakeTransport(hostSnapshot(target), memberSnapshot())
        val coordinator = coordinator(transport)
        coordinator.joinByCode("01ABCDEFGH")

        val failure = runCatching {
            coordinator.transferHost("44444444-4444-4444-8444-444444444444")
        }.exceptionOrNull()

        assertTrue(failure is IllegalArgumentException)
        assertEquals(null, transport.transferredDeviceId)
        assertTrue(coordinator.uiState.value.isHost)
        coordinator.close()
    }

    @Test fun serverRejectionDoesNotApplyMemberSnapshotOrChangeRole() = runBlocking {
        val target = WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room")
        val transport = FakeTransport(hostSnapshot(target), memberSnapshot(), rejectTransfer = true)
        val coordinator = coordinator(transport)
        coordinator.joinByCode("01ABCDEFGH")

        val failure = runCatching { coordinator.transferHost(TARGET_DEVICE_ID) }.exceptionOrNull()

        assertTrue(failure is IllegalStateException)
        assertEquals(1, transport.snapshotCalls.get())
        assertTrue(coordinator.uiState.value.isHost)
        assertEquals(listOf(target), coordinator.uiState.value.hostTransferTargets)
        coordinator.close()
    }

    private fun coordinator(transport: WaveTransport) = WaveCoordinator(
        database = database,
        transport = transport,
        projectionStore = InMemoryProjectionStore(),
    )

    private fun hostSnapshot(target: WaveHostTransferTarget) = WaveSnapshot(
        roomId = ROOM_ID,
        profileId = PROFILE_ID,
        roomEpoch = "1",
        queueVersion = 1,
        role = "HOST",
        state = "OPEN",
        sequence = 1,
        entries = emptyList(),
        preflight = emptyMap(),
        hostTransferTargets = listOf(target),
    )

    private fun memberSnapshot() = hostSnapshot(WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room"))
        .copy(role = "MEMBER", sequence = 2, hostTransferTargets = emptyList())

    private class FakeTransport(
        private val joined: WaveSnapshot,
        private val afterTransfer: WaveSnapshot,
        private val rejectTransfer: Boolean = false,
    ) : WaveTransport {
        val snapshotCalls = AtomicInteger()
        var transferredDeviceId: String? = null
        private var transferred = false

        override suspend fun joinByCode(code: String): WaveSnapshot = joined

        override suspend fun snapshot(roomId: String): WaveSnapshot {
            snapshotCalls.incrementAndGet()
            return if (transferred) afterTransfer else joined
        }

        override fun connect(
            roomId: String,
            afterSequence: Long,
            roomEpoch: String,
            onEvent: (WaveEvent) -> Unit,
            onFailure: () -> Unit,
        ): AutoCloseable = AutoCloseable {}

        override suspend fun transferHost(roomId: String, targetDeviceId: String) {
            if (rejectTransfer) error("server rejected transfer")
            transferredDeviceId = targetDeviceId
            transferred = true
        }
    }

    private class InMemoryProjectionStore : WaveProjectionStore {
        private var room: StoredWaveRoom? = null
        private var queue: List<StoredWaveQueueEntry> = emptyList()

        override suspend fun room(roomId: String): StoredWaveRoom? = room?.takeIf { it.roomId == roomId }

        override suspend fun queue(roomId: String, sequence: Long, limit: Int): List<StoredWaveQueueEntry> =
            queue.filter { it.sequence == sequence }.take(limit)

        override suspend fun advance(roomId: String, roomEpoch: String, sequence: Long, nowMs: Long): Int {
            val current = room ?: return 0
            if (current.roomId != roomId || current.roomEpoch != roomEpoch) return 0
            room = current.copy(lastSequence = sequence, updatedAtMs = nowMs)
            return 1
        }

        override suspend fun replaceSnapshot(
            room: StoredWaveRoom,
            preflight: List<StoredWavePreflight>,
            queue: List<StoredWaveQueueEntry>,
        ) {
            this.room = room
            this.queue = queue
        }
    }

    private companion object {
        const val ROOM_ID = "11111111-1111-4111-8111-111111111111"
        const val PROFILE_ID = "22222222-2222-4222-8222-222222222222"
        const val TARGET_DEVICE_ID = "33333333-3333-4333-8333-333333333333"
    }
}
