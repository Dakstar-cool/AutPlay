package app.autplay.work

import android.net.ConnectivityManager
import android.os.ParcelFileDescriptor
import androidx.room3.useWriterConnection
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.NetworkType
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.await
import app.autplay.AutPlayRuntime
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Normal WorkManager, production runtime, real Room/Keystore/HTTP; no manual coordinator calls. */
@RunWith(AndroidJUnit4::class)
class SyncWorkerLifecycleTest {
    @Test fun historyCommitRetriesSocketFailureAndAutomaticallyAcknowledgesOriginalEvent() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start()
            fixture.disconnect = true
            val event = fixture.record()
            fixture.await { fixture.work().any { it.runAttemptCount >= 1 && it.state == WorkInfo.State.ENQUEUED } }
            assertEquals("PENDING", fixture.database.journalDao().event(event)?.state)
            fixture.disconnect = false
            fixture.awaitAck(event)
            assertTrue(fixture.sentIds.count { it == event } >= 2)
            assertEquals(1, fixture.sentHashes.distinct().size)
        } finally { fixture.close() }
    }

    @Test fun historyEnqueuedDuringRunAndAfterCompletionHasDurableSuccessors() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start()
            fixture.holdPull = true
            val first = fixture.record()
            fixture.await { fixture.pullEntered.count == 0L }
            val second = fixture.record()
            fixture.await { fixture.work().size == 2 }
            assertTrue(fixture.work().any { it.state == WorkInfo.State.BLOCKED })
            fixture.release.countDown()
            fixture.awaitAck(first)
            fixture.awaitAck(second)
            fixture.await { fixture.work().all { it.state == WorkInfo.State.SUCCEEDED } }
            val third = fixture.record()
            fixture.awaitAck(third)
            fixture.await { fixture.work().all { it.state == WorkInfo.State.SUCCEEDED } }
            assertEquals(listOf(first, second, third), fixture.sentIds.toList())
        } finally { fixture.close() }
    }

    @Test fun policyReconciliationCancelsActiveLeaseAndWaitsForRealUnmeteredNetwork() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start()
            fixture.holdPush = true
            val event = fixture.record()
            fixture.await { fixture.pushEntered.count == 0L }
            assertEquals(NetworkType.CONNECTED, fixture.work().single().constraints.requiredNetworkType)
            fixture.wifi(false)
            fixture.await { fixture.network.activeNetworkInfo?.isConnected == true && fixture.network.isActiveNetworkMetered }
            fixture.settings.update(fixture.settings.settings.first().copy(syncOnMeteredNetwork = false))
            fixture.scheduler.reconcile(fixture.request)
            fixture.await { fixture.database.journalDao().event(event)?.state == "PENDING" }
            val pending = requireNotNull(fixture.database.journalDao().event(event))
            assertEquals(0, pending.attemptCount)
            assertEquals(null, pending.leaseToken)
            val replacement = fixture.work().last { !it.state.isFinished }
            assertEquals(NetworkType.UNMETERED, replacement.constraints.requiredNetworkType)
            assertEquals(WorkInfo.State.ENQUEUED, replacement.state)
            val requestsBeforeWifi = fixture.server.requestCount
            fixture.release.countDown()
            delay(500)
            assertEquals(requestsBeforeWifi, fixture.server.requestCount)
            fixture.wifi(true)
            fixture.await { !fixture.network.isActiveNetworkMetered }
            fixture.awaitAck(event)
            assertEquals(1, fixture.sentHashes.distinct().size)
        } finally { fixture.close() }
    }

    @Test fun manualRetryReplacesBackoffAndRunsImmediately() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start()
            fixture.disconnectPull = true
            fixture.scheduler.enqueue(fixture.request)
            fixture.await { fixture.work().any { it.runAttemptCount >= 1 && it.state == WorkInfo.State.ENQUEUED } }
            val original = fixture.work().single().id
            fixture.disconnectPull = false
            fixture.scheduler.reconcile(fixture.request)
            fixture.await { fixture.work().any { it.id != original && it.state == WorkInfo.State.SUCCEEDED } }
            val replacement = fixture.work().single { it.id != original }
            // WorkInfo includes the first execution; the Worker's input count starts at zero.
            assertEquals(1, replacement.runAttemptCount)
            assertEquals(NetworkType.CONNECTED, replacement.constraints.requiredNetworkType)
        } finally { fixture.close() }
    }

    @Test fun connectedReplacementRetainsAndAcknowledgesInFlightIntent() = runBlocking {
        val fixture = Fixture()
        try {
            fixture.start()
            fixture.holdPush = true
            val event = fixture.record()
            fixture.await { fixture.pushEntered.count == 0L }
            assertEquals("SENDING", fixture.database.journalDao().event(event)?.state)
            fixture.scheduler.reconcile(fixture.request)
            fixture.release.countDown()
            fixture.awaitAck(event)
            fixture.await { fixture.work().all { it.state.isFinished } }
            assertTrue(fixture.sentIds.all { it == event })
            assertEquals(1, fixture.sentHashes.distinct().size)
        } finally { fixture.close() }
    }

    private class Fixture {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val database = AutPlayRuntime.database(context)
        val settings = applicationNonSecretSettingsStore(context)
        val network = context.getSystemService(ConnectivityManager::class.java)
        val scheduler = AutPlayRuntime.syncScheduler(context)
        val server = MockWebServer()
        private val workManager = WorkManager.getInstance(context)
        private val credentials = AndroidKeystoreCredentialStore(context)
        private val ids = List(7) { UUID.randomUUID().toString() }
        private val profile = ServerProfileId(ids[0])
        private val binding = ClientEventBinding(UserId(ids[1]), DeviceId(ids[2]), profile, LocalId(ids[3]))
        val request = DeferredWorkRequest(DeferredWorkKind.SYNC, DeferredWorkSubject.Device(binding.deviceId), profile)
        private val workName = "autplay-deferred-sync-device-${ids[2]}-${ids[0]}"
        private lateinit var original: NonSecretSettings
        @Volatile var disconnect = false
        @Volatile var disconnectPull = false
        @Volatile var holdPush = false
        @Volatile var holdPull = false
        val release = CountDownLatch(1)
        val pushEntered = CountDownLatch(1)
        val pullEntered = CountDownLatch(1)
        val sentIds = CopyOnWriteArrayList<String>()
        val sentHashes = CopyOnWriteArrayList<String>()
        private var wifiChanged = false

        suspend fun start() {
            original = settings.settings.first()
            assertTrue("Connected Android network required", network.activeNetworkInfo?.isConnected == true)
            credentials.write(profile, "a".repeat(64).toByteArray())
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    assertEquals("Bearer ${"a".repeat(64)}", request.getHeader("Authorization"))
                    if (request.requestUrl?.encodedPath?.endsWith("/sync/push") == true) {
                        val events = Json.parseToJsonElement(request.body.readUtf8()).jsonObject.getValue("events").jsonArray
                        val acks = events.map { it.jsonObject }.map { event ->
                            val id = event.getValue("event_id").jsonPrimitive.content
                            sentIds += id
                            sentHashes += event.getValue("request_hash").jsonPrimitive.content
                            """{"event_id":"$id","outcome":"APPLIED","aggregate_type":"LISTENING_EVENT","aggregate_local_id":"$id","aggregate_server_id":"$id","server_row_version":1}"""
                        }
                        pushEntered.countDown()
                        if (holdPush) { holdPush = false; check(release.await(40, TimeUnit.SECONDS)) }
                        if (disconnect) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                        return json("""{"acks":[${acks.joinToString(",") }]}""")
                    }
                    pullEntered.countDown()
                    if (disconnectPull) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                    if (holdPull) { holdPull = false; check(release.await(40, TimeUnit.SECONDS)) }
                    return json("""{"next_cursor":"fixture-cursor","has_more":false,"events":[]}""")
                }
            }
            server.start()
            val origin = server.url("/").toString().trimEnd('/')
            settings.update(NonSecretSettings(activeServerProfileId = profile, activeUserId = binding.userId,
                deviceId = binding.deviceId, serverBaseUrl = origin, streamBaseUrl = origin, syncOnMeteredNetwork = true))
            database.journalDao().insertLineage(JournalLineageEntity(ids[4], ids[1], ids[2], ids[3], 1, 1))
            database.syncDao().upsertCursor(SyncCursorEntity(ids[0], ids[4], ids[2], ids[3], "fixture-cursor", 0, 0, null, "READY", null, 1))
            database.libraryDao().upsertTrackRef(UserTrackRefEntity(ids[5], ids[6], null, null, "RESOLVED",
                "Sync fixture", "Fixture", null, 40_000, 1.0, "CLEAN", 1, 0, 1, 1, null, ids[0]))
        }

        suspend fun record(): String {
            val event = LocalId.random()
            LibraryVerticalSliceRepository(database, syncScheduler = scheduler).recordListening(
                binding, event, LocalId(ids[5]), 1_000, 40_000, false, "ORGANIC", now = System.currentTimeMillis())
            return event.value
        }

        suspend fun awaitAck(event: String) = await { database.journalDao().event(event)?.state == "ACKED" }
        suspend fun await(condition: suspend () -> Boolean) = withTimeout(40_000) {
            while (!condition()) delay(50)
        }
        fun work(): List<WorkInfo> = workManager.getWorkInfosForUniqueWork(workName).get(2, TimeUnit.SECONDS)
        fun wifi(enabled: Boolean) {
            check(shell("getprop ro.kernel.qemu").trim() == "1") { "DISPOSABLE_EMULATOR_REQUIRED" }
            wifiChanged = true
            shell("su 0 svc wifi ${if (enabled) "enable" else "disable"}")
        }
        private fun shell(command: String): String = ParcelFileDescriptor.AutoCloseInputStream(
            instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }

        suspend fun close() {
            release.countDown()
            workManager.cancelUniqueWork(workName).await()
            if (wifiChanged) { wifi(true); await { !network.isActiveNetworkMetered } }
            if (::original.isInitialized) settings.update(original)
            credentials.clear(profile)
            database.useWriterConnection { connection ->
                for (table in listOf("listening_event", "offline_journal_event", "sync_runtime_status", "sync_cursor", "user_track_ref")) {
                    connection.usePrepared("DELETE FROM $table WHERE server_profile_id = ?") { it.bindText(1, ids[0]); it.step() }
                }
                connection.usePrepared("DELETE FROM journal_lineage WHERE lineage_id = ?") { it.bindText(1, ids[4]); it.step() }
            }
            server.close()
        }
        private fun json(body: String) = MockResponse().setBody(body).setHeader("Content-Type", "application/json")
    }
}
