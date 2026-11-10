package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.ConcurrentHashMap
import java.util.UUID
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/** Await the actual owner, not a cancellation-prone observation of its result. */
internal suspend fun <T> CompletionStage<T>.settledAwait(): T = suspendCoroutine { continuation ->
    whenComplete { value, error -> if (error == null) continuation.resume(value) else continuation.resumeWithException(error) }
}

internal class DomainSessionCapture(val lease: AccountLease, val lifecycleGeneration: Long, val secureGeneration: Long,
    val control: SecureControl, val owner: SecureRecordOwner, val server: AccountSelectedServer,
    val guard: CapabilityGuard, val m5: M5CapabilityGuard, val transportPolicy: ProtectedTransportPolicy) {
    override fun toString(): String = "DomainSessionCapture(<redacted>)"
}

/** Concrete credential adapter over the actual K owner. No bearer is stored in J or UI state. */
public class DesktopSyncSession(private val account: DesktopAccountRuntime, private val http: DesktopDomainHttp,
    private val currentAuthorityLease: () -> CompletionStage<AccountLease>) {
    private val retired = AtomicBoolean(false)
    private val guardPublication = AtomicReference<AccountLease?>(null)
    private val generation = AtomicLong(1)
    private val controls = ConcurrentHashMap<UUID, AccountIoTicket>()
    internal val runtime: DesktopAccountRuntime get() = account
    internal fun currentGeneration(): Long = generation.get()
    internal fun requireCurrent(expected: Long) {
        if (retired.get() || guardPublication.get() != null || expected != generation.get()) throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }
    /** Invalidate every old capture before K/J guard publication; actual IO still has to drain. */
    internal fun fenceGuardPublication(lease: AccountLease): Unit { guardPublication.set(lease); generation.incrementAndGet() }
    /** Production calls this only after the sole authority's actual currentLease readback. */
    public fun authorityReconciled(lease: AccountLease): Boolean {
        if (retired.get()) return false
        val previous = guardPublication.get() ?: return true
        if (lease == previous || !guardPublication.compareAndSet(previous, null)) return false
        generation.incrementAndGet()
        return true
    }
    internal suspend fun capture(lease: AccountLease): DomainSessionCapture {
        val lifecycle = generation.get(); requireCurrent(lifecycle)
        if (currentAuthorityLease().settledAwait() != lease) throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        val secureGeneration = account.secure.admissionGeneration
        val snapshot = account.secure.snapshot().settledAwait()
        val server = account.selected ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        val slot = snapshot.activeBinding ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        val record = account.secure.read(slot, snapshot).settledAwait()
            ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        try {
            val owner = record.owner
            val payload = record.payload as? SessionPayload ?: throw AccountStoreException(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            val witness = owner.accountProjection ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            requireCurrent(lifecycle); account.secure.requireAdmission(secureGeneration)
            account.requireTarget(owner, server)
            if (!(owner.stage == SecureStage.ACTIVE && !owner.deletionVeto && !owner.detached && owner.boot == account.secure.boot &&
                owner.authorityRevision == snapshot.authorityRevision && lease.profileId.value == owner.profile &&
                lease.bindingCommit.value == owner.bindingCommit && lease.authorityRevision == owner.authorityRevision &&
                lease.sessionFamily?.value == owner.family && lease.sessionGeneration == owner.sessionGeneration &&
                lease.capabilityRevision == payload.d1.revision && witness.lifecycleAllowed && witness.stage == AccountProtectedStage.READY &&
                witness.storeId == lease.storeId && witness.preparation.scope.scopeId == lease.scopeId &&
                witness.preparation.profileId == lease.profileId && witness.preparation.bindingCommit == lease.bindingCommit &&
                witness.preparation.expectedActivationRevision + 1 == lease.activationRevision &&
                witness.currentAuthorityRevision == lease.authorityRevision))
                throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            val m5 = payload.m5 ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            val policyBytes = payload.transportPolicyCopy()
            val policy = try { SecureMediaGuardCodec.readTransport(policyBytes) } finally { policyBytes.fill(0) }
            val selected = server.transport.protectedPolicy(policy.revision)
            if (!(policy.known && policy.apiMode == selected.apiMode && policy.apiPin == selected.apiPin &&
                policy.streamMode == selected.streamMode && policy.streamPin == selected.streamPin))
                throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            return DomainSessionCapture(lease, lifecycle, secureGeneration, snapshot, owner, server, payload.d1, m5, policy)
        } finally { record.close() }
    }
    internal suspend fun control(capture: DomainSessionCapture, endpoint: AccountEndpoint, deadlineMs: Long = 20_000): ByteArray {
        coroutineContext.ensureActive()
        requireCurrent(capture.lifecycleGeneration); account.secure.requireAdmission(capture.secureGeneration)
        val access = if (endpoint.bearer) account.secure.access(capture.control, capture.owner).settledAwait() else null
        val bytes = access?.copy() ?: byteArrayOf()
        try {
            coroutineContext.ensureActive(); requireCurrent(capture.lifecycleGeneration)
            val ticket = account.transport.submit(capture.server.transport, endpoint, bearer = bytes, deadlineMs = deadlineMs)
            controls[ticket.id] = ticket
            try {
                return when (val result = ticket.settled().settledAwait()) {
                    is AccountIoOutcome.Completed -> result.response.use { it.bodyCopy() }
                    is AccountIoOutcome.Failed -> throw AccountControlException(result.code)
                }
            } finally { controls.remove(ticket.id) }
        } finally { bytes.fill(0); access?.close() }
    }
    internal suspend fun request(lease: AccountLease, route: DomainRoute, body: ByteArray = byteArrayOf(),
        onTicket: (DomainIoTicket) -> Unit = {}, deadlineMs: Long = 20_000): DomainResponse {
        coroutineContext.ensureActive()
        val lifecycle = generation.get(); val captured = capture(lease)
        val access = account.secure.access(captured.control, captured.owner).settledAwait()
        try {
            coroutineContext.ensureActive(); requireCurrent(lifecycle)
            val ticket = http.start(captured.server.transport, route, body, access, {
                requireCurrent(lifecycle); account.secure.requireAdmission(captured.secureGeneration)
            }, deadlineMs)
            onTicket(ticket)
            return ticket.settled().settledAwait()
        } finally { access.close() }
    }
    /** Capture the exact same owner again before accepting a late network result. */
    internal suspend fun revalidate(expected: DomainSessionCapture) {
        val fresh = capture(expected.lease)
        if (!(fresh.owner == expected.owner && fresh.guard == expected.guard && fresh.m5 == expected.m5 &&
            fresh.transportPolicy == expected.transportPolicy && fresh.secureGeneration == expected.secureGeneration))
            throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }
    public fun retire(): Unit { retired.set(true); generation.incrementAndGet(); http.retire(); account.transport.retire() }
    public fun outstandingIoCount(): Int = http.outstandingCount() + controls.size
    public fun drain(): CompletionStage<Unit> = java.util.concurrent.CompletableFuture.allOf(
        http.drain().toCompletableFuture(), *controls.values.map { it.settled().toCompletableFuture() }.toTypedArray()).thenApply { Unit }
}
