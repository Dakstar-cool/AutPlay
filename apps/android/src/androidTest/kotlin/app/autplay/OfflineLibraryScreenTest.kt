package app.autplay

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.CURRENT_ONBOARDING_REVISION
import app.autplay.application.importing.CreateLocalImportCommand
import app.autplay.application.importing.ImportResolverState
import app.autplay.application.importing.ImportRowInput
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.importing.MatchCandidateInput
import app.autplay.application.importing.RecordShadowEvaluationCommand
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OfflineLibraryScreenTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before
    fun setUp() = runBlocking {
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = ServerProfileId("51111111-1111-4111-8111-111111111111"),
                activeUserId = UserId("31111111-1111-4111-8111-111111111111"),
                deviceId = DeviceId("41111111-1111-4111-8111-111111111111"),
                serverBaseUrl = "https://offline.test",
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
    }

    @After
    fun tearDown() = runBlocking {
        scenario?.close()
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
    }

    @Test
    fun libraryRowRemainsVisibleAfterActivityRecreation() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        assertEmptyLibrary()
        runBlocking { seedLibraryTrack(PROFILE) }
        assertSingleTrackVisible()

        scenario?.recreate()

        openLibrary()
        assertSingleTrackVisible()
    }

    @Test
    fun freshLaunchWithoutServerConfigurationPersistsStandaloneChangeAcrossRecreation() = runBlocking {
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
        scenario = ActivityScenario.launch(MainActivity::class.java)

        openLibrary()
        assertEmptyLibrary()
        seedLibraryTrack("legacy-unscoped")
        assertSingleTrackVisible()
        scenario?.recreate()
        openLibrary()
        assertSingleTrackVisible()
        Unit
    }

    @Test
    fun libraryTrackTapCreatesTheExactQueueAndKeepsLibraryOpen() = runBlocking {
        seedLibraryTrack(PROFILE)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        assertSingleTrackVisible()
        composeRule.onNodeWithTag("library-track-$TRACK").performClick()

        composeRule.waitUntil(timeoutMillis = 10_000) {
            runBlocking {
                val dao = AutPlayRuntime.database(context).queueDao()
                val snapshot = dao.activeSnapshotOnce() ?: return@runBlocking false
                val entries = dao.entries(snapshot.queueSnapshotId, 10)
                entries.size == 1 && entries.single().localUserTrackRefId == TRACK &&
                    snapshot.currentEntryId == entries.single().queueEntryId
            }
        }
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        assertSingleTrackVisible()
        // This unavailable fixture proves queue activation and navigation, not audio playback.
        Unit
    }

    @Test
    fun libraryDownloadShortcutOpensDownloadsAndRestoresThatRoute() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        val downloads = context.getString(R.string.nav_downloads)
        scrollLibraryTo(hasText(downloads) and hasClickAction())
        composeRule.onNode(hasText(downloads) and hasClickAction()).performClick()
        assertDownloadsRoute()

        scenario?.recreate()

        assertDownloadsRoute()
    }

    @Test
    fun completedLocalSearchRestoresResultsAfterActivityRecreation() = runBlocking {
        seedLibraryTrack(PROFILE)
        scenario = ActivityScenario.launch(MainActivity::class.java)

        waitForNavigation(context.getString(R.string.nav_search))
            .performClick()
        composeRule.onNode(hasSetTextAction()).performTextInput("Offline sample")
        composeRule.onNodeWithTag("local-search-submit").performClick()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("Offline sample").fetchSemanticsNodes().isNotEmpty()
        }

        scenario?.recreate()

        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("Offline sample").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNode(hasSetTextAction()).assertTextContains("Offline sample")
        Unit
    }

    @Test
    fun importScreenExplainsAmbiguousCandidatesAndAppliesManualAccept() = runBlocking {
        val database = AutPlayDatabase.open(context)
        val repository = LocalImportReviewRepository(database)
        database.catalogProjectionDao().upsertRecordings(listOf(recording("71111111-1111-4111-8111-111111111111", "Studio"), recording("72111111-1111-4111-8111-111111111111", "Live")))
        val job = repository.createOrResume(
            CreateLocalImportCommand(
                serverProfileId = "51111111-1111-4111-8111-111111111111",
                adapterId = "ui-fixture",
                adapterVersion = "1",
                envelopeVersion = 1,
                inputSha256 = "c".repeat(64),
                rows = listOf(ImportRowInput("row:0", 0, "Ambiguous", "Artist", rawProvenanceJson = "{\"schema_version\":1}")),
                nowMs = 1,
            ),
        )
        val entry = repository.entriesOnce(job.importJobId).single()
        repository.recordShadowEvaluation(
            RecordShadowEvaluationCommand(
                importEntryId = entry.importEntryId,
                idempotencyKey = "ui-eval",
                resolverState = ImportResolverState.REVIEW_REQUIRED,
                evidenceMode = "METADATA_ONLY",
                matcherVersion = "ui-shadow/1",
                explanationJson = "{\"schema_version\":1,\"reason_code\":\"VERSION_CONFLICT\"}",
                candidates = listOf(candidate("71111111-1111-4111-8111-111111111111", 1, "Studio"), candidate("72111111-1111-4111-8111-111111111111", 2, "Live")),
                nowMs = 2,
            ),
        )
        database.close()

        scenario = ActivityScenario.launch(MainActivity::class.java)
        val settingsLabel = context.getString(R.string.action_open_settings)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithContentDescription(settingsLabel).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithContentDescription(settingsLabel).performClick()
        composeRule.onNodeWithText(context.getString(R.string.settings_music_navigation)).performScrollTo().performClick()
        composeRule.onNodeWithText(context.getString(R.string.nav_import_review)).performScrollTo().performClick()
        val importSummary = context.getString(R.string.import_summary, 1)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(importSummary).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText(importSummary).performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.import_review_item)).performScrollTo().performClick()
        val conflict = context.getString(R.string.import_candidate_conflict)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(conflict).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onAllNodesWithText(conflict)[0]
            .performScrollTo()
            .assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.import_accept_candidate, 1)).performScrollTo().performClick()
        val resolvedSummary = context.getString(R.string.import_review_summary, 0, 1, 0, 0)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(resolvedSummary).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun waitForNavigation(label: String) = composeRule.onNode(hasText(label) and hasClickAction()).also {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(label).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun candidate(id: String, rank: Int, title: String) = MatchCandidateInput(
        localRecordingId = id,
        rank = rank,
        rawScore = 0.9,
        confidence = 0.9,
        evidenceTier = "T1",
        titleSnapshot = title,
        artistSnapshot = "Artist",
        featureEvidenceJson = "[{\"feature\":\"title_similarity\",\"present\":true,\"value\":1.0,\"extractor_version\":\"title/1\"}]",
        hardConflictsJson = "[\"VERSION_MARKER_CONFLICT\"]",
        candidateOriginsJson = "[{\"generator\":\"metadata\",\"rank\":$rank}]",
        extractorVersionsJson = "{\"schema_version\":1}",
    )

    private fun openLibrary() {
        waitForNavigation(context.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        composeRule.onNode(
            hasText(context.getString(R.string.library_section_offline)) and hasClickAction(),
        ).assertDoesNotExist()
        composeRule.onNode(
            hasText(context.getString(R.string.library_filter_unavailable)) and hasClickAction(),
        ).assertDoesNotExist()
    }

    private fun scrollLibraryTo(matcher: SemanticsMatcher) {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            runCatching {
                composeRule.onNodeWithTag("library-product-list").performScrollToNode(matcher)
            }.isSuccess
        }
    }

    private fun assertEmptyLibrary() {
        val empty = context.getString(R.string.library_empty_body)
        scrollLibraryTo(hasText(empty))
        composeRule.onNodeWithText(empty).assertIsDisplayed()
        composeRule.onAllNodesWithTag("library-track-$TRACK").assertCountEquals(0)
    }

    private fun assertSingleTrackVisible() {
        scrollLibraryTo(hasTestTag("library-track-$TRACK"))
        composeRule.onAllNodesWithTag("library-track-$TRACK").assertCountEquals(1)
        composeRule.onNodeWithTag("library-track-$TRACK")
            .assert(hasText("Offline sample"))
            .assert(hasText("AutPlay test"))
            .assertIsDisplayed()
    }

    private fun assertDownloadsRoute() {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithTag("downloads-list").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag("downloads-list").assertIsDisplayed()
        val empty = context.getString(R.string.downloads_empty)
        composeRule.onNodeWithTag("downloads-list").performScrollToNode(hasText(empty))
        composeRule.onNodeWithText(empty).assertIsDisplayed()
        composeRule.onNodeWithTag("library-product-list").assertDoesNotExist()
    }

    private suspend fun seedLibraryTrack(profileId: String) {
        val database = AutPlayRuntime.database(context)
        database.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = TRACK,
                serverUserTrackRefId = null,
                localRecordingId = null,
                serverRecordingId = null,
                resolutionStatus = "UNRESOLVED",
                rawTitle = "Offline sample",
                rawArtist = "AutPlay test",
                rawAlbum = null,
                rawDurationMs = null,
                resolutionConfidence = null,
                syncState = "LOCAL_ONLY",
                serverRowVersion = null,
                lastLocalSequence = 0,
                createdAtMs = 1,
                updatedAtMs = 1,
                deletedAtMs = null,
                serverProfileId = profileId,
            ),
        )
        database.libraryDao().upsertEntry(
            LibraryEntryEntity(
                localLibraryEntryId = ENTRY,
                serverLibraryEntryId = null,
                localUserTrackRefId = TRACK,
                addedAtMs = 1,
                source = "TEST_FIXTURE",
                availabilityStatus = "UNAVAILABLE",
                syncState = "LOCAL_ONLY",
                serverRowVersion = null,
                lastLocalSequence = 0,
                removedAtMs = null,
                updatedAtMs = 1,
                serverProfileId = profileId,
            ),
        )
    }

    private fun recording(id: String, title: String) = RecordingProjectionEntity(
        localRecordingId = id,
        serverRecordingId = null,
        redirectServerRecordingId = null,
        title = title,
        normalizedTitle = title.lowercase(),
        displayArtist = "Artist",
        normalizedArtist = "artist",
        artistCreditJson = "{\"schema_version\":1}",
        durationMs = 180_000,
        recordingKind = "SONG",
        versionText = null,
        explicitState = 0,
        artworkRef = null,
        catalogVersion = 0,
        projectionUpdatedAtMs = 1,
    )

    private companion object {
        const val PROFILE = "51111111-1111-4111-8111-111111111111"
        const val TRACK = "91111111-1111-4111-8111-111111111111"
        const val ENTRY = "92111111-1111-4111-8111-111111111111"
    }
}
