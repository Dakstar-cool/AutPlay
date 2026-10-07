package app.autplay.work

import java.io.IOException
import java.util.concurrent.CancellationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerWorkerPoliciesTest {
    @Test
    fun syncWorkerSeparatesNetworkRetryTerminalAuthAndCancellation() {
        assertEquals(
            SyncWorkerErrorDisposition.RETRY,
            syncWorkerErrorDisposition(IOException("offline")),
        )
        assertEquals(
            SyncWorkerErrorDisposition.FAILURE,
            syncWorkerErrorDisposition(IllegalStateException("SESSION_REQUIRED")),
        )
        assertEquals(
            SyncWorkerErrorDisposition.CANCEL,
            syncWorkerErrorDisposition(CancellationException("cancelled")),
        )
    }

    @Test
    fun vaultIngestStatesPollUntilRealServerTerminalState() {
        assertEquals(VaultUploadOutcome.POLL, vaultUploadOutcome("SEALED"))
        assertEquals(VaultUploadOutcome.POLL, vaultUploadOutcome("PROCESSING"))
        assertEquals(VaultUploadOutcome.POLL, vaultUploadOutcome("COMMIT_PREPARED"))
        assertEquals(VaultUploadOutcome.SUCCESS, vaultUploadOutcome("COMMITTED"))
        assertEquals(VaultUploadOutcome.SUCCESS, vaultUploadOutcome("REUSED"))
        assertEquals(VaultUploadOutcome.FAILURE, vaultUploadOutcome("FAILED"))
        assertEquals(VaultUploadOutcome.FAILURE, vaultUploadOutcome("QUARANTINED"))
    }

    @Test
    fun pausedImportPollingRequiresAnExplicitForegroundRefresh() {
        assertTrue(shouldScheduleRemoteImport("RUNNING", null))
        assertFalse(shouldScheduleRemoteImport("RUNNING", "IMPORT_POLLING_PAUSED"))
        assertFalse(shouldScheduleRemoteImport("RUNNING", "IMPORT_STATUS_UNAVAILABLE"))
        assertFalse(shouldScheduleRemoteImport("COMPLETED", null))
        assertFalse(shouldScheduleRemoteImport("RUNNING", "SESSION_REQUIRED"))
        assertFalse(shouldScheduleRemoteImport("RUNNING", "SERVER_PROFILE_NOT_ACTIVE"))
    }
}
