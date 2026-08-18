package app.autplay

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.NonSecretSettings
import app.autplay.application.importing.CreateLocalImportCommand
import app.autplay.application.importing.ImportResolverState
import app.autplay.application.importing.ImportRowInput
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.importing.MatchCandidateInput
import app.autplay.application.importing.RecordShadowEvaluationCommand
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecordingProjectionEntity
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
            ),
        )
    }

    @After
    fun tearDown() = runBlocking {
        scenario?.close()
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(NonSecretSettings())
    }

    @Test
    fun offlineCommandRemainsVisibleAfterActivityRecreation() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        composeRule.onNodeWithText("Library").performClick()
        composeRule.onNodeWithText("0 local track(s)").assertIsDisplayed()
        composeRule.onNodeWithText("Add offline sample").performClick()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("1 local track(s)").fetchSemanticsNodes().isNotEmpty()
        }

        scenario?.recreate()

        composeRule.onNodeWithText("Library").performClick()
        composeRule.onNodeWithText("1 local track(s)").assertIsDisplayed()
    }

    @Test
    fun freshLaunchWithoutServerConfigurationPersistsStandaloneChangeAcrossRecreation() = runBlocking {
        applicationNonSecretSettingsStore(context).update(NonSecretSettings())
        scenario = ActivityScenario.launch(MainActivity::class.java)

        composeRule.onNodeWithText("Library").performClick()
        composeRule.onNodeWithText("Standalone changes stay local until you choose a server profile").assertIsDisplayed()
        composeRule.onNodeWithText("Add offline sample").performClick()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("1 local track(s)").fetchSemanticsNodes().isNotEmpty()
        }
        scenario?.recreate()
        composeRule.onNodeWithText("Library").performClick()
        composeRule.onNodeWithText("1 local track(s)").assertIsDisplayed()
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
        composeRule.onNodeWithText("More").performClick()
        composeRule.onNodeWithText("Import review").performScrollTo().performClick()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("Import REVIEW_REQUIRED: 1 row(s)").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("Import REVIEW_REQUIRED: 1 row(s)").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText("Review Ambiguous").performScrollTo().performClick()
        composeRule.onAllNodesWithText("Hard-conflict warning: [\"VERSION_MARKER_CONFLICT\"]")[0]
            .performScrollTo()
            .assertIsDisplayed()
        composeRule.onNodeWithText("Accept candidate 1").performScrollTo().performClick()
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText("Review 0 · Resolved 1 · No match 0 · Unresolved 0 · Failed 0")
                .fetchSemanticsNodes().isNotEmpty()
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
}
