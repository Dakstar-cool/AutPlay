package app.autplay.application.recommendation

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import kotlinx.coroutines.runBlocking
import org.erdtman.jcs.JsonCanonicalizer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OfflineRecommendationRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "autplay-p11-recommendation.db"
    private lateinit var database: AutPlayDatabase

    @Before
    fun setUp() = runBlocking {
        context.deleteDatabase(name)
        database = AutPlayDatabase.open(context, name)
        seedLocalTrack(database, PROFILE, TRACK, LOCAL_RECORDING, RECORDING)
    }

    @After
    fun clean() {
        database.close()
        context.deleteDatabase(name)
    }

    @Test
    fun ownerIsolationAndLocallyAvailablePackMappingFailClosed() = runBlocking {
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)

        val feed = repository.loadHomeFeed(BINDING, NOW)
        assertEquals("FRESH_OFFLINE_PACK", feed.statusCode)
        assertEquals(PACK, feed.packId)
        assertEquals(listOf(RECORDING), feed.recommendationSections.values.flatten().map { it.recordingId })

        val other = ClientEventBinding(UserId(OTHER_USER), DeviceId(OTHER_DEVICE), ServerProfileId(OTHER_PROFILE))
        val isolated = repository.loadHomeFeed(other, NOW)
        assertEquals("NO_USABLE_OFFLINE_PACK", isolated.statusCode)
        assertTrue(isolated.recommendationSections.isEmpty())
        assertTrue(database.recommendationPackDao().latest(OTHER_PROFILE, OTHER_USER, 5).isEmpty())

        val sameProfileOtherOwner = ClientEventBinding(UserId(OTHER_USER), DeviceId(OTHER_DEVICE), ServerProfileId(PROFILE))
        val crossOwnerAttempt = runCatching {
            repository.storeVerifiedPack(
                pack(OTHER_USER, OTHER_DEVICE, OTHER_PACK, OTHER_REQUEST),
                sameProfileOtherOwner,
                NOW,
            )
        }
        assertTrue(crossOwnerAttempt.isFailure)
        assertTrue(database.recommendationPackDao().latest(PROFILE, OTHER_USER, 5).isEmpty())
    }

    @Test
    fun mockedProductionTransportRefreshesAndStoresVerifiedPack() = runBlocking {
        val source = pack()
        val repository = OfflineRecommendationRepository(database)
        val decoded = repository.refreshPack(
            BINDING,
            RecommendationPackTransport { binding, request ->
                assertEquals(BINDING, binding)
                assertEquals("cpu-baseline", request.pipelineKey)
                DownloadedRecommendationPack(
                    offlinePackId = source.offlinePackId,
                    recommendationRequestId = REQUEST,
                    payloadVersion = 1,
                    payloadEncoding = "RAW_JSON",
                    payloadBase64 = Base64.getEncoder().encodeToString(source.payload),
                    payloadSha256 = source.payloadSha256.joinToString("") { "%02x".format(it.toInt() and 0xff) },
                    createdAtMs = source.createdAtMs,
                    expiresAtMs = source.expiresAtMs,
                )
            },
            NOW,
        )
        assertEquals(PACK, decoded.offlinePackId)
        assertEquals(PACK, database.recommendationPackDao().latest(PROFILE, USER, 5).single().offlinePackId)
    }

    @Test
    fun samePresentationRecompositionAndRestartReuseFirstStableEventId() = runBlocking {
        val firstRepository = OfflineRecommendationRepository(database, eventIdFactory = { LocalId(EVENT_ONE) })
        firstRepository.storeVerifiedPack(pack(), BINDING, NOW)
        val item = firstRepository.loadHomeFeed(BINDING, NOW).recommendationSections.values.flatten().single()
        val first = firstRepository.recordPresentation(BINDING, LocalId(PACK), item, NOW)
        val recomposed = firstRepository.recordPresentation(BINDING, LocalId(PACK), item, NOW + 1)
        assertFalse(first.duplicate)
        assertTrue(recomposed.duplicate)
        assertEquals(EVENT_ONE, recomposed.impressionEventId.value)

        database.close()
        database = AutPlayDatabase.open(context, name)
        val restartedRepository = OfflineRecommendationRepository(database, eventIdFactory = { LocalId(EVENT_TWO) })
        val restartedItem = restartedRepository.loadHomeFeed(BINDING, NOW + 2).recommendationSections.values.flatten().single()
        val restarted = restartedRepository.recordPresentation(BINDING, LocalId(PACK), restartedItem, NOW + 2)

        assertTrue(restarted.duplicate)
        assertEquals(EVENT_ONE, restarted.impressionEventId.value)
        assertNotEquals(EVENT_TWO, restarted.impressionEventId.value)
        assertEquals(1, database.recommendationPackDao().presentationCount(PROFILE, USER))
        assertEquals(1, database.journalDao().eventCount())
        val journal = database.journalDao().event(EVENT_ONE)!!
        assertEquals("RECOMMENDATION_IMPRESSION_RECORDED", journal.eventType)
        assertEquals("USER_INTERACTION_EVENT", journal.aggregateType)
        assertTrue(journal.payloadJson.contains("\"source_rank\":1"))
        assertTrue(journal.payloadJson.contains("\"offline_pack_id\":\"$PACK\""))
    }

    @Test
    fun mappingAndJournalRollBackTogetherWhenJournalStepFails() = runBlocking {
        val base = OfflineRecommendationRepository(database)
        base.storeVerifiedPack(pack(), BINDING, NOW)
        val item = base.loadHomeFeed(BINDING, NOW).recommendationSections.values.flatten().single()
        val failing = OfflineRecommendationRepository(
            database,
            eventIdFactory = { LocalId(EVENT_ONE) },
            failureInjector = PresentationFailureInjector { error("injected") },
        )

        val failure = runCatching {
            failing.recordPresentation(BINDING, LocalId(PRESENTATION_TWO), item, NOW)
        }
        assertTrue(failure.isFailure)
        assertEquals(0, database.recommendationPackDao().presentationCount(PROFILE, USER))
        assertEquals(0, database.journalDao().eventCount())
    }

    @Test
    fun relevantArtistReleaseDoesNotRequireOwningItsTracksAndRemainsProfileScoped() = runBlocking {
        OfflineRecommendationRepository(database).storeVerifiedPack(pack(), BINDING, NOW)
        database.catalogProjectionDao().upsertReleases(
            listOf(
                ReleaseProjectionEntity(
                    localReleaseId = RELEASE,
                    serverReleaseId = null,
                    serverReleaseGroupId = null,
                    title = "New release without owned tracks",
                    displayArtist = "AutPlay Artist",
                    releaseDateText = "2026-08-17",
                    releaseType = "ALBUM",
                    artworkRef = null,
                    catalogVersion = 8,
                    projectionUpdatedAtMs = NOW,
                    isDeleted = false,
                ),
            ),
        )

        val ownedArtistReleases = database.recommendationPackDao().recentRelevantReleases(PROFILE, USER, 10)
        val otherProfileReleases = database.recommendationPackDao().recentRelevantReleases(OTHER_PROFILE, OTHER_USER, 10)
        assertEquals(listOf(RELEASE), ownedArtistReleases.map { it.localReleaseId })
        assertTrue(otherProfileReleases.isEmpty())
    }

    private suspend fun seedLocalTrack(
        db: AutPlayDatabase,
        profile: String,
        track: String,
        localRecording: String,
        serverRecording: String,
    ) {
        db.catalogProjectionDao().upsertRecordings(
            listOf(
                RecordingProjectionEntity(
                    localRecordingId = localRecording,
                    serverRecordingId = serverRecording,
                    redirectServerRecordingId = null,
                    title = "Local recommendation",
                    normalizedTitle = "local recommendation",
                    displayArtist = "AutPlay Artist",
                    normalizedArtist = "autplay artist",
                    artistCreditJson = "{}",
                    durationMs = 180_000,
                    recordingKind = "SONG",
                    versionText = null,
                    explicitState = 0,
                    artworkRef = null,
                    catalogVersion = 7,
                    projectionUpdatedAtMs = NOW,
                ),
            ),
        )
        db.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = track,
                serverUserTrackRefId = null,
                localRecordingId = localRecording,
                serverRecordingId = serverRecording,
                resolutionStatus = "RESOLVED",
                rawTitle = "Local recommendation",
                rawArtist = "AutPlay Artist",
                rawAlbum = null,
                rawDurationMs = 180_000,
                resolutionConfidence = 1.0,
                syncState = "SYNCED",
                serverRowVersion = 1,
                lastLocalSequence = 0,
                createdAtMs = NOW,
                updatedAtMs = NOW,
                deletedAtMs = null,
                serverProfileId = profile,
            ),
        )
        db.libraryDao().upsertEntry(
            LibraryEntryEntity(
                localLibraryEntryId = LIBRARY_ENTRY,
                serverLibraryEntryId = null,
                localUserTrackRefId = track,
                addedAtMs = NOW,
                source = "IMPORT",
                availabilityStatus = "LOCAL",
                syncState = "SYNCED",
                serverRowVersion = 1,
                lastLocalSequence = 0,
                removedAtMs = null,
                updatedAtMs = NOW,
                serverProfileId = profile,
            ),
        )
    }

    private fun pack(
        user: String = USER,
        device: String = DEVICE,
        packId: String = PACK,
        requestId: String = REQUEST,
    ): RecommendationPackEntity {
        val raw = """{"payload_version":1,"offline_pack_id":"$packId","recommendation_request_id":"$requestId","user_id":"$user","device_id":"$device","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$EXPIRES,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":1,"exploration":0.1,"seed":42,"shadow":false},"items":[{"offline_pack_id":"$packId","recording_id":"$RECORDING","source_rank":1,"pack_position":1,"section":"for_you","score":1.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":1,"raw_score":1.0,"provenance":{"kind":"explicit"}}]}]}"""
        val bytes = JsonCanonicalizer(raw).encodedString.toByteArray(StandardCharsets.UTF_8)
        return RecommendationPackEntity(
            offlinePackId = packId,
            serverProfileId = PROFILE,
            ownerUserId = user,
            catalogSnapshot = 7,
            modelBundleVersion = "cpu-v1",
            payloadVersion = 1,
            payloadEncoding = "RAW_JSON",
            payload = bytes,
            payloadSha256 = MessageDigest.getInstance("SHA-256").digest(bytes),
            createdAtMs = CREATED,
            expiresAtMs = EXPIRES,
        )
    }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val DEVICE = "33333333-3333-4333-8333-333333333333"
        const val PACK = "44444444-4444-4444-8444-444444444444"
        const val REQUEST = "55555555-5555-4555-8555-555555555555"
        const val RECORDING = "66666666-6666-4666-8666-666666666666"
        const val LOCAL_RECORDING = "77777777-7777-4777-8777-777777777777"
        const val TRACK = "88888888-8888-4888-8888-888888888888"
        const val LIBRARY_ENTRY = "99999999-9999-4999-8999-999999999999"
        const val EVENT_ONE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        const val EVENT_TWO = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        const val PRESENTATION_TWO = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        const val RELEASE = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        const val OTHER_PROFILE = "d1111111-1111-4111-8111-111111111111"
        const val OTHER_USER = "d2222222-2222-4222-8222-222222222222"
        const val OTHER_DEVICE = "d3333333-3333-4333-8333-333333333333"
        const val OTHER_PACK = "d4444444-4444-4444-8444-444444444444"
        const val OTHER_REQUEST = "d5555555-5555-4555-8555-555555555555"
        const val CREATED = 1_000L
        const val NOW = 2_000L
        const val EXPIRES = 100_000L
        val BINDING = ClientEventBinding(UserId(USER), DeviceId(DEVICE), ServerProfileId(PROFILE))
    }
}
