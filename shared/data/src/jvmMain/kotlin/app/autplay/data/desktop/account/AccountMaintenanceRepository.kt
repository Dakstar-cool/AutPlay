package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*

internal class AccountMaintenanceRepository(private val owner:AccountRepositoryOwner):DesktopAccountMaintenance {
    override suspend fun readConflicts(lease:AccountLease,after:AccountConflictKey?,limit:Int):List<AccountConflictReview> {
        require(limit in 1..100)
        return owner.read(lease){sql,_->sql.query("""SELECT profile_id,source_id,aggregate_type,local_id,server_id,kind,resolution_state,base_version,observed_version,
            (SELECT r.event_id FROM j_conflict_repair r JOIN j_event e ON e.scope_id=r.scope_id AND e.event_id=r.event_id
                WHERE r.scope_id=c.scope_id AND r.profile_id=c.profile_id AND r.source_id=c.source_id AND r.aggregate_type=c.aggregate_type
                AND r.local_id=c.local_id ORDER BY e.sequence DESC LIMIT 1)
            FROM j_conflict c WHERE scope_id=? AND (profile_id,source_id,aggregate_type,local_id)>(?,?,?,?)
            ORDER BY profile_id,source_id,aggregate_type,local_id LIMIT ?""",lease.scopeId,after?.profileId?.value?:"",after?.sourceId?.value?:"",
            after?.aggregateType?:"",after?.localId?.value?:"",limit){
            AccountConflictReview(AccountConflictKey(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),it.getText(2),AccountUuid(it.getText(3))),
                sql.nullable(it,4)?.let(::AccountUuid),it.getText(5),it.getText(6),if(it.isNull(7))null else it.getLong(7),if(it.isNull(8))null else it.getLong(8),sql.nullable(it,9)?.let(::AccountUuid))}}
    }
    override suspend fun retryDeadLetter(lease:AccountLease,operationId:AccountUuid,eventId:AccountUuid,expectedAttempt:Long,expectedLeaseRevision:Long):Unit {
        require(expectedAttempt in 0..8 && expectedLeaseRevision in 0..DesktopAccountLimits.MAX_REVISION)
        owner.write(lease,operationId,"EXPLICIT_DELIVERY_RETRY",bytes("event_id" to eventId.value,"attempt" to expectedAttempt.toString(),"lease_revision" to expectedLeaseRevision.toString())){sql,w,replay->
            if(!replay){
                val binding=w.preparation.binding?:fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
                val remote=w.preparation.scope.remote?:fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
                require(sql.number("""SELECT count(*) FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                    WHERE e.scope_id=? AND e.event_id=? AND e.profile_id=? AND e.user_id=? AND e.device_id=? AND d.state='DEAD_LETTER'
                    AND d.attempt=? AND d.lease_revision=? AND d.lease_boot IS NULL AND d.lease_token IS NULL
                    AND NOT EXISTS(SELECT 1 FROM j_ack a WHERE a.scope_id=e.scope_id AND a.event_id=e.event_id AND a.terminal=1)""",
                    lease.scopeId,eventId,lease.profileId,remote.userId,binding.deviceId,expectedAttempt,expectedLeaseRevision)==1L)
                // Retain a stricter original Retry-After, the immutable event and its exact sequence.
                sql.mutate("j_event_delivery",listOf(lease.scopeId,eventId),"UPDATE j_event_delivery SET state='PENDING',attempt=0,last_error='EXPLICIT_RETRY' WHERE scope_id=? AND event_id=?",lease.scopeId,eventId)
            }
        }
    }
    override suspend fun repairConflict(lease:AccountLease,conflict:AccountConflictReview,correction:AccountDomainChange):AccountDomainReceipt {
        val k=conflict.key
        require(k.profileId==lease.profileId && correction.command.localId==k.localId &&
            AccountDomainWire.type(correction.command).second==k.aggregateType && conflict.observedVersion!=null &&
            conflict.rawKind in setOf("STALE_VERSION","EDIT_VS_DELETE","VERSION_CONFLICT"))
        val input=bytes("profile" to k.profileId.value,"source" to k.sourceId.value,"type" to k.aggregateType,"local" to k.localId.value,
            "kind" to conflict.rawKind,"resolution" to conflict.rawResolution,"observed" to conflict.observedVersion.toString(),"base" to (conflict.baseVersion?.toString()?:""),
            "server" to (conflict.serverId?.value?:""))+domainInput(correction)
        return owner.write(lease,correction.operationId,"CONFLICT_REPAIR",input){sql,w,replay->
            if(!replay){
                require(sql.number("""SELECT count(*) FROM j_conflict WHERE scope_id=? AND profile_id=? AND source_id=? AND aggregate_type=? AND local_id=?
                    AND kind=? AND resolution_state=? AND observed_version=? AND server_id IS ? AND base_version IS ?""",lease.scopeId,k.profileId,k.sourceId,k.aggregateType,k.localId,
                    conflict.rawKind,conflict.rawResolution,conflict.observedVersion,conflict.serverId,conflict.baseVersion)==1L)
                require(sql.number("SELECT count(*) FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=? AND observed_version=? AND server_id=?",
                    lease.scopeId,k.aggregateType,k.localId,conflict.observedVersion,conflict.serverId)==1L)
                require(sql.number("""SELECT count(*) FROM j_event e JOIN j_ack a ON a.scope_id=e.scope_id AND a.event_id=e.event_id
                    WHERE e.scope_id=? AND e.event_id=? AND e.aggregate_type=? AND e.aggregate_local_id=? AND a.terminal=1 AND a.outcome='CONFLICT'""",
                    lease.scopeId,k.sourceId,k.aggregateType,k.localId)==1L)
                if(sql.number("""SELECT count(*) FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                    WHERE e.scope_id=? AND e.aggregate_type=? AND e.aggregate_local_id=? AND d.state NOT IN ('ACKNOWLEDGED','REJECTED')
                    AND e.event_id<>? AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.source_id
                        WHERE r.scope_id=e.scope_id AND r.source_id=e.event_id AND a.terminal=1)""",lease.scopeId,k.aggregateType,k.localId,k.sourceId)!=0L)
                    fail(AccountFailure.ACCOUNT_BUSY)
                val receipt=AccountDomainMutationWriter(sql,lease,w).mutate(correction)
                // Local repair is separate evidence; the exact server conflict remains replayable.
                sql.mutate("j_conflict_repair",listOf(lease.scopeId,k.profileId,k.sourceId,k.aggregateType,k.localId,correction.operationId),"""INSERT INTO j_conflict_repair
                    (scope_id,profile_id,source_id,aggregate_type,local_id,operation_id,event_id) VALUES(?,?,?,?,?,?,?)""",lease.scopeId,k.profileId,k.sourceId,k.aggregateType,k.localId,
                    correction.operationId,receipt.eventId?:fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE))
            }
            domainReceipt(sql,lease,correction.operationId)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }
    override suspend fun retentionInventory(lease:AccountLease):AccountRetentionInventory=owner.read(lease){sql,w->
        val account=w.preparation.scope.portableAccountId
        val ddl=DesktopAccountSchema.create+DesktopAccountPageSchema.create+AccountRepositorySchema.create+AccountCatalogSchema.create+
            AccountMediaSchema.create+AccountIntentSchema.create+AccountInteractionSchema.create+AccountImportReviewSchema.create
        val tablePattern=Regex("CREATE TABLE ([a-z][a-z0-9_]*)\\s*\\(")
        val names=ddl.mapNotNull{tablePattern.find(it)?.groupValues?.get(1)}.distinct().sorted()
        var unknown=sql.number("SELECT count(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT IN (${names.joinToString(","){"?"}})",*names.toTypedArray())
        val tables=names.map{name->
            val columns=sql.query("PRAGMA table_info($name)"){it.getText(1)}.toSet()
            val (relation,predicate,args)=when {
                "scope_id" in columns->Triple("SCOPE","scope_id=?",arrayOf<Any?>(lease.scopeId))
                "source_scope_id" in columns && "target_scope_id" in columns->Triple("SOURCE_OR_TARGET_SCOPE","source_scope_id=? OR target_scope_id=?",arrayOf<Any?>(lease.scopeId,lease.scopeId))
                "account_id" in columns && account!=null->Triple("PROVEN_PORTABLE_ACCOUNT","account_id=?",arrayOf<Any?>(account))
                name in setOf("j_source_copy","j_source_outcome")->Triple("SELECTED_SOURCE_SCOPE","selection_id IN (SELECT selection_id FROM j_source_selection WHERE scope_id=?)",arrayOf<Any?>(lease.scopeId))
                name=="j_source_registration"->Triple("REGISTERED_SOURCE_SCOPE","source_store_id IN (SELECT source_store_id FROM j_source_selection WHERE scope_id=?)",arrayOf<Any?>(lease.scopeId))
                name in setOf("j_store","j_schema_component")->Triple("INSTALLATION_DESCRIPTOR","1=1",emptyArray<Any?>())
                "account_id" in columns && account==null->Triple("NO_PROVEN_PORTABLE_ACCOUNT","1=0",emptyArray<Any?>())
                else->{unknown++;Triple("UNRESOLVED_OWNER_RELATION","1=0",emptyArray<Any?>())}
            }
            AccountRetainedTable(name,relation,sql.number("SELECT count(*) FROM $name WHERE $predicate",*args))
        }
        unknown+=sql.number("SELECT count(*) FROM j_local_intent WHERE scope_id=? AND schema_version<>1",lease.scopeId)
        unknown+=sql.number("SELECT count(*) FROM j_portable_record WHERE account_id=? AND kind NOT IN ('LISTENING','PREFERENCE','PLAYLIST','PLAYLIST_ENTRY','TASTE')",account)
        AccountRetentionInventory(lease.storeId,lease.scopeId,account,tables,listOf("QUIESCED_ISOLATED_COPY","SQLITE_MAIN_WAL_SHM",
            "HELD_MEDIA_AND_COPY_REFERENCES","EXPLICIT_SOURCE_ORIGINALS","BACKUPS_AND_TEMPORARY_RETENTION","UNKNOWN_RETAINED_DISCRIMINATORS",
            "OPERATOR_HOLDS_GRACE_AND_OWNER_CONFIRMATION","K_EXCLUDED_FROM_ORDINARY_EXPORT"),unknown)
    }
}
