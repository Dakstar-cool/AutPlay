package app.autplay.ui

import app.autplay.application.download.DownloadPresentationAction
import app.autplay.application.download.allowedDownloadPresentationActions
import app.autplay.application.importing.ImportJobControlAction
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FunctionalParityPoliciesTest {
    @Test fun importControlsFailClosedForEveryLifecycleState() {
        assertEquals(
            setOf(ImportJobControlAction.PAUSE, ImportJobControlAction.CANCEL),
            allowedImportJobControlActions("PENDING"),
        )
        assertEquals(
            setOf(ImportJobControlAction.PAUSE, ImportJobControlAction.CANCEL),
            allowedImportJobControlActions("REVIEW_REQUIRED"),
        )
        assertEquals(
            setOf(ImportJobControlAction.RESUME, ImportJobControlAction.CANCEL),
            allowedImportJobControlActions("PAUSED"),
        )
        listOf("COMPLETED", "CANCELLED", "FAILED", null).forEach {
            assertTrue(allowedImportJobControlActions(it).isEmpty())
        }
    }

    @Test fun downloadControlsRespectMedia3OwnershipStates() {
        listOf("REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED").forEach {
            assertEquals(setOf(DownloadPresentationAction.CANCEL), allowedDownloadPresentationActions(it))
        }
        assertEquals(setOf(DownloadPresentationAction.PLAY), allowedDownloadPresentationActions("COMPLETED"))
        assertEquals(setOf(DownloadPresentationAction.RETRY), allowedDownloadPresentationActions("FAILED"))
        assertTrue(allowedDownloadPresentationActions("CANCELLED").isEmpty())
        assertTrue(allowedDownloadPresentationActions("UNKNOWN").isEmpty())
    }
}
