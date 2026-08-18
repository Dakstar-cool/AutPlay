package app.autplay.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.application.server.RemoteImportEntry
import app.autplay.application.server.RemoteImportReport
import org.junit.Rule
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.atomic.AtomicBoolean

@RunWith(AndroidJUnit4::class)
class ServerFeaturesScreenTest {
    @get:Rule val compose = createComposeRule()

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
            MaterialTheme {
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
                        onRefreshHealth = {}, onRefreshLibrary = {}, onSearch = {},
                        onChooseServerImport = {}, onRefreshImport = {},
                        onLoadNextImport = { nextLoaded.set(true) }, onCancelImport = {},
                        onResumeImport = {}, onReviewImport = { _, _ -> },
                        onUploadSelectedTrack = {}, onCancelUpload = {},
                        onRecommendations = {}, onExactReplay = {}, onAlgorithmicReplay = {},
                    )
                }
            }
        }

        compose.onNodeWithText("Candidate evidence is not exposed by this server response; blind accept is disabled.")
            .performScrollTo()
            .assertIsDisplayed()
        compose.onNodeWithText("Keep unresolved").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Create Recording").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Load next import rows").performScrollTo().performClick()
        assertTrue(nextLoaded.get())
        compose.onAllNodesWithText("Accept", substring = true).assertCountEquals(0)
    }
}
