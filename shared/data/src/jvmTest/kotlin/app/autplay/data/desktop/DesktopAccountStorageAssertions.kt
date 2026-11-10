package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import app.autplay.application.desktop.account.*
import app.autplay.data.desktop.account.AccountPrivateFiles
import app.autplay.data.desktop.account.AccountSerialOwner
import app.autplay.data.desktop.account.DesktopAccountSqliteStore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * FUTURE assertions, NOT executed. The parent task has added ONE existing persistence-composite call.
 * This source-only page slice adds no annotation, entry point or helper call.
 * Requires a disposable POSIX volume with the J1 free-space reserve; no Windows permission fallback.
 * All helpers stay in this function; no new test annotation/entry point, real K, host or transport.
 * Hashes/UUIDs/native calls below are future fixture instructions, not computed evidence.
 * Fake protected witnesses test J's comparison boundary only, never Keychain or authority security.
 * Real native commit/close faults, disk-full/fsync, package/Room interoperability, full quotas and
 * every remaining page/materialization/portable/host owner still need separate executable gates.
 */
internal suspend fun desktopAccountStorageAssertions(): Unit {
    val homes = ArrayList<Path>()
    val stores = ArrayList<DesktopAccountStore>()
    val outstanding = LinkedHashMap<DesktopAccountStore, AccountDeliveryLease>()
    var time = 1_800_000_000_000L
    var ticks = 1_000_000L
    fun uuid(): AccountUuid = AccountUuid.random()
    fun newHome(): Path = Files.createTempDirectory("autplay-j1-fixture-",
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"))).toRealPath().also(homes::add)
    suspend fun refused(code: AccountFailure, action: suspend () -> Unit): Unit {
        try { action(); fail("expected ${code.name}") }
        catch (failure: AccountStoreException) { assertEquals(code, failure.failure) }
    }
    fun invalid(action: () -> Unit): Unit {
        try { action(); fail("expected invalid input") } catch (_: Exception) { /* Expected typed rejection. */ }
    }
    class ProtectedFixture : DesktopAccountAuthorityPort {
        var state: AccountProtectedStoreState = AccountProtectedStoreState.Reserved(AccountUuid.random())
        var denyInstallation: Boolean = false
        var selected: AccountScopePreparation? = null
        var stage: AccountProtectedStage = AccountProtectedStage.PREPARING
        var allowNew: Boolean = false
        var allowed: Boolean = true
        var denied: Boolean = false
        var denyCompletion: Boolean = false
        var completions: Int = 0
        var failCaptureClose: Boolean = false
        var captureCloseFailures: Int = 0
        val family: AccountUuid = AccountUuid.random()
        val held: AtomicBoolean = AtomicBoolean(false)
        val gate: AtomicBoolean = AtomicBoolean(false)
        val trace: MutableList<String> = ArrayList()
        fun select(plan: AccountScopePreparation): Unit { selected = plan; stage = AccountProtectedStage.PREPARING }
        override fun tryAcquireLifetime(): AccountProtectedLifetime? {
            if (!held.compareAndSet(false, true)) return null
            trace.add("K_LOCK")
            return object : AccountProtectedLifetime {
                override val storeState: AccountProtectedStoreState get() = state
                override fun completeInstallation(storeId: AccountUuid): Boolean {
                    if (!held.get() || !gate.compareAndSet(false, true)) return false
                    return try {
                        if (denyInstallation) false else when (val captured = state) {
                            is AccountProtectedStoreState.Reserved -> if (captured.storeId == storeId) {
                                state = AccountProtectedStoreState.Existing(storeId); true
                            } else false
                            is AccountProtectedStoreState.Existing -> captured.storeId == storeId
                            else -> false
                        }
                    } finally { assertTrue(gate.compareAndSet(true, false)) }
                }
                override fun tryCapture(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture? {
                    if (denied || !held.get() || selected != preparation ||
                        state != AccountProtectedStoreState.Existing(expectedStoreId) || !gate.compareAndSet(false, true)) return null
                    val observed = AccountProtectedWitness(expectedStoreId, preparation, stage,
                        if (preparation.binding == null) null else family, if (preparation.binding == null) 0 else 1,
                        if (preparation.binding == null) null else 1, allowed, allowNew)
                    return object : AccountProtectedCapture {
                        override val witness: AccountProtectedWitness = observed
                        override fun completeReconciliation(readback: AccountActivationReadback): AccountProtectedWitness? {
                            assertTrue(gate.get())
                            assertEquals(observed.storeId, readback.storeId)
                            assertEquals(preparation, readback.preparation)
                            assertEquals(preparation.expectedActivationRevision + 1, readback.activationRevision)
                            completions++
                            if (denyCompletion) return null
                            stage = AccountProtectedStage.READY
                            return observed.copy(stage = stage)
                        }
                        override fun close(): Unit {
                            assertTrue(gate.compareAndSet(true, false)) // Actual release precedes the fault.
                            if (failCaptureClose) {
                                failCaptureClose = false
                                captureCloseFailures++
                                throw IllegalStateException("FIXTURE_CAPTURE_CLOSE")
                            }
                        }
                    }
                }
                override fun close(): Unit {
                    assertFalse(gate.get())
                    assertTrue(held.compareAndSet(true, false))
                    trace.add("K_RELEASE")
                }
            }
        }
    }
    class SqlFaults {
        // One-shot faults around the actual delegate COMMIT; no second connection/writer.
        var commit: String = "NONE"
        var commits: Int = 0
        var appliedCommits: Int = 0
        var statements: Int = 0
        var closes: Int = 0
        var afterCommit: ((SQLiteConnection) -> Unit)? = null
        var beforeClose: (() -> Unit)? = null
        var readbackUnavailable: Boolean = false
        var readbackContradictory: Boolean = false
        var settlementUnavailable: Boolean = false
        fun wrap(connection: SQLiteConnection): SQLiteConnection = object : SQLiteConnection by connection {
            override fun prepare(sql: String): SQLiteStatement {
                statements++
                val statement = connection.prepare(sql)
                return object : SQLiteStatement by statement {
                    override fun step(): Boolean {
                        if (settlementUnavailable && sql in setOf("BEGIN IMMEDIATE", "ROLLBACK")) {
                            throw IllegalStateException("FIXTURE_SETTLEMENT")
                        }
                        if (readbackUnavailable && sql == "SELECT work_revision FROM j_activation WHERE singleton=1") {
                            throw IllegalStateException("FIXTURE_READBACK")
                        }
                        if (sql == "COMMIT") {
                            commits++
                            val fault = commit
                            commit = "NONE"
                            if (fault == "BEFORE") throw IllegalStateException("FIXTURE_COMMIT")
                            val result = statement.step()
                            appliedCommits++
                            afterCommit?.invoke(connection) // Read only, on this same settled writer.
                            readbackUnavailable = fault == "AFTER_UNAVAILABLE"
                            readbackContradictory = fault == "AFTER_CONTRADICTORY"
                            settlementUnavailable = fault == "AFTER_FENCE_FAILURE"
                            if (fault.startsWith("AFTER")) throw IllegalStateException("FIXTURE_COMMIT")
                            return result
                        }
                        return statement.step()
                    }
                    override fun getLong(index: Int): Long {
                        val value = statement.getLong(index)
                        return if (readbackContradictory && index == 0 &&
                            sql == "SELECT work_revision FROM j_activation WHERE singleton=1") value + 1 else value
                    }
                }
            }
            override fun close(): Unit {
                beforeClose?.invoke()
                connection.close()
                closes++
            }
        }
    }
    fun owner(home: Path, protected: ProtectedFixture, faults: SqlFaults? = null): DesktopAccountStore = DesktopAccountSqliteStore(
        protected, AccountPrivateFiles(home), wallMs = { time }, monotonicNanos = { ticks },
        openConnection = { path -> BundledSQLiteDriver().open(path).let { faults?.wrap(it) ?: it } },
    ).also(stores::add)
    suspend fun claim(store: DesktopAccountStore, lease: AccountLease): AccountDeliveryClaim? =
        store.claimPending(lease).awaitResult()?.also { outstanding[store] = it.lease }
    suspend fun <T> loseCallerResult(operation: AccountDeliveryOperation<T>): Unit {
        // Pinned 1.10.2 Timeout.kt: timeout may win even immediately before a successful return.
        // The ticket is retained outside that scope; neither this timeout nor a repeat await replays SQL.
        try {
            withTimeout(1) { operation.awaitResult(); awaitCancellation() }
        } catch (_: TimeoutCancellationException) { /* Recover using the SAME ticket below. */ }
    }
    // FUTURE worker-only assertions, deliberately UNCALLED even by this composite. A reviewed
    // isolated-process harness must own termination: ambiguity forbids in-process close/cleanup.
    suspend fun ambiguousCommitWorkerOnly(
        store: DesktopAccountStore, protected: ProtectedFixture, lease: AccountLease,
        faults: SqlFaults, phase: String, cut: String, cleanupFailure: Boolean = false,
    ): Unit {
        require(phase in listOf("CLAIM", "RENEW", "FAIL"))
        require(cut in listOf("AFTER_UNAVAILABLE", "AFTER_CONTRADICTORY", "AFTER_FENCE_FAILURE"))
        require(!cleanupFailure || phase != "FAIL") // Settlement itself acquires no protected capture.
        val current = if (phase == "CLAIM") null else checkNotNull(claim(store, lease)).lease
        val commitsBefore = faults.commits
        val closeFailuresBefore = protected.captureCloseFailures
        faults.commit = cut
        protected.failCaptureClose = cleanupFailure
        when (phase) {
            "CLAIM" -> {
                val ticket = store.claimPending(lease)
                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                val outcome = ticket.awaitOutcome()
                assertTrue(outcome is AccountDeliveryOutcome.Failed) // No handle from unknown SQL + failed cleanup.
                assertSame(outcome, ticket.awaitOutcome())
            }
            "RENEW" -> {
                val ticket = store.renewDelivery(checkNotNull(current))
                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                val outcome = ticket.awaitOutcome()
                assertTrue(outcome is AccountDeliveryOutcome.Failed)
                assertSame(outcome, ticket.awaitOutcome())
            }
            "FAIL" -> refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) {
                store.failDelivery(checkNotNull(current), AccountDeliveryFailure.RETRYABLE)
            }
        }
        assertEquals(commitsBefore + 1, faults.commits)
        assertEquals(closeFailuresBefore + if (cleanupFailure) 1 else 0, protected.captureCloseFailures)
        assertFalse(protected.gate.get())
        current?.let { exact -> refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) {
            store.failDelivery(exact, AccountDeliveryFailure.RETRYABLE)
        } }
        refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { store.close() }
        assertTrue(protected.held.get())
        refused(AccountFailure.ACCOUNT_CLOSED) { store.claimPending(lease).awaitResult() }
        assertEquals(commitsBefore + 1, faults.commits) // No second writer, lease expiry release or domain replay.
    }
    suspend fun settle(store: DesktopAccountStore, lease: AccountDeliveryLease, reason: AccountDeliveryFailure): Unit {
        store.failDelivery(lease, reason)
        outstanding.remove(store)
    }
    suspend fun select(store: DesktopAccountStore, protected: ProtectedFixture, plan: AccountScopePreparation): AccountLease {
        protected.select(plan)
        val prepared = store.prepareScope(plan)
        assertEquals("PREPARED", prepared.rawActivationState)
        assertEquals(plan, prepared.unresolvedPreparation)
        protected.stage = AccountProtectedStage.BINDING_COMMIT_PENDING
        return store.activateScope(plan.activationId)
    }
    fun change(entry: AccountUuid = uuid()): AccountCreateTrack = AccountCreateTrack(uuid(), uuid(), entry, "Title", "Artist", time)
    fun database(home: Path): Path = AccountPrivateFiles(home).database
    fun closedSql(home: Path, action: (SQLiteConnection) -> Unit): Unit {
        // Only caller-owned disposable fixtures, after that owner's actual close. No live second connection.
        BundledSQLiteDriver().open(database(home).toString()).use(action)
    }
    fun scalar(connection: SQLiteConnection, statement: String): Long = connection.prepare(statement).use {
        assertTrue(it.step()); val value = it.getLong(0); assertFalse(it.step()); value
    }
    fun asciiCanonical(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + asciiCanonical(it.value)
        }
        is JsonArray -> value.joinToString(",", "[", "]") { asciiCanonical(it) }
        else -> value.toString()
    }
    fun futureEvent(original: AccountEventV1, addition: JsonObject): ByteArray {
        val parsed = Json.parseToJsonElement(original.canonicalBytes.toString(Charsets.UTF_8)) as JsonObject
        val withoutHash = JsonObject(parsed - "request_hash" + addition)
        val hash = MessageDigest.getInstance("SHA-256").digest(asciiCanonical(withoutHash).toByteArray(Charsets.UTF_8))
            .joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
        return asciiCanonical(JsonObject(withoutHash + ("request_hash" to JsonPrimitive(hash)))).toByteArray(Charsets.UTF_8)
    }

    /** FUTURE I1 storage scenario inside the existing composite. No HTTP or real K authority. */
    suspend fun initialImportEntryDecisions(): Unit {
        val home = newHome(); val k = ProtectedFixture(); var j = owner(home,k)
        val boot = j.initialize(); assertEquals(AccountProtectedStoreState.Existing(boot.storeId), k.state)
        val identity = AccountScopeIdentity(uuid(),uuid(),AccountRemoteOwner(uuid(),uuid()))
        val preparation = AccountScopePreparation(uuid(),identity,uuid(),AccountRemoteBinding(uuid(),uuid(),1,"b".repeat(64),"c".repeat(64)),1,0,
            activationId = uuid())
        k.allowNew = true
        var lease = select(j,k,preparation); k.allowNew = false
        fun initial(): AccountImportEntryReview = AccountImportEntryReview(uuid(),uuid(),uuid(),uuid(),uuid(),uuid(),uuid())
        fun command(reference: AccountImportReviewReference, action: AccountImportReviewAction,
            rank: Int? = null, evidence: String? = null): AccountImportReviewCommand {
            val operation = uuid()
            return AccountImportReviewCommand(operation,uuid(),reference,requireNotNull(reference.predecessor),action,rank,evidence,
                operation.value,lease.bindingCommit,lease.authorityRevision,lease.activationRevision)
        }
        val entry = initial()
        val exact = command(entry,AccountImportReviewAction.KEEP_UNRESOLVED)
        val body = exact.bodyCopy()
        assertEquals("{\"predecessor_decision_id\":\"${entry.predecessor.value}\",\"action\":\"KEEP_UNRESOLVED\",\"selected_rank\":null,\"idempotency_key\":\"${exact.operationId.value}\"}",body.toString(Charsets.UTF_8))
        assertTrue(checkNotNull(j.domain).readImportReviews(lease,null).isEmpty())
        var pending = checkNotNull(j.importReviewCommands).prepare(lease,exact)
        assertEquals(exact,pending.command)
        assertEquals(pending,checkNotNull(j.importReviewCommands).prepare(lease,exact))
        refused(AccountFailure.ACCOUNT_BUSY) {
            checkNotNull(j.importReviewCommands).prepare(lease,command(entry,AccountImportReviewAction.CREATE_RECORDING))
        }
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) {
            checkNotNull(j.importReviewCommands).prepare(lease,exact.copy(bindingCommit=uuid()))
        }
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) {
            checkNotNull(j.importReviewCommands).prepare(lease,exact.copy(authorityRevision=lease.authorityRevision+1))
        }
        invalid { command(entry,AccountImportReviewAction.ACCEPT,1,"d".repeat(64)) }
        invalid { command(entry,AccountImportReviewAction.REJECT,1,"d".repeat(64)) }
        invalid { exact.copy(predecessor=uuid()) }
        suspend fun reopen(clearSchedule: Boolean): Unit {
            val prior = lease; j.close()
            if(clearSchedule) closedSql(home) { sql ->
                // Disposable fixture only: advance eligibility without a wall-clock sleep.
                sql.prepare("UPDATE j_work SET next_attempt_ms=0 WHERE scope_id=? AND work_id=?").use {
                    it.bindText(1,prior.scopeId.value); it.bindText(2,exact.workId.value); assertFalse(it.step())
                }
            }
            j = owner(home,k); j.initialize()
            // A process restart changes boot/activation, not the authority of the retained command.
            val activation = preparation.copy(activationId = uuid(), expectedAuthorityRevision = prior.authorityRevision,
                expectedActivationRevision = prior.activationRevision)
            lease = select(j,k,activation)
            assertNotEquals(prior.bootId,lease.bootId)
            assertEquals(prior.bindingCommit,lease.bindingCommit)
            assertEquals(prior.authorityRevision,lease.authorityRevision)
            assertTrue(lease.activationRevision>prior.activationRevision)
            pending = checkNotNull(j.importReviewCommands).read(lease,exact.operationId)!!
            assertEquals(exact,pending.command); assertArrayEquals(body,pending.command.bodyCopy())
            refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { checkNotNull(j.importReviewCommands).read(prior,exact.operationId) }
        }
        // Every lost response keeps the same entry provenance/body/key through actual owner reopen.
        for(attempt in 1..8) {
            if(attempt>1) reopen(clearSchedule=true)
            val commands = checkNotNull(j.importReviewCommands)
            val claim = checkNotNull(commands.claim(lease,uuid(),exact.operationId,pending.revision))
            pending = commands.markSent(lease,uuid(),exact.operationId,claim,pending.revision)
            pending = commands.settle(lease,uuid(),exact.operationId,claim,pending.revision,null)
            assertEquals("OUTCOME_PENDING",pending.rawState); assertEquals(attempt,pending.attempts)
            assertTrue(pending.nextAttemptAtMs>0); assertArrayEquals(body,pending.command.bodyCopy())
        }
        assertEquals("DEAD_LETTER",pending.rawWorkState); assertTrue(pending.retryExhausted)
        reopen(clearSchedule=false)
        val commands = checkNotNull(j.importReviewCommands)
        assertNull(commands.claim(lease,uuid(),exact.operationId,pending.revision))
        val retainedRetryAt = pending.nextAttemptAtMs
        pending = commands.retryDeadLetter(lease,uuid(),exact.operationId,pending.revision)
        assertEquals(retainedRetryAt,pending.nextAttemptAtMs); assertEquals(exact,pending.command)
        reopen(clearSchedule=true)
        val retry = checkNotNull(j.importReviewCommands)
        val claim = checkNotNull(retry.claim(lease,uuid(),exact.operationId,pending.revision))
        pending = retry.markSent(lease,uuid(),exact.operationId,claim,pending.revision)
        val outcome = AccountImportReviewOutcome(uuid(),entry.serverEntryId,"MANUAL_UNRESOLVED",null,true)
        val settlement = uuid()
        pending = retry.settle(lease,settlement,exact.operationId,claim,pending.revision,outcome)
        assertEquals("COMMITTED",pending.rawState); assertEquals(outcome,pending.outcome)
        assertEquals(pending,retry.settle(lease,settlement,exact.operationId,claim,pending.revision-1,outcome))
        assertTrue(checkNotNull(j.domain).readImportReviews(lease,null).isEmpty())

        // CREATE_RECORDING receives only the server's returned Recording; no candidate is invented.
        val create = command(initial(),AccountImportReviewAction.CREATE_RECORDING)
        var created = retry.prepare(lease,create)
        val createClaim = checkNotNull(retry.claim(lease,uuid(),create.operationId,created.revision))
        created = retry.markSent(lease,uuid(),create.operationId,createClaim,created.revision)
        val createdOutcome = AccountImportReviewOutcome(uuid(),create.reference.serverEntryId,"MANUAL_MATCH",uuid(),false)
        created = retry.settle(lease,uuid(),create.operationId,createClaim,created.revision,createdOutcome)
        assertEquals(createdOutcome,created.outcome); assertNull(created.command.selectedRank)

        // The old ranked mapping still requires its exact retained row and evidence selection.
        val mapped = AccountImportReview(uuid(),uuid(),uuid(),uuid(),uuid(),uuid(),1,uuid(),uuid())
        val ranked = command(mapped,AccountImportReviewAction.ACCEPT,1,"e".repeat(64))
        refused(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) { retry.prepare(lease,ranked) }
        checkNotNull(j.domain).retainImportReview(lease,mapped)
        val rankedPending = retry.prepare(lease,ranked)
        assertEquals(mapped,rankedPending.command.reference); assertEquals(1,rankedPending.command.selectedRank)
        assertEquals("e".repeat(64),rankedPending.command.selectedEvidenceSha256)
        j.close()
        closedSql(home) { sql ->
            assertEquals(2L,scalar(sql,"""SELECT count(*) FROM j_import_review_command
                WHERE reference_kind='ENTRY_CONTEXT_V1' AND review_id IS NULL AND candidate_id IS NULL
                AND mapping_rank IS NULL AND selected_rank IS NULL AND evidence_sha256 IS NULL
                AND entry_context_id IS NOT NULL AND state='COMMITTED'"""))
            assertEquals(1L,scalar(sql,"SELECT count(*) FROM j_import_review WHERE rank=1"))
        }
    }

    try {
        invalid { AccountUuid("not-a-uuid") }
        invalid { AccountUuid("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA") }
        invalid { CatalogOwnerScope(uuid().value) }
        assertEquals(64, CatalogOwnerScope("a".repeat(64)).value.length)
        invalid { AccountScopeIdentity(uuid(), null, null) }

        val home = newHome()
        assertEquals(home.toRealPath(), home)
        val aliasedHome = home.resolve("untrusted-home-alias")
        Files.createSymbolicLink(aliasedHome, home)
        refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) {
            AccountPrivateFiles(aliasedHome).requireVirginNamespace()
        }
        assertFalse(Files.exists(home.resolve("Library")))
        val protected = ProtectedFixture()
        val reservedStore = (protected.state as AccountProtectedStoreState.Reserved).storeId
        var store = owner(home, protected)
        val initial = store.initialize()
        assertEquals(reservedStore, initial.storeId)
        assertEquals("SUSPENDED", initial.rawActivationState)
        assertTrue(initial.scopes.isEmpty())
        assertEquals(AccountProtectedStoreState.Existing(initial.storeId), protected.state)
        val portable = uuid()
        val root = AccountScopeIdentity(uuid(), portable, null)
        val rootPlan = AccountScopePreparation(uuid(), root, uuid(), null, 1, 0, activationId = uuid())
        protected.select(rootPlan)
        val prepared = store.prepareScope(rootPlan)
        assertEquals("PREPARED", prepared.rawActivationState)
        assertEquals(1L, prepared.activationRevision)
        refused(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE) { store.activateScope(rootPlan.activationId) }
        protected.stage = AccountProtectedStage.BINDING_COMMIT_PENDING
        protected.denyCompletion = true
        refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { store.activateScope(rootPlan.activationId) }
        assertEquals("APPLIED", store.reconciliationSnapshot().rawPreparationStage)
        store.close()
        assertFalse(protected.held.get())
        store = owner(home, protected)
        val reopened = store.initialize()
        assertEquals("SUSPENDED", reopened.rawActivationState)
        assertNotEquals(initial.bootId, reopened.bootId)
        assertEquals(rootPlan, reopened.unresolvedPreparation)
        protected.denyCompletion = false
        val rootLease = store.activateScope(rootPlan.activationId)
        assertEquals(rootLease, store.activateScope(rootPlan.activationId)) // Positive idempotent reply recovery.
        assertTrue(store.readTracks(rootLease, null).isEmpty())
        val local = change()
        val localReceipt = store.createTrack(rootLease, local)
        assertNull(localReceipt.eventId)
        assertEquals(local.operationId, localReceipt.localIntentId)
        assertEquals(localReceipt, store.createTrack(rootLease, local))
        refused(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) { store.createTrack(rootLease, local.copy(title = "Different")) }
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { store.readTracks(rootLease.copy(scopeId = uuid()), null) }
        refused(AccountFailure.ACCOUNT_INPUT_INVALID) { store.readTracks(rootLease, null, 101) }
        refused(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION) { store.claimPending(rootLease).awaitResult() }

        val remote = AccountRemoteOwner(uuid(), uuid())
        val bound = AccountScopeIdentity(uuid(), portable, remote)
        val binding = AccountRemoteBinding(uuid(), uuid(), 1, "c".repeat(64), "d".repeat(64))
        val plan = AccountScopePreparation(uuid(), bound, uuid(), binding, 2, rootLease.activationRevision, activationId = uuid())
        protected.select(plan)
        store.prepareScope(plan)
        protected.stage = AccountProtectedStage.BINDING_COMMIT_PENDING
        refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { store.activateScope(plan.activationId) }
        protected.allowNew = true
        val boundLease = store.activateScope(plan.activationId)
        protected.allowNew = false
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { store.readTracks(rootLease, null) }
        val first = change()
        store.createTrack(boundLease, first)
        val collision = change(first.libraryEntryId)
        refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { store.createTrack(boundLease, collision) }
        val second = collision.copy(libraryEntryId = uuid())
        store.createTrack(boundLease, second)
        assertEquals(2, store.readTracks(boundLease, null).size)
        var batch = checkNotNull(claim(store, boundLease))
        assertEquals(listOf(1L, 2L), batch.events.map { it.sequence }) // Counter rolled back with failed projection.
        val immutable = batch.events.first()
        val exactBytes = immutable.canonicalBytes
        val exactHash = immutable.requestHash
        immutable.canonicalBytes.fill(0)
        immutable.requestHash.fill(0)
        assertArrayEquals(exactBytes, immutable.canonicalBytes)
        assertArrayEquals(exactHash, immutable.requestHash)
        assertArrayEquals(exactBytes, AccountEventV1.fromCanonical(exactBytes).canonicalBytes)
        val additive = futureEvent(immutable, JsonObject(mapOf("future_value" to JsonPrimitive("PRESERVED"),
            "event_type" to JsonPrimitive("FUTURE_EVENT_TYPE"))))
        val unknown = AccountEventV1.fromCanonical(additive)
        assertEquals("FUTURE_EVENT_TYPE", unknown.eventType)
        assertArrayEquals(additive, unknown.canonicalBytes)
        val futureTime = futureEvent(immutable, JsonObject(mapOf("occurred_at" to JsonPrimitive("+10000-01-01T00:00:00Z"))))
        assertArrayEquals(futureTime, AccountEventV1.fromCanonical(futureTime).canonicalBytes)
        invalid { AccountEventV1.fromCanonical(futureEvent(immutable, JsonObject(mapOf(
            "future_value" to JsonObject(mapOf("access_token" to JsonPrimitive("synthetic"))))))) }
        invalid { AccountEventV1.fromCanonical(exactBytes.copyOf().also { it[it.lastIndex] = 0 }) }
        refused(AccountFailure.ACCOUNT_BUSY) { store.claimPending(boundLease).awaitResult() }
        val renewalTicket = store.renewDelivery(batch.lease) // Retained before the timeout scope.
        loseCallerResult(renewalTicket)
        val renewed = renewalTicket.awaitResult()
        assertEquals(renewed, renewalTicket.awaitResult())
        outstanding[store] = renewed
        refused(AccountFailure.ACCOUNT_LEASE_STALE) { store.failDelivery(batch.lease, AccountDeliveryFailure.RETRYABLE) }
        batch = AccountDeliveryClaim(renewed, batch.events)
        settle(store, batch.lease, AccountDeliveryFailure.RETRYABLE)
        for (attempt in 2..8) {
            time += 3_600_001
            ticks += 3_600_001_000_000L
            val retry = checkNotNull(claim(store, boundLease))
            assertArrayEquals(exactBytes, retry.events.first().canonicalBytes)
            assertArrayEquals(exactHash, retry.events.first().requestHash)
            settle(store, retry.lease, AccountDeliveryFailure.RETRYABLE)
        }
        time += 3_600_001
        refused(AccountFailure.ACCOUNT_DELIVERY_BLOCKED) { claim(store, boundLease) }
        assertTrue(store.readDelivery(boundLease, null).all { it.rawState == "DEAD_LETTER" && it.attempts == 8 })

        // A new profile aliases the existing lineage and keeps its allocator; original event profiles survive.
        val aliasPlan = plan.copy(bindingCommit = uuid(), profileId = uuid(), expectedAuthorityRevision = 3,
            expectedActivationRevision = boundLease.activationRevision, activationId = uuid())
        val aliasLease = select(store, protected, aliasPlan)
        store.createTrack(aliasLease, change())
        refused(AccountFailure.ACCOUNT_DELIVERY_BLOCKED) { claim(store, aliasLease) }
        assertEquals(3, store.readDelivery(aliasLease, null).size)
        assertEquals(2, store.readDelivery(aliasLease, null).count { it.originalProfileId == plan.profileId })
        assertEquals(0, store.readDelivery(aliasLease, null).single { it.sequence == 3L }.attempts)
        val returnPlan = plan.copy(expectedAuthorityRevision = 4,
            expectedActivationRevision = aliasLease.activationRevision, activationId = uuid())
        var current = select(store, protected, returnPlan)
        assertNotEquals(boundLease.activationRevision, current.activationRevision)
        assertEquals(boundLease.bindingCommit, current.bindingCommit)
        assertEquals(boundLease.sessionFamily, current.sessionFamily)
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { store.readTracks(boundLease, null) }
        store.suspendScope(current)
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { store.activateScope(returnPlan.activationId) }
        val fresh = returnPlan.copy(expectedAuthorityRevision = 5,
            expectedActivationRevision = current.activationRevision, activationId = uuid())
        current = select(store, protected, fresh)
        protected.denied = true
        refused(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE) { store.readTracks(current, null) }
        protected.denied = false
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { store.activateScope(fresh.activationId) }
        current = select(store, protected, fresh.copy(expectedAuthorityRevision = 6,
            expectedActivationRevision = current.activationRevision, activationId = uuid()))

        // Eight retained owner scopes, no inactive eviction. Existing ROOT data is still present on selection.
        repeat(6) { index ->
            val identity = AccountScopeIdentity(uuid(), uuid(), null)
            current = select(store, protected, AccountScopePreparation(uuid(), identity, uuid(), null,
                7L + index, current.activationRevision, activationId = uuid()))
        }
        val ninth = AccountScopePreparation(uuid(), AccountScopeIdentity(uuid(), uuid(), null), uuid(), null,
            20, current.activationRevision, activationId = uuid())
        protected.select(ninth)
        refused(AccountFailure.ACCOUNT_STORAGE_FULL) { store.prepareScope(ninth) }
        assertEquals(8, store.reconciliationSnapshot().scopes.size)
        current = select(store, protected, rootPlan.copy(expectedAuthorityRevision = 21,
            expectedActivationRevision = current.activationRevision, activationId = uuid()))
        assertEquals(local.trackRefId, store.readTracks(current, null).single().trackRefId)
        store.close()

        // Native constraints reject immutable rewrites and downgrades; unknown bytes remain untouched.
        closedSql(home) { sql ->
            sql.prepare("SELECT canonical_bytes FROM j_event WHERE event_id=?").use {
                it.bindText(1, first.operationId.value); assertTrue(it.step()); assertArrayEquals(exactBytes, it.getBlob(0))
            }
            invalid { sql.execSQL("UPDATE j_event SET sequence=sequence+100") }
            invalid { sql.execSQL("UPDATE j_profile_alias SET profile_id='00000000-0000-4000-8000-000000000000'") }
            invalid { sql.execSQL("UPDATE j_lineage SET next_sequence=1") }
            invalid { sql.execSQL("DELETE FROM j_scope") }
            assertEquals(3L, scalar(sql, "SELECT count(*) FROM j_event"))
            assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_local_intent"))
            sql.execSQL("UPDATE j_event_delivery SET state='FUTURE_DELIVERY_STATE' WHERE attempt=8")
        }
        store = owner(home, protected)
        store.initialize()
        val existingPlan = rootPlan.copy(expectedAuthorityRevision = 22,
            expectedActivationRevision = current.activationRevision, activationId = uuid())
        current = select(store, protected, existingPlan)
        val back = fresh.copy(expectedAuthorityRevision = 23,
            expectedActivationRevision = current.activationRevision, activationId = uuid())
        current = select(store, protected, back)
        assertEquals(2, store.readDelivery(current, null).count { it.rawState == "FUTURE_DELIVERY_STATE" })
        // Same device cannot get a new epoch by recreating a local profile.
        val reset = back.copy(bindingCommit = uuid(), profileId = uuid(), binding = binding.copy(journalEpoch = uuid()),
            expectedAuthorityRevision = 24, expectedActivationRevision = current.activationRevision, activationId = uuid())
        protected.select(reset)
        store.prepareScope(reset)
        protected.stage = AccountProtectedStage.BINDING_COMMIT_PENDING
        protected.allowNew = true
        refused(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) { store.activateScope(reset.activationId) }
        store.close()
        closedSql(home) { assertEquals(1L, scalar(it, "SELECT count(*) FROM j_lineage")) }

        suspend fun installationAndCapacityCases(): Unit {
            // A legacy virgin claim cannot authorize SQL creation after J2/K2.
            val virginHome = newHome()
            val legacyVirgin = ProtectedFixture().apply { state = AccountProtectedStoreState.Virgin }
            val virginOwner = owner(virginHome, legacyVirgin)
            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { virginOwner.initialize() }
            assertFalse(Files.exists(database(virginHome)))
            virginOwner.close()

            // Lost READY acknowledgement completes only the exact preselected installation UUID.
            val reservedHome = newHome()
            val reservedK = ProtectedFixture().apply { denyInstallation = true }
            val reservedId = (reservedK.state as AccountProtectedStoreState.Reserved).storeId
            val incomplete = owner(reservedHome, reservedK)
            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { incomplete.initialize() }
            assertEquals(AccountProtectedStoreState.Reserved(reservedId), reservedK.state)
            incomplete.close()
            reservedK.denyInstallation = false
            val resumedInstallation = owner(reservedHome, reservedK)
            assertEquals(reservedId, resumedInstallation.initialize().storeId)
            assertEquals(AccountProtectedStoreState.Existing(reservedId), reservedK.state)
            resumedInstallation.close()
            reservedK.state = AccountProtectedStoreState.Reserved(uuid()) // Deliberate foreign reservation.
            val wrongReservation = owner(reservedHome, reservedK)
            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { wrongReservation.initialize() }
            wrongReservation.close()
            closedSql(reservedHome) { sql -> sql.prepare("SELECT store_id FROM j_store WHERE singleton=1").use {
                assertTrue(it.step()); assertEquals(reservedId.value, it.getText(0)); assertFalse(it.step())
            } }

            val missingHome = newHome()
            val missingK = ProtectedFixture().apply { state = AccountProtectedStoreState.Existing(uuid()) }
            val missing = owner(missingHome, missingK)
            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { missing.initialize() }
            assertFalse(Files.exists(database(missingHome)))
            missing.close()

            val futureHome = newHome()
            val futureK = ProtectedFixture()
            val originalStore = owner(futureHome, futureK)
            val originalId = originalStore.initialize().storeId
            assertEquals(AccountProtectedStoreState.Existing(originalId), futureK.state)
            originalStore.close()
            closedSql(futureHome) { it.execSQL("PRAGMA user_version=2") }
            val refusedStore = owner(futureHome, futureK)
            refused(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED) { refusedStore.initialize() }
            refusedStore.close()
            closedSql(futureHome) { assertEquals(2L, scalar(it, "PRAGMA user_version")) }

            // Actual file-lock contention under two independent simulated K owners, then positive reopen.
            val lockHome = newHome()
            val lockK = ProtectedFixture()
            val firstOwner = owner(lockHome, lockK)
            val lockId = firstOwner.initialize().storeId
            val competingK = ProtectedFixture().apply { state = AccountProtectedStoreState.Existing(lockId) }
            val competing = owner(lockHome, competingK)
            refused(AccountFailure.ACCOUNT_BUSY) { competing.initialize() }
            competing.close()
            assertEquals(AccountProtectedStoreState.Existing(lockId), lockK.state)
            firstOwner.close()
            val nextOwner = owner(lockHome, lockK)
            assertEquals(lockId, nextOwner.initialize().storeId)
            nextOwner.close()

            // Profile capacity is reserved BEFORE the protected publication/activation step.
            val profilesHome = newHome()
            val profilesK = ProtectedFixture()
            val profiles = owner(profilesHome, profilesK)
            assertEquals(AccountProtectedStoreState.Existing(profiles.initialize().storeId), profilesK.state)
            val sharedRoot = AccountScopeIdentity(uuid(), uuid(), null)
            var profileRevision = 0L
            var firstProfile: AccountUuid? = null
            repeat(32) { index ->
                val profile = uuid()
                if (firstProfile == null) firstProfile = profile
                val selected = select(profiles, profilesK,
                    AccountScopePreparation(uuid(), sharedRoot, profile, null, 1L + index, profileRevision, activationId = uuid()))
                profileRevision = selected.activationRevision
            }
            val completionsBeforeRefusal = profilesK.completions
            val thirtyThird = AccountScopePreparation(uuid(), sharedRoot, uuid(), null, 33, profileRevision, activationId = uuid())
            profilesK.select(thirtyThird)
            refused(AccountFailure.ACCOUNT_STORAGE_FULL) { profiles.prepareScope(thirtyThird) }
            assertEquals(completionsBeforeRefusal, profilesK.completions)
            assertEquals(32, profiles.reconciliationSnapshot().scopes.single().profileIds.size)
            val crossing = thirtyThird.copy(scope = AccountScopeIdentity(uuid(), uuid(), null), profileId = checkNotNull(firstProfile))
            profilesK.select(crossing)
            refused(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) { profiles.prepareScope(crossing) }
            assertEquals(1, profiles.reconciliationSnapshot().scopes.size) // Failed alias admission rolled back new scope.
            profiles.close()
        }
        installationAndCapacityCases()

        suspend fun deliveryAndDrainCases(): Unit {
            // A lease expiring is not an actual external pass settlement. Close keeps J AND K ownership.
            val drainingHome = newHome()
            val drainingK = ProtectedFixture().apply { allowNew = true }
            val draining = owner(drainingHome, drainingK)
            assertEquals(AccountProtectedStoreState.Existing(draining.initialize().storeId), drainingK.state)
            val drainingPlan = AccountScopePreparation(uuid(), bound.copy(scopeId = uuid()), uuid(), binding.copy(deviceId = uuid()), 1, 0,
                activationId = uuid())
            val drainingLease = select(draining, drainingK, drainingPlan)
            draining.createTrack(drainingLease, change())
            val drainingBatch = checkNotNull(claim(draining, drainingLease))
            val beforeRollback = time
            time--
            refused(AccountFailure.ACCOUNT_CLOCK_ROLLBACK) { draining.renewDelivery(drainingBatch.lease).awaitResult() }
            time = beforeRollback + DesktopAccountLimits.LEASE_MS + 1
            ticks += (DesktopAccountLimits.LEASE_MS + 1) * 1_000_000
            refused(AccountFailure.ACCOUNT_LEASE_STALE) { draining.renewDelivery(drainingBatch.lease).awaitResult() }
            refused(AccountFailure.ACCOUNT_BUSY) { draining.claimPending(drainingLease).awaitResult() }
            refused(AccountFailure.ACCOUNT_BUSY) { draining.close() }
            assertTrue(drainingK.held.get())
            refused(AccountFailure.ACCOUNT_CLOSED) { draining.readTracks(drainingLease, null) }
            settle(draining, drainingBatch.lease, AccountDeliveryFailure.RETRYABLE) // Exact settlement allowed after retire.
            draining.close()
            assertFalse(drainingK.held.get())
            val drainedReopen = owner(drainingHome, drainingK)
            val drainedSnapshot = drainedReopen.initialize()
            assertEquals("SUSPENDED", drainedSnapshot.rawActivationState)
            assertNotEquals(drainingLease.bootId, drainedSnapshot.bootId)
            drainedReopen.close()

            // New-command bounds precede every SQL transaction/allocation. Stored future bytes above survive.
            val boundaryHome = newHome()
            val boundaryK = ProtectedFixture().apply { allowNew = true }
            val boundaryFaults = SqlFaults()
            val boundary = owner(boundaryHome, boundaryK, boundaryFaults)
            assertEquals(AccountProtectedStoreState.Existing(boundary.initialize().storeId), boundaryK.state)
            val boundaryPlan = AccountScopePreparation(uuid(), bound.copy(scopeId = uuid()), uuid(), binding.copy(deviceId = uuid()), 1, 0,
                activationId = uuid())
            val boundaryLease = select(boundary, boundaryK, boundaryPlan)
            val commitsBeforeInvalid = boundaryFaults.commits
            for (badTime in listOf(-1L, 253_402_300_800_000L, DesktopAccountLimits.MAX_REVISION)) {
                try {
                    boundary.createTrack(boundaryLease, change().copy(occurredAtMs = badTime))
                    fail("expected new event time rejection")
                } catch (_: IllegalArgumentException) { /* Constructor rejects before store admission. */ }
                assertEquals(commitsBeforeInvalid, boundaryFaults.commits)
                assertTrue(boundary.readTracks(boundaryLease, null).isEmpty())
                assertTrue(boundary.readDelivery(boundaryLease, null).isEmpty())
            }
            boundary.createTrack(boundaryLease, change().copy(occurredAtMs = 0))
            boundary.createTrack(boundaryLease, change().copy(occurredAtMs = 253_402_300_799_999L))
            val initialTicket = boundary.claimPending(boundaryLease)
            loseCallerResult(initialTicket)
            val recovered = checkNotNull(initialTicket.awaitResult())
            outstanding[boundary] = recovered.lease
            assertSame(recovered, initialTicket.awaitResult())
            assertEquals(listOf(1L, 2L), recovered.events.map { it.sequence })
            assertEquals(listOf("1970-01-01T00:00:00Z", "9999-12-31T23:59:59.999Z"), recovered.events.map { it.occurredAt })
            assertTrue(boundary.readDelivery(boundaryLease, null).all { it.attempts == 1 })
            val firstRenewal = boundary.renewDelivery(recovered.lease)
            loseCallerResult(firstRenewal)
            val firstRenewed = firstRenewal.awaitResult()
            outstanding[boundary] = firstRenewed
            val secondRenewal = boundary.renewDelivery(firstRenewed)
            loseCallerResult(secondRenewal)
            val secondRenewed = secondRenewal.awaitResult()
            outstanding[boundary] = secondRenewed
            assertNotEquals(initialTicket.operationId, firstRenewal.operationId)
            assertNotEquals(firstRenewal.operationId, secondRenewal.operationId)
            assertEquals(firstRenewed, firstRenewal.awaitResult()) // Historical ticket never returns the newer result.
            assertSame(recovered, initialTicket.awaitResult())
            assertEquals(firstRenewed.revision + 1, secondRenewed.revision)
            refused(AccountFailure.ACCOUNT_LEASE_STALE) { boundary.failDelivery(recovered.lease, AccountDeliveryFailure.RETRYABLE) }
            refused(AccountFailure.ACCOUNT_LEASE_STALE) { boundary.failDelivery(firstRenewed, AccountDeliveryFailure.RETRYABLE) }
            refused(AccountFailure.ACCOUNT_BUSY) { boundary.close() } // Actual external owner has not settled.
            assertTrue(boundaryK.held.get())
            settle(boundary, secondRenewed, AccountDeliveryFailure.RETRYABLE)
            boundary.close()
            assertFalse(boundaryK.held.get())
            closedSql(boundaryHome) { sql ->
                assertEquals(3L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                assertEquals(2L, scalar(sql, "SELECT count(*) FROM j_event"))
                assertEquals(2L, scalar(sql, "SELECT count(*) FROM j_track_ref"))
                assertEquals(2L, scalar(sql, "SELECT count(*) FROM j_library_entry"))
                assertEquals(0L, scalar(sql, "SELECT count(*) FROM j_local_intent"))
            }

            // Whole-lineage obstacles cannot be hidden by ready rows later in sequence or another alias.
            for (obstacle in listOf("GAP", "PREFIX_GAP", "MISSING_DELIVERY", "OLD_ALIAS", "DELAYED", "DEAD_LETTER", "PREFIX")) {
                val edgeHome = newHome()
                val edgeK = ProtectedFixture().apply { allowNew = true }
                var edge = owner(edgeHome, edgeK)
                assertEquals(AccountProtectedStoreState.Existing(edge.initialize().storeId), edgeK.state)
                val edgePlan = AccountScopePreparation(uuid(), bound.copy(scopeId = uuid()), uuid(), binding.copy(deviceId = uuid()), 1, 0,
                    activationId = uuid())
                var edgeLease = select(edge, edgeK, edgePlan)
                repeat(3) { edge.createTrack(edgeLease, change()) }
                edge.close()
                closedSql(edgeHome) { sql ->
                    // Corruption is constructed ONLY in this closed disposable fixture, never a repair path.
                    when (obstacle) {
                        "GAP", "PREFIX_GAP" -> {
                            val missing = if (obstacle == "GAP") 1 else 2
                            sql.prepare("DELETE FROM j_event_delivery WHERE event_id IN (SELECT event_id FROM j_event WHERE sequence=?)").use {
                                it.bindLong(1, missing.toLong()); it.step()
                            }
                            sql.execSQL("DROP TRIGGER j_event_retain")
                            sql.prepare("DELETE FROM j_event WHERE sequence=?").use { it.bindLong(1, missing.toLong()); it.step() }
                            // Keep the recognized foundation DDL intact so the positive control reaches
                            // the intended lineage gap, rather than the additive install's shape refusal.
                            sql.execSQL("CREATE TRIGGER j_event_retain BEFORE DELETE ON j_event BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END")
                        }
                        "MISSING_DELIVERY" -> sql.execSQL("DELETE FROM j_event_delivery WHERE event_id IN (SELECT event_id FROM j_event WHERE sequence=1)")
                        "DELAYED", "PREFIX" -> sql.prepare("""UPDATE j_event_delivery SET next_retry_ms=?
                            WHERE event_id IN (SELECT event_id FROM j_event WHERE sequence=?)""").use {
                            it.bindLong(1, time + 60_000); it.bindLong(2, if (obstacle == "PREFIX") 2 else 1); it.step()
                        }
                        "DEAD_LETTER" -> sql.execSQL("""UPDATE j_event_delivery SET state='DEAD_LETTER',attempt=8
                            WHERE event_id IN (SELECT event_id FROM j_event WHERE sequence=1)""")
                        else -> Unit
                    }
                }
                edge = owner(edgeHome, edgeK)
                edge.initialize()
                val resumedEdge = edgePlan.copy(activationId = uuid(), expectedAuthorityRevision = edgeLease.authorityRevision + 1,
                    expectedActivationRevision = edgeLease.activationRevision)
                edgeLease = select(edge, edgeK, resumedEdge)
                if (obstacle == "OLD_ALIAS") {
                    edgeLease = select(edge, edgeK, resumedEdge.copy(bindingCommit = uuid(), profileId = uuid(),
                        expectedAuthorityRevision = edgeLease.authorityRevision + 1, expectedActivationRevision = edgeLease.activationRevision, activationId = uuid()))
                    edge.createTrack(edgeLease, change()) // New alias sequence 4 cannot jump unfinished 1-3.
                }
                when (obstacle) {
                    "DELAYED" -> assertNull(claim(edge, edgeLease))
                    "PREFIX", "PREFIX_GAP" -> {
                        val prefix = checkNotNull(claim(edge, edgeLease))
                        assertEquals(listOf(1L), prefix.events.map { it.sequence })
                        settle(edge, prefix.lease, AccountDeliveryFailure.RETRYABLE)
                        assertNull(claim(edge, edgeLease)) // Retry-delayed 1 also fences ready 3.
                    }
                    else -> refused(AccountFailure.ACCOUNT_DELIVERY_BLOCKED) { claim(edge, edgeLease) }
                }
                assertTrue(edge.readDelivery(edgeLease, null).filter { it.sequence > 1 }.all { it.attempts == 0 })
                assertTrue(edge.readDelivery(edgeLease, null).filter { it.sequence <= 3 }.all { it.originalProfileId == edgePlan.profileId })
                edge.close()
                closedSql(edgeHome) { sql ->
                    assertEquals(0L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
                    assertEquals(if (obstacle == "OLD_ALIAS") 5L else 4L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                }
            }

            // Real delegate commit, with a lost response or a refusal before COMMIT; assert exact readback.
            for (phase in listOf("CLAIM", "RENEW", "FAIL")) for (cut in listOf("BEFORE", "AFTER")) {
                val faultHome = newHome()
                val faultK = ProtectedFixture().apply { allowNew = true }
                val faults = SqlFaults()
                val faultStore = owner(faultHome, faultK, faults)
                assertEquals(AccountProtectedStoreState.Existing(faultStore.initialize().storeId), faultK.state)
                val faultPlan = AccountScopePreparation(uuid(), bound.copy(scopeId = uuid()), uuid(), binding.copy(deviceId = uuid()), 1, 0,
                    activationId = uuid())
                val faultLease = select(faultStore, faultK, faultPlan)
                faultStore.createTrack(faultLease, change())
                var delivery = if (phase == "CLAIM") null else checkNotNull(claim(faultStore, faultLease)).lease
                val commitsBeforeFault = faults.commits
                faults.commit = cut
                when (phase) {
                    "CLAIM" -> {
                        val ticket = faultStore.claimPending(faultLease)
                        if (cut == "BEFORE") {
                            refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { ticket.awaitResult() }
                            refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { ticket.awaitResult() }
                            assertEquals(0, faultStore.readDelivery(faultLease, null).single().attempts)
                        } else {
                            val result = checkNotNull(ticket.awaitResult())
                            assertSame(result, ticket.awaitResult())
                            delivery = result.lease
                            outstanding[faultStore] = result.lease
                        }
                    }
                    "RENEW" -> {
                        val original = checkNotNull(delivery)
                        val ticket = faultStore.renewDelivery(original)
                        if (cut == "BEFORE") refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { ticket.awaitResult() }
                        else {
                            delivery = ticket.awaitResult()
                            outstanding[faultStore] = checkNotNull(delivery)
                            assertEquals(original.revision + 1, checkNotNull(delivery).revision)
                            assertEquals(delivery, ticket.awaitResult())
                            refused(AccountFailure.ACCOUNT_LEASE_STALE) { faultStore.failDelivery(original, AccountDeliveryFailure.RETRYABLE) }
                        }
                    }
                    "FAIL" -> {
                        val exact = checkNotNull(delivery)
                        if (cut == "BEFORE") {
                            refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { faultStore.failDelivery(exact, AccountDeliveryFailure.RETRYABLE) }
                            assertEquals("IN_FLIGHT", faultStore.readDelivery(faultLease, null).single().rawState)
                        } else {
                            settle(faultStore, exact, AccountDeliveryFailure.RETRYABLE)
                            delivery = null
                            assertEquals("PENDING", faultStore.readDelivery(faultLease, null).single().rawState)
                        }
                    }
                }
                assertEquals(commitsBeforeFault + 1, faults.commits) // No automatic COMMIT/domain replay.
                if (phase == "CLAIM" && cut == "BEFORE") delivery = checkNotNull(claim(faultStore, faultLease)).lease
                delivery?.let { settle(faultStore, it, AccountDeliveryFailure.RETRYABLE) }
                assertEquals(1, faultStore.readDelivery(faultLease, null).single().attempts)
                faultStore.close()
                assertFalse(faultK.held.get())
                closedSql(faultHome) { sql ->
                    assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_track_ref"))
                    assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event"))
                    assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                    assertEquals(0L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
                }
            }

            // Real synthetic COMMIT followed by capture.close releasing its gate AND THEN throwing.
            // Both phases include ordinary success, settled lost-COMMIT-response and rollback controls.
            // The containing helper is wired once for future execution; no native/SQL evidence is claimed.
            for (phase in listOf("CLAIM", "RENEW")) for (cut in listOf("NONE", "AFTER", "BEFORE"))
                for (cleanupFailure in listOf(false, true)) coroutineScope {
                    val cleanupHome = newHome()
                    val cleanupK = ProtectedFixture().apply { allowNew = true }
                    val faults = SqlFaults()
                    val cleanupStore = owner(cleanupHome, cleanupK, faults)
                    assertEquals(AccountProtectedStoreState.Existing(cleanupStore.initialize().storeId), cleanupK.state)
                    val cleanupPlan = AccountScopePreparation(uuid(), bound.copy(scopeId = uuid()), uuid(), binding.copy(deviceId = uuid()), 1, 0,
                        activationId = uuid())
                    val account = select(cleanupStore, cleanupK, cleanupPlan)
                    cleanupStore.createTrack(account, change())
                    data class CommittedRow(
                        val state: String, val attempt: Long, val token: String?, val revision: Long,
                        val expiry: Long?, val workRevision: Long, val retry: Long,
                    )
                    var row: CommittedRow? = null
                    faults.afterCommit = { sql ->
                        sql.prepare("""SELECT d.state,d.attempt,d.lease_token,d.lease_revision,d.lease_expires_ms,
                            a.work_revision,d.next_retry_ms FROM j_event_delivery d CROSS JOIN j_activation a
                            WHERE a.singleton=1 LIMIT 2""").use {
                            assertTrue(it.step())
                            row = CommittedRow(it.getText(0), it.getLong(1), if (it.isNull(2)) null else it.getText(2),
                                it.getLong(3), if (it.isNull(4)) null else it.getLong(4), it.getLong(5), it.getLong(6))
                            assertFalse(it.step())
                        }
                    }
                    val initial = if (phase == "RENEW") cleanupStore.claimPending(account) else null
                    val original = initial?.awaitResult()?.lease
                    original?.let { outstanding[cleanupStore] = it }
                    val previousRow = row
                    val beforeCommits = faults.commits
                    val beforeApplied = faults.appliedCommits
                    val externalEntered = CountDownLatch(1)
                    val externalRelease = CountDownLatch(1)
                    val externalSettled = AtomicBoolean(false)
                    val external = launch(Dispatchers.Default) {
                        externalEntered.countDown()
                        check(externalRelease.await(5, TimeUnit.SECONDS))
                        externalSettled.set(true) // Actual synthetic external pass returned, not cancellation/expiry.
                    }
                    faults.beforeClose = { assertTrue(externalSettled.get()) }
                    try {
                        assertTrue(withContext(Dispatchers.IO) { externalEntered.await(5, TimeUnit.SECONDS) })
                        time += 10
                        ticks += 10_000_000L
                        faults.commit = cut
                        cleanupK.failCaptureClose = cleanupFailure
                        val ticket: AccountDeliveryOperation<out Any?> = if (phase == "CLAIM") cleanupStore.claimPending(account)
                            else cleanupStore.renewDelivery(checkNotNull(original))
                        // Keep this exact ticket before even a caller timeout on outcome recovery.
                        try { withTimeout(1) { ticket.awaitOutcome(); awaitCancellation() } }
                        catch (_: TimeoutCancellationException) { /* Recover the same immutable outcome. */ }
                        val outcome = ticket.awaitOutcome()
                        val afterStatements = faults.statements
                        val afterRow = row
                        assertSame(outcome, ticket.awaitOutcome())
                        assertEquals(beforeCommits + 1, faults.commits)
                        assertEquals(beforeApplied + if (cut == "BEFORE") 0 else 1, faults.appliedCommits)
                        assertEquals(if (cleanupFailure) 1 else 0, cleanupK.captureCloseFailures)
                        assertFalse(cleanupK.gate.get()) // Release really happened despite the exception.

                        val handle: AccountDeliverySettlement? = when {
                            cut == "BEFORE" -> {
                                assertTrue(outcome is AccountDeliveryOutcome.Failed)
                                refused(if (cleanupFailure) AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED
                                    else AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { ticket.awaitResult() }
                                assertEquals(previousRow, row) // No committed result invented from in-transaction data.
                                null
                            }
                            cleanupFailure -> {
                                assertTrue(outcome is AccountDeliveryOutcome.CommittedForSettlement)
                                val committed = outcome as AccountDeliveryOutcome.CommittedForSettlement
                                assertEquals(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED, committed.failure)
                                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                                assertSame(committed.handle, (ticket.awaitOutcome() as AccountDeliveryOutcome.CommittedForSettlement).handle)
                                committed.handle
                            }
                            else -> {
                                assertTrue(outcome is AccountDeliveryOutcome.Completed)
                                val result = ticket.awaitResult()
                                assertSame(result, ticket.awaitResult())
                                val exact = when (result) {
                                    is AccountDeliveryClaim -> result.lease
                                    is AccountDeliveryLease -> result
                                    else -> error("FIXTURE_RESULT")
                                }
                                AccountDeliverySettlement(ticket.operationId, exact)
                            }
                        }
                        assertEquals(afterStatements, faults.statements) // Repeated awaits execute no SQL at all.
                        assertEquals(afterRow, row)
                        handle?.let {
                            outstanding[cleanupStore] = it.lease
                            assertEquals(ticket.operationId, it.operationId)
                            val committed = checkNotNull(row)
                            assertEquals("IN_FLIGHT", committed.state)
                            assertEquals(1L, committed.attempt)
                            assertEquals(it.lease.token.value, committed.token)
                            assertEquals(it.lease.revision, committed.revision)
                            assertEquals(it.lease.revision, committed.workRevision)
                            assertEquals(it.lease.expiresAtMs, committed.expiry)
                            assertEquals(0L, committed.retry)
                            assertEquals(if (phase == "CLAIM") 1L else checkNotNull(original).revision + 1, it.lease.revision)
                            assertEquals(time + DesktopAccountLimits.LEASE_MS, it.lease.expiresAtMs)
                            original?.let { old ->
                                assertEquals(old.token, it.lease.token)
                                refused(AccountFailure.ACCOUNT_LEASE_STALE) { cleanupStore.failDelivery(old, AccountDeliveryFailure.RETRYABLE) }
                                refused(AccountFailure.ACCOUNT_LEASE_STALE) { cleanupStore.settleDelivery(
                                    AccountDeliverySettlement(checkNotNull(initial).operationId, old), AccountDeliveryFailure.RETRYABLE) }
                            }
                            // Even a current exact lease with another operation ID cannot consume this ticket.
                            refused(AccountFailure.ACCOUNT_LEASE_STALE) { cleanupStore.settleDelivery(
                                it.copy(operationId = initial?.operationId ?: uuid()), AccountDeliveryFailure.RETRYABLE) }
                        }
                        if (cleanupFailure) {
                            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { cleanupStore.readTracks(account, null) }
                            refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { cleanupStore.claimPending(account).awaitResult() }
                            (handle?.lease ?: original)?.let { exact -> refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) {
                                cleanupStore.renewDelivery(exact).awaitResult()
                            } }
                        } else assertEquals(1, cleanupStore.readTracks(account, null).size) // Ordinary success control.
                        assertEquals(beforeCommits + 1, faults.commits)
                        cleanupStore.retire()
                        val live = handle?.lease ?: original
                        if (live != null) {
                            time += DesktopAccountLimits.LEASE_MS + 1
                            ticks += (DesktopAccountLimits.LEASE_MS + 1) * 1_000_000
                            refused(AccountFailure.ACCOUNT_BUSY) { cleanupStore.close() }
                            assertEquals(0, faults.closes)
                            assertTrue(cleanupK.held.get())
                            assertFalse(externalSettled.get())
                            assertFalse(cleanupK.trace.contains("K_RELEASE"))
                            assertEquals(afterRow, row) // No expiry-based takeover, token/revision/attempt change.
                        }
                        refused(AccountFailure.ACCOUNT_CLOSED) { cleanupStore.claimPending(account).awaitResult() }
                        externalRelease.countDown()
                        external.join()
                        assertTrue(externalSettled.get())
                        cleanupK.allowed = false // Settlement must not clear this veto or reacquire capture authority.
                        cleanupK.denied = true
                        if (handle != null) cleanupStore.settleDelivery(handle, AccountDeliveryFailure.AUTHORITY_UNAVAILABLE)
                        else original?.let { cleanupStore.failDelivery(it, AccountDeliveryFailure.AUTHORITY_UNAVAILABLE) }
                        outstanding.remove(cleanupStore)
                        if (live != null) {
                            assertEquals("AUTHORITY_BLOCKED", checkNotNull(row).state)
                            assertEquals(1L, checkNotNull(row).attempt)
                            assertEquals(live.revision, checkNotNull(row).revision)
                            assertEquals(live.revision, checkNotNull(row).workRevision)
                            assertNull(checkNotNull(row).token)
                            assertNull(checkNotNull(row).expiry)
                            assertEquals(beforeCommits + 2, faults.commits)
                            handle?.let { refused(AccountFailure.ACCOUNT_LEASE_STALE) {
                                cleanupStore.settleDelivery(it, AccountDeliveryFailure.RETRYABLE)
                            } }
                        }
                        assertFalse(cleanupK.allowed)
                        assertTrue(cleanupK.denied)
                        cleanupStore.close()
                        assertEquals(1, faults.closes)
                        assertFalse(cleanupK.held.get())
                        assertEquals(listOf("K_LOCK", "K_RELEASE"), cleanupK.trace)
                        assertSame(outcome, ticket.awaitOutcome()) // Also recoverable after actual store close.
                        closedSql(cleanupHome) { sql ->
                            assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_track_ref"))
                            assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event"))
                            assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                            assertEquals(0L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
                            assertEquals(if (live == null) 0L else 1L, scalar(sql, "SELECT attempt FROM j_event_delivery"))
                        }
                    } finally { externalRelease.countDown(); external.join() }
                }

            // Remaining child-process gate: ambiguousCommitWorkerOnly supplies exact assertions for
            // unavailable/contradictory readback and rollback/empty-fence failure for claim/renew/fail.
            // Use cleanupFailure=true for claim/renew to combine unknown SQL with release-then-throw cleanup.
            // That owner must stay alive until the isolated worker exits; never force-close/delete it in
            // this composite's finally. No in-process ambiguity recovery or success is claimed here.

            // Reserved progress admission has both a positive and negative boundary control.
            val capacityHome = newHome()
            val privateFiles = AccountPrivateFiles(capacityHome)
            try {
                assertFalse(privateFiles.acquire())
                privateFiles.createNewDatabase()
                privateFiles.requireSpace(DesktopAccountLimits.MAIN_BYTES - 4_096, 4_096, progress = true)
                refused(AccountFailure.ACCOUNT_STORAGE_FULL) {
                    privateFiles.requireSpace(DesktopAccountLimits.MAIN_BYTES - 4_096, 4_096, progress = false)
                }
            } finally { privateFiles.close() }

            // Executor proof only: cancellation/timeout are not completion and do not release ownership.
            coroutineScope {
                val serial = AccountSerialOwner()
                val entered = CountDownLatch(1)
                val release = CountDownLatch(1)
                val settled = AtomicBoolean(false)
                val disposed = AtomicBoolean(false)
                val executionThreads = ArrayList<Long>()
                val firstCall = launch(Dispatchers.Default) {
                    serial.submit {
                        executionThreads.add(Thread.currentThread().id)
                        entered.countDown()
                        check(release.await(5, TimeUnit.SECONDS))
                        settled.set(true)
                    }
                }
                assertTrue(withContext(Dispatchers.IO) { entered.await(5, TimeUnit.SECONDS) })
                val queued = (1..7).map {
                    launch(start = CoroutineStart.UNDISPATCHED) { serial.submit { executionThreads.add(Thread.currentThread().id) } }
                }
                try {
                    refused(AccountFailure.ACCOUNT_BUSY) { serial.submit { Unit } }
                    firstCall.cancel()
                    val closing = launch(start = CoroutineStart.UNDISPATCHED) {
                        serial.close { assertTrue(settled.get()); disposed.set(true) }
                    }
                    assertFalse(closing.isCompleted)
                    assertFalse(disposed.get())
                    refused(AccountFailure.ACCOUNT_CLOSED) { serial.submit { Unit } }
                    release.countDown()
                    firstCall.join()
                    queued.forEach { it.join() }
                    closing.join()
                    assertTrue(disposed.get())
                    assertEquals(1, executionThreads.toSet().size)
                } finally { release.countDown(); serial.close { } }
            }
        }
        deliveryAndDrainCases()

        initialImportEntryDecisions()
    } finally {
        // Future cleanup is limited to literal disposable roots created above; never delete an unsettled DB.
        for ((store, lease) in outstanding.toMap()) store.failDelivery(lease, AccountDeliveryFailure.RETRYABLE)
        for (store in stores.asReversed()) store.close()
        for (root in homes) {
            val absolute = root.toAbsolutePath().normalize()
            check(absolute.fileName.toString().startsWith("autplay-j1-fixture-"))
            Files.walk(absolute).use { paths ->
                paths.sorted(Comparator.reverseOrder()).forEach { path ->
                    check(path.toAbsolutePath().normalize().startsWith(absolute))
                    Files.delete(path)
                }
            }
        }
    }
}
