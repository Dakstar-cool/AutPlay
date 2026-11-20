package app.autplay.application.recommendation

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.library.projectMetadata
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.AggregateRedirectEntity
import app.autplay.data.local.entity.TombstoneEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.local.entity.UserTrackPreferenceEntity
import app.autplay.data.local.entity.TrackMetadataEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
    fun downloadedServerTrackWithoutLocalRecordingProjectionRemainsEligible() = runBlocking {
        val original = checkNotNull(database.libraryDao().trackRef(TRACK))
        database.libraryDao().upsertTrackRef(original.copy(localRecordingId = null))
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        val feed = repository.loadHomeFeed(BINDING, NOW)
        assertEquals(listOf(RECORDING), feed.recommendationSections.values.flatten().map { it.recordingId })

        database.libraryDao().upsertTrackRef(original.copy(localRecordingId = null, resolutionStatus = "UNRESOLVED"))
        assertTrue(repository.loadHomeFeed(BINDING, NOW + 1).recommendationSections.isEmpty())
        database.libraryDao().upsertTrackRef(original.copy(localRecordingId = null, deletedAtMs = NOW))
        assertTrue(repository.loadHomeFeed(BINDING, NOW + 2).recommendationSections.isEmpty())
    }

    @Test
    fun serverOnlyRecommendationStillRejectsRedirectedRecordings() = runBlocking {
        val original = checkNotNull(database.libraryDao().trackRef(TRACK))
        database.libraryDao().upsertTrackRef(original.copy(localRecordingId = null))
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        assertFalse(repository.loadHomeFeed(BINDING, NOW).recommendationSections.isEmpty())
        database.syncDao().upsertRedirect(AggregateRedirectEntity(
            serverProfileId = PROFILE, aggregateType = "RECORDING", aliasLocalId = LOCAL_RECORDING,
            aliasServerId = RECORDING, canonicalLocalId = "00000000-0000-0000-0000-000000000901",
            canonicalServerId = "00000000-0000-0000-0000-000000000902", createdByServerSequence = 1, createdAtMs = NOW,
        ))
        assertTrue(repository.loadHomeFeed(BINDING, NOW + 1).recommendationSections.isEmpty())
    }

    @Test
    fun serverOnlyRecommendationStillRejectsRecordingTombstones() = runBlocking {
        val original = checkNotNull(database.libraryDao().trackRef(TRACK))
        database.libraryDao().upsertTrackRef(original.copy(localRecordingId = null))
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        assertFalse(repository.loadHomeFeed(BINDING, NOW).recommendationSections.isEmpty())
        database.syncDao().upsertTombstone(TombstoneEntity(
            tombstoneId = "00000000-0000-0000-0000-000000000903", serverProfileId = PROFILE,
            aggregateType = "RECORDING", aggregateLocalId = LOCAL_RECORDING,
            aggregateServerId = RECORDING, deletedByEventId = EVENT_ONE,
            deletedAtMs = NOW, retainUntilMs = NOW + 1000, serverAcked = true,
        ))
        assertTrue(repository.loadHomeFeed(BINDING, NOW + 1).recommendationSections.isEmpty())
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
    fun temporalDeltaSurvivesRestartCreatesImpressionOnlyOnVisibilityAndPurgesOnUnbind() = runBlocking {
        val repository = OfflineRecommendationRepository(database, eventIdFactory = { LocalId(EVENT_ONE) })
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        LibraryVerticalSliceRepository(database).setPreference(
            BINDING,
            LocalId(TRACK),
            LocalId(PREFERENCE_EVENT),
            "LIKED",
            false,
            null,
            NOW + 1,
        )

        val firstItem = repository.loadHomeFeed(BINDING, NOW + 2).recommendationSections.values.flatten().single()
        val deltaId = requireNotNull(firstItem.deltaId)
        val deltaEventId = requireNotNull(firstItem.impressionEventId)
        assertEquals("local_temporal_delta", firstItem.source)
        assertEquals(1, database.recommendationPackDao().deltaCount(PROFILE, USER))
        assertEquals(1, database.journalDao().eventCount())

        database.close()
        database = AutPlayDatabase.open(context, name)
        val restarted = OfflineRecommendationRepository(database, eventIdFactory = { LocalId(EVENT_TWO) })
        val restartedItem = restarted.loadHomeFeed(BINDING, NOW + 3).recommendationSections.values.flatten().single()
        assertEquals(deltaId, restartedItem.deltaId)
        assertEquals(deltaEventId, restartedItem.impressionEventId)
        assertEquals(1, database.journalDao().eventCount())

        val presented = restarted.recordPresentation(BINDING, LocalId(PACK), restartedItem, NOW + 3)
        val repeated = restarted.recordPresentation(BINDING, LocalId(PACK), restartedItem, NOW + 4)
        assertEquals(deltaEventId, presented.impressionEventId.value)
        assertTrue(repeated.duplicate)
        assertEquals(2, database.journalDao().eventCount())

        restarted.purgeLocalRecommendationContext(PROFILE)
        assertTrue(database.recommendationPackDao().latest(PROFILE, USER, 5).isEmpty())
        assertEquals(0, database.recommendationPackDao().deltaCount(PROFILE, USER))
        assertEquals(0, database.recommendationPackDao().presentationCount(PROFILE, USER))
        assertEquals(2, database.journalDao().eventCount())
        assertEquals(TRACK, requireNotNull(database.libraryDao().trackRef(TRACK)).localUserTrackRefId)
    }

    @Test
    fun expiredDeltaFallsBackWithoutChangingExistingPresentationAttribution() = runBlocking {
        val repository = OfflineRecommendationRepository(database, eventIdFactory = { LocalId(EVENT_ONE) })
        val parentExpiry = NOW + OfflineTemporalDeltaCodec.MAX_DELTA_LIFETIME_MS + 10_000
        repository.storeVerifiedPack(pack(expiresAtMs = parentExpiry), BINDING, NOW)
        LibraryVerticalSliceRepository(database).setPreference(
            BINDING,
            LocalId(TRACK),
            LocalId(PREFERENCE_EVENT),
            "LIKED",
            false,
            null,
            NOW + 1,
        )
        val deltaItem = repository.loadHomeFeed(BINDING, NOW + 2).recommendationSections.values.flatten().single()
        val first = repository.recordPresentation(BINDING, LocalId(PACK), deltaItem, NOW + 3)

        val afterExpiry = NOW + 2 + OfflineTemporalDeltaCodec.MAX_DELTA_LIFETIME_MS
        val baseItem = repository.loadHomeFeed(BINDING, afterExpiry).recommendationSections.values.flatten().single()
        assertEquals("offline_pack", baseItem.source)
        assertEquals(null, baseItem.deltaId)

        val repeated = repository.recordPresentation(BINDING, LocalId(PACK), baseItem, afterExpiry)
        assertTrue(repeated.duplicate)
        assertEquals(first.impressionEventId, repeated.impressionEventId)
        assertEquals(1, database.recommendationPackDao().presentationCount(PROFILE, USER))
        assertEquals(2, database.journalDao().eventCount())
        repository.activatePresentedPlayback(BINDING, LocalId(PACK), baseItem, first.impressionEventId, afterExpiry, { true })
        val queue = requireNotNull(app.autplay.application.playback.PlaybackPersistenceRepository(database).restoreActive(afterExpiry))
        assertEquals(
            repository.attributionJson(LocalId(PACK), first.impressionEventId, deltaItem),
            queue.entries.single().recommendationAttributionJson,
        )
        assertEquals(2, database.journalDao().eventCount())
    }

    @Test
    fun refreshRevalidatesBindingBeforeCommittingDownloadedPack() = runBlocking {
        val source = pack()
        val repository = OfflineRecommendationRepository(database)
        val failure = runCatching {
            repository.refreshPack(
                BINDING,
                RecommendationPackTransport { _, _ ->
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
                isBindingActive = { false },
            )
        }
        assertTrue(failure.isFailure)
        assertTrue(database.recommendationPackDao().latest(PROFILE, USER, 5).isEmpty())
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

    @Test
    fun standaloneWaveStartsWithoutAProfileOrRecommendationAttribution() = runBlocking {
        val trackId = LocalId.random().value
        seedLocalTrack(database, "legacy-unscoped", trackId, LocalId.random().value, LocalId.random().value)
        database.localAudioDao().upsertDownloadIntent(app.autplay.data.local.entity.DownloadIntentEntity(
            downloadIntentId = LocalId.random().value, localUserTrackRefId = trackId,
            serverAudioVariantId = LocalId.random().value, state = "COMPLETED",
            media3DownloadId = null, desiredStorageClass = "USER_DOWNLOAD", qualityPolicy = "ORIGINAL",
            sourcePolicy = "VAULT", failureCode = null, createdAtMs = NOW, updatedAtMs = NOW, completedAtMs = NOW,
        ))
        val guard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(null, null, null)
        val snapshot = requireNotNull(PersonalWaveQueue(database).start(null, guard, false, { true }) { _, _ ->
            error("standalone start attempted HTTP")
        })
        val entry = database.queueDao().entries(snapshot.value, 1).single()
        assertEquals(trackId, entry.localUserTrackRefId)
        assertEquals(null, entry.recommendationAttributionJson)
        assertEquals(0, database.journalDao().eventCount())
    }

    @Test
    fun wavePackUnlocksRemixOnlyWhileItsOriginalHasAnOwnerLike() = runBlocking {
        val dao = database.libraryDao()
        database.trackMetadataDao().upsert(TrackMetadataEntity(
            PROFILE, TRACK, 1,
            """{"revision":1,"state":"READY","fields":{"title":"Local recommendation (Club Remix)"}}""",
            null, NOW,
        ))
        val ref = requireNotNull(dao.trackRef(TRACK))
        val original = LocalId.random().value
        val other = LocalId.random().value
        dao.upsertTrackRef(ref.copy(localUserTrackRefId = original, localRecordingId = null, serverRecordingId = null))
        dao.upsertTrackRef(ref.copy(localUserTrackRefId = other, localRecordingId = null,
            serverRecordingId = null, serverProfileId = OTHER_PROFILE))
        val like = UserTrackPreferenceEntity(original, "LIKED", null, false, "SYNCED", 0, NOW, PROFILE)
        dao.upsertPreference(like.copy(localUserTrackRefId = other, serverProfileId = OTHER_PROFILE))
        dao.upsertPreference(like.copy(localUserTrackRefId = TRACK))
        val now = System.currentTimeMillis()
        OfflineRecommendationRepository(database).storeVerifiedPack(pack(expiresAtMs = now + 60_000), BINDING, now)
        val guard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(null, null, PROFILE)
        val queue = PersonalWaveQueue(database)
        val home = OfflineRecommendationRepository(database)
        assertTrue(home.loadHomeFeed(BINDING, now).recommendationSections.isEmpty())
        assertEquals(null, queue.start(BINDING, guard, false, { true }) { _, _ -> error("offline") })
        dao.upsertPreference(like)
        assertEquals(TRACK, home.loadHomeFeed(BINDING, now).recommendationSections.values.flatten().single().localUserTrackRefId)
        // The one-item pack has no room for the nineteen ordinary wave tracks.
        assertEquals(null, queue.start(BINDING, guard, false, { true }) { _, _ -> error("offline") })
        dao.upsertPreference(like.copy(preference = "NEUTRAL"))
        assertTrue(home.loadHomeFeed(BINDING, now).recommendationSections.isEmpty())
    }

    @Test
    fun retitledCoverInOfflineFeedRequiresTheLikedMusicBrainzRecordingOfThatWork() = runBlocking {
        val dao = database.libraryDao()
        val ref = requireNotNull(dao.trackRef(TRACK))
        val original = LocalId.random().value
        val originalMbid = LocalId.random().value
        val coverMbid = LocalId.random().value
        val work = LocalId.random().value
        val foreign = LocalId.random().value
        dao.upsertTrackRef(ref.copy(localUserTrackRefId = original, localRecordingId = null, serverRecordingId = null))
        dao.upsertTrackRef(ref.copy(localUserTrackRefId = foreign, localRecordingId = null, serverRecordingId = null,
            serverProfileId = OTHER_PROFILE))
        suspend fun project(profile: String, id: String, recording: String, workId: String, cover: Boolean, revision: Int) {
            val title = if (cover) "Другие слова" else "First song"
            val artist = if (cover) "Группа" else "Original singer"
            val role = if (cover) "COVER" else "PERFORMANCE"
            val parent = if (cover) "\"$originalMbid\"" else "null"
            val json = """{"revision":$revision,"state":"READY","fields":{"title":"$title","artist":"$artist",
                "mb_recording_id":"$recording"},"musicbrainz_performance_v1":{"role":"$role","work_id":"$workId",
                "recording_id":"$recording","original_recording_id":$parent}}"""
            assertTrue(database.projectMetadata(profile, id, kotlinx.serialization.json.Json.parseToJsonElement(json).jsonObject))
        }
        project(PROFILE, TRACK, coverMbid, work, true, 1)
        project(OTHER_PROFILE, foreign, originalMbid, work, false, 1)
        val like = UserTrackPreferenceEntity(original, "LIKED", null, false, "SYNCED", 0, NOW, PROFILE)
        dao.upsertPreference(like.copy(localUserTrackRefId = foreign, serverProfileId = OTHER_PROFILE))
        dao.upsertPreference(like.copy(localUserTrackRefId = TRACK))
        dao.upsertPreference(like)
        val home = OfflineRecommendationRepository(database)
        val now = System.currentTimeMillis()
        home.storeVerifiedPack(pack(expiresAtMs = now + 60_000), BINDING, now)
        suspend fun visible() = home.loadHomeFeed(BINDING, now).recommendationSections.values.flatten()
            .map { it.localUserTrackRefId }
        project(PROFILE, original, LocalId.random().value, work, false, 1)
        assertTrue(visible().isEmpty())
        project(PROFILE, original, originalMbid, LocalId.random().value, false, 2)
        assertTrue(visible().isEmpty())
        project(PROFILE, original, originalMbid, work, false, 3)
        assertEquals(listOf(TRACK), visible())
        dao.upsertPreference(like.copy(excludedFromTaste = true))
        assertTrue(visible().isEmpty())
        dao.upsertPreference(like.copy(preference = "NEUTRAL"))
        assertTrue(visible().isEmpty())
        dao.upsertPreference(like)
        val week = 7L * 24 * 60 * 60 * 1000
        val played = app.autplay.data.local.entity.ListeningEventEntity(LocalId.random().value, original,
            null, now - week, 12_000, null, null, "ORGANIC", "GENERAL", null, "NONE", false, "CLEAN", now,
            serverProfileId = PROFILE)
        database.historyDao().insert(played)
        assertEquals(listOf(TRACK), visible())
        database.historyDao().insert(played.copy(listeningEventId = LocalId.random().value, startedAtMs = now + 1))
        database.historyDao().insert(played.copy(listeningEventId = LocalId.random().value, startedAtMs = now - 1,
            excludedFromTaste = true))
        assertEquals(listOf(TRACK), visible())
        database.historyDao().insert(played.copy(listeningEventId = LocalId.random().value, startedAtMs = now - week + 1))
        assertTrue(visible().isEmpty())
        assertTrue(home.loadHomeFeed(BINDING, now + 1).recommendationSections.isEmpty())
    }

    @Test
    fun frequentSkipsThinOnlyTheAffectedVersionTypeAndSurface() = runBlocking {
        val dao = database.libraryDao()
        val template = requireNotNull(dao.trackRef(TRACK))
        val parent = LocalId.random().value
        dao.upsertTrackRef(template.copy(localUserTrackRefId = parent, localRecordingId = null, serverRecordingId = null))
        dao.upsertPreference(UserTrackPreferenceEntity(parent, "LIKED", null, false, "SYNCED", 0, NOW, PROFILE))
        database.trackMetadataDao().upsert(TrackMetadataEntity(PROFILE, TRACK, 1,
            """{"revision":1,"state":"READY","fields":{"title":"Local recommendation (Club Remix)"}}""", null, NOW))
        val day = 86_400_000L
        val start = System.currentTimeMillis()
        val now = (0..2).map { start + it * day }.first { !retainedVariation(RECORDING, it) }
        val repo = OfflineRecommendationRepository(database)
        repo.storeVerifiedPack(pack(expiresAtMs = now + 4 * day), BINDING, now)
        suspend fun visible(at: Long) = repo.loadHomeFeed(BINDING, at).recommendationSections.values.flatten().map { it.localUserTrackRefId }
        suspend fun play(ms: Long, ratio: Double, surface: String, kind: String? = null, age: Long = 8L, excluded: Boolean = false) {
            val event = app.autplay.data.local.entity.ListeningEventEntity(LocalId.random().value, TRACK, RECORDING,
                now - age * day, ms, null, ratio, "RECOMMENDED", "GENERAL", REQUEST, "NONE", excluded, "CLEAN", now,
                recommendationAttributionJson = """{"surface":"$surface"}""", serverProfileId = PROFILE, variationKind = kind)
            database.historyDao().insert(event)
        }
        play(29_999, .9, "offline_pack")
        play(30_000, .2, "home")
        play(30_000, .1, "home")
        play(30_000, .21, "home")
        assertEquals(listOf(TRACK), visible(now))
        play(90_000, 1.0, "home")
        play(1000, .1, "personal_wave", "LIVE")
        play(1000, .1, "home", age = 30)
        play(1000, .1, "home", age = -1, excluded = true)
        play(1000, .1, "home", excluded = true)
        assertEquals(setOf(VariationSkips("home","REMIX",5,3), VariationSkips("personal_wave","LIVE",1,1)), dao.variationSkips(PROFILE, now).toSet())
        assertTrue(dao.variationSkips(OTHER_PROFILE, now).isEmpty())
        assertTrue(visible(now).isEmpty())
        val retained = (1..2).map { now + it * day }.first { retainedVariation(RECORDING, it) }
        assertEquals(listOf(TRACK), visible(retained))
        play(90_000, 1.0, "home")
        assertEquals(listOf(TRACK), visible(now))
        // Other devices can still put this category over the account-wide threshold.
        assertTrue(dao.selectSongRefs(PROFILE, listOf(TRACK), now, wave = false, lessFrequentRecordings = setOf(RECORDING)).isEmpty())
    }

    @Test
    fun onlineWaveDefersRetitledRemixToTwentiethPositionAndPreservesItsSourceRank() = runBlocking {
        val dao = database.libraryDao()
        val template = requireNotNull(dao.trackRef(TRACK))
        val entry = requireNotNull(dao.entry(LIBRARY_ENTRY))
        val parent = LocalId.random().value
        val mix = LocalId.random().value
        val source = LocalId.random().value
        dao.upsertTrackRef(template.copy(localUserTrackRefId = source, localRecordingId = null,
            serverRecordingId = null, rawTitle = "Earlier song", rawArtist = "Original artist"))
        suspend fun metadata(id: String, json: String) = database.projectMetadata(
            PROFILE, id, kotlinx.serialization.json.Json.parseToJsonElement(json).jsonObject)
        assertTrue(metadata(source, """{"revision":1,"state":"READY","fields":{"mb_recording_id":"$parent"}}"""))
        assertTrue(metadata(TRACK, """{"revision":1,"state":"READY","fields":{"title":"New title","artist":"New DJ",
            "mb_recording_id":"$mix"},"musicbrainz_performance_v1":{"role":"REMIX","work_id":null,
            "recording_id":"$mix","original_recording_id":"$parent"}}"""))
        dao.upsertPreference(UserTrackPreferenceEntity(source, "LIKED", null, false, "SYNCED", 0, NOW, PROFILE))
        val recordings = mutableListOf(RECORDING)
        // The last recording is another retitled remix whose metadata hasn't synced yet.
        repeat(20) { n ->
            val ref = LocalId.random().value
            val recording = LocalId.random().value
            dao.upsertTrackRef(template.copy(localUserTrackRefId = ref, localRecordingId = null,
                serverRecordingId = recording, rawTitle = "Song $n"))
            dao.upsertEntry(entry.copy(localLibraryEntryId = LocalId.random().value, localUserTrackRefId = ref))
            recordings.add(recording)
        }
        val guard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(null, null, PROFILE)
        val id = requireNotNull(PersonalWaveQueue(database).start(BINDING, guard, true, { true }) { _, _ ->
            app.autplay.application.server.ServerRecommendationResult(REQUEST, "served", recordings.mapIndexed { i, r ->
                app.autplay.application.server.ServerRecommendationItem(r, i + 1, 1.0, "BASELINE", "recommendations",
                    confirmedVariation = i == 0 || i == recordings.lastIndex)
            })
        })
        val entries = database.queueDao().entries(id.value, 100)
        assertEquals(20, entries.size)
        assertEquals(TRACK, entries.last().localUserTrackRefId)
        val attribution = kotlinx.serialization.json.Json.parseToJsonElement(requireNotNull(entries.last().recommendationAttributionJson)).jsonObject
        assertEquals("1", attribution.getValue("source_rank").jsonPrimitive.content)
        assertEquals("20", attribution.getValue("display_position").jsonPrimitive.content)

        val day = 86_400_000L
        val today = System.currentTimeMillis()
        val limited = (0..2).map { today + it * day }.first { !retainedVariation(RECORDING, it) }
        val history = app.autplay.application.library.LibraryVerticalSliceRepository(database)
        repeat(5) { index ->
            val event = LocalId.random()
            val at = limited - 9 * day + index
            repeat(2) {
                history.recordListening(BINDING, event, LocalId(TRACK), 1000, null, false, "WAVE",
                    now = at, personalWaveFallback = true)
            }
            val saved = requireNotNull(database.historyDao().event(PROFILE, event.value))
            assertEquals("REMIX", saved.variationKind)
            assertEquals("personal_wave", saved.variationSurface)
        }
        val refs = entries.map { it.localUserTrackRefId }
        assertEquals(19, dao.selectSongRefs(PROFILE, refs, limited, wave = true).size)
        assertTrue(TRACK in dao.selectSongRefs(PROFILE, refs, limited, wave = false))
        val next = (1..2).map { limited + it * day }.first { retainedVariation(RECORDING, it) }
        assertEquals(TRACK, dao.selectSongRefs(PROFILE, refs, next, wave = true).last())
    }

    @Test
    fun waveUsesAnOwnedFreshPackWhenDisconnected() = runBlocking {
        val now = System.currentTimeMillis()
        OfflineRecommendationRepository(database).storeVerifiedPack(pack(expiresAtMs = now + 60_000), BINDING, now)
        val guard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(null, null, PROFILE)
        val id = requireNotNull(PersonalWaveQueue(database).start(BINDING, guard, false, { true }) { _, _ ->
            error("offline start attempted HTTP")
        })
        val entry = database.queueDao().entries(id.value, 1).single()
        val attr = kotlinx.serialization.json.Json.parseToJsonElement(requireNotNull(entry.recommendationAttributionJson)).jsonObject
        assertEquals(PACK, attr.getValue("offline_pack_id").jsonPrimitive.content)
        assertEquals(REQUEST, attr.getValue("recommendation_request_id").jsonPrimitive.content)
        assertEquals(0, database.journalDao().eventCount())
    }

    @Test
    fun waveQueueRecordsOnlyPlayedItemsAndKeepsOwnerAfterRestart() = runBlocking {
        val cursor = app.autplay.application.sync.SyncBindingInitializer(database).ensure(BINDING)
        val guard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(null, null, PROFILE)
        val queue = PersonalWaveQueue(database)
        val id = requireNotNull(queue.start(BINDING, guard, online = true, stillActive = { true }) { _, wave ->
            assertEquals("GENERAL", wave.mode)
            app.autplay.application.server.ServerRecommendationResult(REQUEST, "served", listOf(
                app.autplay.application.server.ServerRecommendationItem(RECORDING, 1, 0.5, "BASELINE", "recommendations"),
            ))
        })
        assertEquals(0, database.journalDao().eventCount())
        database.close()
        database = AutPlayDatabase.open(context, name)
        val playback = app.autplay.application.playback.PlaybackPersistenceRepository(database)
        val restored = requireNotNull(playback.restoreActive())
        val bound = BINDING.copy(journalEpoch = LocalId(cursor.journalEpoch))
        assertEquals(id, PersonalWaveSession.restore(restored.snapshot.sourceContextId, bound)?.id)
        val entry = LocalId(restored.entries.single().queueEntryId)
        val wrong = app.autplay.playback.PlaybackSessionOwnerBinding(OTHER_USER, DEVICE, PROFILE)
        val now = System.currentTimeMillis()
        assertEquals(null, playback.startSessionIfActive(id, entry, 0, now, wrong))
        val owner = app.autplay.playback.PlaybackSessionOwnerBinding(USER, DEVICE, PROFILE)
        val started = requireNotNull(playback.startSessionIfActive(id, entry, 0, now, owner))
        val impression = requireNotNull(database.journalDao().event(requireNotNull(started.attribution).localImpressionId.value))
        assertEquals("RECOMMENDATION_IMPRESSION_RECORDED", impression.eventType)
        playback.finalizeSession(started, 12_000, 180_000, 12_000, now + 12_000)
        val listen = requireNotNull(database.journalDao().event(started.listeningEventId.value))
        assertTrue(listen.deviceSequence > impression.deviceSequence)
        assertEquals(REQUEST, kotlinx.serialization.json.Json.parseToJsonElement(listen.payloadJson)
            .jsonObject.getValue("recommendation").jsonObject.getValue("recommendation_request_id").jsonPrimitive.content)
        assertEquals(2, database.journalDao().eventCount())
    }

    @Test
    fun presentedPlaybackSurvivesRestartAndFinalizesOneCausalJournalFact() = runBlocking {
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        val item = repository.loadHomeFeed(BINDING, NOW).recommendationSections.values.flatten().single()
        val impression = repository.recordPresentation(BINDING, LocalId(PACK), item, NOW)
        val snapshotId = repository.activatePresentedPlayback(
            BINDING, LocalId(PACK), item, impression.impressionEventId, NOW + 1, { true },
        )
        val playback = app.autplay.application.playback.PlaybackPersistenceRepository(database)
        val queue = requireNotNull(playback.restoreActive(NOW + 2))
        assertEquals(snapshotId.value, queue.snapshot.queueSnapshotId)
        assertEquals(1, queue.entries.size)
        assertEquals(TRACK, queue.entries.single().localUserTrackRefId)
        assertEquals("RECOMMENDED", queue.entries.single().sourceOrigin)
        assertEquals(1, database.journalDao().eventCount())
        val owner = app.autplay.playback.PlaybackSessionOwnerBinding(USER, DEVICE, PROFILE)
        val session = playback.startSession(LocalId(queue.entries.single().queueEntryId), 0, NOW + 3, owner)
        playback.checkpoint(session, 90_000, 90_000, "OFF", "OFF", null, NOW + 90_003)
        database.close()
        database = AutPlayDatabase.open(context, name)
        val restarted = app.autplay.application.playback.PlaybackPersistenceRepository(database)
        val recovered = requireNotNull(restarted.recoverSession())
        assertEquals(impression.impressionEventId, recovered.attribution?.localImpressionId)
        assertEquals(owner, recovered.ownerBinding)
        restarted.finalizeSession(recovered, 100_000, 180_000, 10_000, NOW + 100_003)
        restarted.finalizeSession(recovered, 100_000, 180_000, 10_000, NOW + 100_004)
        val fact = requireNotNull(database.journalDao().event(session.listeningEventId.value))
        val payload = kotlinx.serialization.json.Json.parseToJsonElement(fact.payloadJson).jsonObject
        assertEquals("RECOMMENDED", payload["event_origin"]?.jsonPrimitive?.content)
        val attribution = requireNotNull(payload["recommendation"]).jsonObject
        assertEquals(impression.impressionEventId.value, attribution["impression_event_local_id"]?.jsonPrimitive?.content)
        assertEquals(REQUEST, attribution["recommendation_request_id"]?.jsonPrimitive?.content)
        assertEquals(RECORDING, attribution["recording_id"]?.jsonPrimitive?.content)
        assertEquals("1", attribution["source_rank"]?.jsonPrimitive?.content)
        assertEquals(100_000L, database.historyDao().event(session.listeningEventId.value)?.playedMs)
        assertTrue(fact.deviceSequence > impression.deviceSequence)
        assertEquals(2, database.journalDao().eventCount())
    }

    @Test
    fun playbackRequiresExactPersistedImpressionAndOwnerWithoutCreatingFacts() = runBlocking {
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        val item = repository.loadHomeFeed(BINDING, NOW).recommendationSections.values.flatten().single()
        val missing = runCatching {
            repository.activatePresentedPlayback(BINDING, LocalId(PACK), item, LocalId(EVENT_ONE), NOW, { true })
        }
        assertEquals("RECOMMENDATION_IMPRESSION_REQUIRED", missing.exceptionOrNull()?.message)
        assertEquals(0, database.journalDao().eventCount())
        val impression = repository.recordPresentation(BINDING, LocalId(PACK), item, NOW)
        val cases = listOf(
            Triple(BINDING, item, LocalId(EVENT_TWO)),
            Triple(BINDING.copy(deviceId = DeviceId(OTHER_DEVICE)), item, impression.impressionEventId),
            Triple(BINDING.copy(userId = UserId(OTHER_USER)), item, impression.impressionEventId),
            Triple(BINDING.copy(serverProfileId = ServerProfileId(OTHER_PROFILE)), item, impression.impressionEventId),
            Triple(BINDING.copy(journalEpoch = LocalId(OTHER_REQUEST)), item, impression.impressionEventId),
            Triple(BINDING, item.copy(localUserTrackRefId = OTHER_REQUEST), impression.impressionEventId),
            Triple(BINDING, item.copy(recordingId = OTHER_REQUEST), impression.impressionEventId),
            Triple(BINDING, item.copy(sourceRank = 2), impression.impressionEventId),
        )
        for ((owner, candidate, event) in cases) {
            assertTrue(runCatching {
                repository.activatePresentedPlayback(owner, LocalId(PACK), candidate, event, NOW + 1, { true })
            }.isFailure)
            assertEquals(null, database.queueDao().activeSnapshotOnce())
            assertEquals(1, database.journalDao().eventCount())
        }
    }

    @Test
    fun playbackRejectsInactiveBindingRemovedTrackAndWaveWithoutReplacingQueue() = runBlocking {
        val repository = OfflineRecommendationRepository(database)
        repository.storeVerifiedPack(pack(), BINDING, NOW)
        val item = repository.loadHomeFeed(BINDING, NOW).recommendationSections.values.flatten().single()
        val impression = repository.recordPresentation(BINDING, LocalId(PACK), item, NOW)
        val inactive = runCatching {
            repository.activatePresentedPlayback(BINDING, LocalId(PACK), item, impression.impressionEventId, NOW + 1, { false })
        }
        assertEquals("RECOMMENDATION_PROFILE_NOT_ACTIVE", inactive.exceptionOrNull()?.message)
        var checks = 0
        val changed = runCatching {
            repository.activatePresentedPlayback(BINDING, LocalId(PACK), item, impression.impressionEventId, NOW + 1, { ++checks == 1 })
        }
        assertEquals("RECOMMENDATION_PROFILE_NOT_ACTIVE", changed.exceptionOrNull()?.message)
        val track = requireNotNull(database.libraryDao().trackRef(TRACK))
        database.libraryDao().upsertTrackRef(track.copy(deletedAtMs = NOW))
        val removed = runCatching {
            repository.activatePresentedPlayback(BINDING, LocalId(PACK), item, impression.impressionEventId, NOW + 1, { true })
        }
        assertEquals("RECOMMENDATION_ITEM_NO_LONGER_ELIGIBLE", removed.exceptionOrNull()?.message)
        database.libraryDao().upsertTrackRef(track)
        assertEquals(null, database.queueDao().activeSnapshotOnce())
        val playback = app.autplay.application.playback.PlaybackPersistenceRepository(database)
        playback.activateQueue(LocalId(EVENT_TWO), listOf(app.autplay.application.playback.NewPlaybackQueueEntry(
            LocalId(EVENT_ONE), LocalId(TRACK), "ORGANIC", "LOCAL_THEN_VAULT",
        )), "WAVE", null, PROFILE, "GENERAL", NOW)
        val previous = database.queueDao().activeSnapshotOnce()
        val wave = runCatching {
            repository.activatePresentedPlayback(BINDING, LocalId(PACK), item, impression.impressionEventId, NOW + 1, { true })
        }
        assertEquals("ORDINARY_QUEUE_AUTHORITY_CHANGED", wave.exceptionOrNull()?.message)
        assertEquals(previous, database.queueDao().activeSnapshotOnce())
        assertEquals(1, database.journalDao().eventCount())
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
        expiresAtMs: Long = EXPIRES,
    ): RecommendationPackEntity {
        val raw = """{"payload_version":1,"offline_pack_id":"$packId","recommendation_request_id":"$requestId","user_id":"$user","device_id":"$device","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$expiresAtMs,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":1,"exploration":0.1,"seed":42,"shadow":false},"items":[{"offline_pack_id":"$packId","recording_id":"$RECORDING","source_rank":1,"pack_position":1,"section":"for_you","score":1.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":1,"raw_score":1.0,"provenance":{"kind":"explicit"}}]}]}"""
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
            expiresAtMs = expiresAtMs,
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
        const val PREFERENCE_EVENT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
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
