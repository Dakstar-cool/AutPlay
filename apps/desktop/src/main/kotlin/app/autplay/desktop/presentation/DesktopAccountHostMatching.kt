package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.desktop.sync.*
import app.autplay.ui.desktop.*
import java.util.UUID

/** Host-serial adapter. Every closure retains the exact lease/item/context displayed by its view. */
internal class DesktopAccountHostMatching(private val runtime: DesktopImportMatchingRuntime, private val sync: DesktopSyncRuntime,
    private val links: DesktopOwnedImportLinkAdmission?) {
    private var targets: Map<String, suspend (String?) -> Unit> = emptyMap()
    private var exactLease: AccountLease? = null
    private var ownedPage: DesktopOwnedImportPage? = null
    private var knownJob: AccountUuid? = null
    private var ownedEntry: DesktopOwnedImportEntry? = null
    private var linkContext: DesktopImportReviewContext? = null
    private var localJob: DesktopImportJob? = null
    private var localOffset: Int = 0
    private var localCandidate: AccountUuid? = null
    private var linkReview: DesktopImportLinkReview? = null
    fun view(lease: AccountLease?, language: DesktopLanguage): DesktopMatchingView {
        if(exactLease != null && exactLease != lease) invalidate()
        val owner = UUID.randomUUID().toString(); val next = linkedMapOf<String,suspend (String?) -> Unit>()
        fun text(ru: String,en: String): String = if(language==DesktopLanguage.Russian) ru else en
        fun target(action: suspend () -> Unit): String = UUID.randomUUID().toString().also { next[it]={input->require(input==null);action()} }
        fun inputTarget(action: suspend (AccountUuid) -> Unit): String = UUID.randomUUID().toString().also { next[it]={input->action(AccountUuid(requireNotNull(input)))} }
        val state = if(lease!=null && runtime.ownsLease(lease))runtime.state.value else DesktopImportMatchingState()
        val decision = if(lease!=null && runtime.decisions.ownsProjection(lease)) runtime.decisions.state.value else DesktopImportDecisionState()
        val paused = state.paused; val busy = state.busy || decision.busy
        val entries = if (lease == null) emptyList() else state.items.map { item ->
            val context = item.context
            val actions = if(paused || context == null) emptySet() else context.actions.filter {
                runtime.decisionAvailability(lease,item,it).isEmpty()
            }.toSet()
            fun choice(action: AccountImportReviewAction,rank: Int?): DesktopMatchingTarget = DesktopMatchingTarget(
                target { runtime.freeze(lease,item,action,rank) },when(action) {
                    AccountImportReviewAction.ACCEPT -> text("Принять","Accept")
                    AccountImportReviewAction.REJECT -> text("Отклонить кандидата","Reject candidate")
                    AccountImportReviewAction.KEEP_UNRESOLVED -> text("Оставить неразрешённым","Keep unresolved")
                    AccountImportReviewAction.CREATE_RECORDING -> text("Создать Recording","Create Recording")
                })
            val candidates = context?.candidates?.map { candidate -> candidateView(candidate,
                if(candidate.available) actions.filter { it in setOf(AccountImportReviewAction.ACCEPT,AccountImportReviewAction.REJECT) }
                    .map { choice(it,candidate.rank) } else emptyList()) } ?: emptyList()
            DesktopMatchingEntry(item.reference.reviewId.value,item.reference.serverJobId.value,item.reference.serverEntryId.value,
                context?.predecessor?.value,item.rawStatus ?: "?",item.rawResolverState,candidates,
                actions.filter { it in setOf(AccountImportReviewAction.KEEP_UNRESOLVED,AccountImportReviewAction.CREATE_RECORDING) }.map { choice(it,null) },
                if(paused) null else target { runtime.inspect(lease,item) },
                if(paused) null else target { runtime.cancelKnownJob(lease,item) },
                if(paused) null else target { runtime.resumeKnownJob(lease,item) })
        }
        val pending = decision.pending.map { row ->
            DesktopMatchingPending(row.command.operationId.value,row.command.action.name,row.command.selectedRank,row.command.predecessor.value,
                row.rawState,row.outcome?.decisionId?.value,row.outcome?.recordingId?.value,
                if(lease != null && !paused && !row.terminal && row.available && !runtime.decisions.hasRetainedOwner())
                    if(row.retryExhausted) target { runtime.retryDeadLetter(lease,row) }
                    else if(row.nextAttemptAtMs<=System.currentTimeMillis())target { runtime.replay(lease,row) } else null
                else null,row.rawWorkState,row.attempts,
                row.nextAttemptAtMs.takeIf { it>0 }?.let { java.time.format.DateTimeFormatter.ofLocalizedDateTime(java.time.format.FormatStyle.SHORT)
                    .withLocale(if(language==DesktopLanguage.Russian)java.util.Locale.forLanguageTag("ru-RU") else java.util.Locale.ENGLISH)
                    .withZone(java.time.ZoneId.systemDefault()).format(java.time.Instant.ofEpochMilli(it)) },row.retryExhausted,
                localJobId=row.command.reference.localJobId.value,localEntryId=row.command.reference.localEntryId.value,
                serverJobId=row.command.reference.serverJobId.value,serverEntryId=row.command.reference.serverEntryId.value,
                initialEntry=row.command.reference is AccountImportEntryReview)
        }
        val review = decision.review
        val confirmation = review?.let { frozen ->
            next["confirm:${frozen.id.value}"] = { input -> require(input==null);runtime.confirm(requireNotNull(lease),frozen) }
            next["dismiss:${frozen.id.value}"] = { input -> require(input==null);runtime.decisions.dismiss(frozen) }
            DesktopMatchingConfirmation(frozen.id.value,frozen.context.jobId.value,frozen.context.entryId.value,
                frozen.command.predecessor.value,frozen.command.action.name,
                frozen.context.candidates.singleOrNull { it.rank == frozen.command.selectedRank }?.let { candidateView(it,emptyList()) },
                frozen.command.operationId.value,
                localJobId=frozen.command.reference.takeIf { it is AccountImportEntryReview }?.localJobId?.value,
                localEntryId=frozen.command.reference.takeIf { it is AccountImportEntryReview }?.localEntryId?.value)
        }
        val conflicts=sync.state.value.conflictDocuments.map { document ->
            val bytes=document.documentCopy()
            try { DesktopMatchingConflict("${document.cursor.profileId.value}:${document.cursor.sourceId.value}:${document.cursor.aggregateType}:${document.cursor.localId.value}",
                document.cursor.aggregateType,document.cursor.localId.value,document.serverId?.value,document.kind,document.rawResolutionState,
                document.baseVersion,document.observedVersion,bytes.toString(Charsets.UTF_8)) } finally { bytes.fill(0) }
        }
        val frozenLink=linkReview
        val linkConfirmation=frozenLink?.let { frozen ->
            next["confirm-link:${frozen.id.value}"]={input->
                require(input==null);requireNotNull(links).confirm(requireNotNull(lease),frozen);linkReview=null;runtime.refresh(lease)
            }
            next["dismiss-link:${frozen.id.value}"]={input->require(input==null);links?.dismiss(frozen);linkReview=null}
            DesktopMatchingLinkConfirmation(frozen.id.value,frozen.localJob.id,frozen.localCandidateId,frozen.owned.jobId.value,
                frozen.owned.entryId.value,requireNotNull(frozen.context.predecessor).value,frozen.selectedRank)
        }
        val local=localJob
        val localTargets=if(lease!=null && links!=null && local!=null && !paused) local.candidates.drop(localOffset).take(100).map { candidate ->
            DesktopMatchingTarget(target { localCandidate=AccountUuid(candidate.candidate.id);linkReview=null },
                "${local.id} / ${candidate.candidate.id}: ${candidate.candidate.title}")
        } else emptyList()
        val owned=ownedEntry; val context=linkContext; val selected=localCandidate
        val choices=if(lease!=null && links!=null && owned!=null && context!=null && local!=null && selected!=null && !paused) {
            val ranked =
            context.candidates.filter { it.available }.map { candidate ->
                DesktopMatchingTarget(target { linkReview=links.freeze(lease,AccountUuid(local.id),selected,owned,candidate.rank) },
                    text("Проверить связь с рангом","Review link to rank")+" ${candidate.rank}: ${candidate.recordingId.value}")
            }
            val initial = if(state.available && context.candidates.isEmpty() && context.predecessor != null &&
                context.predecessor == owned.predecessor && context.entryState == owned.state)
                context.actions.filter { it in setOf(AccountImportReviewAction.KEEP_UNRESOLVED,AccountImportReviewAction.CREATE_RECORDING) }.map { action ->
                    DesktopMatchingTarget(target { runtime.freezeInitialEntry(lease,links,local,selected,owned,context,action) },
                        if(action == AccountImportReviewAction.KEEP_UNRESOLVED)
                            text("Проверить связь и оставить неразрешённым","Review link and keep unresolved") else
                            text("Проверить связь и создать Recording","Review link and create Recording"))
                } else emptyList()
            ranked + initial
        } else emptyList()
        val result = DesktopMatchingView(owner,busy,lease != null && state.available,paused,entries,pending,confirmation,
            (decision.failure ?: state.failure)?.name,
            if(lease != null && !paused) target { runtime.refresh(lease);sync.readConflicts(lease) } else null,
            if(lease != null && !paused) state.nextAfter?.let { after -> target { runtime.refresh(lease,after) } } else null,
            if(lease != null && !paused) target { runtime.pause() } else null,
            if(lease != null && paused) target { runtime.resumeReview() } else null,
            if(runtime.decisions.hasRetainedOwner()) target { runtime.decisions.drainRetained() } else null,
            mappingUnavailable=lease != null && state.available && state.items.isEmpty(),
            pendingNextTarget=if(lease != null && !paused && decision.pending.size==100)
                target { runtime.decisions.refresh(lease,decision.pending.last().command.operationId) } else null,
            conflicts=conflicts,conflictNextTarget=if(lease!=null)sync.state.value.nextConflict?.let { after -> target { sync.readConflicts(lease,after) } } else null,
            remoteLookupTarget=if(lease!=null && links!=null && !paused)inputTarget { job ->
                ownedPage=links.report(lease,job);knownJob=job;ownedEntry=null;linkContext=null;linkReview=null
            } else null,
            localLookupTarget=if(lease!=null && links!=null && !paused)inputTarget { job ->
                localJob=links.localJob(job);localOffset=0;localCandidate=null;linkReview=null
            } else null,
            ownedEntries=if(lease!=null && !paused)ownedPage?.entries?.map { entry -> DesktopMatchingTarget(target {
                ownedEntry=entry;linkContext=runtime.decisions.context(lease,entry.jobId,entry.entryId);linkReview=null
            },"${entry.jobId.value} / ${entry.entryId.value}: ${entry.state}") } ?: emptyList() else emptyList(),
            ownedNextTarget=if(lease!=null && links!=null && !paused)ownedPage?.nextAfter?.let { after -> target {
                ownedPage=links.report(lease,requireNotNull(knownJob),after);ownedEntry=null;linkContext=null;linkReview=null
            } } else null,
            localCandidates=localTargets,localNextTarget=if(local!=null && localOffset+100<local.candidates.size && !paused)
                target { localOffset+=100 } else null,
            selectedLocalCandidate=localCandidate?.value,linkChoices=choices,linkConfirmation=linkConfirmation,
            linkCandidates=context?.candidates?.map { candidateView(it,emptyList()) } ?: emptyList(),
            linkUnavailable=if(context!=null && context.candidates.isEmpty())
                text("Ранжированных кандидатов нет. Выберите точную локальную запись и доступное действие для явной связи и решения.",
                    "There are no ranked candidates. Select the exact local entry and an available action to review the link and decision.") else null)
        exactLease=lease; targets=next.toMap()
        return result
    }
    fun targetIds(): Set<String> = targets.keys
    fun capture(lease: AccountLease,target: String,input: String?): suspend () -> Unit {
        require(lease == exactLease) { "ACCOUNT_MATCH_REVIEW_CHANGED" }
        val action=targets[target] ?: throw DesktopSyncException(DesktopSyncFailure.CONTEXT_CHANGED)
        targets=emptyMap()
        return {action(input)}
    }
    fun invalidate() {
        targets=emptyMap(); exactLease=null;ownedPage=null;knownJob=null;ownedEntry=null;linkContext=null
        localJob=null;localOffset=0;localCandidate=null;linkReview=null
    }
    private fun candidateView(c: DesktopImportReviewCandidate,actions: List<DesktopMatchingTarget>): DesktopMatchingCandidate =
        DesktopMatchingCandidate(c.rank,c.recordingId.value,c.currentTitle,c.currentArtist,c.durationMs?.let { "$it ms" },c.confidence,
            c.evidenceTier,c.evidenceSchema,c.evidenceSha256,c.hardConflicts,c.available,actions)
}
