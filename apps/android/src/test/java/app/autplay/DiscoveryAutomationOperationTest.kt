package app.autplay

import app.autplay.application.server.RemoteDiscoveryPolicy
import app.autplay.ui.PendingDiscoveryOperation
import app.autplay.ui.discoveryPolicyEditorCanMutate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DiscoveryAutomationOperationTest {
    @Test
    fun unresolvedCommandReusesItsOperationAndBlocksDifferentIntent() {
        val pending = resolvePendingDiscoveryOperation(null, "POLICY|A", "operation-1")

        assertEquals(
            pending,
            resolvePendingDiscoveryOperation(pending, "POLICY|A", "operation-2"),
        )
        assertNull(resolvePendingDiscoveryOperation(pending, "RUN|B", "operation-3"))
        assertEquals(
            PendingDiscoveryOperation("RUN|B", "operation-4"),
            resolvePendingDiscoveryOperation(null, "RUN|B", "operation-4"),
        )
    }

    @Test
    fun policyEditorRequiresAnAuthoritativeSnapshot() {
        assertEquals(
            false,
            discoveryPolicyEditorCanMutate(
                isBound = true,
                snapshotLoaded = false,
                busy = false,
                selectedPolicy = null,
            ),
        )
    }

    @Test
    fun policyEditorPreservesUnknownFutureModes() {
        assertEquals(
            false,
            discoveryPolicyEditorCanMutate(
                isBound = true,
                snapshotLoaded = true,
                busy = false,
                selectedPolicy = policy("FUTURE_DISCOVERY_MODE", "FUTURE_IMPORT_MODE"),
            ),
        )
        assertEquals(
            true,
            discoveryPolicyEditorCanMutate(
                isBound = true,
                snapshotLoaded = true,
                busy = false,
                selectedPolicy = policy("SCHEDULED", "REVIEW_REQUIRED"),
            ),
        )
    }

    private fun policy(discoveryMode: String, importMode: String) = RemoteDiscoveryPolicy(
        policyId = "44444444-4444-4444-8444-444444444444",
        canonicalArtistId = "22222222-2222-4222-8222-222222222222",
        providerArtistId = "20",
        discoveryMode = discoveryMode,
        importMode = importMode,
        automationEnabled = true,
        revision = 1,
        lastCheckedAt = null,
        nextEligibleAt = null,
    )
}
