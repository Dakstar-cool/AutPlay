package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

/** Only already-owned J facts are captured. Standalone data enters solely through selected consent. */
internal object AccountDomainPortableCapture {
    fun capture(sql:AccountPageSql,lease:AccountLease,w:AccountProtectedWitness,change:AccountDomainChange,
        itemIndex:Int = 0):Unit {
        require(itemIndex in 0..100)
        val account=w.preparation.scope.portableAccountId?:return
        val c=change.command;val scope=lease.scopeId;val id=c.localId
        fun track(track:AccountUuid):Triple<AccountUuid,String?,String?> = sql.query("""SELECT t.track_ref_id,t.title,t.artist,d.recording_id,d.title_is_null,d.artist_is_null
            FROM j_track_ref t LEFT JOIN j_track_detail d ON d.scope_id=t.scope_id AND d.track_ref_id=t.track_ref_id WHERE t.scope_id=? AND t.track_ref_id=?""",scope,track){
            Triple(sql.nullable(it,3)?.let(::AccountUuid)?:AccountUuid(it.getText(0)),if(!it.isNull(4)&&it.getLong(4)==1L)null else description(it.getText(1),2_000),
                if(!it.isNull(5)&&it.getLong(5)==1L)null else description(it.getText(2),2_000))}.single()
        fun common(builder:JsonObjectBuilder):Unit {builder.put("version",1);builder.put("updated_at_ms",change.occurredAtMs)}
        fun metadata(builder:JsonObjectBuilder,t:Triple<AccountUuid,String?,String?>):Unit {
            builder.put("track_key",t.first.value);builder.put("title",t.second?.let(::JsonPrimitive)?:JsonNull);builder.put("artist",t.third?.let(::JsonPrimitive)?:JsonNull)
        }
        val record:AccountPortableRecord=when(c){
            is AccountDomainCommand.Listen -> {
                val p=c.payload;val trackId=p.id("local_user_track_ref_id");val t=track(trackId)
                val checkpointStart=sql.query("SELECT started_ms FROM j_playback_checkpoint WHERE scope_id=? AND listening_id=?",scope,id){it.getLong(0)}.singleOrNull()
                val started=c.startedAtMs?:checkpointStart?:fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                require(started in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS && (checkpointStart==null||checkpointStart==started))
                val genres=sql.query("SELECT exact_document FROM j_track_metadata WHERE scope_id=? AND track_id=?",scope,trackId){AccountSyncWireV1.document(it.getBlob(0))}.singleOrNull()
                    ?.get("fields")?.jsonObject?.get("genres")?.let{it as? JsonArray}?.mapNotNull{(it as? JsonPrimitive)?.takeIf{v->v.isString}?.content?.let{v->description(v,100)}}?.distinct()?.take(12).orEmpty()
                AccountPortableRecord(id,"LISTENING",buildJsonObject {
                    common(this);metadata(this,t);put("event_ids",JsonArray(listOf(JsonPrimitive(id.value))))
                    put("genres",JsonArray(genres.map(::JsonPrimitive)));put("started_at_ms",started);put("played_ms",p.getValue("played_ms"))
                    put("duration_ms",p.getValue("track_duration_ms"));put("excluded_from_taste",p.getValue("excluded_from_taste"))
                    put("event_origin",p.getValue("event_origin"));put("context",p.getValue("context"))
                })
            }
            is AccountDomainCommand.Preference -> {
                val t=track(c.trackId)
                AccountPortableRecord(AccountPortableV1.stableId(account,"preference:v1:"+t.first.value),"PREFERENCE",buildJsonObject {
                    common(this);metadata(this,t);put("preference",c.value);put("rating",JsonNull);put("excluded_from_taste",c.excludedFromTaste)
                })
            }
            is AccountDomainCommand.Interaction -> {
                if(c.payload.string("interaction_type",100)!="RECOMMENDATION_FEEDBACK_RECORDED")return
                val r=c.payload.obj("recommendation");val selected=c.payload.string("feedback_type",64)=="SELECTED"
                AccountPortableRecord(id,"TASTE",buildJsonObject{common(this);put("track_key",r.id("recording_id").value)
                    put("interaction",if(selected)"LIKE" else "DISLIKE");put("weight",if(selected)1_000 else -1_000)})
            }
            is AccountDomainCommand.Playlist -> playlist(sql,scope,id,change.occurredAtMs,false)
            is AccountDomainCommand.Entry -> entry(sql,scope,id,change.occurredAtMs,false,::track)
            is AccountDomainCommand.Delete -> when(c.aggregateType){
                "PLAYLIST"->playlist(sql,scope,id,change.occurredAtMs,true)
                "PLAYLIST_ENTRY"->entry(sql,scope,id,change.occurredAtMs,true,::track)
                else->return
            }
            else->return
        }
        val checked=AccountPortableV1.decode(record.wire())
        val old=sql.query("SELECT account_id,scope_id FROM j_portable_owner WHERE profile_id=?",lease.profileId){it.getText(0) to it.getText(1)}.singleOrNull()
        if(old!=null && old!=(account.value to scope.value))fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        if(old==null)sql.mutate("j_portable_owner",listOf(lease.profileId),"""INSERT INTO j_portable_owner
            (profile_id,account_id,scope_id,binding_commit,authority_revision,device_key_thumbprint,cutoff_ms) VALUES(?,?,?,?,?,?,0)""",
            lease.profileId,account,scope,lease.bindingCommit,lease.authorityRevision,w.preparation.binding?.deviceKeyThumbprint)
        val original=AccountPortableV1.canonical(record.wire());AccountPortableMerge.capacity(sql,account,original.size.toLong())
        sql.mutate("j_portable_original",listOf(account,change.operationId,itemIndex),"INSERT INTO j_portable_original(account_id,operation_id,item_index,record_id,kind,exact_record) VALUES(?,?,?,?,?,?)",
            account,change.operationId,itemIndex,record.recordId,record.kind,original)
        AccountPortableMerge.merge(sql,account,checked)
    }
    private fun playlist(sql:AccountPageSql,scope:AccountUuid,id:AccountUuid,time:Long,deleted:Boolean):AccountPortableRecord=sql.query("""SELECT p.name,p.description,s.server_id FROM j_playlist p
        LEFT JOIN j_projection_state s ON s.scope_id=p.scope_id AND s.aggregate_type='PLAYLIST' AND s.local_id=p.playlist_id WHERE p.scope_id=? AND p.playlist_id=?""",scope,id){
        AccountPortableRecord(sql.nullable(it,2)?.let(::AccountUuid)?:id,"PLAYLIST",buildJsonObject {
            put("version",1);put("updated_at_ms",time);put("name",description(sql.nullable(it,0),500)?:fail(AccountFailure.ACCOUNT_INPUT_INVALID))
            put("description",description(sql.nullable(it,1),2_000)?.let(::JsonPrimitive)?:JsonNull);put("deleted",deleted)
        })}.single()
    private fun entry(sql:AccountPageSql,scope:AccountUuid,id:AccountUuid,time:Long,deleted:Boolean,track:(AccountUuid)->Triple<AccountUuid,String?,String?>):AccountPortableRecord =
        sql.query("""SELECT e.playlist_id,e.track_ref_id,e.position_key,s.server_id,p.server_id FROM j_playlist_entry e
            LEFT JOIN j_projection_state s ON s.scope_id=e.scope_id AND s.aggregate_type='PLAYLIST_ENTRY' AND s.local_id=e.entry_id
            LEFT JOIN j_projection_state p ON p.scope_id=e.scope_id AND p.aggregate_type='PLAYLIST' AND p.local_id=e.playlist_id WHERE e.scope_id=? AND e.entry_id=?""",scope,id){
            val t=track(AccountUuid(it.getText(1)))
            AccountPortableRecord(sql.nullable(it,3)?.let(::AccountUuid)?:id,"PLAYLIST_ENTRY",buildJsonObject {
                put("version",1);put("updated_at_ms",time);put("playlist_id",sql.nullable(it,4)?:it.getText(0));put("track_key",t.first.value)
                put("title",t.second?.let(::JsonPrimitive)?:JsonNull);put("artist",t.third?.let(::JsonPrimitive)?:JsonNull)
                put("position_key",it.getText(2));put("deleted",deleted)
            })}.single()
    private fun description(value:String?,max:Int):String?=value?.filter{it.code>=32}?.let{
        if(it.codePointCount(0,it.length)>max)it.substring(0,it.offsetByCodePoints(0,max)) else it}?.takeIf{it.isNotBlank()}
}
