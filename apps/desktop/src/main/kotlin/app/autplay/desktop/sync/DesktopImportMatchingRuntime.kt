package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

public typealias DesktopMatchAction = AccountImportReviewAction
public enum class DesktopMatchBoundary { CANDIDATE_EVIDENCE_UNAVAILABLE, DURABLE_DECISION_ACTION_UNAVAILABLE,
    PREDECESSOR_CHANGED, ENTRY_NOT_IN_REPORT_PAGE, NO_CURRENT_PREDECESSOR, POLICY_UNAVAILABLE }
public data class DesktopMatchReviewItem(public val reference: AccountImportReview,
    public val rawStatus: String?, public val rawResolverState: String?, public val candidateCount: Long?,
    public val currentPredecessor: AccountUuid?, public val boundaries: Set<DesktopMatchBoundary>,
    public val context: DesktopImportReviewContext? = null)
public data class DesktopImportMatchingState(public val items: List<DesktopMatchReviewItem> = emptyList(),
    public val nextAfter: AccountUuid? = null, public val paused: Boolean = false, public val busy: Boolean = false,
    public val available: Boolean = false, public val failure: DesktopSyncFailure? = null)

/**
 * L03 reads only jobs already linked by owned J review records. It neither starts/imports/uploads a
 * remote job nor discovers/downloads music. T02/T03 remain deferred. I1 supplies owner-scoped
 * complete review contexts and a named J command family; explicit confirmation precedes HTTP.
 */
public class DesktopImportMatchingRuntime(private val store: DesktopAccountStore, private val sync: DesktopSyncTransport) {
    private val domain = store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    public val decisions: DesktopImportDecisionRuntime = DesktopImportDecisionRuntime(store,sync)
    private val closed = AtomicBoolean(false)
    private val busy = AtomicBoolean(false)
    private val mutable = MutableStateFlow(DesktopImportMatchingState())
    public val state: StateFlow<DesktopImportMatchingState> = mutable.asStateFlow()
    private var exactLease: AccountLease? = null
    public fun ownsLease(lease: AccountLease): Boolean = !closed.get() && exactLease==lease
    private fun admit() { if (closed.get() || mutable.value.paused) throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE) }

    public suspend fun refresh(lease: AccountLease, after: AccountUuid? = null): DesktopImportMatchingState {
        admit(); coroutineContext.ensureActive()
        if (!busy.compareAndSet(false, true)) return mutable.value
        mutable.value = mutable.value.copy(busy = true, failure = null)
        try {
            val references = domain.readImportReviews(lease, after, 100)
            decisions.refresh(lease)
            admit(); coroutineContext.ensureActive()
            // At most 100 owned references; reporting is lazy and explicit per selected job below.
            exactLease = lease
            mutable.value = DesktopImportMatchingState(references.map { reference ->
                DesktopMatchReviewItem(reference, null, null, null, null, setOf(
                    DesktopMatchBoundary.CANDIDATE_EVIDENCE_UNAVAILABLE))
            }, references.lastOrNull()?.reviewId?.takeIf { references.size == 100 }, available = true)
        } catch (failure: DesktopSyncException) { mutable.value = mutable.value.copy(failure = failure.failure) }
        catch (_: Exception) { mutable.value = mutable.value.copy(available = false, failure = DesktopSyncFailure.UNAVAILABLE) }
        finally { busy.set(false); mutable.value = mutable.value.copy(busy = false) }
        return mutable.value
    }
    public suspend fun inspect(lease: AccountLease, item: DesktopMatchReviewItem): DesktopMatchReviewItem {
        admit(); coroutineContext.ensureActive(); owned(lease, item)
        val context = decisions.context(lease,item.reference.serverJobId,item.reference.serverEntryId)
        val boundaries = mutableSetOf<DesktopMatchBoundary>()
        if (context.predecessor == null) boundaries.add(DesktopMatchBoundary.NO_CURRENT_PREDECESSOR)
        if (context.actions.isEmpty()) boundaries.add(DesktopMatchBoundary.POLICY_UNAVAILABLE)
        if (context.candidates.any { !it.available }) boundaries.add(DesktopMatchBoundary.CANDIDATE_EVIDENCE_UNAVAILABLE)
        if (store.importReviewCommands == null) boundaries.add(DesktopMatchBoundary.DURABLE_DECISION_ACTION_UNAVAILABLE)
        val updated = item.copy(rawStatus=context.entryState,rawResolverState=context.resolverState,
            candidateCount=context.candidates.size.toLong(),currentPredecessor=context.predecessor,boundaries=boundaries,context=context)
        admit(); coroutineContext.ensureActive(); owned(lease,item)
        mutable.value = mutable.value.copy(items=mutable.value.items.map { if(it === item) updated else it })
        return updated
    }
    /** This concrete result is consumed by UI; no action is advertised as executable without custody. */
    public fun decisionAvailability(lease: AccountLease, item: DesktopMatchReviewItem, action: DesktopMatchAction): Set<DesktopMatchBoundary> {
        owned(lease, item)
        if (item.context?.actions?.contains(action) != true) return item.boundaries + DesktopMatchBoundary.POLICY_UNAVAILABLE
        return item.boundaries.filter { boundary ->
            boundary != DesktopMatchBoundary.CANDIDATE_EVIDENCE_UNAVAILABLE || action in setOf(DesktopMatchAction.ACCEPT, DesktopMatchAction.REJECT)
        }.toSet()
    }
    public suspend fun freeze(lease: AccountLease,item: DesktopMatchReviewItem,action: DesktopMatchAction,rank: Int?): DesktopImportDecisionReview {
        admit(); owned(lease,item); require(decisionAvailability(lease,item,action).isEmpty())
        return decisions.freeze(lease,item.reference,requireNotNull(item.context),action,rank)
    }
    /** Initial zero-candidate linkage is part of the exact decision, never a fabricated ranked row. */
    public suspend fun freezeInitialEntry(lease: AccountLease, links: DesktopOwnedImportLinkAdmission,
        local: app.autplay.application.desktop.DesktopImportJob, localEntry: AccountUuid,
        owned: DesktopOwnedImportEntry, context: DesktopImportReviewContext,
        action: DesktopMatchAction): DesktopImportDecisionReview {
        admit(); coroutineContext.ensureActive(); require(ownsLease(lease))
        return links.freezeEntryDecision(lease,local,localEntry,owned,context,action)
    }
    public suspend fun confirm(lease: AccountLease,review: DesktopImportDecisionReview): AccountImportReviewPending {
        admit(); return decisions.confirm(lease,review)
    }
    public suspend fun replay(lease: AccountLease,pending: AccountImportReviewPending): AccountImportReviewPending {
        admit(); return decisions.replay(lease,pending)
    }
    public suspend fun retryDeadLetter(lease: AccountLease,pending: AccountImportReviewPending): AccountImportReviewPending {
        admit();return decisions.retryDeadLetter(lease,pending)
    }
    public suspend fun drainRetained(): Unit { decisions.drainRetained() }
    /** Stops new review admission immediately; an admitted HTTP result still drains its exact owner. */
    public fun pause() { mutable.value = mutable.value.copy(paused = true) }
    public fun resumeReview() { if (!closed.get()) mutable.value = mutable.value.copy(paused = false) }
    public suspend fun cancelKnownJob(lease: AccountLease, item: DesktopMatchReviewItem): String = control(lease, item, "cancel")
    public suspend fun resumeKnownJob(lease: AccountLease, item: DesktopMatchReviewItem): String = control(lease, item, "resume")
    private suspend fun control(lease: AccountLease, item: DesktopMatchReviewItem, action: String): String {
        admit(); coroutineContext.ensureActive(); owned(lease, item)
        if (!busy.compareAndSet(false, true)) throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
        mutable.value = mutable.value.copy(busy = true)
        try {
            val captured = sync.session.capture(lease)
            sync.session.request(lease, DomainRoute.ImportControl(item.reference.serverJobId, action)).use { response ->
                sync.requireSuccess(response, if (action == "resume") setOf(202) else setOf(200))
                val root = DomainJson.document(response.bytes, 4096)
                val result = if (action == "cancel") DomainJson.text(root, "result", 100) else {
                    require(DomainJson.text(root, "import_job_id", 36) == item.reference.serverJobId.value)
                    AccountUuid(DomainJson.text(root, "delivery_job_id", 36)); "RESUMED"
                }
                sync.session.revalidate(captured); return result
            }
        } catch (_: Exception) {
            mutable.value = mutable.value.copy(paused = true, available = false, failure = DesktopSyncFailure.OUTCOME_PENDING)
            throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
        } finally { busy.set(false); mutable.value = mutable.value.copy(busy = false) }
    }
    private fun owned(lease: AccountLease, item: DesktopMatchReviewItem) {
        require(!closed.get() && lease == exactLease && mutable.value.items.any { it === item }) { "ACCOUNT_MATCH_REVIEW_CHANGED" }
    }
    public fun retire() { closed.set(true); decisions.retire(); exactLease = null; mutable.value = DesktopImportMatchingState(paused = true) }
}
