package app.autplay.work

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerWorkerPoliciesTest {
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
    }
}
