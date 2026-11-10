package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

internal class AccountImportReviewRepository(private val owner: AccountRepositoryOwner) : DesktopAccountImportReviewCommands {
    override suspend fun prepare(lease: AccountLease, command: AccountImportReviewCommand): AccountImportReviewPending {
        current(lease, command)
        val c = command; val r = c.reference; val mapped = r as? AccountImportReview; val body = c.bodyCopy()
        return owner.write(lease, c.operationId, "IMPORT_DECISION_PREPARE", input(c)) { sql, _, replay ->
            if (!replay) {
                if (mapped != null) {
                    val mapping = sql.query("""SELECT local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,rank,predecessor,operation_id
                        FROM j_import_review WHERE scope_id=? AND review_id=?""", lease.scopeId, r.reviewId) {
                        AccountImportReview(r.reviewId, AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), AccountUuid(it.getText(2)),
                            AccountUuid(it.getText(3)), AccountUuid(it.getText(4)), it.getLong(5).toInt(), sql.nullable(it,6)?.let(::AccountUuid), AccountUuid(it.getText(7)))
                    }.singleOrNull()
                    if (mapping != r) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                    val predecessor = sql.text("SELECT predecessor FROM j_import_review_state WHERE scope_id=? AND server_job_id=? AND server_entry_id=?",
                        lease.scopeId,r.serverJobId,r.serverEntryId)?.let(::AccountUuid)
                    // A new explicitly admitted immutable mapping can observe a later external review.
                    // Neither it nor this cached prior outcome replaces the server's POST authority.
                    if (c.predecessor != r.predecessor && c.predecessor != predecessor) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                } else if (r !is AccountImportEntryReview || c.predecessor != r.predecessor) {
                    fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                }
                if (sql.number("""SELECT count(*) FROM j_import_review_command WHERE scope_id=? AND state NOT IN ('COMMITTED','REFUSED')
                    AND ((server_job_id=? AND server_entry_id=?) OR (local_job_id=? AND local_entry_id=?))""",
                    lease.scopeId,r.serverJobId,r.serverEntryId,r.localJobId,r.localEntryId) != 0L) fail(AccountFailure.ACCOUNT_BUSY)
                if (sql.number("SELECT count(*) FROM j_work WHERE state NOT IN ('COMPLETED','CANCELED')") >= DesktopAccountLimits.WORK)
                    fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                sql.mutate("j_work", listOf(lease.scopeId,c.workId), """INSERT INTO j_work
                    (scope_id,work_id,kind,subject_id,generation,next_attempt_ms,state) VALUES(?,?,'IMPORT_REVIEW',?,1,0,'PENDING')""",
                    lease.scopeId,c.workId,c.operationId)
                sql.mutate("j_import_review_command", listOf(lease.scopeId,c.operationId), """INSERT INTO j_import_review_command
                    (scope_id,operation_id,format_version,work_id,review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,
                    mapping_rank,mapping_predecessor,mapping_operation,predecessor,action,selected_rank,evidence_sha256,idempotency_key,
                    binding_commit,authority_revision,activation_revision,revision,state,exact_body,reference_kind,entry_context_id)
                    VALUES(?,?,1,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,
                        ?,?,?,1,'PREPARED',?,?,?)""",
                    lease.scopeId,c.operationId,c.workId,mapped?.reviewId,r.localJobId,r.localEntryId,mapped?.candidateId,r.serverJobId,r.serverEntryId,
                    mapped?.rank,r.predecessor,r.operationId,c.predecessor,c.action.name,c.selectedRank,c.selectedEvidenceSha256,c.idempotencyKey,
                    c.bindingCommit,c.authorityRevision,c.activationRevision,body,
                    if(mapped != null) "MAPPED_CANDIDATE" else "ENTRY_CONTEXT_V1",if(mapped == null) r.reviewId else null)
            }
            read(sql,lease,c.operationId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }
    override suspend fun read(lease: AccountLease, operationId: AccountUuid): AccountImportReviewPending? =
        owner.read(lease) { sql, _ -> read(sql,lease,operationId) }
    override suspend fun readPage(lease: AccountLease, after: AccountUuid?, limit: Int): List<AccountImportReviewPending> {
        require(limit in 1..100)
        return owner.read(lease) { sql, _ -> sql.query("SELECT operation_id FROM j_import_review_command WHERE scope_id=? AND operation_id>? ORDER BY operation_id LIMIT ?",
            lease.scopeId,after?.value ?: "",limit) { AccountUuid(it.getText(0)) }.map { read(sql,lease,it) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) } }
    }
    override suspend fun retryDeadLetter(lease: AccountLease, operationId: AccountUuid, commandId: AccountUuid,
        expectedRevision: Long): AccountImportReviewPending = owner.write(lease,operationId,"IMPORT_DECISION_RETRY",
        bytes("command" to commandId.value,"revision" to expectedRevision.toString())) { sql,_,replay ->
        val pending=read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        current(lease,pending.command)
        if(!replay) {
            require(!pending.terminal && pending.available && pending.revision==expectedRevision && pending.retryExhausted)
            val generation=sql.number("SELECT generation FROM j_work WHERE scope_id=? AND work_id=?",lease.scopeId,pending.command.workId)
            require(generation in 1 until DesktopAccountLimits.MAX_REVISION/8)
            sql.mutate("j_work",listOf(lease.scopeId,pending.command.workId),"UPDATE j_work SET generation=?,state='UNCERTAIN' WHERE scope_id=? AND work_id=?",
                generation+1,lease.scopeId,pending.command.workId)
        }
        read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }
    override suspend fun claim(lease: AccountLease, operationId: AccountUuid, commandId: AccountUuid, expectedRevision: Long): AccountWorkClaim? =
        owner.write(lease, operationId, "IMPORT_DECISION_CLAIM", bytes("command" to commandId.value,"revision" to expectedRevision.toString())) { sql, _, replay ->
            val pending = read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
            current(lease,pending.command)
            val c = pending.command
            if (!replay) {
                if (!pending.available || pending.terminal || pending.revision != expectedRevision) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                if (sql.number("SELECT count(*) FROM j_work WHERE state='RUNNING'") != 0L) return@write null
                val row = sql.query("SELECT state,lease_revision,generation,next_attempt_ms FROM j_work WHERE scope_id=? AND work_id=? AND kind='IMPORT_REVIEW' AND subject_id=?",
                    lease.scopeId,c.workId,c.operationId) { WorkRow(it.getText(0),it.getLong(1),it.getLong(2),it.getLong(3)) }.singleOrNull() ?: fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                if(row.state !in setOf("PENDING","UNCERTAIN") || row.revision>=DesktopAccountLimits.MAX_REVISION || attempts(row.revision,row.generation)>=8)
                    return@write null
                if(row.nextAt>System.currentTimeMillis())return@write null
                val claim = AccountWorkClaim(AccountWorkDescriptor(c.workId,AccountWorkKind.IMPORT_REVIEW,c.operationId,row.generation,row.nextAt),
                    lease.bootId,operationId,row.revision+1,System.currentTimeMillis()+DesktopAccountLimits.LEASE_MS)
                sql.mutate("j_work",listOf(lease.scopeId,c.workId),"""UPDATE j_work SET state='RUNNING',boot_id=?,lease_token=?,lease_revision=?,expires_ms=?
                    WHERE scope_id=? AND work_id=?""",claim.bootId,claim.leaseToken,claim.leaseRevision,claim.expiresAtMs,lease.scopeId,c.workId)
                sql.mutate("j_work_outcome",listOf(lease.scopeId,operationId),"""INSERT INTO j_work_outcome
                    (scope_id,operation_id,work_id,kind,subject_id,generation,next_attempt_ms,boot_id,lease_token,lease_revision,expires_ms)
                    VALUES(?,?,?,'IMPORT_REVIEW',?,?,?,?,?,?,?)""",lease.scopeId,operationId,c.workId,c.operationId,row.generation,row.nextAt,
                    claim.bootId,claim.leaseToken,claim.leaseRevision,claim.expiresAtMs)
            }
            val result=sql.query("SELECT boot_id,lease_token,lease_revision,expires_ms,generation,next_attempt_ms FROM j_work_outcome WHERE scope_id=? AND operation_id=? AND work_id=?",
                lease.scopeId,operationId,c.workId) { AccountWorkClaim(AccountWorkDescriptor(c.workId,AccountWorkKind.IMPORT_REVIEW,c.operationId,it.getLong(4),it.getLong(5)),
                    AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),it.getLong(2),it.getLong(3)) }.singleOrNull()
            if(result==null && sql.number("SELECT count(*) FROM j_work WHERE scope_id=? AND work_id=? AND lease_token=?",
                lease.scopeId,c.workId,operationId)!=0L)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            result
        }
    override suspend fun markSent(lease: AccountLease, operationId: AccountUuid, commandId: AccountUuid,
        claim: AccountWorkClaim, expectedRevision: Long): AccountImportReviewPending = owner.write(lease,operationId,"IMPORT_DECISION_SENT",
        claimInput(commandId,claim,expectedRevision)) { sql, _, replay ->
        if (!replay) {
            val pending = exact(sql,lease,commandId,claim,expectedRevision)
            current(lease,pending.command)
            if (claim.bootId != lease.bootId || claim.expiresAtMs <= System.currentTimeMillis()) fail(AccountFailure.ACCOUNT_LEASE_STALE)
            advance(sql,lease,commandId,expectedRevision,"SENT",null)
        }
        read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }
    override suspend fun settle(lease: AccountLease, operationId: AccountUuid, commandId: AccountUuid,
        claim: AccountWorkClaim, expectedRevision: Long, outcome: AccountImportReviewOutcome?, refused: Boolean, retryAfterMs: Long): AccountImportReviewPending {
        require(!refused || outcome == null)
        require(retryAfterMs in 0..86_400_000)
        val body = outcome?.bodyCopy()
        val marker = claimInput(commandId,claim,expectedRevision) + bytes("refused" to refused.toString(),"retry_after" to retryAfterMs.toString()) + (body ?: byteArrayOf())
        return owner.settle(lease,operationId,"IMPORT_DECISION_SETTLE",marker) { sql, replay ->
            if (!replay) {
                val retained = read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                // SENT may have committed while its readback failed before any HTTP admission.
                val actualRevision = if (outcome == null && !refused && retained.rawState == "SENT" &&
                    expectedRevision < DesktopAccountLimits.MAX_REVISION && retained.revision == expectedRevision+1)
                    retained.revision else expectedRevision
                val pending = exact(sql,lease,commandId,claim,actualRevision)
                require(pending.rawState in setOf("PREPARED","SENT","OUTCOME_PENDING"))
                val c = pending.command; val r = c.reference
                if (outcome != null) {
                    require(outcome.importEntryId == r.serverEntryId)
                    val expectedStatus = when(c.action) {
                        AccountImportReviewAction.ACCEPT, AccountImportReviewAction.CREATE_RECORDING -> "MANUAL_MATCH"
                        AccountImportReviewAction.REJECT -> "REVIEW_REQUIRED"
                        AccountImportReviewAction.KEEP_UNRESOLVED -> "MANUAL_UNRESOLVED"
                    }
                    require(outcome.status == expectedStatus && (outcome.recordingId != null) == (expectedStatus == "MANUAL_MATCH"))
                    sql.mutate("j_import_review_state",listOf(lease.scopeId,r.serverJobId,r.serverEntryId),"""INSERT INTO j_import_review_state
                        (scope_id,server_job_id,server_entry_id,operation_id,predecessor,status,recording_id) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(scope_id,server_job_id,server_entry_id) DO UPDATE SET operation_id=excluded.operation_id,
                        predecessor=excluded.predecessor,status=excluded.status,recording_id=excluded.recording_id""",
                        lease.scopeId,r.serverJobId,r.serverEntryId,c.operationId,outcome.decisionId,outcome.status,outcome.recordingId)
                }
                val state = if (outcome != null) "COMMITTED" else if (refused) "REFUSED" else "OUTCOME_PENDING"
                advance(sql,lease,commandId,actualRevision,state,body)
                val attempt=attempts(claim.leaseRevision,claim.descriptor.generation)
                val wait=maxOf(minOf(3_600_000L,1_000L shl (attempt-1).coerceIn(0,7)),retryAfterMs)
                val now=System.currentTimeMillis();require(now in 0..DesktopAccountLimits.MAX_REVISION-wait)
                sql.mutate("j_work",listOf(lease.scopeId,c.workId),"UPDATE j_work SET state=?,next_attempt_ms=? WHERE scope_id=? AND work_id=?",
                    if(outcome!=null || refused)"COMPLETED" else if(attempt>=8)"DEAD_LETTER" else "UNCERTAIN",
                    if(outcome!=null || refused)0L else now+wait,lease.scopeId,c.workId)
            }
            read(sql,lease,commandId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }
    private fun exact(sql: AccountPageSql, lease: AccountLease, id: AccountUuid, claim: AccountWorkClaim, revision: Long): AccountImportReviewPending {
        val p = read(sql,lease,id) ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        if (!p.available || p.terminal || p.revision != revision || claim.descriptor.kind != AccountWorkKind.IMPORT_REVIEW ||
            claim.descriptor.workId != p.command.workId || claim.descriptor.subjectId != id ||
            sql.number("""SELECT count(*) FROM j_work WHERE scope_id=? AND work_id=? AND state='RUNNING' AND boot_id=?
                AND lease_token=? AND lease_revision=? AND expires_ms=? AND kind='IMPORT_REVIEW' AND subject_id=? AND generation=?""",
                lease.scopeId,p.command.workId,claim.bootId,claim.leaseToken,claim.leaseRevision,claim.expiresAtMs,id,claim.descriptor.generation) != 1L)
            fail(AccountFailure.ACCOUNT_LEASE_STALE)
        return p
    }
    private fun advance(sql: AccountPageSql, lease: AccountLease, id: AccountUuid, revision: Long, state: String, outcome: ByteArray?) {
        require(revision in 1 until DesktopAccountLimits.MAX_REVISION)
        sql.mutate("j_import_review_command",listOf(lease.scopeId,id),"UPDATE j_import_review_command SET revision=?,state=?,outcome=? WHERE scope_id=? AND operation_id=?",
            revision+1,state,outcome,lease.scopeId,id)
    }
    private fun current(lease: AccountLease, c: AccountImportReviewCommand) {
        if (c.bindingCommit != lease.bindingCommit || c.authorityRevision != lease.authorityRevision ||
            c.activationRevision > lease.activationRevision) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }
    private fun read(sql: AccountPageSql, lease: AccountLease, id: AccountUuid): AccountImportReviewPending? = sql.query("""SELECT
        format_version,work_id,review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,mapping_rank,
        mapping_predecessor,mapping_operation,predecessor,action,selected_rank,evidence_sha256,idempotency_key,binding_commit,
        authority_revision,activation_revision,revision,state,exact_body,outcome,reference_kind,entry_context_id FROM j_import_review_command WHERE scope_id=? AND operation_id=?""",lease.scopeId,id) {
        if (it.getLong(0) != 1L) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        val r: AccountImportReviewReference = when(it.getText(23)) {
            "MAPPED_CANDIDATE" -> {
                if(it.isNull(2) || it.isNull(5) || it.isNull(8) || !it.isNull(24)) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                AccountImportReview(AccountUuid(it.getText(2)),AccountUuid(it.getText(3)),AccountUuid(it.getText(4)),AccountUuid(it.getText(5)),
                    AccountUuid(it.getText(6)),AccountUuid(it.getText(7)),it.getLong(8).toInt(),sql.nullable(it,9)?.let(::AccountUuid),AccountUuid(it.getText(10)))
            }
            "ENTRY_CONTEXT_V1" -> {
                if(!it.isNull(2) || !it.isNull(5) || !it.isNull(8) || it.isNull(9) || it.isNull(24)) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                AccountImportEntryReview(AccountUuid(it.getText(24)),AccountUuid(it.getText(3)),AccountUuid(it.getText(4)),
                    AccountUuid(it.getText(6)),AccountUuid(it.getText(7)),AccountUuid(it.getText(9)),AccountUuid(it.getText(10)))
            }
            else -> fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        }
        val action = AccountImportReviewAction.entries.singleOrNull { action -> action.name == it.getText(12) } ?: fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        val c = AccountImportReviewCommand(id,AccountUuid(it.getText(1)),r,AccountUuid(it.getText(11)),action,
            if(it.isNull(13)) null else it.getLong(13).toInt(),sql.nullable(it,14),it.getText(15),AccountUuid(it.getText(16)),it.getLong(17),it.getLong(18))
        if (!it.getBlob(21).contentEquals(c.bodyCopy())) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        val outcome = if (it.isNull(22)) null else {
            val b = it.getBlob(22); require(b.size <= 4096)
            val o = Json.parseToJsonElement(b.toString(Charsets.UTF_8)).jsonObject
            require(o.keys == setOf("decision_id","import_entry_id","status","recording_id","replayed"))
            AccountImportReviewOutcome(AccountUuid(o.getValue("decision_id").jsonPrimitive.content),AccountUuid(o.getValue("import_entry_id").jsonPrimitive.content),
                o.getValue("status").jsonPrimitive.content,o["recording_id"]?.takeIf { v -> v != JsonNull }?.jsonPrimitive?.content?.let(::AccountUuid),
                o.getValue("replayed").jsonPrimitive.boolean)
        }
        val work=sql.query("SELECT state,lease_revision,generation,next_attempt_ms FROM j_work WHERE scope_id=? AND work_id=?",lease.scopeId,c.workId) {
            WorkRow(it.getText(0),it.getLong(1),it.getLong(2),it.getLong(3)) }.single()
        val revision=it.getLong(19);val state=it.getText(20)
        if(revision !in 1..DesktopAccountLimits.MAX_REVISION || outcome?.importEntryId?.let { entry -> entry!=r.serverEntryId }==true ||
            state=="COMMITTED" && outcome==null || state=="REFUSED" && outcome!=null ||
            state in setOf("COMMITTED","REFUSED") && work.state!="COMPLETED") fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        AccountImportReviewPending(c,revision,state,outcome,work.state,attempts(work.revision,work.generation),work.nextAt)
    }.singleOrNull()
    private fun input(c: AccountImportReviewCommand): ByteArray {
        val mapped=c.reference as? AccountImportReview
        // Preserve the legacy mapped operation marker exactly; entry contexts have a named discriminator.
        val provenance=if(mapped==null) bytes("reference_kind" to "ENTRY_CONTEXT_V1","entry_context" to c.reference.reviewId.value) else byteArrayOf()
        return provenance + bytes("operation" to c.operationId.value,"work" to c.workId.value,
        "review" to c.reference.reviewId.value,"local_job" to c.reference.localJobId.value,"local_entry" to c.reference.localEntryId.value,
        "candidate" to (mapped?.candidateId?.value ?: ""),"server_job" to c.reference.serverJobId.value,"server_entry" to c.reference.serverEntryId.value,
        "mapping_rank" to (mapped?.rank?.toString() ?: ""),"mapping_predecessor" to (c.reference.predecessor?.value ?: ""),
        "mapping_operation" to c.reference.operationId.value,"predecessor" to c.predecessor.value,"action" to c.action.name,
        "rank" to (c.selectedRank?.toString() ?: ""),"evidence" to (c.selectedEvidenceSha256 ?: ""),"idempotency" to c.idempotencyKey,
        "binding" to c.bindingCommit.value,"authority" to c.authorityRevision.toString(),"activation" to c.activationRevision.toString()) + c.bodyCopy()
    }
    private fun claimInput(id: AccountUuid, claim: AccountWorkClaim, revision: Long): ByteArray = bytes("command" to id.value,
        "revision" to revision.toString(),"work" to claim.descriptor.workId.value,"boot" to claim.bootId.value,"token" to claim.leaseToken.value,
        "lease_revision" to claim.leaseRevision.toString(),"expires" to claim.expiresAtMs.toString(),"generation" to claim.descriptor.generation.toString())
    private data class WorkRow(val state:String,val revision:Long,val generation:Long,val nextAt:Long) {
        init {
            if(revision !in 0..DesktopAccountLimits.MAX_REVISION || generation !in 1..DesktopAccountLimits.MAX_REVISION/8 ||
                nextAt !in 0..DesktopAccountLimits.MAX_REVISION)fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        }
    }
    private fun attempts(revision:Long,generation:Long):Int {
        require(generation in 1..DesktopAccountLimits.MAX_REVISION/8)
        val count=revision-(generation-1)*8
        require(count in 0..8)
        return count.toInt()
    }
}
