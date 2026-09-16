package app.autplay.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.server.RemoteImportEntry
import app.autplay.application.server.RemoteImportReport
import app.autplay.application.server.RemoteLibraryEntry
import app.autplay.application.server.ServerRecommendationItem
import app.autplay.application.server.ServerRecommendationResult
import org.junit.Rule
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.atomic.AtomicBoolean

@RunWith(AndroidJUnit4::class)
class ServerFeaturesScreenTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun candidateAcceptanceIsAbsentWhenServerDoesNotExposeEvidence() {
        val nextLoaded = AtomicBoolean(false)
        val entry = RemoteImportEntry(
            sourceRowKey = "row-1",
            importEntryId = "11111111-1111-4111-8111-111111111111",
            status = "PENDING",
            resolverState = "DEFERRED_EVIDENCE",
            decisionId = "22222222-2222-4222-8222-222222222222",
            candidateCount = 2,
            errorCode = null,
        )
        compose.setContent {
            androidx.compose.runtime.CompositionLocalProvider(LocalDeveloperMode provides true) { MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ServerFeaturesScreen(
                        isBound = true,
                        selectedTrackLabel = null,
                        selectedTrackUploadEligible = false,
                        state = ServerFeaturesUiState(
                            importReport = RemoteImportReport(
                                importJobId = "33333333-3333-4333-8333-333333333333",
                                state = "RUNNING",
                                progressCurrent = 1,
                                progressTotal = 2,
                                counts = mapOf("REVIEW_REQUIRED" to 1),
                                entries = listOf(entry),
                                nextAfter = "page-2",
                            ),
                        ),
                        actions = ServerFeaturesActions(
                            refreshHealth = {}, refreshLibrary = {}, search = {},
                            chooseServerImport = {}, refreshImport = {},
                            loadNextImport = { nextLoaded.set(true) }, cancelImport = {},
                            resumeImport = {}, reviewImport = { _, _ -> },
                            uploadSelectedTrack = {}, cancelUpload = {},
                            recommendations = {}, exactReplay = {}, algorithmicReplay = {},
                        ),
                    )
                }
            } }
        }

        compose.onNodeWithText(context.getString(R.string.server_import_needs_choice))
            .performScrollTo()
            .assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.import_keep_unresolved)).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.import_create_new_track)).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.action_show_more)).performScrollTo().performClick()
        assertTrue(nextLoaded.get())
        compose.onAllNodesWithText("Accept", substring = true).assertCountEquals(0)
    }

    @Test
    fun searchAndRecommendationRowsExposeAuthoritativeReplayWithTruthfulLabel() {
        val exact = AtomicBoolean(false)
        val algorithmic = AtomicBoolean(false)
        compose.setContent {
            androidx.compose.runtime.CompositionLocalProvider(LocalDeveloperMode provides true) { MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ServerFeaturesScreen(
                        isBound = true,
                        selectedTrackLabel = null,
                        selectedTrackUploadEligible = false,
                        state = ServerFeaturesUiState(
                            searchAttempted = true,
                            searchResults = listOf(RemoteLibraryEntry("library-entry", "track-ref", "UPLOAD", "AVAILABLE", 1)),
                            recommendationAttempted = true,
                            recommendation = ServerRecommendationResult(
                                requestId = "request-identity",
                                replay = "exact",
                                items = listOf(ServerRecommendationItem("recording", 1, .9, "recent_affinity", "for_you")),
                            ),
                        ),
                        actions = noOpActions(
                            exactReplay = { exact.set(true) },
                            algorithmicReplay = { algorithmic.set(true) },
                        ),
                    )
                }
            } }
        }

        compose.onNodeWithText("UPLOAD · AVAILABLE").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("for_you · recent_affinity").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.server_recommendations_home)).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.server_recommendations_repeat)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.server_recommendations_refresh)).performScrollTo().performClick()
        assertTrue(exact.get())
        assertTrue(algorithmic.get())
    }

    private fun noOpActions(
        exactReplay: () -> Unit = {},
        algorithmicReplay: () -> Unit = {},
    ) = ServerFeaturesActions(
        refreshHealth = {}, refreshLibrary = {}, search = {}, chooseServerImport = {}, refreshImport = {},
        loadNextImport = {}, cancelImport = {}, resumeImport = {}, reviewImport = { _, _ -> },
        uploadSelectedTrack = {}, cancelUpload = {}, recommendations = {}, exactReplay = exactReplay,
        algorithmicReplay = algorithmicReplay,
    )
}
