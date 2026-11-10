package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import app.autplay.application.portableaccount.PortableAccountDocument
import app.autplay.application.portableaccount.PortableAccountLineage
import app.autplay.application.securestate.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

/** Uses actual protected current/predecessor ROOT documents; a public ID is never an ownership proof. */
public class DesktopPortableDataTransport(private val sync: DesktopSyncTransport) : AccountPortableDataTransport {
    internal var deadlineNanos: Long? = null
    private fun deadline(): Long {
        val left = deadlineNanos?.let { (it - System.nanoTime()) / 1_000_000 } ?: 20_000
        if (left < 100) throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER, 1000)
        return minOf(20_000, left)
    }
    override suspend fun download(lease: AccountLease, accountId: AccountUuid, source: String,
        afterTime: String, afterId: AccountUuid): ByteArray {
        val proof = verifyLineage(lease, accountId)
        sync.session.request(lease, DomainRoute.PortableRead(source, afterTime, afterId), deadlineMs = deadline()).use { reply ->
            sync.requireSuccess(reply)
            val root = DomainJson.document(reply.bytes, DesktopAccountLimits.PORTABLE_PAGE_BYTES)
            require(DomainJson.text(root, "account_id", 36) == accountId.value)
            // J owns exact original bytes, raw-source cursor advancement, aliases and record merging.
            revalidate(proof)
            return reply.bytes.copyOf()
        }
    }
    override suspend fun upload(lease: AccountLease, accountId: AccountUuid, records: List<AccountPortableRecord>) {
        require(records.size in 1..32 && records.map { it.recordId }.distinct().size == records.size)
        val proof = verifyLineage(lease, accountId)
        val body = DomainJson.canonical(buildJsonObject {
            put("account_id", accountId.value); put("records", JsonArray(records.map { AccountPortableV1.decode(it.wire()).wire() }))
        })
        try {
            require(body.size <= DesktopAccountLimits.PORTABLE_PAGE_BYTES)
            sync.session.request(lease, DomainRoute.PortableWrite(), body, deadlineMs = deadline()).use { reply ->
                sync.requireSuccess(reply)
                val result = DomainJson.document(reply.bytes, 65_536)
                require(result.keys == setOf("account_id", "accepted") && DomainJson.text(result, "account_id", 36) == accountId.value)
                val accepted = result["accepted"] as? JsonArray ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
                require(accepted.size == records.size)
                // Existing server may return a preexisting logical ID when listening aliases converge.
                accepted.forEach { value -> val p = value as? JsonPrimitive; require(p?.isString == true); AccountUuid(requireNotNull(p).content) }
                revalidate(proof)
            }
        } finally { body.fill(0) }
    }
    private class RootWitness(val session: DomainSessionCapture, val rootValueId: String) {
        override fun toString(): String = "PortableRootWitness(<redacted>)"
    }
    private suspend fun verifyLineage(lease: AccountLease, accountId: AccountUuid): RootWitness {
        coroutineContext.ensureActive()
        val captured = sync.session.capture(lease)
        require(captured.owner.account == accountId.value)
        val secure = sync.session.runtime.secure
        val slot = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)
        val reference = captured.control.selected.singleOrNull { it.slot == slot }
            ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
        sync.session.request(lease, DomainRoute.PortableCurrent(), deadlineMs = deadline()).use { reply ->
            sync.requireSuccess(reply)
            val response = DomainJson.document(reply.bytes, 16_384)
            require(response.keys == setOf("account", "public_id"))
            val raw = DomainJson.canonical(response.getValue("account"))
            val remote = try { PortableAccountDocument.parse(raw) } finally { raw.fill(0) }
            val record = secure.read(slot, captured.control).settledAwait() ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
            try {
                val root = record.payload as? PortableRootPayload ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
                require(record.owner.account == accountId.value && !record.owner.deletionVeto && !record.owner.detached)
                val passport = root.passportCopy(); val history = root.lineageCopy()
                try {
                    val current = PortableAccountDocument.parse(passport)
                    require(current.accountId == accountId.value && remote.accountId == current.accountId &&
                        remote.publicId == current.publicId && DomainJson.text(response, "public_id", 24) == remote.publicId)
                    PortableAccountLineage.parse(current, history.toString(Charsets.UTF_8)).use { lineage ->
                        require((listOf(current) + lineage.predecessors.map { it.document }).any { ancestor ->
                            ancestor.fields == remote.fields && ancestor.publicKeySpki.contentEquals(remote.publicKeySpki)
                        }) { "ACCOUNT_PORTABLE_ROOT_LINEAGE_REQUIRED" }
                    }
                } finally { passport.fill(0); history.fill(0) }
            } finally { record.close() }
        }
        return RootWitness(captured, reference.valueId).also { revalidate(it) }
    }
    private suspend fun revalidate(witness: RootWitness) {
        sync.session.revalidate(witness.session)
        val current = sync.session.runtime.secure.snapshot().settledAwait()
        require(current.selected.singleOrNull { it.slot == SecureRecordKind.PORTABLE_ROOT.reserved }?.valueId == witness.rootValueId)
    }
}

public enum class DesktopPortablePhase { UNAVAILABLE, IDLE, RUNNING, COMPLETE, CONTINUE, PAUSED, OUTCOME_PENDING, RETIRED }
public data class DesktopPortableViewState(public val phase: DesktopPortablePhase = DesktopPortablePhase.IDLE,
    public val statistics: AccountPortableStatistics? = null, public val failure: DesktopSyncFailure? = null)

/** Drives the real J per-source cursors and normalized alias statistics, with no server-total addition. */
public class DesktopPortableDataRuntime(private val store: DesktopAccountStore, sync: DesktopSyncTransport) {
    private val data = store.portableData ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    private val transport = DesktopPortableDataTransport(sync)
    private val coordinator = AccountPortablePassCoordinator(data, transport)
    private val busy = AtomicBoolean(false)
    private val retired = AtomicBoolean(false)
    private val mutable = MutableStateFlow(DesktopPortableViewState())
    public val state: StateFlow<DesktopPortableViewState> = mutable.asStateFlow()
    public suspend fun runPass(lease: AccountLease): DesktopPortableViewState {
        if (retired.get() || !busy.compareAndSet(false, true)) return mutable.value
        mutable.value = DesktopPortableViewState(DesktopPortablePhase.RUNNING, mutable.value.statistics)
        transport.deadlineNanos = System.nanoTime() + 60_000_000_000L
        try {
            coroutineContext.ensureActive()
            val result = coordinator.run(lease, AccountUuid.random())
            coroutineContext.ensureActive()
            mutable.value = DesktopPortableViewState(if (result == AccountPortablePassOutcome.COMPLETE)
                DesktopPortablePhase.COMPLETE else DesktopPortablePhase.CONTINUE, data.statistics(lease))
        } catch (failure: DesktopSyncException) {
            mutable.value = mutable.value.copy(phase = DesktopPortablePhase.PAUSED, failure = failure.failure)
        } catch (_: Exception) {
            mutable.value = mutable.value.copy(phase = DesktopPortablePhase.OUTCOME_PENDING, failure = DesktopSyncFailure.OUTCOME_PENDING)
        } finally { transport.deadlineNanos = null; busy.set(false) }
        return mutable.value
    }
    public suspend fun refresh(lease: AccountLease): AccountPortableStatistics = data.statistics(lease).also {
        mutable.value = mutable.value.copy(statistics = it)
    }
    public fun retire() { retired.set(true); mutable.value = mutable.value.copy(phase = DesktopPortablePhase.RETIRED) }
}
