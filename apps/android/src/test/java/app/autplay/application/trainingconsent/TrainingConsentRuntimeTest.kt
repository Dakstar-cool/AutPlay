package app.autplay.application.trainingconsent

import app.autplay.application.selfpairing.text
import app.autplay.application.selfpairing.integer
import app.autplay.data.security.CredentialStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class TrainingConsentRuntimeTest {
    @Test fun lostReplyReplaysExactIntentAndReportsCurrentPolicyAfterWithdrawal() = runBlocking {
        val h = Harness()
        val first = h.runtime()
        first.load()
        h.port.loseNext = true
        first.choose("GRANTED")
        assertTrue(first.state.value.pending)
        assertFalse(first.state.value.confirmed!!.granted)
        val pending = h.store.read()!!
        h.port.decision = "WITHDRAWN"; h.port.revision = 2
        val restarted = h.runtime()
        restarted.load()
        assertEquals("WITHDRAWN", restarted.state.value.confirmed!!.decision)
        assertEquals(2L, restarted.state.value.confirmed!!.revision)
        assertFalse(restarted.state.value.pending)
        assertNull(h.store.read())
        assertEquals(listOf(pending.text("operation_id"), pending.text("operation_id")), h.port.requests.map { it.text("operation_id") })
    }

    @Test fun delayedGrantResponseCannotOverwriteASupersedingWithdrawal() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayed = CompletableDeferred()
        val old = launch { runtime.choose("GRANTED") }
        h.port.started.await()
        val captured = h.port.captured!!
        runtime.choose("WITHDRAWN")
        assertEquals("WITHDRAWN", runtime.state.value.confirmed!!.decision)
        h.port.delayed!!.complete(captured)
        old.join()
        assertEquals("WITHDRAWN", runtime.state.value.confirmed!!.decision)
        assertNull(h.store.read())
    }

    @Test fun staleGrantIsAuthoritativelyRejectedThenRefreshLoadsPrivatePolicy() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.decision = "DENIED"; h.port.revision = 1
        runtime.choose("GRANTED")
        assertNull(h.store.read())
        assertNull(runtime.state.value.confirmed)
        assertTrue(runtime.state.value.error)
        runtime.load()
        assertEquals("DENIED", runtime.state.value.confirmed!!.decision)
    }

    @Test fun wrongAccountReplyRetainsUnknownOperationWithoutConfirmingIt() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.wrongAccount = true
        runtime.choose("GRANTED")
        assertTrue(runtime.state.value.pending)
        assertFalse(runtime.state.value.confirmed!!.granted)
        assertNotNull(h.store.read())
    }

    @Test fun bindingChangeDuringMutationCannotPublishToNewBindingOrEraseJournal() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayed = CompletableDeferred()
        val request = launch { runtime.choose("GRANTED") }
        h.port.started.await()
        h.current = false
        h.port.delayed!!.complete(h.port.captured!!)
        request.join()
        assertFalse(runtime.state.value.confirmed!!.granted)
        assertNotNull(h.store.read())
    }

    @Test fun delayedPolicyReadCannotReplaceANewerConfirmedMutation() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayedRead = CompletableDeferred()
        val read = launch { runtime.load() }
        h.port.readStarted.await()
        runtime.choose("GRANTED")
        h.port.delayedRead!!.complete(policy("UNKNOWN", 0))
        read.join()
        assertTrue(runtime.state.value.confirmed!!.granted)
    }

    @Test fun cancellationPropagatesAndMalformedJournalNeverContactsServer() = runBlocking {
        val h = Harness()
        h.credentials.failRead = CancellationException("test cancellation")
        try { h.runtime().load(); fail("Cancellation must propagate") }
        catch (_: CancellationException) { }
        h.credentials.failRead = null
        h.store.write( buildJsonObject { put("schema_version", 99) })
        val runtime = h.runtime()
        runtime.load()
        assertTrue(runtime.state.value.error)
        assertEquals(0, h.port.reads)
        assertTrue(h.port.requests.isEmpty())
        assertNotNull(h.store.read())
    }

    @Test fun delayedOldFailuresCannotRestorePendingAfterConfirmedWithdrawal() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayed = CompletableDeferred()
        val old = launch { runtime.choose("GRANTED") }
        h.port.started.await()
        runtime.choose("WITHDRAWN")
        h.port.delayed!!.completeExceptionally(java.io.IOException("late failure"))
        old.join()
        assertEquals("WITHDRAWN", runtime.state.value.confirmed!!.decision)
        assertFalse(runtime.state.value.pending)
        assertFalse(runtime.state.value.error)
        assertNull(h.store.read())
    }

    @Test fun delayedReadFailureCannotChangeNewerConfirmedMutation() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayedRead = CompletableDeferred()
        val old = launch { runtime.load() }
        h.port.readStarted.await()
        runtime.choose("GRANTED")
        h.port.delayedRead!!.completeExceptionally(java.io.IOException("late read failure"))
        old.join()
        assertTrue(runtime.state.value.confirmed!!.granted)
        assertFalse(runtime.state.value.error)
    }

    @Test fun sameAccountNewBindingCanPrivatelySupersedeOldIntentEvenIfReadIsOffline() = runBlocking {
        val h = Harness()
        val first = h.runtime()
        first.load(); h.port.loseNext = true; first.choose("GRANTED")
        val original = h.store.read()!!
        val offline = object : TrainingConsentPort {
            override suspend fun get(): JsonObject = throw java.io.IOException("offline")
            override suspend fun decide(request: JsonObject): JsonObject = throw java.io.IOException("offline")
        }
        val rebound = TrainingConsentRuntime(ACCOUNT, "binding-v2", h.store, offline) { true }
        rebound.load()
        assertTrue(rebound.state.value.pending)
        rebound.choose("WITHDRAWN")
        val privateIntent = h.store.read()!!
        assertEquals("WITHDRAWN", privateIntent.text("decision"))
        assertEquals("binding-v2", privateIntent.text("binding_key"))
        assertNotEquals(original.text("operation_id"), privateIntent.text("operation_id"))
        val resumed = TrainingConsentRuntime(ACCOUNT, "binding-v2", h.store, h.port) { true }
        resumed.load()
        assertEquals("WITHDRAWN", resumed.state.value.confirmed!!.decision)
        assertNull(h.store.read())
    }

    @Test fun anotherAccountUsesItsOwnJournalAndDoesNotReplayOrEraseOriginalIntent() = runBlocking {
        val h = Harness()
        val original = h.runtime()
        original.load(); h.port.loseNext = true; original.choose("GRANTED")
        val originalIntent = h.store.read()!!
        val other = "22222222-2222-4222-8222-222222222222"
        val slot = app.autplay.data.security.CredentialJournalSlots.trainingConsentProfile(ServerProfileId(ACCOUNT), other, "https://api.example.test")
        val journal = TrainingConsentJournalStore(h.credentials, slot)
        val otherPort = object : TrainingConsentPort {
            override suspend fun get() = policy("DENIED", 1, other)
            override suspend fun decide(request: JsonObject): JsonObject = error("No automatic mutation")
        }
        val runtime = TrainingConsentRuntime(other, "other-binding", journal, otherPort) { true }
        runtime.load()
        assertEquals("DENIED", runtime.state.value.confirmed!!.decision)
        assertFalse(runtime.state.value.pending)
        assertEquals(originalIntent, h.store.read())
        assertNull(journal.read())
    }

    @Test fun bindingStorageFailureDuringLoadingIsReportedWithoutEscaping() = runBlocking {
        val h = Harness()
        h.bindingFailure = java.io.IOException("binding storage unavailable")
        val runtime = h.runtime()
        runtime.load()
        assertTrue(runtime.state.value.error)
        assertFalse(runtime.state.value.busy)
        assertNull(runtime.state.value.confirmed)
    }

    @Test fun bindingStorageFailureAfterUnknownMutationKeepsDurableIntent() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.load()
        h.port.delayed = CompletableDeferred()
        val request = launch { runtime.choose("GRANTED") }
        h.port.started.await()
        val pending = h.store.read()!!
        h.bindingFailure = java.io.IOException("binding storage unavailable")
        h.port.delayed!!.completeExceptionally(java.io.IOException("lost reply"))
        request.join()
        assertTrue(runtime.state.value.error)
        assertTrue(runtime.state.value.pending)
        assertFalse(runtime.state.value.busy)
        assertEquals(pending, h.store.read())
    }

    @Test fun revisionOutsideCanonicalJsonIntegerDomainCannotConfirmPolicy() = runBlocking {
        for (revision in listOf(TRAINING_CONSENT_MAX_REVISION, TRAINING_CONSENT_MAX_REVISION + 1)) {
            val h = Harness()
            h.port.decision = "GRANTED"
            h.port.revision = revision
            val runtime = h.runtime()
            runtime.load()
            assertTrue(runtime.state.value.error)
            assertNull(runtime.state.value.confirmed)
            assertTrue(h.port.requests.isEmpty())
        }
    }

    @Test fun terminalPrivateRevisionNeverCreatesAnUnreplayableJournal() = runBlocking {
        val h = Harness()
        h.port.decision = "WITHDRAWN"
        h.port.revision = TRAINING_CONSENT_MAX_REVISION
        val runtime = h.runtime()
        runtime.load()
        assertEquals("WITHDRAWN", runtime.state.value.confirmed!!.decision)
        runtime.choose("GRANTED")
        runtime.choose("WITHDRAWN")
        assertNull(h.store.read())
        assertTrue(h.port.requests.isEmpty())
        assertFalse(runtime.state.value.busy)
        assertFalse(runtime.state.value.pending)
    }

    private class Harness {
        val credentials = Memory()
        val store = TrainingConsentJournalStore(credentials, app.autplay.data.security.CredentialJournalSlots.trainingConsentProfile(ServerProfileId(ACCOUNT), ACCOUNT, "https://api.example.test"))
        val port = Server()
        var current = true
        var bindingFailure: Exception? = null
        fun runtime() = TrainingConsentRuntime(ACCOUNT, "binding-v1", store, port) { bindingFailure?.let { throw it }; current }
    }
    private class Memory : CredentialStore {
        val values = mutableMapOf<ServerProfileId, ByteArray>()
        var failRead: Exception? = null
        override suspend fun read(profileId: ServerProfileId): ByteArray? { failRead?.let { throw it }; return values[profileId]?.copyOf() }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
    }
    private class Server : TrainingConsentPort {
        var decision = "UNKNOWN"
        var revision = 0L
        var reads = 0
        var loseNext = false
        var wrongAccount = false
        val receipts = mutableMapOf<String, Pair<String, Long>>()
        val requests = mutableListOf<JsonObject>()
        val started = CompletableDeferred<Unit>()
        val readStarted = CompletableDeferred<Unit>()
        var delayed: CompletableDeferred<JsonObject>? = null
        var delayedRead: CompletableDeferred<JsonObject>? = null
        var captured: JsonObject? = null
        override suspend fun get(): JsonObject {
            reads++
            delayedRead?.let { readStarted.complete(Unit); return it.await() }
            return policy(decision, revision)
        }
        override suspend fun decide(request: JsonObject): JsonObject {
            requests += request
            val operation = request.text("operation_id")
            val desired = request.text("decision")
            if (operation !in receipts) {
                if (desired == "GRANTED" && request.integer("expected_revision") != revision)
                    throw TrainingConsentFailure("consent_revision_conflict")
                decision = if (desired == "GRANTED") desired else if (decision in setOf("GRANTED", "WITHDRAWN") || desired == "WITHDRAWN") "WITHDRAWN" else "DENIED"
                revision++
                receipts[operation] = decision to revision
            }
            val receipt = receipts.getValue(operation)
            val response = buildJsonObject {
                policy(decision, revision, if (wrongAccount) "00000000-0000-4000-8000-000000000000" else ACCOUNT).forEach { (key, value) -> put(key, value) }
                put("operation_id", operation); put("applied_decision", receipt.first); put("applied_revision", receipt.second)
            }
            if (loseNext) { loseNext = false; throw java.io.IOException("lost reply") }
            if (requests.size == 1 && delayed != null) {
                captured = response; started.complete(Unit); return delayed!!.await()
            }
            return response
        }
    }
    private companion object {
        const val ACCOUNT = "11111111-1111-4111-8111-111111111111"
        fun policy(decision: String, revision: Long, account: String = ACCOUNT) = buildJsonObject {
            put("schema_version", 1); put("account_id", account); put("decision", decision)
            put("revision", revision); put("policy_version", 1)
            put("changed_at", if (decision == "UNKNOWN") JsonNull else JsonPrimitive("2026-09-18T12:00:00Z"))
        }
    }
}
