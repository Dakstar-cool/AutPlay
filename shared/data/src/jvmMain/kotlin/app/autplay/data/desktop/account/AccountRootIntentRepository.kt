package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

/** ROOT-only account metadata is already owned, but still needs explicit selected wire consent. */
internal class AccountRootIntentRepository(private val owner:AccountRepositoryOwner):DesktopAccountIntentMaterialization {
    override suspend fun freeze(lease:AccountLease,sourceScopeId:AccountUuid,selectionId:AccountUuid,reviewRevision:Long,changeIds:List<AccountUuid>):AccountIntentSelection {
        require(changeIds.size in 1..100 && changeIds.distinct().size==changeIds.size && reviewRevision in 1..DesktopAccountLimits.MAX_REVISION)
        val reviewedAt=owner.read(lease){sql,_->sql.query("SELECT exact_selection FROM j_account_intent_selection WHERE selection_id=?",selectionId){AccountSyncWireV1.document(it.getBlob(0)).number("reviewed_at_ms",0)}.singleOrNull()?:System.currentTimeMillis()}
        val frozen=owner.read(lease){sql,w->descriptor(sql,lease,w,sourceScopeId,selectionId,reviewRevision,changeIds,reviewedAt)}
        return owner.write(lease,selectionId,"ROOT_INTENT_PREPARE",frozen){sql,w,replay->
            val current=descriptor(sql,lease,w,sourceScopeId,selectionId,reviewRevision,changeIds,reviewedAt)
            if(!current.contentEquals(frozen))fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            if(!replay)sql.mutate("j_account_intent_selection",listOf(selectionId),"""INSERT INTO j_account_intent_selection
                (selection_id,source_scope_id,target_scope_id,account_id,review_revision,binding_commit,authority_revision,exact_selection,state) VALUES(?,?,?,?,?,?,?,?,'FROZEN')""",
                selectionId,sourceScopeId,lease.scopeId,w.preparation.scope.portableAccountId,reviewRevision,lease.bindingCommit,lease.authorityRevision,frozen)
            summary(AccountSyncWireV1.document(frozen))
        }
    }
    override suspend fun materialize(lease:AccountLease,selectionId:AccountUuid,reviewRevision:Long):AccountIntentMaterializationResult {
        val frozen=owner.read(lease){sql,_->sql.query("SELECT exact_selection FROM j_account_intent_selection WHERE selection_id=? AND target_scope_id=? AND review_revision=?",
            selectionId,lease.scopeId,reviewRevision){it.getBlob(0)}.singleOrNull()?:fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)}
        val root=AccountSyncWireV1.document(frozen);val source=root.id("source_scope_id");val items=root.array("items",100).map{it.jsonObject}
        val operation=AccountPortableV1.stableId(selectionId,"root-materialization:v1")
        return owner.write(lease,operation,"ROOT_INTENT_MATERIALIZE",frozen){sql,w,replay->
            if(!descriptor(sql,lease,w,source,selectionId,reviewRevision,items.map{it.id("change_id")},root.number("reviewed_at_ms",0)).contentEquals(frozen))fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            val account=w.preparation.scope.portableAccountId?:fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
            val unavailable=mutableListOf<AccountUuid>();val links=mutableListOf<AccountMaterializationLink>()
            val writer=AccountDomainMutationWriter(sql,lease,w)
            val tracks=root.array("tracks",100).map{it.jsonObject}.associateBy{it.id("id")}
            val playlists=root.array("playlists",100).map{it.jsonObject}.associateBy{it.id("id")}
            val entries=root.array("entries",100).map{it.jsonObject}.associateBy{it.id("id")}
            fun mapped(type:String,id:AccountUuid):AccountUuid?=sql.text("""SELECT target_id FROM j_account_aggregate_map
                WHERE source_scope_id=? AND aggregate_type=? AND source_id=? AND target_scope_id=?""",source,type,id,lease.scopeId)?.let(::AccountUuid)
            fun claim(type:String,id:AccountUuid,target:AccountUuid=id):AccountUuid {
                val old=mapped(type,id);if(old!=null){if(old!=target)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT);return old}
                sql.mutate("j_account_aggregate_map",listOf(source,type,id,lease.scopeId),"INSERT INTO j_account_aggregate_map(source_scope_id,aggregate_type,source_id,target_scope_id,target_id) VALUES(?,?,?,?,?)",source,type,id,lease.scopeId,target)
                return target
            }
            fun track(id:AccountUuid):AccountUuid {
                mapped("USER_TRACK_REF",id)?.let{return it}
                val p=tracks[id]?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                if(sql.number("SELECT count(*) FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",lease.scopeId,id)!=0L)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                val library=p.id("library_id");claim("USER_TRACK_REF",id);claim("LIBRARY_ENTRY",library)
                AccountAccountTrackWriter.create(sql,lease,w,AccountCreateTrack(AccountUuid.random(),id,library,p.string("title",4_096,0),p.string("artist",4_096,0),root.number("reviewed_at_ms",0)))
                return id
            }
            fun library(id:AccountUuid,trackId:AccountUuid):AccountUuid {
                val tr=track(trackId)
                mapped("LIBRARY_ENTRY",id)?.let{return it}
                if(sql.number("SELECT (SELECT count(*) FROM j_library_entry WHERE scope_id=? AND entry_id=?)+(SELECT count(*) FROM j_library_relation WHERE scope_id=? AND entry_id=?)",lease.scopeId,id,lease.scopeId,id)!=0L)
                    fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                claim("LIBRARY_ENTRY",id)
                writer.mutate(AccountDomainChange(AccountUuid.random(),root.number("reviewed_at_ms",0),AccountDomainCommand.Library(id,tr,false)))
                return id
            }
            fun playlist(id:AccountUuid):AccountUuid {
                mapped("PLAYLIST",id)?.let{return it}
                val p=playlists[id]?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                if(sql.number("SELECT count(*) FROM j_playlist WHERE scope_id=? AND playlist_id=?",lease.scopeId,id)!=0L)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                claim("PLAYLIST",id)
                writer.mutate(AccountDomainChange(AccountUuid.random(),root.number("reviewed_at_ms",0),AccountDomainCommand.Playlist(id,p.string("name",500),nullable(p,"description"),true)))
                return id
            }
            fun entry(id:AccountUuid):AccountUuid {
                mapped("PLAYLIST_ENTRY",id)?.let{return it}
                val p=entries[id]?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                claim("PLAYLIST_ENTRY",id)
                writer.mutate(AccountDomainChange(AccountUuid.random(),root.number("reviewed_at_ms",0),AccountDomainCommand.Entry(id,playlist(p.id("playlist_id")),track(p.id("track_id")),p.string("position_key",128),false)))
                return id
            }
            for(i in items){
                val change=i.id("change_id");val type=i.string("aggregate_type",100);val eventType=i.string("event_type",200);val local=i.id("local_id")
                val old=link(sql,source,change)
                if(old!=null){if(old.targetAccountId!=account)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT);links.add(old);continue}
                if(replay){unavailable.add(change);continue}
                if(i.number("version",1)!=1L){unavailable.add(change);continue}
                val p=i.obj("payload");var event=AccountUuid.random();var target=local
                val command:AccountDomainCommand?=when(eventType){
                    "USER_TRACK_REF_CREATED"->{
                        claim("USER_TRACK_REF",local);val library=p.id("library_entry_local_id");claim("LIBRARY_ENTRY",library)
                        AccountAccountTrackWriter.create(sql,lease,w,AccountCreateTrack(event,local,library,p.string("title",4_096,0),p.string("artist",4_096,0),i.number("occurred_ms",0)));null
                    }
                    "USER_TRACK_REF_PATCHED"->AccountDomainCommand.PatchTrack(track(local),p)
                    "LIBRARY_ENTRY_UPSERTED"->{
                        val t=track(p.id("local_user_track_ref_id"));val removed=p["removed_at_ms"]!=null&&p["removed_at_ms"]!=JsonNull
                        if(removed)library(local,p.id("local_user_track_ref_id")) else claim(type,local)
                        AccountDomainCommand.Library(local,t,removed)
                    }
                    "USER_TRACK_PREFERENCE_SET"->{val t=track(p.id("local_user_track_ref_id"));claim(type,local)
                        AccountDomainCommand.Preference(local,t,p.string("preference",100),p.bool("excluded_from_taste"),p["attribution"]?.takeIf{it!=JsonNull}?.jsonObject)}
                    "PLAYLIST_CREATED"->{claim(type,local);AccountDomainCommand.Playlist(local,p.string("name",500),nullable(p,"description"),true)}
                    "PLAYLIST_METADATA_PATCHED"->{val id=playlist(local);val prior=sql.query("SELECT name,description FROM j_playlist WHERE scope_id=? AND playlist_id=?",lease.scopeId,id){it.getText(0) to sql.nullable(it,1)}.single()
                        AccountDomainCommand.Playlist(id,if ("name" in p) p.string("name",500) else prior.first,if ("description" in p) nullable(p,"description") else prior.second,false)}
                    "PLAYLIST_ENTRY_UPSERTED","PLAYLIST_ENTRY_MOVED"->{
                        val ep=entries[local]?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                        val pl=playlist(p.optionalId("local_playlist_id")?:ep.id("playlist_id"));val tr=track(p.optionalId("local_user_track_ref_id")?:ep.id("track_id"))
                        if(eventType=="PLAYLIST_ENTRY_MOVED")entry(local) else claim(type,local)
                        val before=p.optionalId("before_local_playlist_entry_id")?.let{entry(it)}
                        AccountDomainCommand.Entry(local,pl,tr,p["position_key"]?.jsonPrimitive?.content?:ep.string("position_key",128),eventType=="PLAYLIST_ENTRY_MOVED",
                            p["attribution"]?.takeIf{it!=JsonNull}?.jsonObject,"position_key" !in p,before)
                    }
                    "AGGREGATE_DELETED"->{when(type){"USER_TRACK_REF"->track(local);"PLAYLIST"->playlist(local);"PLAYLIST_ENTRY"->entry(local);"LIBRARY_ENTRY"->{
                        val tr=sql.text("SELECT track_ref_id FROM j_library_entry WHERE scope_id=? AND entry_id=? UNION ALL SELECT track_ref_id FROM j_library_relation WHERE scope_id=? AND entry_id=?",source,local,source,local)?.let(::AccountUuid)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                        library(local,tr)};else->fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)};AccountDomainCommand.Delete(local,type)}
                    "LISTENING_EVENT_RECORDED"->{
                        val start=i.nullNumber("started_ms",0,DesktopAccountLimits.MAX_EVENT_TIME_MS)
                        if(start==null){unavailable.add(change);null}else{
                            val tr=track(p.id("local_user_track_ref_id"));event=AccountUuid.random();target=event;claim(type,local,event)
                            val payload=JsonObject(p+mapOf("local_user_track_ref_id" to JsonPrimitive(tr.value),"server_user_track_ref_id" to
                                (sql.text("SELECT server_track_ref_id FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",lease.scopeId,tr)?.let(::JsonPrimitive)?:JsonNull)))
                            AccountDomainCommand.Listen(event,payload,start)
                        }
                    }
                    else->{unavailable.add(change);null}
                }
                if(command!=null)writer.mutate(AccountDomainChange(event,i.number("occurred_ms",0),command))
                if(change in unavailable)continue
                val receipt=domainReceipt(sql,lease,event)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                val lineage=sql.text("SELECT lineage_id FROM j_event WHERE scope_id=? AND event_id=?",lease.scopeId,receipt.eventId?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))?.let(::AccountUuid)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                sql.mutate("j_account_intent_link",listOf(source,change),"""INSERT INTO j_account_intent_link
                    (source_scope_id,change_id,target_scope_id,account_id,target_id,event_id,lineage_id,selection_id) VALUES(?,?,?,?,?,?,?,?)""",source,change,lease.scopeId,account,target,receipt.eventId,lineage,selectionId)
                sql.mutate("j_local_intent",listOf(source,change),"UPDATE j_local_intent SET state='MATERIALIZED_EXTERNAL' WHERE scope_id=? AND change_id=?",source,change)
                if(command is AccountDomainCommand.Listen)joinListening(sql,account,local,event)
                links.add(link(sql,source,change)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))
            }
            if(!replay)sql.mutate("j_account_intent_selection",listOf(selectionId),"UPDATE j_account_intent_selection SET state=? WHERE selection_id=?",if(unavailable.isEmpty())"MATERIALIZED" else "PARTIAL",selectionId)
            AccountIntentMaterializationResult(links,unavailable)
        }
    }
    private fun descriptor(sql:AccountPageSql,l:AccountLease,w:AccountProtectedWitness,source:AccountUuid,selection:AccountUuid,review:Long,ids:List<AccountUuid>,reviewedAt:Long):ByteArray {
        val account=w.preparation.scope.portableAccountId?:fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        require(w.preparation.binding!=null && w.preparation.scope.remote!=null && source!=l.scopeId)
        require(sql.number("SELECT count(*) FROM j_scope WHERE scope_id=? AND account_id=? AND server_instance_id IS NULL",source,account)==1L)
        val tracks=linkedSetOf<AccountUuid>();val playlists=linkedSetOf<AccountUuid>();val entries=linkedSetOf<AccountUuid>()
        val items=ids.map{id->sql.query("""SELECT i.event_type,i.aggregate_type,i.aggregate_local_id,i.schema_version,i.occurred_at_ms,i.canonical_payload,o.local_sequence,i.profile_id
            FROM j_local_intent i JOIN j_local_intent_order o ON o.scope_id=i.scope_id AND o.change_id=i.change_id WHERE i.scope_id=? AND i.change_id=?""",source,id){
            val event=it.getText(0);val type=it.getText(1);val local=AccountUuid(it.getText(2));val p=AccountSyncWireV1.document(it.getBlob(5))
            if(it.getLong(3)==1L)when(type){"USER_TRACK_REF"->tracks.add(local);"USER_TRACK_PREFERENCE","LISTENING_EVENT"->p.optionalId("local_user_track_ref_id")?.let(tracks::add)
                "PLAYLIST"->playlists.add(local);"PLAYLIST_ENTRY"->entries.add(local);"LIBRARY_ENTRY"->sql.text("SELECT track_ref_id FROM j_library_entry WHERE scope_id=? AND entry_id=? UNION ALL SELECT track_ref_id FROM j_library_relation WHERE scope_id=? AND entry_id=?",source,local,source,local)?.let{v->tracks.add(AccountUuid(v))}}
            val started=if(it.getLong(3)==1L && type=="LISTENING_EVENT")sql.query("SELECT started_ms FROM j_playback_checkpoint WHERE scope_id=? AND listening_id=?",source,local){s->s.getLong(0)}.singleOrNull()
                ?:sql.query("SELECT exact_input FROM j_repository_commit WHERE scope_id=? AND operation_id=? AND kind='DOMAIN'",source,id){s->AccountSyncWireV1.document(s.getBlob(0)).nullNumber("started_at_ms",0,DesktopAccountLimits.MAX_EVENT_TIME_MS)}.singleOrNull() else null
            buildJsonObject{put("change_id",id.value);put("event_type",event);put("aggregate_type",type);put("local_id",local.value);put("version",it.getLong(3));put("occurred_ms",it.getLong(4));put("payload",p);put("local_sequence",it.getLong(6));put("original_profile_id",it.getText(7));put("started_ms",started?.let(::JsonPrimitive)?:JsonNull)}
        }.singleOrNull()?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)}.sortedBy{it.number("local_sequence",1)}
        val entryDocs=entries.map{id->sql.query("SELECT playlist_id,track_ref_id,position_key FROM j_playlist_entry WHERE scope_id=? AND entry_id=?",source,id){
            playlists.add(AccountUuid(it.getText(0)));tracks.add(AccountUuid(it.getText(1)));buildJsonObject{put("id",id.value);put("playlist_id",it.getText(0));put("track_id",it.getText(1));put("position_key",it.getText(2))}}.single()}
        require(tracks.size+playlists.size+entries.size<=100)
        val trackDocs=tracks.map{id->sql.query("SELECT title,artist FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",source,id){
            val library=sql.query("SELECT canonical_payload FROM j_local_intent WHERE scope_id=? AND aggregate_local_id=? AND event_type='USER_TRACK_REF_CREATED'",source,id){s->AccountSyncWireV1.document(s.getBlob(0)).id("library_entry_local_id")}.single()
            buildJsonObject{put("id",id.value);put("title",it.getText(0));put("artist",it.getText(1));put("library_id",library.value)}}.single()}
        val playlistDocs=playlists.map{id->sql.query("SELECT name,description FROM j_playlist WHERE scope_id=? AND playlist_id=?",source,id){buildJsonObject{put("id",id.value);put("name",sql.nullable(it,0)?.let(::JsonPrimitive)?:JsonNull);put("description",sql.nullable(it,1)?.let(::JsonPrimitive)?:JsonNull)}}.single()}
        return AccountPortableV1.canonical(buildJsonObject{
            put("selection_id",selection.value);put("source_scope_id",source.value);put("target_scope_id",l.scopeId.value);put("account_id",account.value);put("review_revision",review)
            put("binding_commit",l.bindingCommit.value);put("authority_revision",l.authorityRevision);put("reviewed_at_ms",reviewedAt.also{require(it in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS)})
            put("items",JsonArray(items));put("tracks",JsonArray(trackDocs.sortedBy{it.id("id").value}));put("playlists",JsonArray(playlistDocs.sortedBy{it.id("id").value}));put("entries",JsonArray(entryDocs.sortedBy{it.id("id").value}))
        })
    }
    private fun summary(p:JsonObject):AccountIntentSelection=AccountIntentSelection(p.id("selection_id"),p.id("source_scope_id"),p.id("target_scope_id"),p.number("review_revision",1),p.array("items",100).map{val i=it.jsonObject;AccountIntentReviewItem(i.id("change_id"),i.string("event_type",200),i.string("aggregate_type",100),i.id("local_id"))})
    private fun nullable(p:JsonObject,k:String):String?=if(p[k]==JsonNull)null else p.string(k,2_000,0)
    private fun link(sql:AccountPageSql,source:AccountUuid,id:AccountUuid):AccountMaterializationLink?=sql.query("SELECT target_scope_id,account_id,target_id,event_id,lineage_id FROM j_account_intent_link WHERE source_scope_id=? AND change_id=?",source,id){
        AccountMaterializationLink(id.value,AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),AccountUuid(it.getText(2)),AccountUuid(it.getText(4)),AccountUuid(it.getText(3)),null)}.singleOrNull()
    private fun joinListening(sql:AccountPageSql,account:AccountUuid,original:AccountUuid,event:AccountUuid):Unit {
        val id=sql.text("SELECT record_id FROM j_listening_alias WHERE account_id=? AND event_id=?",account,event)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        val record=sql.query("SELECT record_id,kind,document FROM j_portable_record WHERE account_id=? AND record_id=?",account,id){AccountPortableMerge.decode(it.getText(0),it.getText(1),it.getBlob(2))}.single()
        AccountPortableMerge.merge(sql,account,AccountPortableV1.decode(record.copy(document=JsonObject(record.document+("event_ids" to JsonArray((record.document.getValue("event_ids").jsonArray+JsonPrimitive(original.value)).distinct())))).wire()))
    }
}
