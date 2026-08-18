package app.autplay.data.local

import androidx.room3.useWriterConnection
import androidx.room3.withWriteTransaction
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.library.AddLocalTrackCommand
import app.autplay.application.library.AddLocalTrackResult
import app.autplay.application.library.LocalCommandFailureInjector
import app.autplay.application.library.LocalLibraryCommandRepository
import app.autplay.application.library.MaterializationFailureInjector
import app.autplay.application.library.MaterializeLocalChangeCommand
import app.autplay.application.search.SafeFtsQueryBuilder
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.LocalIntentPayloadErrorCode
import app.autplay.application.sync.LocalIntentPayloadException
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.PlaylistEntryEntity
import app.autplay.data.local.entity.QueueSnapshotEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.LocalId
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AutPlayDatabaseTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val databaseName = "autplay-p05-test.db"
    private lateinit var database: AutPlayDatabase

    @Before
    fun setUp() {
        context.deleteDatabase(databaseName)
        database = AutPlayDatabase.open(context, databaseName)
    }

    @After
    fun tearDown() {
        database.close()
        context.deleteDatabase(databaseName)
    }

    @Test
    fun freshOpenCommandCommitAndRestartPreserveDomainAndJournal() = runBlocking {
        val command = command(1)
        val result = LocalLibraryCommandRepository(database).add(command) as AddLocalTrackResult.Journaled
        val binding = requireNotNull(command.binding)

        assertEquals(1L, result.deviceSequence)
        assertEquals(1, database.journalDao().countLineages())
        assertEquals(0, database.journalDao().outboxCount())
        assertNotNull(database.libraryDao().trackRef(command.trackRefId.value))
        assertNotNull(database.libraryDao().entry(command.libraryEntryId.value))
        val event = requireNotNull(database.journalDao().event(command.localChangeId.value))
        assertEquals("USER_TRACK_REF_CREATED", event.eventType)
        assertEquals(binding.userId.value, event.userId)
        assertEquals(binding.deviceId.value, event.deviceId)
        assertEquals(binding.serverProfileId.value, event.serverProfileId)
        assertEquals(result.journalLineageId.value, event.journalLineageId)
        assertEquals(32, event.requestHash.size)
        database.close()

        database = AutPlayDatabase.open(context, databaseName)
        assertNotNull(database.libraryDao().trackRef(command.trackRefId.value))
        assertNotNull(database.libraryDao().entry(command.libraryEntryId.value))
        assertEquals(1L, database.journalDao().event(command.localChangeId.value)?.deviceSequence)
        Unit
    }

    @Test
    fun failureBetweenDomainWritesAndJournalRollsBackEverything() = runBlocking {
        val repository = LocalLibraryCommandRepository(
            database,
            LocalCommandFailureInjector { throw InjectedFailure() },
        )

        var failure: Throwable? = null
        try {
            repository.add(command(2))
        } catch (error: InjectedFailure) {
            failure = error
        }

        assertNotNull(failure)
        assertEquals(0, database.libraryDao().trackRefCount())
        assertEquals(0, database.libraryDao().entryCount())
        assertEquals(0, database.journalDao().eventCount())
        assertEquals(0, database.journalDao().outboxCount())
        assertEquals(0, database.journalDao().countLineages())
        Unit
    }

    @Test
    fun freshStandaloneCommandCommitsDomainAndOutboxAcrossRestart() = runBlocking {
        val command = command(20, binding = null)
        val result = LocalLibraryCommandRepository(database).add(command) as AddLocalTrackResult.Standalone

        assertEquals(command.localChangeId, result.localChangeId)
        assertEquals(1, database.libraryDao().trackRefCount())
        assertEquals(1, database.libraryDao().entryCount())
        assertEquals(1, database.journalDao().outboxCount())
        assertEquals(0, database.journalDao().eventCount())
        assertEquals(0, database.journalDao().countLineages())
        assertEquals(0L, database.libraryDao().trackRef(command.trackRefId.value)?.lastLocalSequence)
        database.close()

        database = AutPlayDatabase.open(context, databaseName)
        val restored = requireNotNull(database.journalDao().outbox(command.localChangeId.value))
        assertEquals("UNMATERIALIZED", restored.materializationState)
        assertEquals(command.trackRefId.value, restored.aggregateLocalId)
        assertNotNull(database.libraryDao().entry(command.libraryEntryId.value))
        Unit
    }

    @Test
    fun standaloneFailureRollsBackDomainSearchAndOutbox() = runBlocking {
        val repository = LocalLibraryCommandRepository(
            database,
            LocalCommandFailureInjector { throw InjectedFailure() },
        )

        assertFails { repository.add(command(21, binding = null)) }

        assertEquals(0, database.libraryDao().trackRefCount())
        assertEquals(0, database.libraryDao().entryCount())
        assertEquals(0, database.journalDao().outboxCount())
        assertEquals(0, database.journalDao().eventCount())
        Unit
    }

    @Test
    fun materializationIsAtomicIdempotentAndUsesImmutableStoredPayload() = runBlocking {
        val standalone = command(22, binding = null)
        LocalLibraryCommandRepository(database).add(standalone)
        val eventId = LocalId(uuid(229))
        val first = LocalLibraryCommandRepository(database).materializeOutboxToJournal(
            MaterializeLocalChangeCommand(
                localChangeId = standalone.localChangeId,
                eventId = eventId,
                binding = binding(journalEpochSeed = 9204),
                materializedAtMs = 30,
            ),
        )

        assertEquals(eventId, first.eventId)
        assertEquals(1L, first.deviceSequence)
        val outbox = requireNotNull(database.journalDao().outbox(standalone.localChangeId.value))
        val event = requireNotNull(database.journalDao().event(eventId.value))
        assertEquals("MATERIALIZED", outbox.materializationState)
        assertEquals(eventId.value, outbox.materializedEventId)
        assertEquals(outbox.payloadJson, event.payloadJson)
        assertEquals(32, event.requestHash.size)
        assertEquals("DIRTY", database.libraryDao().trackRef(standalone.trackRefId.value)?.syncState)
        assertEquals(1L, database.libraryDao().entry(standalone.libraryEntryId.value)?.lastLocalSequence)

        val retried = LocalLibraryCommandRepository(database).materializeOutboxToJournal(
            MaterializeLocalChangeCommand(
                localChangeId = standalone.localChangeId,
                eventId = LocalId(uuid(230)),
                binding = binding(profileSeed = 9205, journalEpochSeed = 9204),
                materializedAtMs = 31,
            ),
        )
        assertEquals(first, retried)
        assertEquals(1, database.journalDao().eventCount())
        assertEquals(2L, database.journalDao().lineageById(first.journalLineageId.value)?.nextDeviceSequence)
        Unit
    }

    @Test
    fun materializationFailureRollsBackEventCounterDomainAndLinkThenRetryUsesSequenceOne() = runBlocking {
        val standalone = command(23, binding = null)
        LocalLibraryCommandRepository(database).add(standalone)
        val failing = LocalLibraryCommandRepository(
            database = database,
            materializationFailureInjector = MaterializationFailureInjector { throw InjectedFailure() },
        )
        val materialize = MaterializeLocalChangeCommand(
            localChangeId = standalone.localChangeId,
            eventId = LocalId(uuid(239)),
            binding = binding(journalEpochSeed = 9304),
            materializedAtMs = 40,
        )

        assertFails { failing.materializeOutboxToJournal(materialize) }
        assertEquals(0, database.journalDao().eventCount())
        assertEquals(0, database.journalDao().countLineages())
        assertEquals("UNMATERIALIZED", database.journalDao().outbox(standalone.localChangeId.value)?.materializationState)
        assertEquals(0L, database.libraryDao().trackRef(standalone.trackRefId.value)?.lastLocalSequence)

        val result = LocalLibraryCommandRepository(database).materializeOutboxToJournal(materialize)
        assertEquals(1L, result.deviceSequence)
        assertEquals(1, database.journalDao().eventCount())
        Unit
    }

    @Test
    fun recreatedProfilesShareOneDeviceLineageAndMonotonicSequence() = runBlocking {
        val firstBinding = binding(profileSeed = 9403, journalEpochSeed = 9404)
        val secondBinding = binding(profileSeed = 9405, journalEpochSeed = 9404)
        val first = LocalLibraryCommandRepository(database).add(command(24, firstBinding)) as AddLocalTrackResult.Journaled
        val second = LocalLibraryCommandRepository(database).add(command(25, secondBinding)) as AddLocalTrackResult.Journaled

        assertEquals(first.journalLineageId, second.journalLineageId)
        assertEquals(1L, first.deviceSequence)
        assertEquals(2L, second.deviceSequence)
        assertEquals(1, database.journalDao().countLineages())
        assertEquals(firstBinding.serverProfileId.value, database.journalDao().event(first.eventId.value)?.serverProfileId)
        assertEquals(secondBinding.serverProfileId.value, database.journalDao().event(second.eventId.value)?.serverProfileId)
        assertNotEquals(
            database.journalDao().event(first.eventId.value)?.requestHash?.toList(),
            database.journalDao().event(second.eventId.value)?.requestHash?.toList(),
        )
        Unit
    }

    @Test
    fun differentDevicesHaveIndependentSequenceOneAndSameDeviceEpochMismatchFailsClosed() = runBlocking {
        val firstBinding = binding(deviceSeed = 9502, profileSeed = 9503, journalEpochSeed = 9504)
        val secondBinding = binding(deviceSeed = 9512, profileSeed = 9513, journalEpochSeed = 9514)
        val first = LocalLibraryCommandRepository(database).add(command(26, firstBinding)) as AddLocalTrackResult.Journaled
        val second = LocalLibraryCommandRepository(database).add(command(27, secondBinding)) as AddLocalTrackResult.Journaled

        assertEquals(1L, first.deviceSequence)
        assertEquals(1L, second.deviceSequence)
        assertNotEquals(first.journalLineageId, second.journalLineageId)
        assertEquals(2, database.journalDao().countLineages())

        assertFails {
            LocalLibraryCommandRepository(database).add(
                command(28, firstBinding.copy(serverProfileId = secondBinding.serverProfileId, journalEpoch = LocalId(uuid(9599)))),
            )
        }
        assertEquals(2, database.journalDao().eventCount())
        assertEquals(2, database.journalDao().countLineages())
        Unit
    }

    @Test
    fun compositeForeignKeysRejectEventAndCursorBindingMismatch() = runBlocking {
        val lineage = journalLineage(50)
        database.journalDao().insertLineage(lineage)

        assertFails {
            database.journalDao().insert(
                journalEvent(
                    seed = 50,
                    lineageId = lineage.lineageId,
                    state = "PENDING",
                    leaseExpiresAtMs = null,
                ).copy(deviceId = uuid(5050)),
            )
        }
        assertFails {
            database.syncDao().upsertCursor(
                SyncCursorEntity(
                    serverProfileId = uuid(5003),
                    journalLineageId = lineage.lineageId,
                    deviceId = lineage.deviceId,
                    journalEpoch = uuid(5051),
                    opaqueCursor = null,
                    lastPulledServerSequence = 0,
                    lastAckedDeviceSequence = 0,
                    bootstrapSnapshotId = null,
                    bootstrapState = "NOT_STARTED",
                    lastSyncAtMs = null,
                    updatedAtMs = 50,
                ),
            )
        }
        assertEquals(0, database.journalDao().eventCount())
        assertNull(database.syncDao().cursor(uuid(5003)))
        Unit
    }

    @Test
    fun unsafeAndUnknownOutboxPayloadsRemainPreservedAndCannotMaterialize() = runBlocking {
        val unsafe = outboxFixture(
            seed = 29,
            payload = "{\"artist\":{\"raw_audio\":\"x\"},\"library_entry_local_id\":\"id\",\"title\":\"t\"}",
        )
        val unknown = outboxFixture(seed = 30, eventType = "FUTURE_EVENT", payload = "{\"future\":true}")
        database.journalDao().insertOutbox(unsafe)
        database.journalDao().insertOutbox(unknown)

        assertPayloadFails(LocalIntentPayloadErrorCode.UNSAFE_PROPERTY_NAME) {
            LocalLibraryCommandRepository(database).materializeOutboxToJournal(
                materializeCommand(unsafe.localChangeId, 299, 9604),
            )
        }
        assertPayloadFails(LocalIntentPayloadErrorCode.UNSUPPORTED_EVENT_TYPE) {
            LocalLibraryCommandRepository(database).materializeOutboxToJournal(
                materializeCommand(unknown.localChangeId, 309, 9614),
            )
        }
        assertEquals(unsafe.payloadJson, database.journalDao().outbox(unsafe.localChangeId)?.payloadJson)
        assertEquals(unknown.payloadJson, database.journalDao().outbox(unknown.localChangeId)?.payloadJson)
        assertEquals(0, database.journalDao().eventCount())
        assertEquals(0, database.journalDao().countLineages())
        Unit
    }

    @Test
    fun ftsExternalContentTracksInsertUpdateDeleteRebuildAndHostileInput() = runBlocking {
        val builder = SafeFtsQueryBuilder()
        val row = TrackSearchContentEntity(
            rowId = 7,
            localUserTrackRefId = uuid(7),
            title = "Музыка offline",
            artist = "AutPlay",
            album = "Foundation",
            aliases = null,
            transliterations = null,
        )
        database.searchDao().insertContent(row)
        assertEquals(listOf(row.localUserTrackRefId), database.searchDao().search(requireNotNull(builder.build("музыка")), 10))

        assertEquals(1, database.searchDao().updateContent(row.copy(title = "Restart proof")))
        assertTrue(database.searchDao().search(requireNotNull(builder.build("музыка")), 10).isEmpty())
        assertEquals(listOf(row.localUserTrackRefId), database.searchDao().search(requireNotNull(builder.build("restart")), 10))
        assertTrue(database.searchDao().search(requireNotNull(builder.build("title:restart OR *")), 10).isEmpty())

        database.useWriterConnection { connection ->
            connection.usePrepared("INSERT INTO track_search_fts(track_search_fts) VALUES('rebuild')") { statement ->
                statement.step()
            }
        }
        assertEquals(listOf(row.localUserTrackRefId), database.searchDao().search(requireNotNull(builder.build("restart")), 10))
        database.searchDao().deleteContent(row.copy(title = "Restart proof"))
        assertTrue(database.searchDao().search(requireNotNull(builder.build("restart")), 10).isEmpty())
        Unit
    }

    @Test
    fun ftsRowIdsAreAllocatedWithoutUuidDerivedCollisions() = runBlocking {
        val first = TrackSearchContentEntity(
            localUserTrackRefId = uuid(801),
            title = "First collision proof",
            artist = null,
            album = null,
            aliases = null,
            transliterations = null,
        )
        val second = first.copy(localUserTrackRefId = uuid(802), title = "Second collision proof")

        val firstRowId = database.searchDao().insertContent(first)
        val secondRowId = database.searchDao().insertContent(second)

        assertTrue(firstRowId != secondRowId)
        assertEquals(listOf(first.localUserTrackRefId), database.searchDao().search("\"first\"", 10))
        assertEquals(listOf(second.localUserTrackRefId), database.searchDao().search("\"second\"", 10))
        Unit
    }

    @Test
    fun duplicatePlaylistTrackIsAllowedButActiveOrderIsUnique() = runBlocking {
        val track = trackRef(10)
        database.libraryDao().upsertTrackRef(track)
        val playlist = playlist(10)
        database.playlistDao().insertPlaylist(playlist)
        database.playlistDao().insertEntry(playlistEntry(10, playlist, track, "a"))
        database.playlistDao().insertEntry(playlistEntry(11, playlist, track, "b"))
        assertEquals(2, database.playlistDao().activeEntryCount(playlist.localPlaylistId))

        assertFails {
            database.playlistDao().insertEntry(playlistEntry(12, playlist, track, "a"))
        }
        assertEquals(2, database.playlistDao().activeEntryCount(playlist.localPlaylistId))
        Unit
    }

    @Test
    fun onlyOneQueueSnapshotCanOwnTheActiveSlot() = runBlocking {
        database.queueDao().insertSnapshot(queueSnapshot(20, true))
        assertFails { database.queueDao().insertSnapshot(queueSnapshot(21, true)) }
        Unit
    }

    @Test
    fun unknownStringAndMissingUriStateRoundTripWithoutDeletion() = runBlocking {
        val track = trackRef(30).copy(resolutionStatus = "FUTURE_SERVER_STATE")
        database.libraryDao().upsertTrackRef(track)
        val audio = LocalAudioStateEntity(
            localAudioStateId = uuid(31),
            localUserTrackRefId = track.localUserTrackRefId,
            localRecordingId = null,
            serverAudioVariantId = null,
            contentUri = "content://app.autplay.test/missing/31",
            persistedUriPermission = false,
            localSha256 = null,
            fingerprintAlgorithm = null,
            fingerprintVersion = null,
            fingerprintPayload = null,
            codec = null,
            container = null,
            bitrateBps = null,
            sampleRateHz = null,
            channels = null,
            durationMs = null,
            status = "MISSING",
            storageClass = "USER_DOWNLOAD",
            byteSize = null,
            lastAccessedAtMs = null,
            lastVerifiedAtMs = null,
            createdAtMs = 31,
            updatedAtMs = 31,
        )
        database.localAudioDao().upsertState(audio)

        assertEquals("FUTURE_SERVER_STATE", database.libraryDao().trackRef(track.localUserTrackRefId)?.resolutionStatus)
        assertEquals(audio, database.localAudioDao().state(audio.localAudioStateId))
        assertFails { audio.copy(contentUri = "file:///private/track.mp3") }
        Unit
    }

    @Test
    fun expiredJournalLeaseRecoversWithoutChangingIdentityOrHash() = runBlocking {
        val lineage = journalLineage(40)
        database.journalDao().insertLineage(lineage)
        val event = journalEvent(40, lineage.lineageId, state = "SENDING", leaseExpiresAtMs = 50)
        database.journalDao().insert(event)

        assertEquals(1, database.journalDao().recoverExpiredLeases(lineage.lineageId, 51))
        val recovered = requireNotNull(database.journalDao().event(event.eventId))
        assertEquals("PENDING", recovered.state)
        assertNull(recovered.leaseToken)
        assertNull(recovered.leaseExpiresAtMs)
        assertEquals(event.eventId, recovered.eventId)
        assertArrayEquals(event.requestHash, recovered.requestHash)
        Unit
    }

    @Test
    fun tenThousandRowLibraryFixtureUsesBoundedDaoReads() = runBlocking {
        val tracks = (1..10_000).map(::trackRef)
        val entries = tracks.mapIndexed { index, track -> libraryEntry(index + 1, track) }
        database.withWriteTransaction {
            database.libraryDao().upsertTrackRefs(tracks)
            database.libraryDao().upsertEntries(entries)
        }

        assertEquals(10_000, database.libraryDao().trackRefCount())
        assertEquals(10_000, database.libraryDao().entryCount())
        Unit
    }

    @Test
    fun bundledDriverSupportsWalForeignKeysAndFts5OnMinimumSdk() {
        val path = context.getDatabasePath("bundled-driver-p05.db")
        BundledSQLiteDriver().open(path.absolutePath).use { connection ->
            connection.prepare("PRAGMA journal_mode = WAL").use { statement ->
                assertTrue(statement.step())
                assertEquals("wal", statement.getText(0).lowercase())
            }
            connection.prepare("PRAGMA foreign_keys = ON").use { statement -> statement.step() }
            connection.prepare("PRAGMA foreign_keys").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
            connection.prepare("CREATE VIRTUAL TABLE p05_fts USING FTS5(text)").use { it.step() }
            connection.prepare("INSERT INTO p05_fts(text) VALUES ('offline музыка')").use { it.step() }
            connection.prepare("SELECT count(*) FROM p05_fts WHERE p05_fts MATCH 'музыка'").use { statement ->
                assertTrue(statement.step())
                assertEquals(1L, statement.getLong(0))
            }
        }
        assertTrue(path.delete())
    }

    private fun command(
        seed: Int,
        binding: ClientEventBinding? = binding(),
    ) = AddLocalTrackCommand(
        binding = binding,
        trackRefId = LocalId(uuid(seed * 10 + 1)),
        libraryEntryId = LocalId(uuid(seed * 10 + 2)),
        localChangeId = LocalId(uuid(seed * 10 + 3)),
        title = "Offline title $seed",
        artist = "AutPlay artist $seed",
        occurredAtMs = seed.toLong(),
    )

    private fun trackRef(seed: Int) = UserTrackRefEntity(
        localUserTrackRefId = uuid(seed * 10 + 1),
        serverUserTrackRefId = null,
        localRecordingId = null,
        serverRecordingId = null,
        resolutionStatus = "UNRESOLVED",
        rawTitle = "Track $seed",
        rawArtist = "Artist $seed",
        rawAlbum = null,
        rawDurationMs = null,
        resolutionConfidence = null,
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = seed.toLong(),
        createdAtMs = seed.toLong(),
        updatedAtMs = seed.toLong(),
        deletedAtMs = null,
    )

    private fun libraryEntry(seed: Int, track: UserTrackRefEntity) = LibraryEntryEntity(
        localLibraryEntryId = uuid(seed * 10 + 2),
        serverLibraryEntryId = null,
        localUserTrackRefId = track.localUserTrackRefId,
        addedAtMs = seed.toLong(),
        source = "LOCAL",
        availabilityStatus = "METADATA_ONLY",
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = seed.toLong(),
        removedAtMs = null,
        updatedAtMs = seed.toLong(),
    )

    private fun playlist(seed: Int) = PlaylistEntity(
        localPlaylistId = uuid(seed * 10 + 4),
        serverPlaylistId = null,
        name = "Playlist $seed",
        description = null,
        visibility = "PRIVATE",
        playlistType = "MANUAL",
        smartRuleVersion = null,
        smartRuleJson = null,
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = seed.toLong(),
        createdAtMs = seed.toLong(),
        updatedAtMs = seed.toLong(),
        deletedAtMs = null,
    )

    private fun playlistEntry(
        seed: Int,
        playlist: PlaylistEntity,
        track: UserTrackRefEntity,
        position: String,
    ) = PlaylistEntryEntity(
        localPlaylistEntryId = uuid(seed * 10 + 5),
        serverPlaylistEntryId = null,
        localPlaylistId = playlist.localPlaylistId,
        localUserTrackRefId = track.localUserTrackRefId,
        positionKey = position,
        activePositionKey = position,
        sourcePosition = null,
        addedAtMs = seed.toLong(),
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = seed.toLong(),
        removedAtMs = null,
    )

    private fun queueSnapshot(seed: Int, active: Boolean) = QueueSnapshotEntity(
        queueSnapshotId = uuid(seed * 10 + 6),
        queueType = "USER",
        sourceContextId = null,
        currentEntryId = null,
        currentPositionMs = 0,
        shuffleMode = "OFF",
        repeatMode = "OFF",
        seed = null,
        generationVersion = null,
        isActive = active,
        activeSlot = if (active) "ACTIVE" else null,
        createdAtMs = seed.toLong(),
        updatedAtMs = seed.toLong(),
    )

    private fun journalLineage(seed: Int) = JournalLineageEntity(
        lineageId = uuid(seed * 10 + 8),
        userId = binding().userId.value,
        deviceId = binding().deviceId.value,
        journalEpoch = uuid(seed * 10 + 9),
        nextDeviceSequence = seed.toLong() + 1,
        createdAtMs = seed.toLong(),
    )

    private fun journalEvent(
        seed: Int,
        lineageId: String,
        state: String,
        leaseExpiresAtMs: Long?,
    ) = OfflineJournalEventEntity(
        eventId = uuid(seed * 10 + 7),
        journalLineageId = lineageId,
        idempotencyKey = uuid(seed * 10 + 7),
        userId = binding().userId.value,
        deviceId = binding().deviceId.value,
        serverProfileId = binding().serverProfileId.value,
        deviceSequence = seed.toLong(),
        eventType = "USER_TRACK_REF_CREATED",
        schemaVersion = 1,
        aggregateType = "USER_TRACK_REF",
        aggregateLocalId = uuid(seed * 10 + 1),
        aggregateServerId = null,
        baseServerRowVersion = null,
        payloadJson = "{}",
        requestHash = ByteArray(32) { seed.toByte() },
        occurredAtMs = seed.toLong(),
        state = state,
        attemptCount = 1,
        nextAttemptAtMs = null,
        leaseToken = if (leaseExpiresAtMs == null) null else "process-local-lease",
        leaseExpiresAtMs = leaseExpiresAtMs,
        lastErrorCode = null,
        ackedAtMs = null,
    )

    private fun outboxFixture(
        seed: Int,
        eventType: String = "USER_TRACK_REF_CREATED",
        payload: String,
    ) = LocalMutationOutboxEntity(
        localChangeId = uuid(seed * 10 + 3),
        eventType = eventType,
        schemaVersion = 1,
        aggregateType = "USER_TRACK_REF",
        aggregateLocalId = uuid(seed * 10 + 1),
        payloadJson = payload,
        occurredAtMs = seed.toLong(),
        materializationState = "UNMATERIALIZED",
    )

    private fun materializeCommand(
        localChangeId: String,
        eventSeed: Int,
        journalEpochSeed: Int,
    ) = MaterializeLocalChangeCommand(
        localChangeId = LocalId(localChangeId),
        eventId = LocalId(uuid(eventSeed)),
        binding = binding(journalEpochSeed = journalEpochSeed),
        materializedAtMs = eventSeed.toLong(),
    )

    private fun binding(
        userSeed: Int = 9001,
        deviceSeed: Int = 9002,
        profileSeed: Int = 9003,
        journalEpochSeed: Int? = null,
    ) = ClientEventBinding(
        userId = UserId(uuid(userSeed)),
        deviceId = DeviceId(uuid(deviceSeed)),
        serverProfileId = ServerProfileId(uuid(profileSeed)),
        journalEpoch = journalEpochSeed?.let { LocalId(uuid(it)) },
    )

    private suspend fun assertPayloadFails(
        code: LocalIntentPayloadErrorCode,
        block: suspend () -> Unit,
    ) {
        var failure: LocalIntentPayloadException? = null
        try {
            block()
        } catch (error: LocalIntentPayloadException) {
            failure = error
        }
        assertEquals(code, failure?.code)
        assertEquals(code.name, failure?.message)
    }

    private suspend fun assertFails(block: suspend () -> Unit) {
        var failed = false
        try {
            block()
        } catch (_: Exception) {
            failed = true
        }
        assertTrue(failed)
    }

    private fun uuid(seed: Int): String = UUID(0L, seed.toLong()).toString()

    private class InjectedFailure : RuntimeException()
}
