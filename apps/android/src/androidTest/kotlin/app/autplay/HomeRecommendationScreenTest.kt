package app.autplay

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import org.erdtman.jcs.JsonCanonicalizer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.assertTrue
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class HomeRecommendationScreenTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before
    fun setUp() = runBlocking {
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase(AutPlayDatabase.DATABASE_NAME)
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = ServerProfileId(PROFILE),
                activeUserId = UserId(USER),
                deviceId = DeviceId(DEVICE),
                serverBaseUrl = "https://offline.test",
            ),
        )
        val db = AutPlayDatabase.open(context)
        seed(db)
        OfflineRecommendationRepository(db).storeVerifiedPack(pack(), BINDING, NOW)
        db.close()
    }

    @After
    fun tearDown() = runBlocking {
        scenario?.close()
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase(AutPlayDatabase.DATABASE_NAME)
        applicationNonSecretSettingsStore(context).update(NonSecretSettings())
    }

    @Test
    fun profileHomeShowsRelevantReleaseAndOfflineRecommendationWithoutDuplicateImpression() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openHome()
        scrollHomeTo(hasTestTag("home-recommendation"))
        composeRule.onNodeWithText("Relevant release").performScrollTo().assertIsDisplayed()
        composeRule.onAllNodesWithTag("home-recommendation")[0].performScrollTo().assertIsDisplayed()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            runBlocking { AutPlayRuntime.database(context).journalDao().eventCount() == 1 }
        }

        scenario?.recreate()
        openHome()
        scrollHomeTo(hasTestTag("home-recommendation"))
        composeRule.onAllNodesWithTag("home-recommendation")[0].performScrollTo().assertIsDisplayed()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            runBlocking { AutPlayRuntime.database(context).journalDao().eventCount() == 1 }
        }
    }

    @Test
    fun activeOwnerSwitchClearsPreviousHomeFeedBeforeRenderingNewOwner() = runBlocking {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openHome()
        scrollHomeTo(hasTestTag("home-recommendation"))

        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = ServerProfileId(PROFILE),
                activeUserId = UserId(OTHER_USER),
                deviceId = DeviceId(OTHER_DEVICE),
                serverBaseUrl = "https://offline.test",
            ),
        )
        scrollHomeTo(hasText(context.getString(R.string.home_empty_recommendations)))
        assertTrue(composeRule.onAllNodesWithTag("home-recommendation").fetchSemanticsNodes().isEmpty())
        // The same recording remains in the local recently-added projection outside the
        // profile-owned recommendation card; standalone content must survive owner changes.
        scrollHomeTo(hasTestTag("home-recent"))
        composeRule.onNodeWithTag("home-recent")
            .assert(hasText("Local recommendation"))
            .assertIsDisplayed()
        Unit
    }

    private fun openHome() {
        val label = context.getString(R.string.nav_home)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(label).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onAllNodesWithText(label)[0].performClick()
    }

    private fun scrollHomeTo(matcher: SemanticsMatcher) {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            runCatching {
                composeRule.onNodeWithTag("home-product-list").performScrollToNode(matcher)
            }.isSuccess
        }
    }

    private suspend fun seed(db: AutPlayDatabase) {
        db.catalogProjectionDao().upsertRecordings(
            listOf(
                RecordingProjectionEntity(
                    localRecordingId = LOCAL_RECORDING,
                    serverRecordingId = RECORDING,
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
                RecordingProjectionEntity(
                    localRecordingId = NEW_RELEASE_RECORDING,
                    serverRecordingId = NEW_RELEASE_SERVER_RECORDING,
                    redirectServerRecordingId = null,
                    title = "Brand-new single",
                    normalizedTitle = "brand-new single",
                    displayArtist = "AutPlay Artist",
                    normalizedArtist = "autplay artist",
                    artistCreditJson = "{}",
                    durationMs = 175_000,
                    recordingKind = "SONG",
                    versionText = null,
                    explicitState = 0,
                    artworkRef = null,
                    catalogVersion = 8,
                    projectionUpdatedAtMs = NOW,
                ),
            ),
        )
        db.catalogProjectionDao().upsertReleases(
            listOf(
                ReleaseProjectionEntity(
                    localReleaseId = RELEASE,
                    serverReleaseId = null,
                    serverReleaseGroupId = null,
                    title = "Relevant release",
                    displayArtist = "AutPlay Artist",
                    releaseDateText = "2026-08-17",
                    releaseType = "ALBUM",
                    artworkRef = null,
                    catalogVersion = 7,
                    projectionUpdatedAtMs = NOW,
                    isDeleted = false,
                ),
            ),
        )
        db.catalogProjectionDao().upsertReleaseTracks(
            listOf(
                ReleaseTrackProjectionEntity(
                    localReleaseTrackId = RELEASE_TRACK,
                    serverReleaseTrackId = null,
                    localReleaseId = RELEASE,
                    // This recording has no UserTrackRef. The release is relevant because the
                    // active profile owns another recording by the same normalized artist.
                    localRecordingId = NEW_RELEASE_RECORDING,
                    mediumPosition = 1,
                    sequenceNo = 1,
                    numberText = "1",
                    creditedTitle = "Local recommendation",
                    creditedArtist = "AutPlay Artist",
                    durationMs = 180_000,
                ),
            ),
        )
        db.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = TRACK,
                serverUserTrackRefId = null,
                localRecordingId = LOCAL_RECORDING,
                serverRecordingId = RECORDING,
                resolutionStatus = "RESOLVED",
                rawTitle = "Local recommendation",
                rawArtist = "AutPlay Artist",
                rawAlbum = "Relevant release",
                rawDurationMs = 180_000,
                resolutionConfidence = 1.0,
                syncState = "SYNCED",
                serverRowVersion = 1,
                lastLocalSequence = 0,
                createdAtMs = NOW,
                updatedAtMs = NOW,
                deletedAtMs = null,
                serverProfileId = PROFILE,
            ),
        )
        db.libraryDao().upsertEntry(
            LibraryEntryEntity(
                localLibraryEntryId = LIBRARY_ENTRY,
                serverLibraryEntryId = null,
                localUserTrackRefId = TRACK,
                addedAtMs = NOW,
                source = "IMPORT",
                availabilityStatus = "LOCAL",
                syncState = "SYNCED",
                serverRowVersion = 1,
                lastLocalSequence = 0,
                removedAtMs = null,
                updatedAtMs = NOW,
                serverProfileId = PROFILE,
            ),
        )
    }

    private fun pack(): RecommendationPackEntity {
        val raw = """{"payload_version":1,"offline_pack_id":"$PACK","recommendation_request_id":"$REQUEST","user_id":"$USER","device_id":"$DEVICE","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$EXPIRES,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":1,"exploration":0.1,"seed":42,"shadow":false},"items":[{"offline_pack_id":"$PACK","recording_id":"$RECORDING","source_rank":1,"pack_position":1,"section":"for_you","score":1.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":1,"raw_score":1.0,"provenance":{"kind":"explicit"}}]}]}"""
        val bytes = JsonCanonicalizer(raw).encodedString.toByteArray(StandardCharsets.UTF_8)
        return RecommendationPackEntity(
            offlinePackId = PACK,
            serverProfileId = PROFILE,
            ownerUserId = USER,
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
        const val RELEASE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        const val RELEASE_TRACK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        const val NEW_RELEASE_RECORDING = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        const val NEW_RELEASE_SERVER_RECORDING = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        const val CREATED = 1_000L
        const val NOW = 2_000L
        const val EXPIRES = 4_102_444_800_000L
        const val OTHER_USER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        const val OTHER_DEVICE = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        val BINDING = ClientEventBinding(UserId(USER), DeviceId(DEVICE), ServerProfileId(PROFILE))
    }
}
