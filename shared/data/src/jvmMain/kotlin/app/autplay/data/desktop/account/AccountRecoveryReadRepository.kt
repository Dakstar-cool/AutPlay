package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*

/** Immutable scoped reads of existing J1 data. No schema, wire or storage budget expansion. */
internal class AccountRecoveryReadRepository(private val owner: AccountRepositoryOwner): DesktopAccountRecoveryReads {
    override suspend fun deferredPage(lease: AccountLease): AccountRetainedSyncPage? = owner.read(lease) { sql,witness ->
        val blocked=sql.number("SELECT count(*) FROM j_deferred_page WHERE scope_id=? AND profile_id=? AND state='BLOCKED'",lease.scopeId,lease.profileId)
        if(blocked==0L)return@read null
        sql.query("""SELECT r.operation_id,r.lineage_id,d.kind,d.reason,r.safe_wire FROM j_deferred_page d
            JOIN j_page_result r ON r.scope_id=d.scope_id AND r.profile_id=d.profile_id AND r.operation_id=d.operation_id
            JOIN j_profile_alias a ON a.scope_id=r.scope_id AND a.profile_id=r.profile_id AND a.lineage_id=r.lineage_id
            WHERE d.scope_id=? AND d.profile_id=? AND d.state='BLOCKED' AND length(r.safe_wire) BETWEEN 1 AND 8388608""",
            lease.scopeId,lease.profileId) {
            val kind=it.getText(2); val wire=it.getBlob(4); val root=AccountSyncWireV1.document(wire)
            val binding=witness.preparation.binding ?: fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            val events=if(kind=="ACK") AccountSyncWireV1.acks(root,binding.deviceId,lease.profileId,binding.journalEpoch).acks.map { ack -> ack.event }
                else emptyList()
            if(kind !in setOf("ACK","PULL","BOOTSTRAP")) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            AccountRetainedSyncPage(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),kind,it.getText(3),wire,events)
        }.singleOrNull() ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }
    override suspend fun conflicts(lease: AccountLease, after: AccountConflictCursor?, limit: Int): AccountConflictPage {
        require(limit in 1..100)
        require(after==null || after.aggregateType.length in 1..100 && after.aggregateType.all { it.code in 33..126 })
        return owner.read(lease) { sql,_ ->
            val rows=sql.query("""SELECT c.profile_id,c.source_id,c.aggregate_type,c.local_id,c.server_id,c.kind,c.resolution_state,
                c.base_version,c.observed_version,length(c.safe_evidence) FROM j_conflict c JOIN j_profile_alias a
                ON a.scope_id=c.scope_id AND a.profile_id=c.profile_id WHERE c.scope_id=?
                AND (c.profile_id,c.source_id,c.aggregate_type,c.local_id)>(?,?,?,?)
                ORDER BY c.profile_id,c.source_id,c.aggregate_type,c.local_id LIMIT ?""",lease.scopeId,
                after?.profileId?.value ?: "",after?.sourceId?.value ?: "",after?.aggregateType ?: "",after?.localId?.value ?: "",limit+1) {
                ConflictRow(AccountConflictCursor(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),it.getText(2),AccountUuid(it.getText(3))),
                    sql.nullable(it,4)?.let(::AccountUuid),it.getText(5),it.getText(6),if(it.isNull(7))null else it.getLong(7),
                    if(it.isNull(8))null else it.getLong(8),it.getLong(9))
            }
            val page=rows.take(limit)
            var used=0L
            val documents=page.map { row ->
                if(row.size !in 1..DesktopAccountLimits.PAGE_BYTES || row.size > DesktopAccountLimits.PAGE_BYTES-used)
                    fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                used+=row.size
                val c=row.cursor
                val wire=sql.query("SELECT safe_evidence FROM j_conflict WHERE scope_id=? AND profile_id=? AND source_id=? AND aggregate_type=? AND local_id=?",
                    lease.scopeId,c.profileId,c.sourceId,c.aggregateType,c.localId) { it.getBlob(0) }.single()
                AccountSyncWireV1.document(wire)
                AccountConflictDocument(c,row.server,row.kind,row.state,row.base,row.observed,wire)
            }
            AccountConflictPage(java.util.Collections.unmodifiableList(documents),if(rows.size>limit)page.last().cursor else null)
        }
    }
    private data class ConflictRow(val cursor:AccountConflictCursor,val server:AccountUuid?,val kind:String,val state:String,
        val base:Long?,val observed:Long?,val size:Long)
}
