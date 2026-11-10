package app.autplay.desktop.sync

import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.account.*
import java.util.UUID
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

/** Created only from an actual owner-authenticated existing report, not from titles or user IDs. */
public class DesktopOwnedImportEntry internal constructor(public val jobId: AccountUuid,
    public val entryId: AccountUuid, public val predecessor: AccountUuid?, public val state: String,
    internal val lease: AccountLease, internal val capture: DomainSessionCapture)
public data class DesktopOwnedImportPage(public val entries: List<DesktopOwnedImportEntry>, public val nextAfter: String?)
public class DesktopImportLinkReview internal constructor(public val id: AccountUuid,
    public val localJob: DesktopImportJob, public val localCandidateId: String,
    public val owned: DesktopOwnedImportEntry, public val context: DesktopImportReviewContext,
    public val selectedRank: Int, internal val reference: AccountImportReview)

/** Read-only local evidence plus explicit owner report admission. No remote creation/upload path. */
public class DesktopOwnedImportLinkAdmission(private val local: DesktopLibraryStore,
    private val account: DesktopAccountStore, private val sync: DesktopSyncTransport,
    private val decisions: DesktopImportDecisionRuntime) {
    private var review: DesktopImportLinkReview? = null
    @Volatile private var retired = false
    public suspend fun localJob(jobId: AccountUuid): DesktopImportJob {
        require(!retired); coroutineContext.ensureActive()
        return (local.loadImportJob(jobId.value) ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)).also {
            require(it.revision != null && it.phase != DesktopImportJobPhase.UNSUPPORTED)
        }
    }
    public suspend fun report(lease: AccountLease, knownJob: AccountUuid, after: String? = null): DesktopOwnedImportPage {
        require(!retired); coroutineContext.ensureActive()
        val capture = sync.session.capture(lease)
        sync.session.request(lease,DomainRoute.ImportReport(knownJob,after)).use { response ->
            sync.requireSuccess(response)
            val root = DomainJson.document(response.bytes,524_288)
            require(AccountUuid(DomainJson.text(root,"import_job_id",36)) == knownJob)
            val rows = root["entries"] as? JsonArray ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
            require(rows.size <= 100)
            val entries = rows.map { value ->
                val row = value as? JsonObject ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
                DesktopOwnedImportEntry(knownJob,AccountUuid(DomainJson.text(row,"import_entry_id",36)),
                    row.getValue("decision_id").takeIf { it != JsonNull }?.jsonPrimitive?.content?.let(::AccountUuid),
                    DomainJson.text(row,"status",100),lease,capture)
            }
            require(entries.map { it.entryId }.distinct().size == entries.size)
            val next = root.getValue("next_after").takeIf { it != JsonNull }?.let { DomainJson.text(root,"next_after",1000) }
            require(next == null || next != after)
            sync.session.revalidate(capture); require(!retired); coroutineContext.ensureActive()
            return DesktopOwnedImportPage(java.util.Collections.unmodifiableList(entries),next)
        }
    }
    /** Caller displays both exact local manifest identity and owned server identity for consent. */
    public suspend fun freeze(lease: AccountLease, localJobId: AccountUuid, candidateId: AccountUuid,
        owned: DesktopOwnedImportEntry, rank: Int): DesktopImportLinkReview {
        require(!retired && owned.lease == lease); coroutineContext.ensureActive()
        sync.session.revalidate(owned.capture)
        val job = local.loadImportJob(localJobId.value) ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
        require(job.revision != null && job.phase != DesktopImportJobPhase.UNSUPPORTED &&
            job.candidates.any { it.candidate.id == candidateId.value })
        val context = decisions.context(lease,owned.jobId,owned.entryId)
        require(context.predecessor != null && context.predecessor == owned.predecessor && context.candidates.any { it.rank == rank && it.available })
        val operation = uuid()
        // The local manifest candidate is the local import entry identity; no separate entry ID exists.
        val reference = AccountImportReview(uuid(),localJobId,candidateId,candidateId,owned.jobId,owned.entryId,rank,context.predecessor,operation)
        return DesktopImportLinkReview(uuid(),job,candidateId.value,owned,context,rank,reference).also { review=it }
    }
    public suspend fun confirm(lease: AccountLease, shown: DesktopImportLinkReview): AccountImportReview {
        require(!retired && review === shown && lease == shown.owned.lease); coroutineContext.ensureActive()
        sync.session.revalidate(shown.owned.capture)
        val localNow = local.loadImportJob(shown.localJob.id)
        require(localNow == shown.localJob)
        val fresh = decisions.context(lease,shown.owned.jobId,shown.owned.entryId)
        require(fresh.predecessor == shown.context.predecessor && fresh.candidates.singleOrNull { it.rank == shown.selectedRank } ==
            shown.context.candidates.singleOrNull { it.rank == shown.selectedRank })
        require(!retired); coroutineContext.ensureActive()
        val domain = account.domain ?: throw DesktopSyncException(DesktopSyncFailure.UNSUPPORTED)
        domain.retainImportReview(lease,shown.reference)
        review=null
        return shown.reference
    }
    /**
     * An initial empty context has no legacy candidate/rank mapping. Its explicit local/server
     * linkage is published atomically with the named decision command only after the UI confirms.
     */
    public suspend fun freezeEntryDecision(lease: AccountLease, shownLocal: DesktopImportJob, localEntry: AccountUuid,
        owned: DesktopOwnedImportEntry, shownContext: DesktopImportReviewContext,
        action: AccountImportReviewAction): DesktopImportDecisionReview {
        require(!retired && owned.lease == lease && shownLocal.revision != null &&
            shownLocal.phase != DesktopImportJobPhase.UNSUPPORTED &&
            shownLocal.candidates.count { it.candidate.id == localEntry.value } == 1)
        require(shownContext.jobId == owned.jobId && shownContext.entryId == owned.entryId &&
            shownContext.predecessor != null && shownContext.predecessor == owned.predecessor &&
            shownContext.entryState == owned.state && shownContext.candidates.isEmpty() && action in shownContext.actions &&
            action in setOf(AccountImportReviewAction.KEEP_UNRESOLVED,AccountImportReviewAction.CREATE_RECORDING))
        val reference = AccountImportEntryReview(uuid(),AccountUuid(shownLocal.id),localEntry,owned.jobId,owned.entryId,
            requireNotNull(shownContext.predecessor),uuid())
        return decisions.freezeEntry(lease,reference,shownContext,action) {
            require(!retired && owned.lease == lease); coroutineContext.ensureActive()
            sync.session.revalidate(owned.capture)
            val fresh = decisions.context(lease,owned.jobId,owned.entryId)
            require(fresh.sameSnapshot(shownContext) && action in fresh.actions && fresh.candidates.isEmpty())
            require(local.loadImportJob(shownLocal.id) == shownLocal)
            sync.session.revalidate(owned.capture); require(!retired); coroutineContext.ensureActive()
        }
    }
    public fun dismiss(shown: DesktopImportLinkReview) { if(review === shown) review=null }
    public fun retire() { retired=true; review=null }
    private fun uuid(): AccountUuid = AccountUuid(UUID.randomUUID().toString())
}
