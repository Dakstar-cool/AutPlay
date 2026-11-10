package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

public enum class DesktopSyncPhase { UNAVAILABLE, IDLE, NEGOTIATING, PUSHING, PULLING, BOOTSTRAPPING,
    SYNCED, PAUSED, OUTCOME_PENDING, RESET_REQUIRED, RETIRED }
public data class DesktopSyncDelivery(public val eventId: AccountUuid, public val sequence: Long,
    public val state: String, public val attempts: Int, public val nextRetryAtMs: Long)
public data class DesktopSyncViewState(public val phase: DesktopSyncPhase = DesktopSyncPhase.UNAVAILABLE,
    public val busy: Boolean = false, public val pendingCount: Long? = null, public val deadLetterCount: Long? = null,
    public val conflictCount: Long? = null, public val serverState: String? = null,
    public val deliveries: List<DesktopSyncDelivery> = emptyList(), public val acknowledgedThrough: Long? = null,
    public val retryAtMs: Long? = null, public val failure: DesktopSyncFailure? = null,
    public val retainedOperations: Int = 0,
    public val conflictDocuments: List<AccountConflictDocument> = emptyList(),
    public val nextConflict: AccountConflictCursor? = null)

/**
 * One production pass owner over J's actual transaction/ticket APIs. The host runs runLoop in its
 * existing lifecycle scope; this class creates no detached worker and promises no post-Quit work.
 */
public class DesktopSyncRuntime(private val store: DesktopAccountStore, private val transport: DesktopSyncTransport,
    private val binding: DesktopSyncBindingCoordinator) {
    private val pages = store.syncPages ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    private val domain = store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    private val running = AtomicBoolean(false)
    private val retired = AtomicBoolean(false)
    private val uncertain = AtomicBoolean(false)
    private val mutable = MutableStateFlow(DesktopSyncViewState(phase = DesktopSyncPhase.IDLE))
    public val state: StateFlow<DesktopSyncViewState> = mutable.asStateFlow()
    private var claimTicket: AccountDeliveryOperation<AccountDeliveryClaim?>? = null
    private var delivery: AccountDeliveryClaim? = null
    private var captureTicket: AccountPageOperation<AccountPageCapture>? = null
    private var capture: AccountPageCapture? = null
    private var application: AccountPageOperation<AccountPageReceipt>? = null
    private var io: DomainIoTicket? = null
    private var deferredPage: Pair<AccountPageKind, ByteArray>? = null
    private var deferredAck: Pair<List<AccountUuid>, ByteArray>? = null
    private var pullLimit = 100
    private var workOperation: AccountUuid? = null
    private var workLease: AccountLease? = null
    private var workClaim: AccountWorkClaim? = null
    private var queuedWork: AccountWorkDescriptor? = null
    private var workEnqueued = false
    private var workSettlement: AccountUuid? = null
    private var workSettlementState: String? = null
    private var workSettlementNext: Long? = null

    /** Explicit Retry calls this same path. It never resigns, rewrites, renumbers or rekeys an event. */
    public suspend fun retryPending(lease: AccountLease): DesktopSyncViewState = runPass(lease)

    public suspend fun runPass(lease: AccountLease): DesktopSyncViewState {
        if (retired.get() || uncertain.get() || !running.compareAndSet(false, true)) return mutable.value
        val caller = coroutineContext[Job]
        val started = System.nanoTime()
        transport.passDeadlineNanos = started + 60_000_000_000L
        fun admit() {
            if (retired.get() || caller?.isActive == false) throw CancellationException("ACCOUNT_SYNC_FENCED")
            transport.session.requireCurrent(transport.session.currentGeneration())
        }
        fun timeLeft(): Boolean = System.nanoTime() - started < 60_000_000_000L
        mutable.value = mutable.value.copy(busy = true, failure = null, retryAtMs = null)
        var complete = false
        try {
            admit()
            if (claimTicket != null || captureTicket != null || application != null || io != null || workClaim != null || workOperation != null)
                throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
            mutable.value = mutable.value.copy(phase = DesktopSyncPhase.NEGOTIATING)
            binding.confirm(lease)
            admit()
            val retained = store.recoveryReads?.deferredPage(lease)
            if (retained != null) {
                val current = pages.readSyncState(lease)
                require(retained.lineageId == current.lineageId)
                when (retained.rawKind) {
                    "ACK" -> if (deferredAck == null) deferredAck = retained.eventIds to retained.wireCopy()
                    "PULL" -> if (deferredPage == null) deferredPage = AccountPageKind.PULL to retained.wireCopy()
                    "BOOTSTRAP" -> if (deferredPage == null) deferredPage = AccountPageKind.BOOTSTRAP to retained.wireCopy()
                    else -> throw DesktopSyncException(DesktopSyncFailure.UNSUPPORTED)
                }
            }
            if (!timeLeft()) throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER, 1000)
            val work = queuedWork ?: AccountWorkDescriptor(AccountUuid.random(), AccountWorkKind.SYNC,
                lease.scopeId, 1, System.currentTimeMillis()).also { queuedWork = it }
            if (!workEnqueued) { domain.enqueueWork(lease, work.workId, work); workEnqueued = true }
            val operation = AccountUuid.random(); workOperation = operation; workLease = lease
            workClaim = domain.claimWork(lease, operation, work.workId)
            if (workClaim == null) { workOperation = null; workLease = null; throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE) }
            workOperation = null
            var pushes = 0
            while (deferredPage == null && pushes < 5 && timeLeft()) {
                admit()
                val sync = pages.readSyncState(lease)
                val ticket = store.claimPending(lease); claimTicket = ticket
                when (val result = ticket.awaitOutcome()) {
                    is AccountDeliveryOutcome.Completed -> { delivery = result.result; claimTicket = null }
                    is AccountDeliveryOutcome.CommittedForSettlement -> {
                        uncertain.set(true)
                        store.settleDelivery(result.handle, AccountDeliveryFailure.AUTHORITY_UNAVAILABLE)
                        claimTicket = null; throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
                    }
                    is AccountDeliveryOutcome.Failed -> throw AccountStoreException(result.failure)
                }
                val claimed = delivery ?: break
                admit()
                val old = deferredAck
                if (old != null && old.first != claimed.events.map { it.eventId }) throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
                if (old != null) applyAck(claimed, old.second)
                else {
                    transport.push(claimed, sync) { io = it }.use { response ->
                        io = null; transport.requireSuccess(response); admit()
                        applyAck(claimed, response.bytes)
                    }
                }
                pushes++
                if (deferredAck != null) break
            }
            var pulled = 0
            while (pulled < 10 && timeLeft()) {
                admit()
                val sync = pages.readSyncState(lease)
                if (sync.bootstrapState !in setOf("READY", "NOT_STARTED", "RESET_REQUIRED", "BOOTSTRAPPING"))
                    throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
                val kind = if (sync.bootstrapState == "READY") AccountPageKind.PULL else AccountPageKind.BOOTSTRAP
                mutable.value = mutable.value.copy(phase = if (kind == AccountPageKind.PULL) DesktopSyncPhase.PULLING else DesktopSyncPhase.BOOTSTRAPPING)
                val ticket = pages.capturePage(lease, kind); captureTicket = ticket
                when (val outcome = ticket.awaitOutcome()) {
                    is AccountPageOutcome.Completed -> { capture = outcome.result; captureTicket = null }
                    is AccountPageOutcome.Retained -> {
                        uncertain.set(true)
                        capture = outcome.result; captureTicket = null
                        throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
                    }
                    is AccountPageOutcome.Failed -> throw AccountStoreException(outcome.failure)
                }
                val exact = checkNotNull(capture)
                admit()
                val retained = deferredPage
                val receipt = if (retained != null) {
                    check(retained.first == kind) { "ACCOUNT_SYNC_DEFERRED_KIND_CHANGED" }
                    applyPage(exact, retained.second)
                } else {
                    try {
                        transport.page(exact, pullLimit) { io = it }.use { response ->
                            io = null; transport.requireSuccess(response); admit(); applyPage(exact, response.bytes)
                        }
                    } catch (failure: DesktopSyncException) {
                        val reset = when (failure.failure) {
                            DesktopSyncFailure.CURSOR_INVALID -> AccountPageReset.CURSOR_INVALID
                            DesktopSyncFailure.BOOTSTRAP_SNAPSHOT_INVALID -> AccountPageReset.BOOTSTRAP_SNAPSHOT_INVALID
                            // Device reset needs a new authenticated device/lineage, never a same-device bootstrap.
                            DesktopSyncFailure.DEVICE_RESET_REQUIRED -> throw failure
                            else -> throw failure
                        }
                        val resetTicket = pages.resetPage(exact, reset); application = resetTicket
                        val resetReceipt = pageResult(resetTicket)
                        application = null; capture = null
                        resetReceipt
                    }
                }
                pulled++
                if (receipt.state == "DEFERRED") {
                    mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = DesktopSyncFailure.CAPACITY)
                    break
                }
                if (receipt.state == "RESET_REQUIRED") continue
                if (!receipt.hasMore) { complete = true; break }
            }
            admit()
            refresh(lease)
            if (complete && deferredAck == null && deferredPage == null && mutable.value.pendingCount == 0L && mutable.value.serverState == "SYNCED")
                mutable.value = mutable.value.copy(phase = DesktopSyncPhase.SYNCED)
            else if ((mutable.value.conflictCount ?: 0) > 0)
                mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = DesktopSyncFailure.CONFLICT)
            else if (mutable.value.phase !in setOf(DesktopSyncPhase.PAUSED, DesktopSyncPhase.OUTCOME_PENDING))
                mutable.value = mutable.value.copy(phase = DesktopSyncPhase.IDLE)
        } catch (_: CancellationException) {
            mutable.value = mutable.value.copy(phase = if (retired.get()) DesktopSyncPhase.RETIRED else DesktopSyncPhase.PAUSED)
        } catch (failure: DesktopSyncException) {
            mutable.value = mutable.value.copy(phase = when (failure.failure) {
                DesktopSyncFailure.DEVICE_RESET_REQUIRED -> DesktopSyncPhase.RESET_REQUIRED
                DesktopSyncFailure.OUTCOME_PENDING -> DesktopSyncPhase.OUTCOME_PENDING
                else -> DesktopSyncPhase.PAUSED
            }, failure = failure.failure, retryAtMs = failure.retryAfterMs.takeIf { it > 0 }?.let { System.currentTimeMillis() + it })
        } catch (failure: AccountStoreException) {
            mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = when (failure.failure) {
                AccountFailure.ACCOUNT_DELIVERY_BLOCKED -> DesktopSyncFailure.DELIVERY_BLOCKED
                AccountFailure.ACCOUNT_STORAGE_FULL -> DesktopSyncFailure.CAPACITY
                AccountFailure.ACCOUNT_SCOPE_CHANGED -> DesktopSyncFailure.CONTEXT_CHANGED
                AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE -> DesktopSyncFailure.AUTH_REQUIRED
                else -> DesktopSyncFailure.OUTCOME_PENDING
            })
        } catch (failure: AccountControlException) {
            mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = when (failure.code) {
                AccountControlFailure.TRUST_REQUIRED -> DesktopSyncFailure.TRUST_REQUIRED
                AccountControlFailure.AUTHENTICATION_REQUIRED, AccountControlFailure.FORBIDDEN -> DesktopSyncFailure.AUTH_REQUIRED
                AccountControlFailure.CONTEXT_CHANGED -> DesktopSyncFailure.CONTEXT_CHANGED
                AccountControlFailure.CAPACITY -> DesktopSyncFailure.CAPACITY
                else -> DesktopSyncFailure.UNAVAILABLE
            })
        } catch (_: Exception) {
            mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = DesktopSyncFailure.UNAVAILABLE)
        } finally {
            withContext(NonCancellable) {
                try { drainRetained(lease) } catch (_: Exception) {
                    uncertain.set(true)
                    mutable.value = mutable.value.copy(phase = DesktopSyncPhase.OUTCOME_PENDING, failure = DesktopSyncFailure.OUTCOME_PENDING)
                }
            }
            mutable.value = mutable.value.copy(busy = false, retainedOperations = retainedCount())
            transport.passDeadlineNanos = null
            running.set(false)
        }
        return mutable.value
    }

    private suspend fun applyAck(claim: AccountDeliveryClaim, bytes: ByteArray) {
        mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PUSHING)
        val ticket = pages.applyAckPage(claim.lease, bytes); application = ticket
        val receipt = pageResult(ticket)
        application = null
        if (receipt.state == "ACK_APPLIED") {
            delivery = null; deferredAck?.second?.fill(0); deferredAck = null
            mutable.value = mutable.value.copy(acknowledgedThrough = receipt.acknowledgedThrough)
        } else {
            if (deferredAck == null) deferredAck = claim.events.map { it.eventId } to bytes.copyOf()
            mutable.value = mutable.value.copy(phase = DesktopSyncPhase.PAUSED, failure = DesktopSyncFailure.CAPACITY)
            throw DesktopSyncException(DesktopSyncFailure.CAPACITY)
        }
    }
    private suspend fun applyPage(exact: AccountPageCapture, bytes: ByteArray): AccountPageReceipt {
        val ticket = if (exact.kind == AccountPageKind.PULL) pages.applyPullPage(exact, bytes) else pages.applyBootstrapPage(exact, bytes)
        application = ticket
        val result = pageResult(ticket)
        application = null; capture = null
        if (result.state == "DEFERRED") {
            if (deferredPage == null) deferredPage = exact.kind to bytes.copyOf()
            if (exact.kind == AccountPageKind.PULL) pullLimit = maxOf(1, pullLimit / 2)
        } else { deferredPage?.second?.fill(0); deferredPage = null }
        return result
    }
    private suspend fun pageResult(ticket: AccountPageOperation<AccountPageReceipt>): AccountPageReceipt = when (val result = ticket.awaitOutcome()) {
        is AccountPageOutcome.Completed -> result.result
        is AccountPageOutcome.Retained -> { uncertain.set(true); throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING) }
        is AccountPageOutcome.Failed -> throw AccountStoreException(result.failure)
    }
    private suspend fun drainRetained(lease: AccountLease) {
        io?.let { ticket -> ticket.settled().handle { reply, _ -> reply?.close(); Unit }.settledAwait(); io = null }
        application?.let { ticket ->
            when (val result = ticket.awaitOutcome()) {
                is AccountPageOutcome.Completed -> { if (result.result.state == "ACK_APPLIED") delivery = null else if (result.result.state in setOf("APPLIED", "DEFERRED", "RESET_REQUIRED")) capture = null }
                is AccountPageOutcome.Retained -> { if (result.result.state == "ACK_APPLIED") delivery = null else capture = null }
                is AccountPageOutcome.Failed -> throw AccountStoreException(result.failure)
            }
            application = null
        }
        claimTicket?.let { ticket ->
            when (val result = ticket.awaitOutcome()) {
                is AccountDeliveryOutcome.Completed -> delivery = result.result
                is AccountDeliveryOutcome.CommittedForSettlement -> store.settleDelivery(result.handle, AccountDeliveryFailure.AUTHORITY_UNAVAILABLE)
                is AccountDeliveryOutcome.Failed -> throw AccountStoreException(result.failure)
            }
            claimTicket = null
        }
        delivery?.let {
            store.failDelivery(it.lease, if (retired.get()) AccountDeliveryFailure.AUTHORITY_UNAVAILABLE else AccountDeliveryFailure.RETRYABLE,
                mutable.value.retryAtMs?.let { time -> maxOf(0, time - System.currentTimeMillis()) } ?: 0)
            delivery = null
        }
        captureTicket?.let { ticket ->
            when (val result = ticket.awaitOutcome()) {
                is AccountPageOutcome.Completed -> capture = result.result
                is AccountPageOutcome.Retained -> capture = result.result
                is AccountPageOutcome.Failed -> throw AccountStoreException(result.failure)
            }
            captureTicket = null
        }
        capture?.let { pages.settlePage(it); capture = null }
        val workOwner = workLease ?: lease
        workOperation?.let { operation ->
            workClaim = domain.recoverWorkOutcome(workOwner, operation)
                ?: throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
            workOperation = null
        }
        workClaim?.let { claim ->
            val pending = mutable.value.phase != DesktopSyncPhase.SYNCED
            val settlement = workSettlement ?: AccountUuid.random().also { workSettlement = it
                workSettlementState = if (pending) "PENDING" else "COMPLETED"
                workSettlementNext = mutable.value.retryAtMs ?: (System.currentTimeMillis() + 1000) }
            domain.settleWork(workOwner, settlement, claim, checkNotNull(workSettlementState), checkNotNull(workSettlementNext))
            if (workSettlementState == "COMPLETED") { queuedWork = null; workEnqueued = false }
            workClaim = null; workLease = null
            workSettlement = null; workSettlementState = null; workSettlementNext = null
        }
    }
    public suspend fun refresh(lease: AccountLease): DesktopSyncViewState {
        val sync = pages.readSyncState(lease)
        val deliveryPage = store.readDelivery(lease, null, 100)
        mutable.value = mutable.value.copy(pendingCount = sync.pendingEventCount, deliveries = deliveryPage.map {
            DesktopSyncDelivery(it.eventId, it.sequence, it.rawState, it.attempts, it.nextRetryAtMs)
        })
        transport.status(lease, sync).use { response ->
            transport.requireSuccess(response)
            val root = DomainJson.document(response.bytes, 65_536)
            require(DomainJson.number(root, "protocol_version") == 1L && DomainJson.text(root, "device_id") == sync.deviceId.value &&
                DomainJson.text(root, "server_profile_id") == lease.profileId.value && DomainJson.text(root, "journal_epoch") == sync.journalEpoch.value)
            mutable.value = mutable.value.copy(serverState = DomainJson.text(root, "state", 100),
                conflictCount = DomainJson.number(root, "conflict_count"), deadLetterCount = DomainJson.number(root, "dead_letter_count"))
        }
        readConflicts(lease)
        return mutable.value
    }
    public suspend fun readConflicts(lease: AccountLease, after: AccountConflictCursor? = null): Unit {
        val owner = store.recoveryReads ?: throw DesktopSyncException(DesktopSyncFailure.UNSUPPORTED)
        val page = owner.conflicts(lease,after,1)
        if (retired.get()) throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
        mutable.value = mutable.value.copy(conflictDocuments=page.documents,nextConflict=page.next)
    }
    public suspend fun runLoop(lease: AccountLease) {
        while (!retired.get()) {
            coroutineContext.ensureActive()
            val outcome = runPass(lease)
            if (outcome.phase in setOf(DesktopSyncPhase.RESET_REQUIRED, DesktopSyncPhase.OUTCOME_PENDING, DesktopSyncPhase.RETIRED) ||
                outcome.failure in setOf(DesktopSyncFailure.AUTH_REQUIRED, DesktopSyncFailure.TRUST_REQUIRED, DesktopSyncFailure.CAPACITY,
                    DesktopSyncFailure.DELIVERY_BLOCKED, DesktopSyncFailure.UNSUPPORTED, DesktopSyncFailure.CONTEXT_CHANGED)) return
            val delayedHead = outcome.deliveries.filter { it.state == "PENDING" }.minOfOrNull { it.nextRetryAtMs }
            val due = maxOf(outcome.retryAtMs ?: 0, delayedHead ?: 0)
            delay(if (due > System.currentTimeMillis()) due - System.currentTimeMillis() else if (outcome.phase == DesktopSyncPhase.SYNCED) 30_000 else 1000)
        }
    }
    /** Synchronous admission fence. Host keeps this owner alive until runPass and drain return. */
    public fun retire() { retired.set(true); transport.session.retire(); mutable.value = mutable.value.copy(phase = DesktopSyncPhase.RETIRED,
        deliveries = emptyList(), pendingCount = null, deadLetterCount = null, conflictCount = null, serverState = null,
        conflictDocuments=emptyList(),nextConflict=null) }
    public suspend fun drain(lease: AccountLease) {
        check(!running.get()) { "ACCOUNT_SYNC_PASS_DRAINING" }
        withContext(NonCancellable) { transport.session.drain().settledAwait(); drainRetained(lease) }
    }
    private fun retainedCount(): Int = listOf(claimTicket, delivery, captureTicket, capture, application, io, workOperation, workClaim).count { it != null }
}
