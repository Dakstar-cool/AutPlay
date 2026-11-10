package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import java.math.BigDecimal
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

public data class DesktopImportReviewCandidate(public val rank: Int, public val recordingId: AccountUuid,
    public val currentTitle: String, public val currentArtist: String, public val durationMs: Long?,
    public val rawScore: String?, public val confidence: String?, public val evidenceTier: String,
    public val evidenceSchema: String, public val evidenceSha256: String, public val hardConflicts: Boolean,
    public val available: Boolean)

public class DesktopImportReviewContext internal constructor(public val jobId: AccountUuid, public val entryId: AccountUuid,
    public val predecessor: AccountUuid?, public val entryState: String, public val resolverState: String?,
    actions: Set<AccountImportReviewAction>, candidates: List<DesktopImportReviewCandidate>) {
    public val actions: Set<AccountImportReviewAction> = java.util.Collections.unmodifiableSet(actions.toSet())
    public val candidates: List<DesktopImportReviewCandidate> = java.util.Collections.unmodifiableList(candidates.toList())
    internal fun sameSnapshot(other: DesktopImportReviewContext): Boolean = jobId == other.jobId && entryId == other.entryId &&
        predecessor == other.predecessor && entryState == other.entryState && resolverState == other.resolverState &&
        actions == other.actions && candidates == other.candidates
    override fun toString(): String = "DesktopImportReviewContext"
}

public class DesktopImportDecisionReview internal constructor(public val id: AccountUuid,
    public val context: DesktopImportReviewContext, public val command: AccountImportReviewCommand,
    internal val lease: AccountLease, internal val initialRevalidate: (suspend () -> Unit)? = null
) { override fun toString(): String = "DesktopImportDecisionReview" }

public data class DesktopImportDecisionState(public val review: DesktopImportDecisionReview? = null,
    public val pending: List<AccountImportReviewPending> = emptyList(), public val busy: Boolean = false,
    public val failure: DesktopSyncFailure? = null, public val ownerLease: AccountLease? = null)

/** I1 executor. It never creates jobs, uploads, mutates local media or uses a K secret journal. */
public class DesktopImportDecisionRuntime(private val store: DesktopAccountStore, private val sync: DesktopSyncTransport) {
    private val commands: DesktopAccountImportReviewCommands get() = store.importReviewCommands
        ?: throw DesktopSyncException(DesktopSyncFailure.UNSUPPORTED)
    private val busy = AtomicBoolean(false)
    private val closed = AtomicBoolean(false)
    private val mutable = MutableStateFlow(DesktopImportDecisionState())
    public val state: StateFlow<DesktopImportDecisionState> = mutable.asStateFlow()
    public fun ownsProjection(lease: AccountLease): Boolean = mutable.value.ownerLease == lease
    private var claimOperation: AccountUuid? = null
    private var exactClaim: AccountWorkClaim? = null
    private var exactPending: AccountImportReviewPending? = null
    private var exactTicket: DomainIoTicket? = null
    private var settlementOperation: AccountUuid? = null
    private var settlementOutcome: AccountImportReviewOutcome? = null
    private var settlementRefused = false
    private var settlementRetryAfterMs: Long = 0
    private var settlementLease: AccountLease? = null

    public suspend fun context(lease: AccountLease, job: AccountUuid, entry: AccountUuid): DesktopImportReviewContext {
        admit(); coroutineContext.ensureActive()
        val captured = sync.session.capture(lease)
        sync.session.request(lease,DomainRoute.ImportReviewContext(job,entry)).use { response ->
            sync.requireSuccess(response)
            val result = decodeContext(response.bytes,job,entry)
            sync.session.revalidate(captured); admit(); coroutineContext.ensureActive()
            return result
        }
    }
    public suspend fun refresh(lease: AccountLease, after: AccountUuid? = null): List<AccountImportReviewPending> {
        admit()
        val rows = commands.readPage(lease,after,100)
        admit();coroutineContext.ensureActive()
        mutable.value = mutable.value.copy(pending=rows.toList(),ownerLease=lease,
            review=mutable.value.review?.takeIf { it.lease==lease })
        return rows
    }
    /** No effect: freeze one exact context and show it before the explicit confirm call. */
    public suspend fun freeze(lease: AccountLease, reference: AccountImportReview, shown: DesktopImportReviewContext,
        action: AccountImportReviewAction, selectedRank: Int?): DesktopImportDecisionReview =
        freezeReference(lease,reference,shown,action,selectedRank,null)

    /** Only the canonical local/owned report admission supplies this exact initial-entry guard. */
    internal suspend fun freezeEntry(lease: AccountLease, reference: AccountImportEntryReview, shown: DesktopImportReviewContext,
        action: AccountImportReviewAction, revalidate: suspend () -> Unit): DesktopImportDecisionReview {
        revalidate()
        return freezeReference(lease,reference,shown,action,null,revalidate)
    }
    private suspend fun freezeReference(lease: AccountLease, reference: AccountImportReviewReference, shown: DesktopImportReviewContext,
        action: AccountImportReviewAction, selectedRank: Int?, initialRevalidate: (suspend () -> Unit)?): DesktopImportDecisionReview {
        admit(); coroutineContext.ensureActive()
        require(!busy.get() && !hasRetainedOwner())
        require(reference.serverJobId == shown.jobId && reference.serverEntryId == shown.entryId && action in shown.actions)
        if(reference is AccountImportEntryReview) require(initialRevalidate != null && shown.candidates.isEmpty() &&
            reference.predecessor == shown.predecessor && action in setOf(AccountImportReviewAction.KEEP_UNRESOLVED,AccountImportReviewAction.CREATE_RECORDING))
        val predecessor = shown.predecessor ?: throw DesktopSyncException(DesktopSyncFailure.CONFLICT)
        val selected = selectedRank?.let { rank -> shown.candidates.singleOrNull { it.rank == rank && it.available } }
        val candidateAction = action in setOf(AccountImportReviewAction.ACCEPT,AccountImportReviewAction.REJECT)
        require(candidateAction == (selected != null) && candidateAction == (selectedRank != null))
        val capture = sync.session.capture(lease); sync.session.revalidate(capture)
        val operation = uuid()
        val command = AccountImportReviewCommand(operation,uuid(),reference,predecessor,action,selectedRank,
            selected?.evidenceSha256,operation.value,lease.bindingCommit,lease.authorityRevision,lease.activationRevision)
        val review = DesktopImportDecisionReview(uuid(),shown,command,lease,initialRevalidate)
        mutable.value = mutable.value.copy(review=review,failure=null,ownerLease=lease,
            pending=if(mutable.value.ownerLease==lease)mutable.value.pending else emptyList())
        return review
    }
    public fun dismiss(review: DesktopImportDecisionReview) {
        if (!busy.get() && mutable.value.review === review) mutable.value = mutable.value.copy(review=null)
    }
    public suspend fun confirm(lease: AccountLease, review: DesktopImportDecisionReview): AccountImportReviewPending {
        admit(); coroutineContext.ensureActive()
        require(lease == review.lease && mutable.value.review === review)
        if (!busy.compareAndSet(false,true)) throw DesktopSyncException(DesktopSyncFailure.DELIVERY_BLOCKED)
        mutable.value = mutable.value.copy(busy=true,failure=null)
        try {
            require(!hasRetainedOwner())
            val fresh = context(lease,review.context.jobId,review.context.entryId)
            if(review.command.reference is AccountImportEntryReview) require(fresh.sameSnapshot(review.context))
            validate(fresh,review.command)
            // The initial guard finishes with the canonical local reread after all of its HTTP.
            review.initialRevalidate?.invoke()
            val prepared = commands.prepare(lease,review.command)
            mutable.value = mutable.value.copy(review=null)
            return dispatch(lease,prepared)
        } finally { busy.set(false); mutable.value = mutable.value.copy(busy=false) }
    }
    /** Explicit exact replay, including after restart. It never replaces operation/body/key. */
    public suspend fun replay(lease: AccountLease, shown: AccountImportReviewPending): AccountImportReviewPending {
        admit(); coroutineContext.ensureActive()
        if (!busy.compareAndSet(false,true)) throw DesktopSyncException(DesktopSyncFailure.DELIVERY_BLOCKED)
        mutable.value = mutable.value.copy(busy=true,failure=null)
        try {
            require(exactClaim == null && exactTicket == null && settlementOperation == null)
            val retained = commands.read(lease,shown.command.operationId) ?: throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
            require(retained == shown && !retained.terminal && retained.available)
            return dispatch(lease,retained)
        } finally { busy.set(false); mutable.value = mutable.value.copy(busy=false) }
    }
    public suspend fun retryDeadLetter(lease: AccountLease,shown: AccountImportReviewPending): AccountImportReviewPending {
        admit();coroutineContext.ensureActive()
        require(!hasRetainedOwner() && !shown.terminal && shown.available && shown.retryExhausted)
        if(!busy.compareAndSet(false,true))throw DesktopSyncException(DesktopSyncFailure.DELIVERY_BLOCKED)
        mutable.value=mutable.value.copy(busy=true)
        try {
            require(commands.read(lease,shown.command.operationId)==shown)
            val result=commands.retryDeadLetter(lease,uuid(),shown.command.operationId,shown.revision)
            mutable.value=mutable.value.copy(pending=mutable.value.pending.map { if(it==shown)result else it },failure=null)
            return result
        } finally {busy.set(false);mutable.value=mutable.value.copy(busy=false)}
    }
    private suspend fun dispatch(lease: AccountLease, pending: AccountImportReviewPending): AccountImportReviewPending {
        val c = pending.command
        if(pending.retryExhausted)throw DesktopSyncException(DesktopSyncFailure.DELIVERY_BLOCKED)
        if(pending.nextAttemptAtMs>System.currentTimeMillis())throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER,
            minOf(86_400_000,pending.nextAttemptAtMs-System.currentTimeMillis()))
        val capture = sync.session.capture(lease)
        val fresh = context(lease,c.reference.serverJobId,c.reference.serverEntryId)
        if (fresh.predecessor == c.predecessor) validate(fresh,c)
        else if (pending.rawState == "PREPARED") throw DesktopSyncException(DesktopSyncFailure.CONFLICT)
        // A changed predecessor after a lost response can only be reconciled by the exact old POST.
        // Server successor replay remains authoritative; labels never prove this command committed.
        exactPending = pending; settlementLease = lease
        claimOperation = uuid()
        exactClaim = commands.claim(lease,requireNotNull(claimOperation),c.operationId,pending.revision)
        if(exactClaim==null) {
            claimOperation=null;exactPending=null;settlementLease=null
            throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER,1000)
        }
        var admittedHttp = false
        var refused = false
        var retryAfterMs=0L
        var outcome: AccountImportReviewOutcome? = null
        try {
            val claim = requireNotNull(exactClaim)
            exactPending = commands.markSent(lease,uuid(),c.operationId,claim,pending.revision)
            sync.session.revalidate(capture); admit(); coroutineContext.ensureActive()
            val body = c.bodyCopy()
            try {
                sync.session.request(lease,DomainRoute.ImportReview(c.reference.serverJobId,c.reference.serverEntryId),body,
                    onTicket={ exactTicket=it; admittedHttp=true }).use { response ->
                    if (response.status == 200) {
                        outcome = decodeOutcome(response.bytes,c)
                        if (c.action == AccountImportReviewAction.ACCEPT && fresh.predecessor == c.predecessor)
                            require(outcome?.recordingId == fresh.candidates.single { it.rank == c.selectedRank &&
                                it.evidenceSha256 == c.selectedEvidenceSha256 }.recordingId)
                        sync.session.revalidate(capture)
                    } else {
                        refused = pending.rawState == "PREPARED" && response.status in setOf(404,409,422)
                        sync.requireSuccess(response)
                    }
                }
            } finally { body.fill(0) }
        } catch (failure: Exception) {
            // request awaits actual IO settlement. Any undecodable/late/denied result stays uncertain.
            outcome = null
            retryAfterMs=(failure as? DesktopSyncException)?.retryAfterMs ?: 0
            mutable.value = mutable.value.copy(failure=(failure as? DesktopSyncException)?.failure ?: DesktopSyncFailure.OUTCOME_PENDING)
        }
        // An unissued request is still retained. The user may replay the same command explicitly.
        if (!admittedHttp) refused = false
        settlementOutcome = outcome; settlementRefused = refused; settlementRetryAfterMs=retryAfterMs; settlementOperation = uuid()
        val result = settleRetained()
        mutable.value = mutable.value.copy(pending=mutable.value.pending.filterNot { it.command.operationId == c.operationId } + result)
        return result
    }
    /** Retry a SQL result whose commit/readback was uncertain; never repeats HTTP or a new claim. */
    public suspend fun settleRetained(): AccountImportReviewPending {
        val lease = requireNotNull(settlementLease); val p = requireNotNull(exactPending)
        val claim = requireNotNull(exactClaim); val operation = requireNotNull(settlementOperation)
        val result = commands.settle(lease,operation,p.command.operationId,claim,p.revision,settlementOutcome,settlementRefused,settlementRetryAfterMs)
        exactTicket = null; exactClaim = null; exactPending = null; claimOperation = null
        settlementOperation = null; settlementOutcome = null; settlementRefused = false; settlementRetryAfterMs=0; settlementLease = null
        return result
    }
    /** Recover only a recorded claim result; a missing outcome does not prove SQL rolled back. */
    public suspend fun recoverRetainedClaim(): AccountImportReviewPending {
        require(exactTicket == null && settlementOperation == null)
        val lease = requireNotNull(settlementLease); val pending = requireNotNull(exactPending)
        val operation = requireNotNull(claimOperation)
        if (exactClaim == null) exactClaim = store.domain?.recoverWorkOutcome(lease,operation)
            ?: throw DesktopSyncException(DesktopSyncFailure.OUTCOME_PENDING)
        settlementOutcome = null; settlementRefused = false; settlementOperation = uuid()
        return settleRetained().also { result ->
            mutable.value = mutable.value.copy(pending=mutable.value.pending.filterNot { it.command.operationId == pending.command.operationId } + result)
        }
    }
    /** Caller joins admitted invocations first; this method never treats cancellation as drain. */
    public suspend fun drainRetained(): Unit {
        if (hasRetainedOwner()) {
            if (settlementOperation != null) settleRetained() else recoverRetainedClaim()
        }
        check(!hasRetainedOwner()) { "ACCOUNT_IMPORT_REVIEW_OUTCOME_PENDING" }
    }
    public fun retire() { closed.set(true); mutable.value = mutable.value.copy(review=null,pending=emptyList(),ownerLease=null) }
    public fun hasRetainedOwner(): Boolean = exactClaim != null || claimOperation != null || exactTicket != null || settlementOperation != null
    private fun admit() { if (closed.get()) throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE) }
    private fun validate(context: DesktopImportReviewContext, c: AccountImportReviewCommand) {
        require(context.jobId == c.reference.serverJobId && context.entryId == c.reference.serverEntryId &&
            context.predecessor == c.predecessor && c.action in context.actions)
        if(c.reference is AccountImportEntryReview) require(context.candidates.isEmpty() && c.selectedRank == null &&
            c.action in setOf(AccountImportReviewAction.KEEP_UNRESOLVED,AccountImportReviewAction.CREATE_RECORDING))
        if (c.selectedRank != null) require(context.candidates.singleOrNull { it.rank == c.selectedRank && it.available }?.evidenceSha256 == c.selectedEvidenceSha256)
    }
    private fun decodeContext(bytes: ByteArray, job: AccountUuid, entry: AccountUuid): DesktopImportReviewContext {
        val root = DomainJson.document(bytes,1_048_576)
        require(DomainJson.number(root,"format") == 1L && DomainJson.text(root,"display_metadata",100) == "CURRENT_CATALOG_DISPLAY")
        require(AccountUuid(DomainJson.text(root,"import_job_id",36)) == job && AccountUuid(DomainJson.text(root,"import_entry_id",36)) == entry)
        val rows = root["candidates"] as? JsonArray ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
        require(rows.size <= 100)
        val candidates = rows.mapIndexed { index, value ->
            val row = value as? JsonObject ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
            val rank = DomainJson.number(row,"rank").toInt(); require(rank == index+1)
            val tier = DomainJson.text(row,"evidence_tier",100).also { require(it.toByteArray(Charsets.UTF_8).size <= 100) }
            val schema = DomainJson.text(row,"evidence_schema_version",100).also { require(it.toByteArray(Charsets.UTF_8).size <= 100) }
            val available = row.getValue("available").jsonPrimitive.boolean
            require(!available || schema == "1" && tier in setOf("T0","T1","T2","T3","T4"))
            DesktopImportReviewCandidate(rank,AccountUuid(DomainJson.text(row,"recording_id",36)),label(row,"current_title"),label(row,"current_artist"),
                nullableNumber(row,"duration_ms"),decimal(row,"raw_score"),decimal(row,"confidence"),tier,schema,
                DomainJson.text(row,"evidence_sha256",64).also { require(it.matches(Regex("[0-9a-f]{64}"))) },
                row.getValue("has_hard_conflicts").jsonPrimitive.boolean,available)
        }
        val rawActions = root["eligible_actions"] as? JsonArray ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
        require(rawActions.size <= 4)
        val known = rawActions.mapNotNull { value -> AccountImportReviewAction.entries.singleOrNull { it.name == value.jsonPrimitive.content } }.toSet()
        val entryState = DomainJson.text(root,"entry_state",100); val resolver = nullableText(root,"resolver_state",100)
        val actions = if (known.size != rawActions.size || candidates.any { !it.available } ||
            resolver !in setOf("AUTO_MATCH","REVIEW_REQUIRED","NO_MATCH","INTEGRITY_CONFLICT","DEFERRED_EVIDENCE") ||
            entryState !in setOf("PENDING","AUTO_MATCH","MANUAL_MATCH","MANUAL_UNRESOLVED","REVIEW_REQUIRED","NO_MATCH","INTEGRITY_CONFLICT","DEFERRED_EVIDENCE","REJECTED")) emptySet() else known
        require(resolver != "INTEGRITY_CONFLICT" || actions.all { it == AccountImportReviewAction.KEEP_UNRESOLVED })
        return DesktopImportReviewContext(job,entry,nullableText(root,"predecessor_decision_id",36)?.let(::AccountUuid),entryState,resolver,actions,candidates)
    }
    private fun decodeOutcome(bytes: ByteArray, c: AccountImportReviewCommand): AccountImportReviewOutcome {
        val root = DomainJson.document(bytes,4096)
        require(root.keys == setOf("decision_id","import_entry_id","status","recording_id","replayed"))
        val outcome = AccountImportReviewOutcome(AccountUuid(DomainJson.text(root,"decision_id",36)),AccountUuid(DomainJson.text(root,"import_entry_id",36)),
            DomainJson.text(root,"status",100),nullableText(root,"recording_id",36)?.let(::AccountUuid),root.getValue("replayed").jsonPrimitive.boolean)
        require(outcome.importEntryId == c.reference.serverEntryId)
        val expected = when(c.action) {
            AccountImportReviewAction.ACCEPT,AccountImportReviewAction.CREATE_RECORDING -> "MANUAL_MATCH"
            AccountImportReviewAction.REJECT -> "REVIEW_REQUIRED"
            AccountImportReviewAction.KEEP_UNRESOLVED -> "MANUAL_UNRESOLVED"
        }
        require(outcome.status == expected && (outcome.recordingId != null) == (expected == "MANUAL_MATCH"))
        return outcome
    }
    private fun label(root: JsonObject, key: String): String = root.getValue(key).jsonPrimitive.let {
        require(it.isString); it.content.also { label -> require(label.toByteArray(Charsets.UTF_8).size <= 4096 && label.none { ch -> ch.code < 32 || ch.code == 127 }) }
    }
    private fun nullableText(root: JsonObject, key: String, max: Int): String? = if (root.getValue(key) == JsonNull) null else DomainJson.text(root,key,max)
    private fun nullableNumber(root: JsonObject, key: String): Long? = if (root.getValue(key) == JsonNull) null else DomainJson.number(root,key)
    private fun decimal(root: JsonObject, key: String): String? = nullableText(root,key,32)?.also {
        val number = BigDecimal(it); require(number >= BigDecimal.ZERO && number <= BigDecimal.ONE)
    }
    private fun uuid(): AccountUuid = AccountUuid(UUID.randomUUID().toString())
}
