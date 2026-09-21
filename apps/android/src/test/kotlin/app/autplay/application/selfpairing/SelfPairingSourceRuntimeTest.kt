package app.autplay.application.selfpairing

import app.autplay.data.security.CredentialStore
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.time.Instant
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class SelfPairingSourceRuntimeTest {
    @Test fun neverDeliveredAgedDecisionRequiresAnotherReviewAndCanExpire() = runBlocking {
        for (elapsed in listOf(180L, 1000L)) {
            val h = Harness()
            val runtime = h.runtime()
            runtime.start(); h.claimed = true; runtime.poll()
            val code = (runtime.state.value as SelfPairingSourceState.Ready).status.comparisonCode
            h.dropDecision = true
            runtime.decide("APPROVE", code)
            assertTrue(runtime.state.value is SelfPairingSourceState.Blocked)
            h.clock = h.clock.plusSeconds(elapsed)
            val resumed = h.runtime()
            resumed.resume()
            val ready = resumed.state.value as SelfPairingSourceState.Ready
            assertEquals(if (elapsed > 900) "EXPIRED" else "CLAIMED", ready.status.state)
            assertEquals(1, h.decisions.size)
            assertFalse(h.approved)
            if (elapsed > 900) {
                resumed.dismiss()
                assertNull(h.store.values[SelfPairingRole.SOURCE.slot])
            }
        }
    }
    @Test fun neverDeliveredStartHasBoundedExpiryAndCanBeClosed() = runBlocking {
        val h = Harness()
        h.neverDelivered = true
        h.runtime().start()
        h.clock = h.clock.plusSeconds(1800)
        val runtime = h.runtime()
        runtime.resume()
        assertEquals(SelfPairingSourceState.ExpiredStart, runtime.state.value)
        runtime.dismiss()
        assertNull(h.store.values[SelfPairingRole.SOURCE.slot])
    }
    @Test fun sourceJournalPreservesLiveCredentialsAndDecisionReplaysAfterRestart() = runBlocking {
        val h = Harness()
        val active = "existing-live-credentials".toByteArray()
        h.store.values[h.context.profile] = active.copyOf()
        val runtime = h.runtime()
        runtime.start()
        val ready = runtime.state.value as SelfPairingSourceState.Ready
        assertNotNull(ready.qrPayload)
        assertFalse(ready.toString().contains("rendezvous_secret"))
        assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
        assertArrayEquals(active, h.store.values[h.context.profile])
        h.claimed = true
        runtime.poll()
        val claimed = runtime.state.value as SelfPairingSourceState.Ready
        h.loseDecision = true
        runtime.decide("APPROVE", claimed.status.comparisonCode)
        assertTrue(runtime.state.value is SelfPairingSourceState.Blocked)
        val resumed = h.runtime()
        resumed.resume()
        assertEquals("APPROVED", (resumed.state.value as SelfPairingSourceState.Ready).status.state)
        assertEquals(2, h.decisions.size)
        assertEquals(h.decisions.first(), h.decisions.last())
        assertArrayEquals(active, h.store.values[h.context.profile])
    }

    @Test fun changedSourceFamilyPreventsApprovalAndAllowsClosingOnlyLocalJournal() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.start(); h.claimed = true; runtime.poll()
        val code = (runtime.state.value as SelfPairingSourceState.Ready).status.comparisonCode
        h.context = h.context.copy(familyId = "10000000-0000-4000-8000-000000000007")
        runtime.decide("APPROVE", code)
        assertEquals(0, h.decisions.size)
        assertEquals(SelfPairingSourceState.Blocked("SELF_PAIRING_SOURCE_CHANGED"), runtime.state.value)
        runtime.dismiss()
        assertNull(h.store.values[SelfPairingRole.SOURCE.slot])
    }

    @Test fun staleRevisionDoesNotAutomaticallyApproveTheNextClaimSnapshot() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.start(); h.claimed = true; runtime.poll()
        val code = (runtime.state.value as SelfPairingSourceState.Ready).status.comparisonCode
        h.staleDecision = true
        runtime.decide("APPROVE", code)
        assertEquals(1, h.decisions.size)
        h.staleDecision = false
        runtime.resume()
        assertEquals(1, h.decisions.size)
        assertEquals("CLAIMED", (runtime.state.value as SelfPairingSourceState.Ready).status.state)
    }

    private class Harness {
        val store = Store()
        var context = SelfPairingSourceBinding(ServerProfileId(ID), USER, DEVICE, FAMILY,
            SelfPairingIdentity(ID, 1, "a".repeat(64), "https://api.test.invalid", "https://stream.test.invalid"))
        var clock = Instant.now()
        val expires = clock.plusSeconds(900)
        var neverDelivered = false
        var ceremony = ""
        var claimed = false
        var approved = false
        var approval = ""
        var loseDecision = false
        var staleDecision = false
        var dropDecision = false
        val decisions = mutableListOf<String>()
        fun runtime() = SelfPairingSourceRuntime(store, SelfPairingSourceContext { context }, transport, { clock })
        private val transport = object : SelfPairingTransport {
            override suspend fun recipient(identity: SelfPairingIdentity, kind: String, ceremonyId: String, secret: ByteArray, request: ByteArray): JsonObject = error("unexpected recipient")
            override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, kind: String, ceremonyId: String, request: ByteArray?): JsonObject {
                assertNotNull(store.values[SelfPairingRole.SOURCE.slot])
                if (neverDelivered) {
                    if (kind == "start") throw IOException("not delivered")
                    throw SelfPairingRemoteFailure("self_pairing_unavailable")
                }
                ceremony = ceremonyId
                if (kind == "decision") {
                    val body = requireNotNull(request).toString(Charsets.UTF_8)
                    decisions += body
                    if (dropDecision) throw IOException("not delivered")
                    if (staleDecision) throw SelfPairingRemoteFailure("self_pairing_revision_conflict")
                    approved = true
                    approval = SelfPairingJson.parse(request).text("operation_id")
                    if (loseDecision) { loseDecision = false; throw IOException("lost") }
                }
                return JsonObject(mapOf(
                    "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
                    "ceremony_id" to JsonPrimitive(ceremony), "state" to JsonPrimitive(if (!expires.isAfter(clock)) "EXPIRED" else if (approved) "APPROVED" else if (claimed) "CLAIMED" else "OPEN"),
                    "revision" to JsonPrimitive(if (approved) 3 else if (claimed) 2 else 1),
                    "expires_at" to JsonPrimitive(expires.toString()), "retry_after_seconds" to JsonPrimitive(2),
                    "account_id" to JsonPrimitive(USER), "account_label" to JsonPrimitive("Existing user"),
                ) + (if (claimed) mapOf(
                    "claim_id" to JsonPrimitive(CLAIM), "claim_request_sha256" to JsonPrimitive("c".repeat(64)),
                    "device_name" to JsonPrimitive("New phone"), "device_key_thumbprint_sha256" to JsonPrimitive("b".repeat(64)),
                    "comparison_code" to JsonPrimitive(SelfPairingProof.comparisonCode(identity, ceremony, "c".repeat(64), "b".repeat(64))),
                ) else emptyMap()) + (if (approved) mapOf("approval_operation_id" to JsonPrimitive(approval)) else emptyMap()))
            }
        }
    }
    private class Store : CredentialStore {
        val values = mutableMapOf<ServerProfileId, ByteArray>()
        override suspend fun read(profileId: ServerProfileId): ByteArray? = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
    }
    private companion object {
        const val ID = "10000000-0000-4000-8000-000000000001"
        const val USER = "10000000-0000-4000-8000-000000000002"
        const val DEVICE = "10000000-0000-4000-8000-000000000003"
        const val FAMILY = "10000000-0000-4000-8000-000000000004"
        const val CLAIM = "10000000-0000-4000-8000-000000000005"
    }
}
