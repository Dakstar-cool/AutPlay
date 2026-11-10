package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

/** Explicit selected immutable source copies. No automatic capture, label matching or Room write. */
internal class AccountMaterializationRepository(private val owner: AccountRepositoryOwner) : DesktopAccountMaterialization {
    override suspend fun readSourceRegistration(lease:AccountLease):AccountSourceRegistration? = owner.read(lease){sql,_->
        sql.query("SELECT source_store_id,owner_ref FROM j_source_registration LIMIT 2"){AccountSourceRegistration(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)))}
            .also{if(it.size>1)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)}.singleOrNull()}
    override suspend fun readSelection(lease:AccountLease,selectionId:AccountUuid):AccountSelectionStatus?=owner.read(lease){sql,_->
        sql.query("SELECT review_revision,state,exact_selection,source_store_id FROM j_source_selection WHERE selection_id=? AND scope_id=?",selectionId,lease.scopeId){
            val root=AccountSyncWireV1.document(it.getBlob(2));val source=AccountUuid(it.getText(3))
            val facts=root.array("facts",100).map{f->f.jsonObject};val links=facts.mapNotNull{f->link(sql,source,f.string("change_id",1_024))}
            AccountSelectionStatus(selectionId,it.getLong(0),it.getText(1),facts.map{f->val id=f.string("change_id",1_024)
                val status=sql.query("SELECT state,reason FROM j_source_outcome WHERE selection_id=? AND change_id=?",selectionId,id){s->s.getText(0) to sql.nullable(s,1)}.singleOrNull()
                AccountSelectedFactStatus(id,f.string("kind",100),status?.first?:if(links.any{l->l.sourceChangeId==id})"MATERIALIZED" else "COPIED",status?.second)},links)
        }.singleOrNull()}
    override suspend fun registerSource(lease: AccountLease, operationId: AccountUuid, canonicalOwnerReference: AccountUuid): AccountSourceRegistration =
        owner.write(lease, operationId, "SOURCE_REGISTER", bytes("owner_ref" to canonicalOwnerReference.value)) { sql, _, replay ->
            val old = sql.text("SELECT source_store_id FROM j_source_registration WHERE owner_ref=?", canonicalOwnerReference)?.let(::AccountUuid)
            if (old != null) AccountSourceRegistration(old, canonicalOwnerReference)
            else {
                if (replay) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                // The sole canonical standalone registration cannot be replaced to evade old claims.
                if (sql.number("SELECT count(*) FROM j_source_registration") != 0L) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                val id = AccountUuid.random()
                sql.mutate("j_source_registration", listOf(id), "INSERT INTO j_source_registration(source_store_id,owner_ref) VALUES(?,?)", id, canonicalOwnerReference)
                AccountSourceRegistration(id, canonicalOwnerReference)
            }
        }
    override suspend fun copySelected(lease: AccountLease, consent: AccountSelectionConsent, source: AccountSourceRegistration, port: DesktopSelectedSourcePort): Unit {
        require(consent.selectedChangeIds.size in 1..100 && consent.selectedChangeIds.distinct().size == consent.selectedChangeIds.size &&
            consent.reviewRevision in 1..DesktopAccountLimits.MAX_REVISION && consent.ownershipCutoffMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS &&
            consent.bindingCommitId == lease.bindingCommit && consent.authorityRevision == lease.authorityRevision)
        val capture = port.capture(source, consent.selectedChangeIds.toList())
        capture.use {
            val snapshot = it.snapshot
            require(snapshot.source == source && snapshot.facts.map { f -> f.changeId }.toSet() == consent.selectedChangeIds.toSet())
            val input = frozen(snapshot, consent)
            owner.write(lease, consent.selectionId, "SOURCE_COPY", input) { sql, witness, replay ->
                if (!capture.stillExact()) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                if (replay) for (fact in snapshot.facts) {
                    val saved=sql.query("SELECT exact_fact FROM j_source_copy WHERE source_store_id=? AND change_id=?",source.sourceStoreId,fact.changeId){it.getBlob(0)}.singleOrNull()
                    if(saved?.contentEquals(fact.canonicalBytes)!=true)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                }
                if (!replay) {
                    if (sql.text("SELECT owner_ref FROM j_source_registration WHERE source_store_id=?", source.sourceStoreId) != source.ownerReference.value) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                    sql.mutate("j_source_selection", listOf(consent.selectionId), """INSERT INTO j_source_selection
                        (selection_id,source_store_id,scope_id,account_id,review_revision,cutoff_ms,binding_commit,authority_revision,state,exact_selection)
                        VALUES(?,?,?,?,?,?,?,?,'COPIED',?)""", consent.selectionId, source.sourceStoreId, lease.scopeId,
                        witness.preparation.scope.portableAccountId, consent.reviewRevision, consent.ownershipCutoffMs,
                        consent.bindingCommitId, consent.authorityRevision, input)
                    for (fact in snapshot.facts) {
                        val prior = sql.query("""SELECT c.exact_fact,s.scope_id,s.account_id FROM j_source_copy c JOIN j_source_selection s
                            ON s.selection_id=c.selection_id WHERE c.source_store_id=? AND c.change_id=?""", source.sourceStoreId, fact.changeId) {
                            Triple(it.getBlob(0), it.getText(1), sql.nullable(it, 2))
                        }.singleOrNull()
                        if (prior != null) {
                            if (!prior.first.contentEquals(fact.canonicalBytes) ||
                                (prior.third ?: prior.second) != (witness.preparation.scope.portableAccountId?.value ?: lease.scopeId.value)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                            continue
                        }
                        accountEventCapacity(sql, fact.canonicalBytes.size.toLong())
                        sql.mutate("j_source_copy", listOf(source.sourceStoreId, fact.changeId), """INSERT INTO j_source_copy
                            (source_store_id,change_id,selection_id,aggregate_id,kind,version,occurred_ms,exact_fact) VALUES(?,?,?,?,?,?,?,?)""",
                            source.sourceStoreId, fact.changeId, consent.selectionId, fact.aggregateId, fact.kind, fact.version, fact.occurredAtMs, fact.canonicalBytes)
                    }
                }
            }
        }
    }
    override suspend fun materializeSelected(lease: AccountLease, selectionId: AccountUuid, reviewRevision: Long,
        port: DesktopSelectedSourcePort): List<AccountMaterializationLink> {
        val retained = owner.read(lease) { sql, _ -> sql.query("""SELECT s.exact_selection,s.source_store_id,r.owner_ref FROM j_source_selection s
            JOIN j_source_registration r ON r.source_store_id=s.source_store_id WHERE s.selection_id=? AND s.scope_id=? AND s.review_revision=?""",
            selectionId, lease.scopeId, reviewRevision) { Triple(it.getBlob(0),AccountUuid(it.getText(1)),AccountUuid(it.getText(2))) }.singleOrNull()
            ?: fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) }
        val root = AccountSyncWireV1.document(retained.first)
        val facts = owner.read(lease) { sql, _ -> root.array("facts",100).map { value -> val f=value.jsonObject
            val change=f.string("change_id",1_024)
            val exact=sql.query("SELECT exact_fact FROM j_source_copy WHERE source_store_id=? AND change_id=?",retained.second,change){it.getBlob(0)}.singleOrNull()
                ?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            AccountSelectedFact(change,f.string("aggregate_id",1_024),f.string("kind",100),
                f.number("version",1,1),f.number("occurred_ms",0,DesktopAccountLimits.MAX_EVENT_TIME_MS),exact) } }
        val source=AccountSourceRegistration(retained.second,retained.third)
        val capture=port.capture(source,facts.map{it.changeId})
        capture.use {
            val snapshot=capture.snapshot
            val consent=AccountSelectionConsent(selectionId,reviewRevision,root.number("cutoff_ms",0),lease.bindingCommit,lease.authorityRevision,facts.map{it.changeId})
            if(!frozen(snapshot,consent).contentEquals(retained.first) || !capture.stillExact() || snapshot.facts.size!=facts.size ||
                facts.any{old->snapshot.facts.singleOrNull{it.changeId==old.changeId}?.canonicalBytes?.contentEquals(old.canonicalBytes)!=true}) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            val manual = manualProofMatches(facts)
            // Stable separate operation identity; the frozen selected bytes remain the input, never current labels.
            val operation=AccountPortableV1.stableId(selectionId,"materialization:v1")
            return owner.write(lease,operation,"SOURCE_MATERIALIZE",retained.first) { sql,witness,replay ->
                if(!capture.stillExact()) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                if(!replay) {
                    val writer=AccountDomainMutationWriter(sql,lease,witness)
                    val ordered=facts.sortedWith(compareBy<AccountSelectedFact>{priority(it.kind)}.thenBy{it.occurredAtMs}.thenBy{it.changeId})
                    var unsupported=false
                    for(fact in ordered) {
                        val existing=link(sql,source.sourceStoreId,fact.changeId)
                        if(existing!=null) {
                            if((existing.targetAccountId?.value?:existing.targetScopeId.value)!=(witness.preparation.scope.portableAccountId?.value?:lease.scopeId.value)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                            continue
                        }
                        if (fact.changeId in manual.supportIds) {
                            // Provenance supports the selected manual edit; it emits no separate wire event.
                            sql.mutate("j_source_outcome", listOf(selectionId, fact.changeId),
                                "INSERT INTO j_source_outcome(selection_id,change_id,state,reason) VALUES(?,?,'SUPPORT_ONLY',?)",
                                selectionId, fact.changeId, "SELECTED_MANUAL_METADATA_PROVENANCE")
                            continue
                        }
                        val body=AccountSyncWireV1.document(fact.canonicalBytes).obj("snapshot")
                        var event=AccountUuid.random()
                        fun mapped(id:String,type:String):AccountUuid = map(sql,source.sourceStoreId,id,type,lease.scopeId)
                        fun existingMap(id:String,type:String):AccountUuid = dependency(sql,lease,witness,snapshot,selectionId,id,type,consent.ownershipCutoffMs,writer)
                        var target:AccountUuid?=null
                        val command:AccountDomainCommand?=when(fact.kind) {
                            "TRACK_IMPORTED" -> {
                                target=mapped(fact.aggregateId,"USER_TRACK_REF")
                                val library=mapped(fact.aggregateId,"LIBRARY_ENTRY")
                                createTrack(sql,lease,witness,AccountCreateTrack(event,target,library,body.string("title",4_096,0),body.string("artist",4_096,0),fact.occurredAtMs))
                                null
                            }
                            "TRACK_PREFERENCE_UPDATED" -> existingMap(body.string("track_id",1_024),"USER_TRACK_REF").let { track ->
                                val prior=sql.query("SELECT preference_id,excluded_from_taste FROM j_preference WHERE scope_id=? AND track_ref_id=?",lease.scopeId,track){AccountUuid(it.getText(0)) to (it.getLong(1)==1L)}.singleOrNull()
                                val localTarget=prior?.first?:mapped(fact.aggregateId,"USER_TRACK_PREFERENCE")
                                target=localTarget
                                AccountDomainCommand.Preference(localTarget,track,body.string("preference",20),prior?.second?:false) }
                            "TRACK_METADATA_UPDATED" -> {
                                if(fact.changeId !in manual.metadataIds){unsupported=true;null}else{
                                    val localTarget=existingMap(fact.aggregateId,"USER_TRACK_REF")
                                    target=localTarget
                                    AccountDomainCommand.PatchTrack(localTarget,buildJsonObject{put("title",body.getValue("title"));put("artist",body.getValue("artist"))})
                                }
                            }
                            "TRACK_REMOVED_FROM_COLLECTION","TRACK_RESTORED_TO_COLLECTION" -> {
                                val localTarget=existingMap(body.string("track_id",1_024),"LIBRARY_ENTRY")
                                target=localTarget
                                AccountDomainCommand.Library(localTarget,existingMap(body.string("track_id",1_024),"USER_TRACK_REF"),fact.kind=="TRACK_REMOVED_FROM_COLLECTION")
                            }
                            "PLAYLIST_CREATED" -> { val localTarget=mapped(fact.aggregateId,"PLAYLIST");target=localTarget;AccountDomainCommand.Playlist(localTarget,body.string("name",500),textOrNull(body,"description"),true) }
                            "PLAYLIST_RENAMED","PLAYLIST_DESCRIPTION_UPDATED" -> {
                                val localTarget=existingMap(fact.aggregateId,"PLAYLIST")
                                target=localTarget
                                val old=sql.query("SELECT name,description FROM j_playlist WHERE scope_id=? AND playlist_id=?",lease.scopeId,target){it.getText(0) to sql.nullable(it,1)}.single()
                                AccountDomainCommand.Playlist(localTarget,if(fact.kind=="PLAYLIST_RENAMED")body.string("name",500) else old.first,
                                    if(fact.kind=="PLAYLIST_DESCRIPTION_UPDATED")textOrNull(body,"description") else old.second,false)
                            }
                            "PLAYLIST_DELETED" -> {val localTarget=existingMap(fact.aggregateId,"PLAYLIST");target=localTarget;AccountDomainCommand.Delete(localTarget,"PLAYLIST")}
                            "PLAYLIST_ENTRY_APPENDED" -> {
                                val localTarget=mapped(fact.aggregateId,"PLAYLIST_ENTRY")
                                target=localTarget
                                val ordinal=body.string("ordinal",20).toLong().also{require(it>=0)}
                                AccountDomainCommand.Entry(localTarget,existingMap(body.string("playlist_id",1_024),"PLAYLIST"),
                                    existingMap(body.string("track_id",1_024),"USER_TRACK_REF"),ordinal.toString().padStart(20,'0'),false)
                            }
                            "PLAYLIST_ENTRY_REMOVED" -> {val localTarget=existingMap(fact.aggregateId,"PLAYLIST_ENTRY");target=localTarget;AccountDomainCommand.Delete(localTarget,"PLAYLIST_ENTRY")}
                            "PLAYLIST_ENTRY_MOVED" -> {
                                val entries=body.array("entries",2).map{it.jsonObject}.sortedBy{it.string("ordinal",20).toLong()}
                                require(entries.size==2 && entries.map{it.string("entry_id",1_024)}.distinct().size==2)
                                val first=entries[0];val second=entries[1]
                                val firstId=existingMap(first.string("entry_id",1_024),"PLAYLIST_ENTRY")
                                val secondId=existingMap(second.string("entry_id",1_024),"PLAYLIST_ENTRY")
                                target=firstId
                                sql.mutate("j_playlist_entry",listOf(lease.scopeId,secondId),"UPDATE j_playlist_entry SET position_key=? WHERE scope_id=? AND entry_id=?",
                                    second.string("ordinal",20).toLong().toString().padStart(20,'0'),lease.scopeId,secondId)
                                AccountDomainCommand.Entry(firstId,existingMap(body.string("playlist_id",1_024),"PLAYLIST"),
                                    existingMap(first.string("track_id",1_024),"USER_TRACK_REF"),first.string("ordinal",20).toLong().toString().padStart(20,'0'),true,
                                    serverOrdering=true,beforeEntryId=secondId)
                            }
                            "LISTENING_CHECKPOINT_SAVED" -> {
                                val origin=(body["event_origin"] as? JsonPrimitive)?.takeIf{it.isString}?.content
                                val context=(body["context"] as? JsonPrimitive)?.takeIf{it.isString}?.content
                                if(!body.bool("finalized") || origin !in setOf("ORGANIC","PLAYLIST","SEARCH","WAVE") ||
                                    context !in setOf("GENERAL","WORKOUT","CYCLING","WORK","SLEEP","PARTY")){unsupported=true;null}
                                else {
                                    require(body.string("event_id",1_024)==fact.aggregateId)
                                    val track=existingMap(body.string("track_id",1_024),"USER_TRACK_REF")
                                    val logical=mapped(fact.aggregateId,"LISTENING_EVENT");event=logical;target=logical
                                    val played=body.string("played_ms",20).toLong().also{require(it in 0..604_800_000)}
                                    val started=body.string("started_at_ms",20).toLong().also{require(it in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS)}
                                    val sessionTaste=body.string("session_taste_state",100);val listenTaste=body.string("listen_taste_state",100)
                                    require(sessionTaste in setOf("INCLUDED","EXCLUDED") && listenTaste in setOf("INCLUDED","EXCLUDED"))
                                    AccountDomainCommand.Listen(logical,buildJsonObject {
                                        put("interaction_type","LISTENING_EVENT_RECORDED");put("local_user_track_ref_id",track.value)
                                        put("server_user_track_ref_id",sql.text("SELECT server_track_ref_id FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",lease.scopeId,track)?.let(::JsonPrimitive)?:JsonNull)
                                        put("recording_id",JsonNull);put("played_ms",played);put("track_duration_ms",JsonNull);put("completion_ratio",JsonNull)
                                        put("event_origin",origin);put("context",context);put("explicit_feedback","NONE")
                                        put("excluded_from_taste",sessionTaste=="EXCLUDED"||listenTaste=="EXCLUDED");put("recommendation",JsonNull)
                                    },started)
                                }
                            }
                            // Current standalone checkpoints have no origin/context. Preserve those exact bytes.
                            else -> {unsupported=true;null}
                        }
                        if(command!=null) {target=command.localId;writer.mutate(AccountDomainChange(event,fact.occurredAtMs,command))}
                        if(target==null){
                            sql.mutate("j_source_outcome",listOf(selectionId,fact.changeId),"INSERT INTO j_source_outcome(selection_id,change_id,state,reason) VALUES(?,?,'UNAVAILABLE',?)",
                                selectionId,fact.changeId,when(fact.kind){"LISTENING_CHECKPOINT_SAVED"->"SOURCE_ORIGIN_CONTEXT_UNAVAILABLE";"TRACK_METADATA_UPDATED"->"MANUAL_PROVENANCE_UNAVAILABLE";else->"NO_SUPPORTED_V1_TRANSLATION"})
                            continue
                        }
                        val receipt=domainReceipt(sql,lease,event)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                        val lineage=receipt.eventId?.let{sql.text("SELECT lineage_id FROM j_event WHERE scope_id=? AND event_id=?",lease.scopeId,it)?.let(::AccountUuid)}
                        sql.mutate("j_materialization",listOf(source.sourceStoreId,fact.changeId),"""INSERT INTO j_materialization
                            (source_store_id,change_id,scope_id,account_id,aggregate_id,lineage_id,event_id,intent_id) VALUES(?,?,?,?,?,?,?,?)""",
                            source.sourceStoreId,fact.changeId,lease.scopeId,witness.preparation.scope.portableAccountId,target,lineage,receipt.eventId,receipt.localIntentId)
                        sql.mutate("j_materialization_event",listOf(source.sourceStoreId,fact.changeId,0),"INSERT INTO j_materialization_event(source_store_id,change_id,item_index,scope_id,event_id,intent_id) VALUES(?,?,0,?,?,?)",
                            source.sourceStoreId,fact.changeId,lease.scopeId,receipt.eventId,receipt.localIntentId)
                        if(command is AccountDomainCommand.Listen){
                            sql.mutate("j_source_listen_alias",listOf(source.sourceStoreId,fact.aggregateId),"""INSERT INTO j_source_listen_alias
                                (source_store_id,source_listen_id,scope_id,account_id,event_id,intent_id) VALUES(?,?,?,?,?,?)""",
                                source.sourceStoreId,fact.aggregateId,lease.scopeId,witness.preparation.scope.portableAccountId,receipt.eventId,receipt.localIntentId)
                            val account=witness.preparation.scope.portableAccountId
                            val originalId=runCatching{AccountUuid(fact.aggregateId)}.getOrNull()
                            if(account!=null && originalId!=null){
                                val recordId=sql.text("SELECT record_id FROM j_listening_alias WHERE account_id=? AND event_id=?",account,event)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                                val record=sql.query("SELECT record_id,kind,document FROM j_portable_record WHERE account_id=? AND record_id=?",account,recordId){AccountPortableMerge.decode(it.getText(0),it.getText(1),it.getBlob(2))}.single()
                                val aliases=(record.document.getValue("event_ids").jsonArray+JsonPrimitive(originalId.value)).distinct()
                                AccountPortableMerge.merge(sql,account,AccountPortableV1.decode(record.copy(document=JsonObject(record.document+("event_ids" to JsonArray(aliases)))).wire()))
                            }
                        }
                    }
                    sql.mutate("j_source_selection",listOf(selectionId),"UPDATE j_source_selection SET state=? WHERE selection_id=?",if(unsupported)"PARTIAL" else "MATERIALIZED",selectionId)
                }
                facts.mapNotNull{link(sql,source.sourceStoreId,it.changeId)}
            }
        }
    }
    private fun frozen(snapshot:AccountSelectedSourceSnapshot,consent:AccountSelectionConsent):ByteArray {
        require(snapshot.facts.size in 1..100 && snapshot.dependencies.size<=100)
        val forbidden=Regex("(^|_)(locator|bookmark|path|url|header|grant|cookie|secret|passport|private_key|keychain)(_|$)")
        fun safe(value:JsonElement,depth:Int=0):Unit {
            require(depth<=10)
            when(value){
                is JsonObject->value.forEach{(key,child)->require(!forbidden.containsMatchIn(key));safe(child,depth+1)}
                is JsonArray->value.forEach{safe(it,depth+1)}
                else->Unit
            }
        }
        snapshot.dependencies.forEach{(id,value)->require(id.length in 1..1_024);safe(value)}
        val document=buildJsonObject {
            put("source_store_id",snapshot.source.sourceStoreId.value);put("owner_ref",snapshot.source.ownerReference.value)
            put("selection_id",consent.selectionId.value);put("review_revision",consent.reviewRevision);put("cutoff_ms",consent.ownershipCutoffMs)
            put("binding_commit",consent.bindingCommitId.value);put("authority_revision",consent.authorityRevision)
            put("dependencies",JsonArray(snapshot.dependencies.toSortedMap().map{(id,value)->buildJsonObject{put("source_id",id);put("snapshot",value)}}))
            put("facts",JsonArray(snapshot.facts.sortedBy{it.changeId}.map { f ->
                require(f.version==1L && f.canonicalBytes.size<=DesktopAccountLimits.PAYLOAD_BYTES && f.changeId.length in 1..1_024 && f.aggregateId.length in 1..1_024 &&
                    f.occurredAtMs in 0..consent.ownershipCutoffMs)
                val d=AccountSyncWireV1.document(f.canonicalBytes)
                safe(d)
                require(d.keys==setOf("version","mutation","snapshot") && d.number("version",1,1)==f.version && d.string("mutation",100)==f.kind)
                buildJsonObject {put("change_id",f.changeId);put("aggregate_id",f.aggregateId);put("kind",f.kind);put("version",f.version)
                    put("occurred_ms",f.occurredAtMs);put("fact",d)}
            }))
        }
        val bytes=AccountPortableV1.canonical(document)
        require(bytes.size<=DesktopAccountLimits.PAGE_BYTES);AccountSyncWireV1.document(bytes)
        return bytes
    }
    private data class ManualProofMatches(val metadataIds: Set<String>, val supportIds: Set<String>)

    /** Parse each bounded original once, before SQL; support-only rows are not mutations/events. */
    private fun manualProofMatches(facts: List<AccountSelectedFact>): ManualProofMatches {
        require(facts.map { it.changeId }.distinct().size == facts.size)
        val proofs = facts.filter { it.kind == "TRACK_TEXT_PROVENANCE_UPDATED" }.map { fact ->
            fact to AccountSyncWireV1.document(fact.canonicalBytes).obj("snapshot")
        }
        val metadataIds = LinkedHashSet<String>()
        val supportIds = LinkedHashSet<String>()
        for (metadata in facts.filter { it.kind == "TRACK_METADATA_UPDATED" }) {
            val body = AccountSyncWireV1.document(metadata.canonicalBytes).obj("snapshot")
            if (body.string("track_id", 1_024) != metadata.aggregateId) continue
            val candidates = proofs.filter { (_, proof) ->
                proof.string("metadata_change_id", 1_024) == metadata.changeId &&
                    proof.string("operation", 100) == "USER" &&
                    proof.string("track_id", 1_024) == metadata.aggregateId
            }
            if (candidates.size != 1) continue
            val (support, proof) = candidates.single()
            if (support.aggregateId != metadata.aggregateId ||
                proof.string("after_title", 4_096, 0) != body.string("title", 4_096, 0) ||
                proof.string("after_artist", 4_096, 0) != body.string("artist", 4_096, 0) ||
                proof.string("sha256", 64) != body.string("sha256", 64) ||
                proof.string("byte_size", 20) != body.string("byte_size", 20)) continue
            metadataIds.add(metadata.changeId)
            supportIds.add(support.changeId)
        }
        return ManualProofMatches(metadataIds, supportIds)
    }

    private fun priority(kind:String):Int=when(kind){"TRACK_IMPORTED"->0;"PLAYLIST_CREATED"->1;"PLAYLIST_ENTRY_APPENDED"->2;else->3}
    private fun dependency(sql:AccountPageSql,lease:AccountLease,w:AccountProtectedWitness,s:AccountSelectedSourceSnapshot,selection:AccountUuid,
        sourceId:String,type:String,time:Long,writer:AccountDomainMutationWriter):AccountUuid {
        sql.query("SELECT scope_id,target_id FROM j_identity_map WHERE source_store_id=? AND source_id=? AND aggregate_type=?",s.source.sourceStoreId,sourceId,type){it.getText(0) to it.getText(1)}.singleOrNull()?.let{
            if(it.first!=lease.scopeId.value)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
            return AccountUuid(it.second)
        }
        if(type=="LIBRARY_ENTRY") {
            dependency(sql,lease,w,s,selection,sourceId,"USER_TRACK_REF",time,writer)
            return sql.text("SELECT target_id FROM j_identity_map WHERE source_store_id=? AND source_id=? AND aggregate_type='LIBRARY_ENTRY' AND scope_id=?",
                s.source.sourceStoreId,sourceId,lease.scopeId)?.let(::AccountUuid)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
        val p=s.dependencies[sourceId]?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        val target=map(sql,s.source.sourceStoreId,sourceId,type,lease.scopeId);val operation=AccountUuid.random()
        when(type){
            "USER_TRACK_REF"->{
                require(p.string("track_id",1_024)==sourceId)
                val library=map(sql,s.source.sourceStoreId,sourceId,"LIBRARY_ENTRY",lease.scopeId)
                createTrack(sql,lease,w,AccountCreateTrack(operation,target,library,p.string("title",4_096,0),p.string("artist",4_096,0),time))
            }
            "PLAYLIST"->{require(p.string("playlist_id",1_024)==sourceId);writer.mutate(AccountDomainChange(operation,time,
                AccountDomainCommand.Playlist(target,p.string("name",500),textOrNull(p,"description"),true)))}
            "PLAYLIST_ENTRY"->{
                require(p.string("entry_id",1_024)==sourceId)
                val playlist=dependency(sql,lease,w,s,selection,p.string("playlist_id",1_024),"PLAYLIST",time,writer)
                val track=dependency(sql,lease,w,s,selection,p.string("track_id",1_024),"USER_TRACK_REF",time,writer)
                val ordinal=p.string("ordinal",20).toLong().also{require(it>=0)}
                writer.mutate(AccountDomainChange(operation,time,AccountDomainCommand.Entry(target,playlist,track,ordinal.toString().padStart(20,'0'),false)))
            }
            else->fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        }
        val receipt=domainReceipt(sql,lease,operation)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        sql.mutate("j_materialization_dependency",listOf(selection,sourceId,type),"""INSERT INTO j_materialization_dependency
            (selection_id,source_id,aggregate_type,scope_id,target_id,event_id,intent_id) VALUES(?,?,?,?,?,?,?)""",selection,sourceId,type,lease.scopeId,target,receipt.eventId,receipt.localIntentId)
        return target
    }
    private fun textOrNull(d:JsonObject,key:String):String?=if(d[key]==JsonNull)null else d.string(key,2_000,0)
    private fun map(sql:AccountPageSql,source:AccountUuid,id:String,type:String,scope:AccountUuid):AccountUuid {
        val old=sql.query("SELECT scope_id,target_id FROM j_identity_map WHERE source_store_id=? AND source_id=? AND aggregate_type=?",source,id,type){it.getText(0) to it.getText(1)}.singleOrNull()
        if(old!=null) {if(old.first!=scope.value)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT);return AccountUuid(old.second)}
        val target=AccountUuid.random()
        sql.mutate("j_identity_map",listOf(source,id,type),"INSERT INTO j_identity_map(source_store_id,source_id,aggregate_type,scope_id,target_id) VALUES(?,?,?,?,?)",source,id,type,scope,target)
        return target
    }
    private fun link(sql:AccountPageSql,source:AccountUuid,change:String):AccountMaterializationLink?=sql.query("""SELECT scope_id,account_id,aggregate_id,lineage_id,event_id,intent_id
        FROM j_materialization WHERE source_store_id=? AND change_id=?""",source,change){AccountMaterializationLink(change,AccountUuid(it.getText(0)),
        sql.nullable(it,1)?.let(::AccountUuid),AccountUuid(it.getText(2)),sql.nullable(it,3)?.let(::AccountUuid),sql.nullable(it,4)?.let(::AccountUuid),sql.nullable(it,5)?.let(::AccountUuid))}.singleOrNull()
    private fun createTrack(sql:AccountPageSql,lease:AccountLease,witness:AccountProtectedWitness,c:AccountCreateTrack):Unit = AccountAccountTrackWriter.create(sql,lease,witness,c)
}

internal object AccountAccountTrackWriter {
    fun create(sql:AccountPageSql,lease:AccountLease,witness:AccountProtectedWitness,c:AccountCreateTrack):Unit {
        val plan=witness.preparation;val scope=lease.scopeId
        val remote = plan.scope.remote
        val binding = plan.binding
        val existing=sql.query("SELECT title,artist,operation_id FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",scope,c.trackRefId){Triple(it.getText(0),it.getText(1),AccountUuid(it.getText(2)))}.singleOrNull()
        if(existing!=null){
            if(existing.first!=c.title||existing.second!=c.artist)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            val receipt=domainReceipt(sql,lease,existing.third)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            sql.mutate("j_domain_receipt",listOf(scope,c.operationId),"INSERT INTO j_domain_receipt(scope_id,operation_id,aggregate_id,event_id,intent_id) VALUES(?,?,?,?,?)",
                scope,c.operationId,c.trackRefId,receipt.eventId,receipt.localIntentId)
            return
        }
        accountEventCapacity(sql,AccountEventV1.trackPayload(c).size.toLong()+4_096)
        val lineage=plan.binding?.let{sql.text("SELECT lineage_id FROM j_profile_alias WHERE scope_id=? AND profile_id=?",scope,lease.profileId)?.let(::AccountUuid)}
        val event=if(lineage!=null && remote!=null && binding!=null) {
            val sequence=sql.number("SELECT next_sequence FROM j_lineage WHERE scope_id=? AND lineage_id=?",scope,lineage)
            val e=AccountEventV1.createTrack(c,remote,binding,lease.profileId,sequence)
            sql.mutate("j_lineage",listOf(scope,lineage),"UPDATE j_lineage SET next_sequence=? WHERE scope_id=? AND lineage_id=?",next(sequence),scope,lineage)
            sql.mutate("j_event",listOf(scope,e.eventId),"""INSERT INTO j_event(scope_id,event_id,lineage_id,profile_id,user_id,device_id,sequence,idempotency_key,
                event_type,aggregate_type,aggregate_local_id,canonical_bytes,request_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",scope,e.eventId,lineage,e.profileId,e.userId,e.deviceId,
                e.sequence,e.idempotencyKey,e.eventType,e.aggregateType,e.aggregateLocalId,e.canonicalBytes,e.requestHash)
            sql.mutate("j_event_delivery",listOf(scope,e.eventId),"INSERT INTO j_event_delivery(scope_id,event_id,state,attempt,next_retry_ms) VALUES(?,?,'PENDING',0,0)",scope,e.eventId)
            e.eventId
        } else {
            if(plan.binding!=null)fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            sql.mutate("j_local_intent",listOf(scope,c.operationId),"""INSERT INTO j_local_intent(scope_id,change_id,profile_id,aggregate_local_id,event_type,aggregate_type,
                schema_version,occurred_at_ms,canonical_payload,state) VALUES(?,?,?,?,'USER_TRACK_REF_CREATED','USER_TRACK_REF',1,?,?,'LOCAL')""",
                scope,c.operationId,lease.profileId,c.trackRefId,c.occurredAtMs,AccountEventV1.trackPayload(c));null
        }
        if(event==null)retainAccountIntentOrder(sql,scope,c.operationId)
        sql.mutate("j_track_ref",listOf(scope,c.trackRefId),"INSERT INTO j_track_ref(scope_id,track_ref_id,operation_id,profile_id,title,artist,occurred_at_ms) VALUES(?,?,?,?,?,?,?)",
            scope,c.trackRefId,c.operationId,lease.profileId,c.title,c.artist,c.occurredAtMs)
        sql.mutate("j_library_entry",listOf(scope,c.libraryEntryId),"INSERT INTO j_library_entry(scope_id,entry_id,track_ref_id) VALUES(?,?,?)",scope,c.libraryEntryId,c.trackRefId)
        sql.mutate("j_domain_receipt",listOf(scope,c.operationId),"INSERT INTO j_domain_receipt(scope_id,operation_id,aggregate_id,event_id,intent_id) VALUES(?,?,?,?,?)",
            scope,c.operationId,c.trackRefId,event,if(event==null)c.operationId else null)
    }
}
