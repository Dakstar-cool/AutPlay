package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

internal object AccountInteractionProjectionWriter {
    fun persist(sql:AccountPageSql,scope:AccountUuid,local:AccountUuid,device:AccountUuid?,p:JsonObject,createdAt:String?):Unit {
        AccountDomainWire.interaction(p);val r=p.obj("recommendation");val kind=p.string("interaction_type",100)
        val exact=AccountPortableV1.canonical(p)
        val old=sql.query("SELECT exact_payload,device_id FROM j_user_interaction WHERE scope_id=? AND interaction_id=?",scope,local){it.getBlob(0) to sql.nullable(it,1)}.singleOrNull()
        if(old!=null && (!old.first.contentEquals(exact)||old.second!=null&&device!=null&&old.second!=device.value))throw AccountPageDeferred("INTERACTION_IDENTITY_CONFLICT")
        sql.mutate("j_user_interaction",listOf(scope,local),"""INSERT INTO j_user_interaction
            (scope_id,interaction_id,device_id,interaction_type,recommendation_request_id,recording_id,source_rank,presentation_id,display_position,
            impression_local_id,impression_server_id,feedback_type,exact_payload,source_created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope_id,interaction_id) DO UPDATE SET device_id=coalesce(device_id,excluded.device_id),source_created_at=coalesce(source_created_at,excluded.source_created_at)""",
            scope,local,device,kind,r.id("recommendation_request_id"),r.id("recording_id"),r.number("source_rank",1,1_000),r.optionalId("presentation_id"),
            r.optionalNumber("display_position",1,1_000),r.optionalId("impression_event_local_id"),r.optionalId("impression_event_server_id"),
            p["feedback_type"]?.jsonPrimitive?.content,exact,createdAt)
    }
    fun local(sql:AccountPageSql,lease:AccountLease,w:AccountProtectedWitness,c:AccountDomainCommand.Interaction):Unit {
        val binding=w.preparation.binding?:fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        require(w.preparation.scope.remote!=null)
        val p=c.payload;AccountDomainWire.interaction(p);val r=p.obj("recommendation")
        if(p.string("interaction_type",100)=="RECOMMENDATION_IMPRESSION_RECORDED"){
            require(sql.number("""SELECT count(*) FROM j_presentation_impression WHERE scope_id=? AND presentation_id=? AND request_id=? AND source_rank=?
                AND recording_id=? AND event_id=?""",lease.scopeId,r.id("presentation_id"),r.id("recommendation_request_id"),r.number("source_rank",1,1_000),r.id("recording_id"),c.localId)==1L)
        }else{
            val impression=r.optionalId("impression_event_server_id")?:r.id("impression_event_local_id")
            require(sql.number("""SELECT count(*) FROM j_user_interaction u LEFT JOIN j_projection_state s ON s.scope_id=u.scope_id
                AND s.aggregate_type='USER_INTERACTION_EVENT' AND s.local_id=u.interaction_id WHERE u.scope_id=? AND (u.interaction_id=? OR s.server_id=?)
                AND u.interaction_type='RECOMMENDATION_IMPRESSION_RECORDED' AND u.device_id=? AND u.recommendation_request_id=? AND u.recording_id=? AND u.source_rank=?""",
                lease.scopeId,impression,impression,binding.deviceId,r.id("recommendation_request_id"),r.id("recording_id"),r.number("source_rank",1,1_000))==1L)
        }
        persist(sql,lease.scopeId,c.localId,binding.deviceId,p,null)
    }
}

internal class AccountInteractionRepository(private val owner:AccountRepositoryOwner):DesktopAccountInteractions {
    override suspend fun presented(lease:AccountLease,requestedEventId:AccountUuid,occurredAtMs:Long,payload:JsonObject):AccountDomainReceipt {
        AccountDomainWire.interaction(payload);require(payload.string("interaction_type",100)=="RECOMMENDATION_IMPRESSION_RECORDED")
        val change=AccountDomainChange(requestedEventId,occurredAtMs,AccountDomainCommand.Interaction(requestedEventId,payload));val r=payload.obj("recommendation")
        return owner.write(lease,requestedEventId,"PRESENTED",domainInput(change)){sql,w,replay->
            val key=listOf(lease.scopeId,r.id("presentation_id"),r.id("recommendation_request_id"),r.number("source_rank",1,1_000))
            val old=sql.query("SELECT event_id,recording_id FROM j_presentation_impression WHERE scope_id=? AND presentation_id=? AND request_id=? AND source_rank=?",*key.toTypedArray()){
                AccountUuid(it.getText(0)) to AccountUuid(it.getText(1))}.singleOrNull()
            if(old!=null){if(old.second!=r.id("recording_id"))fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                return@write domainReceipt(sql,lease,old.first)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)}
            if(replay)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            sql.mutate("j_presentation_impression",key,"INSERT INTO j_presentation_impression(scope_id,presentation_id,request_id,source_rank,recording_id,display_position,event_id) VALUES(?,?,?,?,?,?,?)",
                *key.toTypedArray(),r.id("recording_id"),r.number("display_position",1,1_000),requestedEventId)
            AccountDomainMutationWriter(sql,lease,w).mutate(change)
        }
    }
    override suspend fun readPage(lease:AccountLease,after:AccountUuid?,limit:Int):List<AccountInteractionProjection>{
        require(limit in 1..100)
        return owner.read(lease){sql,_->sql.query("""SELECT u.interaction_id,s.server_id,u.device_id,u.interaction_type,u.exact_payload,u.feedback_type,u.source_created_at,
            EXISTS(SELECT 1 FROM j_event_delivery d WHERE d.scope_id=u.scope_id AND d.event_id=u.interaction_id AND d.state<>'ACKNOWLEDGED')
            FROM j_user_interaction u LEFT JOIN j_projection_state s ON s.scope_id=u.scope_id AND s.aggregate_type='USER_INTERACTION_EVENT' AND s.local_id=u.interaction_id
            WHERE u.scope_id=? AND u.interaction_id>? ORDER BY u.interaction_id LIMIT ?""",lease.scopeId,after?.value?:"",limit){
            AccountInteractionProjection(AccountUuid(it.getText(0)),sql.nullable(it,1)?.let(::AccountUuid),sql.nullable(it,2)?.let(::AccountUuid),it.getText(3),
                AccountSyncWireV1.document(it.getBlob(4)).obj("recommendation"),sql.nullable(it,5),sql.nullable(it,6),it.getLong(7)==1L)}}
    }
}
