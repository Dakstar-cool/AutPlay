package app.autplay.application.sync

import androidx.room3.Room
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionRequiredException
import app.autplay.application.library.AddLocalTrackCommand
import app.autplay.application.library.AddLocalTrackResult
import app.autplay.application.library.LocalLibraryCommandRepository
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

/** Real Room/API26 acceptance seams for cursor atomicity and profile isolation. */
@RunWith(AndroidJUnit4::class)
class SyncCoordinatorAcceptanceTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val db = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(context).setDriver(BundledSQLiteDriver()).build()
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private val otherProfile = ServerProfileId("22222222-2222-4222-8222-222222222222")
    private val binding = ClientEventBinding(UserId("33333333-3333-4333-8333-333333333333"), DeviceId("44444444-4444-4444-8444-444444444444"), profile, LocalId("55555555-5555-4555-8555-555555555555"))
    @After fun close() = db.close()

    @Test fun unknownPageIsDeferredWithoutCursorAdvance() = runBlocking {
        seed(profile, "cursor-a")
        val coordinator = SyncCoordinator(db, FakeTransport(pull = PullPage("cursor-b", false, listOf(RemoteEvent("66666666-6666-4666-8666-666666666666", 1, "FUTURE_EVENT", 99, "{}")))))
        coordinator.run(binding)
        assertEquals("cursor-a", db.syncDao().cursor(profile.value)?.opaqueCursor)
        assertNotNull(db.syncDao().runtimeStatus(profile.value))
    }

    @Test fun invalidCursorPreservesPendingJournalAndRequestsReset() = runBlocking {
        seed(profile, "cursor-a")
        db.journalDao().insert(OfflineJournalEventEntity("99999999-9999-4999-8999-999999999999", "77777777-7777-4777-8777-777777777777", "99999999-9999-4999-8999-999999999999", binding.userId.value, binding.deviceId.value, profile.value, 1, "USER_TRACK_REF_CREATED", 1, "USER_TRACK_REF", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", null, null, "{}", byteArrayOf(1), 1, "PENDING", 0, null, null, null, null, null))
        val coordinator = SyncCoordinator(db, FakeTransport(throwInvalidCursor = true))
        coordinator.run(binding)
        assertEquals("RESET_REQUIRED", db.syncDao().cursor(profile.value)?.bootstrapState)
        assertEquals("PENDING", db.journalDao().event("99999999-9999-4999-8999-999999999999")?.state)
    }

    @Test fun profileStateIsIsolated() = runBlocking {
        seed(profile, "one"); seed(otherProfile, "two")
        SyncCoordinator(db, FakeTransport(pull = PullPage("next", false, emptyList()))).run(binding)
        assertEquals("next", db.syncDao().cursor(profile.value)?.opaqueCursor)
        assertEquals("two", db.syncDao().cursor(otherProfile.value)?.opaqueCursor)
    }

    @Test fun duplicateOriginalConflictLeavesJournalRecoverable() = runBlocking {
        seed(profile, "cursor-a"); val event = pending(1)
        db.journalDao().insert(event)
        SyncCoordinator(db, FakeTransport(acks = listOf(SyncAck(event.eventId, "DUPLICATE", originalOutcome = "CONFLICT", errorCode = "STALE_VERSION", aggregateType = event.aggregateType, aggregateLocalId = event.aggregateLocalId)))).run(binding)
        assertEquals("CONFLICT", db.journalDao().event(event.eventId)?.state)
        assertEquals(1, db.syncDao().observeOpenConflictCount(profile.value).first())
    }

    @Test fun backlogLeasesOnlyFirstHundredContiguousEvents() = runBlocking {
        seed(profile, "cursor-a")
        (1L..101L).forEach { db.journalDao().insert(pending(it)) }
        val transport = FakeTransport()
        SyncCoordinator(db, transport).run(binding)
        assertEquals(100, transport.sent.size)
        assertEquals(101L, db.journalDao().event(pending(101).eventId)?.deviceSequence)
        assertEquals("PENDING", db.journalDao().event(pending(101).eventId)?.state)
    }

    @Test fun sessionRequiredReleasesJournalWithoutConsumingRetryBudget() = runBlocking {
        seed(profile, "cursor-a")
        val event = pending(1)
        db.journalDao().insert(event)

        runCatching { SyncCoordinator(db, FakeTransport(throwSessionRequired = true)).run(binding) }

        val preserved = db.journalDao().event(event.eventId)!!
        assertEquals("PENDING", preserved.state)
        assertEquals(0, preserved.attemptCount)
        assertEquals("SESSION_REQUIRED", preserved.lastErrorCode)
    }

    @Test fun dirtyRemoteDeleteCreatesConflictWithoutOverwrite() = runBlocking {
        seed(profile, "cursor-a")
        val local = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"; val server = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        db.playlistDao().upsertPlaylist(PlaylistEntity(local, server, "Local name", null, "PRIVATE", "MANUAL", null, null, "DIRTY", 1, 1, 1, 1, null, profile.value))
        val event = RemoteEvent("cccccccc-cccc-4ccc-8ccc-cccccccccccc", 1, "AGGREGATE_DELETED", 1, "{}", "PLAYLIST", server, 2, "DELETE", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        SyncCoordinator(db, FakeTransport(pull = PullPage("next", false, listOf(event)))).run(binding)
        assertEquals("Local name", db.playlistDao().playlist(local)?.name)
        assertEquals("DIRTY", db.playlistDao().playlist(local)?.syncState)
        assertEquals(1, db.syncDao().observeOpenConflictCount(profile.value).first())
    }

    @Test fun reorderedPageRollsBackCursor() = runBlocking {
        seed(profile, "cursor-a")
        val events = listOf(RemoteEvent("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", 2, "FUTURE", 99, "{}"), RemoteEvent("ffffffff-ffff-4fff-8fff-ffffffffffff", 1, "FUTURE", 99, "{}"))
        runCatching { SyncCoordinator(db, FakeTransport(pull = PullPage("next", false, events))).run(binding) }
        assertEquals("cursor-a", db.syncDao().cursor(profile.value)?.opaqueCursor)
    }

    @Test fun bootstrapCutsOverOnlyOnFinalPageAndKeepsPendingJournal() = runBlocking {
        seed(profile, "old-cursor")
        val pending = pending(1); db.journalDao().insert(pending)
        db.syncDao().upsertCursor(db.syncDao().cursor(profile.value)!!.copy(bootstrapState = "NOT_STARTED"))
        val server = "abababab-abab-4bab-8bab-abababababab"
        val aggregate = RemoteEvent(server, 0, "USER_TRACK_REF_CREATED", 1, "{\"title\":\"Remote\"}", "USER_TRACK_REF", server, 1)
        val delete = RemoteEvent("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd", 0, "AGGREGATE_DELETED", 1, "{}", "USER_TRACK_REF", server, 2, "DELETE", "dededede-dede-4ede-8ede-dededededede", 9_999)
        val fake = FakeTransport(bootstrapPages = listOf(BootstrapPage("efefefef-efef-4fef-8fef-efefefefefef", "page-two", null, true, listOf(aggregate)), BootstrapPage("efefefef-efef-4fef-8fef-efefefefefef", null, "final-cursor", false, listOf(delete))))
        val coordinator = SyncCoordinator(db, fake)
        coordinator.run(binding)
        // The coordinator deliberately drains bounded pages in one worker run; the cursor is
        // committed only after the second/final bootstrap page has applied atomically.
        assertEquals("final-cursor", db.syncDao().cursor(profile.value)?.opaqueCursor)
        assertEquals("PENDING", db.journalDao().event(pending.eventId)?.state)
        assertFalse(db.syncDao().isServerEventKnown(profile.value, server))
    }

    @Test fun sameServerAggregateIdMaterializesSeparatelyPerProfile() = runBlocking {
        seed(profile, "one"); seed(otherProfile, "two")
        val server = "abababab-abab-4bab-8bab-abababababab"
        val event = RemoteEvent("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd", 1, "USER_TRACK_REF_CREATED", 1, "{\"title\":\"Remote\"}", "USER_TRACK_REF", server, 1)
        SyncCoordinator(db, FakeTransport(pull = PullPage("one-next", false, listOf(event)))).run(binding)
        val secondBinding = binding.copy(serverProfileId = otherProfile)
        SyncCoordinator(db, FakeTransport(pull = PullPage("two-next", false, listOf(event.copy(eventId = "dededede-dede-4ede-8ede-dededededede"))))).run(secondBinding)
        val first = db.libraryDao().trackRefByServerId(profile.value, server)
        val second = db.libraryDao().trackRefByServerId(otherProfile.value, server)
        assertNotNull(first); assertNotNull(second)
        assertFalse(first!!.localUserTrackRefId == second!!.localUserTrackRefId)
    }

    @Test fun malformedAppliedAckIsRetriedBeforeAnyProfileOrVersionMutation() = runBlocking {
        seed(profile, "cursor-a")
        val local = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        db.playlistDao().upsertPlaylist(PlaylistEntity(local, null, "Local", null, "PRIVATE", "MANUAL", null, null, "DIRTY", null, 1, 1, 1, null, "legacy-unscoped"))
        val event = pending(1).copy(aggregateType = "PLAYLIST", aggregateLocalId = local)
        db.journalDao().insert(event)
        val malformed = SyncAck(event.eventId, "APPLIED", aggregateType = "PLAYLIST", aggregateLocalId = "wrong", aggregateServerId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", serverRowVersion = 2)
        SyncCoordinator(db, FakeTransport(acks = listOf(malformed))).run(binding)
        assertEquals("PENDING", db.journalDao().event(event.eventId)?.state)
        assertEquals("legacy-unscoped", db.playlistDao().playlist(local)?.serverProfileId)
        assertEquals(null, db.playlistDao().playlist(local)?.serverRowVersion)
    }

    @Test fun bootstrapDeleteWithoutLiveParentsPersistsTombstonesAndRecordingRedirect() = runBlocking {
        seed(profile, "cursor-a")
        db.syncDao().upsertCursor(db.syncDao().cursor(profile.value)!!.copy(bootstrapState = "NOT_STARTED"))
        val library = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        val playlistEntry = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        val alias = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        val canonical = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        val events = listOf(
            RemoteEvent("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", 0, "AGGREGATE_DELETED", 1, "{}", "LIBRARY_ENTRY", library, 1, "DELETE", "f1111111-1111-4111-8111-111111111111", 8_000),
            RemoteEvent("f2222222-2222-4222-8222-222222222222", 0, "AGGREGATE_DELETED", 1, "{}", "PLAYLIST_ENTRY", playlistEntry, 1, "DELETE", "f3333333-3333-4333-8333-333333333333", 8_000),
            RemoteEvent("f4444444-4444-4444-8444-444444444444", 0, "AGGREGATE_REDIRECT", 1, "{}", "RECORDING", alias, null, "REDIRECT", redirectServerId = canonical),
        )
        SyncCoordinator(db, FakeTransport(bootstrapPages = listOf(BootstrapPage("f5555555-5555-4555-8555-555555555555", null, "final", false, events)))).run(binding)
        assertNotNull(db.syncDao().tombstoneByServerId(profile.value, "LIBRARY_ENTRY", library))
        assertNotNull(db.syncDao().tombstoneByServerId(profile.value, "PLAYLIST_ENTRY", playlistEntry))
        assertEquals(canonical, db.syncDao().redirectByServerId(profile.value, "RECORDING", alias)?.canonicalServerId)
    }

    @Test fun ftsSearchIsProfileScopedAndStandaloneIsExplicitlyLegacyOnly() = runBlocking {
        val first = UserTrackRefEntity("track-one", "server-one", null, null, "UNRESOLVED", "Needle first", null, null, null, null, "CLEAN", null, 0, 1, 1, null, profile.value)
        val second = UserTrackRefEntity("track-two", "server-two", null, null, "UNRESOLVED", "Needle second", null, null, null, null, "CLEAN", null, 0, 1, 1, null, otherProfile.value)
        val legacy = UserTrackRefEntity("track-legacy", null, null, null, "UNRESOLVED", "Needle local", null, null, null, null, "LOCAL_ONLY", null, 0, 1, 1, null, "legacy-unscoped")
        db.libraryDao().upsertTrackRefs(listOf(first, second, legacy))
        db.searchDao().insertContents(listOf(TrackSearchContentEntity(0, first.localUserTrackRefId, first.rawTitle, null, null, null, null), TrackSearchContentEntity(0, second.localUserTrackRefId, second.rawTitle, null, null, null, null), TrackSearchContentEntity(0, legacy.localUserTrackRefId, legacy.rawTitle, null, null, null, null)))
        val search = LocalTrackSearchRepository(db)
        assertEquals(listOf("track-one"), search.search("needle", profile.value).map { it.localUserTrackRefId })
        assertEquals(listOf("track-two"), search.search("needle", otherProfile.value).map { it.localUserTrackRefId })
        assertEquals(listOf("track-legacy"), search.search("needle").map { it.localUserTrackRefId })
    }

    @Test fun offlineRoomJournalSurvivesProcessDeathAndProjectsExactlyOnceToSecondDevice() = runBlocking {
        val firstDatabaseName = "p14-release-e2e-first.db"
        val secondDatabaseName = "p14-release-e2e-second.db"
        context.deleteDatabase(firstDatabaseName)
        context.deleteDatabase(secondDatabaseName)
        val arguments = InstrumentationRegistry.getArguments()
        val realBaseUrl = arguments.getString("p14BaseUrl")
        val firstProfile = ServerProfileId("10000000-0000-4000-8000-000000000001")
        val secondProfile = ServerProfileId("10000000-0000-4000-8000-000000000002")
        val user = UserId(arguments.getString("p14UserId") ?: "10000000-0000-4000-8000-000000000003")
        val firstBinding = ClientEventBinding(
            user,
            DeviceId(arguments.getString("p14FirstDeviceId") ?: "10000000-0000-4000-8000-000000000004"),
            firstProfile,
            LocalId("10000000-0000-4000-8000-000000000005"),
        )
        val secondBinding = ClientEventBinding(
            user,
            DeviceId(arguments.getString("p14SecondDeviceId") ?: "10000000-0000-4000-8000-000000000006"),
            secondProfile,
            LocalId("10000000-0000-4000-8000-000000000007"),
        )
        val scenario = if (realBaseUrl == null) {
            val relay = CrashAfterCommitRelay()
            SyncScenario(relay, relay, { relay.serverAggregateId }, { relay.acceptedEventCount })
        } else {
            val firstToken = requireNotNull(arguments.getString("p14FirstToken"))
            val secondToken = requireNotNull(arguments.getString("p14SecondToken"))
            bindDevice(realBaseUrl, firstToken, firstBinding)
            bindDevice(realBaseUrl, secondToken, secondBinding)
            val first = AckLosingTransport(
                OkHttpSyncTransport(realBaseUrl, StaticCredentialStore(firstToken)),
            )
            SyncScenario(
                first,
                OkHttpSyncTransport(realBaseUrl, StaticCredentialStore(secondToken)),
                { requireNotNull(first.serverAggregateId) },
                { null },
            )
        }
        val trackId = LocalId("10000000-0000-4000-8000-000000000008")
        val entryId = LocalId("10000000-0000-4000-8000-000000000009")
        val eventId = LocalId("10000000-0000-4000-8000-000000000010")
        var firstDatabase: AutPlayDatabase? = null
        var secondDatabase: AutPlayDatabase? = null
        var clock = 100L

        try {
            firstDatabase = AutPlayDatabase.open(context, firstDatabaseName)
            val committed = LocalLibraryCommandRepository(firstDatabase).add(
                AddLocalTrackCommand(
                    binding = firstBinding,
                    trackRefId = trackId,
                    libraryEntryId = entryId,
                    localChangeId = eventId,
                    title = "Offline release track",
                    artist = "AutPlay",
                    occurredAtMs = clock,
                ),
            ) as AddLocalTrackResult.Journaled
            assertEquals(1L, committed.deviceSequence)
            assertEquals("PENDING", firstDatabase.journalDao().event(eventId.value)?.state)

            // A real file-backed Room close/reopen models the client process dying after the
            // local transaction and before any network work starts.
            firstDatabase.close()
            firstDatabase = AutPlayDatabase.open(context, firstDatabaseName)
            runCatching {
                SyncCoordinator(firstDatabase, scenario.firstTransport, nowMs = { clock }).run(firstBinding)
            }.onSuccess { error("FIRST_PUSH_MUST_LOSE_ACK") }
                .onFailure { assertEquals("SIMULATED_ACK_LOSS", it.message) }
            scenario.acceptedEventCount()?.let { assertEquals(1, it) }
            assertEquals("PENDING", firstDatabase.journalDao().event(eventId.value)?.state)

            // The server committed the event, but the client died before receiving its ACK.
            // Reopening Room and retrying later must reuse the immutable ID/hash and consume the
            // relay's duplicate-APPLIED outcome without creating a second server event.
            firstDatabase.close()
            firstDatabase = AutPlayDatabase.open(context, firstDatabaseName)
            clock = 10_000L
            assertEquals(true, SyncCoordinator(firstDatabase, scenario.firstTransport, nowMs = { clock }).run(firstBinding))
            scenario.acceptedEventCount()?.let { assertEquals(1, it) }
            assertEquals("ACKED", firstDatabase.journalDao().event(eventId.value)?.state)
            val firstProjection = requireNotNull(firstDatabase.libraryDao().trackRef(trackId.value))
            assertEquals(scenario.serverAggregateId(), firstProjection.serverUserTrackRefId)
            assertEquals("CLEAN", firstProjection.syncState)

            secondDatabase = AutPlayDatabase.open(context, secondDatabaseName)
            seedSecondDevice(secondDatabase, secondBinding, clock)
            assertEquals(true, SyncCoordinator(secondDatabase, scenario.secondTransport, nowMs = { clock }).run(secondBinding))
            val secondProjection = requireNotNull(
                secondDatabase.libraryDao().trackRefByServerId(
                    secondProfile.value,
                    scenario.serverAggregateId(),
                ),
            )
            assertEquals("Offline release track", secondProjection.rawTitle)
            assertEquals("AutPlay", secondProjection.rawArtist)
            assertEquals("CLEAN", secondProjection.syncState)

            // A second pull is idempotent: no duplicate projection or server event is created.
            assertEquals(true, SyncCoordinator(secondDatabase, scenario.secondTransport, nowMs = { clock + 1 }).run(secondBinding))
            scenario.acceptedEventCount()?.let { assertEquals(1, it) }
            assertEquals(1, secondDatabase.libraryDao().trackRefCount())
        } finally {
            firstDatabase?.close()
            secondDatabase?.close()
            context.deleteDatabase(firstDatabaseName)
            context.deleteDatabase(secondDatabaseName)
        }
    }

    private suspend fun seed(id: ServerProfileId, cursor: String) {
        val lineage = JournalLineageEntity("77777777-7777-4777-8777-777777777777", binding.userId.value, binding.deviceId.value, binding.journalEpoch!!.value, 1, 1)
        if (db.journalDao().lineageById(lineage.lineageId) == null) db.journalDao().insertLineage(lineage)
        db.syncDao().upsertCursor(SyncCursorEntity(id.value, lineage.lineageId, lineage.deviceId, lineage.journalEpoch, cursor, 0, 0, null, "READY", null, 1))
    }

    private fun pending(sequence: Long) = OfflineJournalEventEntity("${sequence.toString().padStart(8, '0')}-0000-4000-8000-000000000000", "77777777-7777-4777-8777-777777777777", "p$sequence", binding.userId.value, binding.deviceId.value, profile.value, sequence, "USER_TRACK_REF_CREATED", 1, "USER_TRACK_REF", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", null, null, "{}", byteArrayOf(1), 1, "PENDING", 0, null, null, null, null, null)

    private suspend fun seedSecondDevice(
        target: AutPlayDatabase,
        targetBinding: ClientEventBinding,
        now: Long,
    ) {
        val lineageId = "10000000-0000-4000-8000-000000000011"
        target.journalDao().insertLineage(
            JournalLineageEntity(
                lineageId,
                targetBinding.userId.value,
                targetBinding.deviceId.value,
                requireNotNull(targetBinding.journalEpoch).value,
                1,
                now,
            ),
        )
        target.syncDao().upsertCursor(
            SyncCursorEntity(
                targetBinding.serverProfileId.value,
                lineageId,
                targetBinding.deviceId.value,
                targetBinding.journalEpoch.value,
                null,
                0,
                0,
                null,
                "NOT_STARTED",
                null,
                now,
            ),
        )
    }

    private suspend fun bindDevice(
        baseUrl: String,
        token: String,
        targetBinding: ClientEventBinding,
    ) = withContext(Dispatchers.IO) {
        val body = """{"protocol_version":1,"device_id":"${targetBinding.deviceId.value}","server_profile_id":"${targetBinding.serverProfileId.value}","journal_epoch":"${requireNotNull(targetBinding.journalEpoch).value}","user_id":"${targetBinding.userId.value}","device_name":"P14 Android E2E","platform":"ANDROID","app_version":"rc1"}"""
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + "/devices/bind")
            .header("Authorization", "Bearer $token")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        OkHttpClient().newCall(request).execute().use { response ->
            check(response.isSuccessful) { "DEVICE_BIND_HTTP_${response.code}" }
        }
    }

    private data class SyncScenario(
        val firstTransport: SyncTransport,
        val secondTransport: SyncTransport,
        val serverAggregateId: () -> String,
        val acceptedEventCount: () -> Int?,
    )

    private class StaticCredentialStore(token: String) : CredentialStore {
        private val tokenBytes = token.toByteArray()
        override suspend fun read(profileId: ServerProfileId): ByteArray = tokenBytes.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }

    private class AckLosingTransport(private val delegate: SyncTransport) : SyncTransport {
        private var loseFirstAck = true
        var serverAggregateId: String? = null
            private set

        override suspend fun push(
            binding: ClientEventBinding,
            events: List<OfflineJournalEventEntity>,
        ): List<SyncAck> {
            val acknowledgements = delegate.push(binding, events)
            acknowledgements.firstOrNull()?.aggregateServerId?.let { serverAggregateId = it }
            if (loseFirstAck) {
                loseFirstAck = false
                throw IOException("SIMULATED_ACK_LOSS")
            }
            return acknowledgements
        }

        override suspend fun pull(binding: ClientEventBinding, cursor: String?): PullPage =
            delegate.pull(binding, cursor)

        override suspend fun bootstrap(
            binding: ClientEventBinding,
            snapshotId: String?,
            pageToken: String?,
            pendingCount: Int,
        ): BootstrapPage = delegate.bootstrap(binding, snapshotId, pageToken, pendingCount)
    }

    private class CrashAfterCommitRelay : SyncTransport {
        val serverAggregateId = "20000000-0000-4000-8000-000000000001"
        private val accepted = linkedMapOf<String, AcceptedEvent>()
        private var loseFirstAck = true
        val acceptedEventCount: Int get() = accepted.size

        override suspend fun push(
            binding: ClientEventBinding,
            events: List<OfflineJournalEventEntity>,
        ): List<SyncAck> {
            val acknowledgements = events.map { event ->
                val existing = accepted[event.eventId]
                val acceptedEvent = existing ?: AcceptedEvent(
                    requestHash = event.requestHash.copyOf(),
                    remote = RemoteEvent(
                        eventId = UUID.nameUUIDFromBytes("server:${event.eventId}".toByteArray()).toString(),
                        sequence = accepted.size.toLong() + 1,
                        eventType = event.eventType,
                        schemaVersion = event.schemaVersion,
                        payloadJson = event.payloadJson,
                        aggregateType = event.aggregateType,
                        aggregateServerId = serverAggregateId,
                        serverRowVersion = 1,
                    ),
                ).also { accepted[event.eventId] = it }
                check(acceptedEvent.requestHash.contentEquals(event.requestHash)) {
                    "IDEMPOTENCY_HASH_MISMATCH"
                }
                SyncAck(
                    eventId = event.eventId,
                    outcome = if (existing == null) "APPLIED" else "DUPLICATE",
                    originalOutcome = if (existing == null) null else "APPLIED",
                    aggregateType = event.aggregateType,
                    aggregateLocalId = event.aggregateLocalId,
                    aggregateServerId = serverAggregateId,
                    serverRowVersion = 1,
                )
            }
            if (loseFirstAck) {
                loseFirstAck = false
                throw IOException("SIMULATED_ACK_LOSS")
            }
            return acknowledgements
        }

        override suspend fun pull(binding: ClientEventBinding, cursor: String?): PullPage =
            PullPage("cursor-${accepted.size}", false, eventsAfter(cursor))

        override suspend fun bootstrap(
            binding: ClientEventBinding,
            snapshotId: String?,
            pageToken: String?,
            pendingCount: Int,
        ): BootstrapPage = BootstrapPage(
            snapshotId = "30000000-0000-4000-8000-000000000001",
            nextPageToken = null,
            snapshotCursor = "cursor-${accepted.size}",
            hasMore = false,
            events = accepted.values.map { it.remote },
        )

        private fun eventsAfter(cursor: String?): List<RemoteEvent> {
            val sequence = cursor?.substringAfter("cursor-", "0")?.toLongOrNull() ?: 0
            return accepted.values.map { it.remote }.filter { it.sequence > sequence }
        }

        private data class AcceptedEvent(
            val requestHash: ByteArray,
            val remote: RemoteEvent,
        )
    }

    private class FakeTransport(
        private val pull: PullPage = PullPage("next", false, emptyList()),
        private val throwInvalidCursor: Boolean = false,
        private val acks: List<SyncAck> = emptyList(),
        private val bootstrapPages: List<BootstrapPage> = emptyList(),
        private val throwSessionRequired: Boolean = false,
    ) : SyncTransport {
        val sent = mutableListOf<app.autplay.data.local.entity.OfflineJournalEventEntity>()
        override suspend fun push(binding: ClientEventBinding, events: List<app.autplay.data.local.entity.OfflineJournalEventEntity>): List<SyncAck> {
            sent += events
            if (throwSessionRequired) throw SessionRequiredException()
            return acks
        }
        override suspend fun pull(binding: ClientEventBinding, cursor: String?): PullPage { if (throwInvalidCursor) throw InvalidCursorException(); return pull }
        private var bootstrapIndex = 0
        override suspend fun bootstrap(binding: ClientEventBinding, snapshotId: String?, pageToken: String?, pendingCount: Int): BootstrapPage = bootstrapPages.getOrElse(bootstrapIndex++) { BootstrapPage("88888888-8888-4888-8888-888888888888", null, "next", false, emptyList()) }
    }
}
