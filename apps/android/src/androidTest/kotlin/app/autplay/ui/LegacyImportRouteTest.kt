package app.autplay.ui

import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import app.autplay.application.importing.ImportJobControlAction
import app.autplay.data.local.entity.LocalImportJobEntity
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class LegacyImportRouteTest {
    @get:Rule val compose = createComposeRule()

    @Test fun pausedJobShowsResumeAndConfirmedCancelWithoutDuplicateSubmission() {
        var submitted: ImportJobControlAction? = null
        compose.setContent {
            AutPlayTheme {
                LegacyImportRoute(
                    state = LegacyImportRouteState(
                        job = job("PAUSED"),
                        items = emptyList(),
                        selectedItem = null,
                        candidates = emptyList(),
                        pendingControl = ImportJobControlAction.RESUME,
                    ),
                    actions = LegacyImportRouteActions({}, {}, { _, _ -> }, { submitted = it }),
                )
            }
        }

        compose.onNodeWithTag("import-pause").assertDoesNotExist()
        compose.onNodeWithTag("import-resume").assertIsNotEnabled()
        compose.onNodeWithTag("import-cancel").assertIsNotEnabled()
        compose.runOnIdle { assertEquals(null, submitted) }
    }

    @Test fun cancellationRequiresExplicitConfirmation() {
        var submitted: ImportJobControlAction? = null
        compose.setContent {
            AutPlayTheme {
                LegacyImportRoute(
                    LegacyImportRouteState(job("PENDING"), emptyList(), null, emptyList()),
                    LegacyImportRouteActions({}, {}, { _, _ -> }, { submitted = it }),
                )
            }
        }
        compose.onNodeWithTag("import-cancel").performClick()
        compose.runOnIdle { assertEquals(null, submitted) }
        compose.onNodeWithTag("import-cancel-confirm").performClick()
        compose.runOnIdle { assertEquals(ImportJobControlAction.CANCEL, submitted) }
    }

    private fun job(state: String) = LocalImportJobEntity(
        importJobId = "job",
        serverProfileId = "legacy-unscoped",
        adapterId = "fixture",
        adapterVersion = "1",
        envelopeVersion = 1,
        inputSha256 = "a".repeat(64),
        inputDigestVerified = true,
        sourceUri = null,
        persistedUriPermission = false,
        sourceAvailability = "AVAILABLE",
        state = state,
        checkpointPosition = 0,
        totalEntries = 2,
        reviewRequiredCount = 0,
        resolvedCount = 0,
        noMatchCount = 0,
        unresolvedCount = 0,
        failedCount = 0,
        reportJson = "{}",
        createdAtMs = 1,
        updatedAtMs = 1,
        completedAtMs = null,
    )
}
