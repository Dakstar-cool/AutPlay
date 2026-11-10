package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import java.security.MessageDigest
import java.text.Normalizer
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.util.Locale
import kotlinx.serialization.json.*

internal object AccountCatalogProjectionWriter {
    val families:Set<String> = setOf("ARTIST","ARTIST_CREDIT","RECORDING_ARTIST_CREDIT","RELEASE_ARTIST_CREDIT")
    private val events=mapOf("ARTIST" to "CATALOG_ARTIST_UPSERTED","ARTIST_CREDIT" to "CATALOG_ARTIST_CREDIT_UPSERTED",
        "RECORDING_ARTIST_CREDIT" to "CATALOG_RECORDING_CREDIT_LINK_UPSERTED","RELEASE_ARTIST_CREDIT" to "CATALOG_RELEASE_CREDIT_LINK_UPSERTED")
    fun apply(sql:AccountPageSql,scope:AccountUuid,snapshot:AccountUuid?,change:AccountWireChange):Unit {
        if(change.schema!=1L || change.operation!="UPSERT" || change.version==null || change.eventType?.let{it!=events[change.type]}==true) throw AccountPageDeferred("UNSUPPORTED_CATALOG_OPERATION")
        val p=change.payload;val id=change.server;val version=change.version
        val table=when(change.type){"ARTIST"->"j_catalog_artist";"ARTIST_CREDIT"->"j_catalog_credit";else->"j_catalog_subject_credit"}
        val key=when(change.type){"ARTIST"->"artist_id";"ARTIST_CREDIT"->"credit_id";else->"subject_id"}
        val old=sql.query("SELECT version FROM $table WHERE scope_id=? AND $key=?"+(if(change.type.endsWith("ARTIST_CREDIT")&&change.type!="ARTIST_CREDIT")" AND subject_type=?" else ""),
            *if(table=="j_catalog_subject_credit")arrayOf(scope,id,change.type) else arrayOf(scope,id)){it.getLong(0)}.singleOrNull()
        if(old!=null && version<old)return
        val deleted=if(p["deleted_at"]==JsonNull)null else p.time("deleted_at")
        when(change.type) {
            "ARTIST" -> {
                require(p.id("artist_id")==id)
                sql.mutate(table,listOf(scope,id),"""INSERT INTO j_catalog_artist(scope_id,artist_id,version,name,sort_name,artist_type,disambiguation,country_code,identity_status,deleted_at,snapshot_id,source_sequence)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,artist_id) DO UPDATE SET version=excluded.version,name=excluded.name,sort_name=excluded.sort_name,
                    artist_type=excluded.artist_type,disambiguation=excluded.disambiguation,country_code=excluded.country_code,identity_status=excluded.identity_status,
                    deleted_at=excluded.deleted_at,snapshot_id=excluded.snapshot_id,source_sequence=excluded.source_sequence""",scope,id,version,p.string("name",1_000),
                    nullable(p,"sort_name",1_000),nullable(p,"artist_type",100),nullable(p,"disambiguation",2_000),nullable(p,"country_code",2),nullable(p,"identity_status",100),deleted,snapshot,change.sequence)
            }
            "ARTIST_CREDIT" -> {
                require(p.id("artist_credit_id")==id)
                val names=p.array("names",1_000).map{it.jsonObject};val positions=names.map{it.number("position",0).toInt()}
                require(positions.distinct().size==positions.size)
                sql.mutate(table,listOf(scope,id),"""INSERT INTO j_catalog_credit(scope_id,credit_id,version,display_name,member_count,deleted_at,snapshot_id,source_sequence)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,credit_id) DO UPDATE SET version=excluded.version,display_name=excluded.display_name,
                    member_count=excluded.member_count,deleted_at=excluded.deleted_at,snapshot_id=excluded.snapshot_id,source_sequence=excluded.source_sequence""",
                    scope,id,version,p.string("display_name",2_000),names.size,deleted,snapshot,change.sequence)
                for(n in names) {
                    val position=n.number("position",0);val values=listOf(n.id("artist_id").value,n.string("credited_name",1_000),n.string("join_phrase",1_000,0),n.string("role",100))
                    val prior=sql.query("SELECT artist_id,credited_name,join_phrase,role FROM j_catalog_credit_member WHERE scope_id=? AND credit_id=? AND version=? AND position=?",scope,id,version,position){(0..3).map(it::getText)}.singleOrNull()
                    if(prior!=null && prior!=values)throw AccountPageDeferred("CATALOG_CREDIT_VERSION_CONFLICT")
                    if(prior==null)sql.mutate("j_catalog_credit_member",listOf(scope,id,version,position),"INSERT INTO j_catalog_credit_member(scope_id,credit_id,version,position,artist_id,credited_name,join_phrase,role) VALUES(?,?,?,?,?,?,?,?)",scope,id,version,position,*values.toTypedArray())
                }
            }
            else -> {
                val subjectKey=if(change.type=="RECORDING_ARTIST_CREDIT")"recording_id" else "release_id"
                require(p.id(subjectKey)==id)
                val credit=p.id("artist_credit_id");val ownerScope=CatalogOwnerScope(p.string("owner_scope_id",64)).value
                val page=p.number("owner_recording_page",0);val count=p.number("owner_recording_page_count",1)
                require(page<count)
                val recordings=p.array("owner_recording_ids",100).map{AccountUuid(it.jsonPrimitive.content)}
                require(recordings.isNotEmpty() && recordings.distinct().size==recordings.size && recordings==recordings.sortedBy{it.value} && (page==count-1 || recordings.size==100))
                val exact=AccountPortableV1.canonical(p)
                val prior=sql.query("""SELECT exact_payload FROM j_catalog_owner_page WHERE scope_id=? AND subject_type=? AND subject_id=?
                    AND credit_id=? AND version=? AND owner_scope_id=? AND page=?""",scope,change.type,id,credit,version,ownerScope,page){it.getBlob(0)}.singleOrNull()
                if(prior!=null && !prior.contentEquals(exact))throw AccountPageDeferred("CATALOG_OWNER_PAGE_CONFLICT")
                if(prior==null) {
                    sql.mutate("j_catalog_owner_page",listOf(scope,change.type,id,credit,version,ownerScope,page),"""INSERT INTO j_catalog_owner_page
                        (scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page,page_count,record_count,exact_payload) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        scope,change.type,id,credit,version,ownerScope,page,count,recordings.size,exact)
                    for(recording in recordings)sql.mutate("j_catalog_owner_recording",listOf(scope,change.type,id,credit,version,ownerScope,recording),"""INSERT INTO j_catalog_owner_recording
                        (scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,recording_id,page) VALUES(?,?,?,?,?,?,?,?)""",scope,change.type,id,credit,version,ownerScope,recording,page)
                }
                sql.mutate(table,listOf(scope,change.type,id),"""INSERT INTO j_catalog_subject_credit(scope_id,subject_type,subject_id,credit_id,version,owner_scope_id,page_count,deleted_at,snapshot_id,source_sequence)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,subject_type,subject_id) DO UPDATE SET credit_id=excluded.credit_id,version=excluded.version,
                    owner_scope_id=excluded.owner_scope_id,page_count=excluded.page_count,deleted_at=excluded.deleted_at,snapshot_id=excluded.snapshot_id,source_sequence=excluded.source_sequence""",
                    scope,change.type,id,credit,version,ownerScope,count,deleted,snapshot,change.sequence)
            }
        }
    }
    fun metadata(sql:AccountPageSql,scope:AccountUuid,track:AccountUuid,p:JsonObject):Unit {
        val revision=p.number("revision",1);val state=p.string("state",100);p.obj("fields");p.obj("provenance");p.time("updated_at")
        val prior=sql.query("SELECT revision,exact_document FROM j_track_metadata WHERE scope_id=? AND track_id=?",scope,track){it.getLong(0) to it.getBlob(1)}.singleOrNull()
        val exact=AccountPortableV1.canonical(p)
        if(prior!=null && revision<prior.first)return
        if(prior!=null && revision==prior.first){if(!prior.second.contentEquals(exact))throw AccountPageDeferred("METADATA_REVISION_CONFLICT");return}
        sql.mutate("j_track_metadata",listOf(scope,track),"""INSERT INTO j_track_metadata(scope_id,track_id,revision,state,exact_document) VALUES(?,?,?,?,?)
            ON CONFLICT(scope_id,track_id) DO UPDATE SET revision=excluded.revision,state=excluded.state,exact_document=excluded.exact_document""",scope,track,revision,state,exact)
        val album=decodeAlbum(p)
        sql.mutate("j_metadata_album",listOf(scope,track),"""INSERT INTO j_metadata_album(scope_id,track_id,metadata_revision,evidence_key,provider,release_id,title,album_artist,release_date,disc_number,track_number,state)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,track_id) DO UPDATE SET metadata_revision=excluded.metadata_revision,evidence_key=excluded.evidence_key,
            provider=excluded.provider,release_id=excluded.release_id,title=excluded.title,album_artist=excluded.album_artist,release_date=excluded.release_date,
            disc_number=excluded.disc_number,track_number=excluded.track_number,state=excluded.state""",scope,track,revision,album?.key,album?.provider,album?.releaseId,album?.title,
            album?.albumArtist,album?.releaseDate,album?.discNumber,album?.trackNumber,if(album==null)"UNAVAILABLE" else "VALIDATED")
    }
    fun attribution(sql:AccountPageSql,scope:AccountUuid,source:AccountUuid,type:String,id:AccountUuid,p:JsonObject,observationKind:String="SERVER"):Unit {
        AccountDomainWire.attribution(p)
        val exact=AccountPortableV1.canonical(p)
        require(observationKind in setOf("LOCAL","SERVER"))
        val old=sql.query("SELECT exact_document FROM j_attribution WHERE scope_id=? AND source_id=? AND aggregate_type=? AND aggregate_id=? AND observation_kind=?",scope,source,type,id,observationKind){it.getBlob(0)}.singleOrNull()
        if(old!=null){if(!old.contentEquals(exact))throw AccountPageDeferred("ATTRIBUTION_CONFLICT");return}
        sql.mutate("j_attribution",listOf(scope,source,type,id,observationKind),"INSERT INTO j_attribution(scope_id,source_id,aggregate_type,aggregate_id,exact_document,observation_kind) VALUES(?,?,?,?,?,?)",scope,source,type,id,exact,observationKind)
    }
    private fun nullable(p:JsonObject,key:String,max:Int):String?=if(p[key]==JsonNull)null else p.string(key,max,0)
}

internal class AccountCatalogRepository(private val owner:AccountRepositoryOwner):DesktopAccountCatalog {
    override suspend fun requireSearchRevision(lease:AccountLease,revision:Long):Unit = owner.read(lease) { sql, _ ->
        if (sql.number("SELECT total_changes()")!=revision) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }
    override suspend fun captureSearch(lease:AccountLease,kind:AccountCatalogSearchKind):AccountCatalogSearchSnapshot {
        val caller = kotlinx.coroutines.currentCoroutineContext()
        return owner.read(lease) { sql, _ -> sql.readSnapshot {
            val deadline = System.nanoTime() + 10_000_000_000L
            fun admit() {
                caller[kotlinx.coroutines.Job]?.let { if (!it.isActive) throw kotlinx.coroutines.CancellationException() }
                if (System.nanoTime() >= deadline) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            }
            // Conservative exact fence: any connection-local J mutation invalidates continuation.
            val revision = sql.number("SELECT total_changes()")
            var bytes = 0L
            val documents = linkedMapOf<String,AccountCatalogSearchDocument>()
            fun append(document:AccountCatalogSearchDocument) {
                admit()
                val a = document.artist; val b = document.album
                val text = listOfNotNull(document.key,a?.name,a?.sortName,a?.disambiguation,a?.rawType,
                    a?.countryCode,a?.rawIdentityStatus,b?.provider,b?.releaseId,b?.title,b?.albumArtist,b?.releaseDate)
                val cost = 256L + text.sumOf { it.length.toLong() * 4 }
                if (cost > 64L*1024*1024-bytes) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                if (documents.put(document.key,document)!=null) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                bytes += cost
            }
            var cursor:AccountUuid? = null
            if (kind==AccountCatalogSearchKind.ARTIST) {
                do {
                    admit()
                    val page = artistPage(sql,lease.scopeId,cursor,100)
                    for (artist in page.items) append(AccountCatalogSearchDocument(artist.artistId.value,artist=artist))
                    if (page.exhausted) break
                    val next = page.scannedThrough ?: fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                    if (next==cursor) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                    cursor = next
                } while (true)
            } else {
                do {
                    admit()
                    val rows = AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.TRACK,cursor,100)
                    for (row in rows) {
                        admit()
                        if (row.header.removed || row.header.hasConflict || row.header.rawProjectionState?.let { it!="COMPLETE" }==true) continue
                        val album = sql.query("SELECT exact_document FROM j_track_metadata WHERE scope_id=? AND track_id=?",
                            lease.scopeId,row.header.localId) { decodeAlbum(AccountSyncWireV1.document(it.getBlob(0))) }.singleOrNull() ?: continue
                        // Edition-level fields must agree across the entire capture. Position is per track.
                        val grouped = album.copy(discNumber=null,trackNumber=null)
                        val old = documents[album.key]
                        if (old==null) append(AccountCatalogSearchDocument(album.key,album=grouped,albumAnchor=row.header.localId))
                        else if (old.album!=grouped) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                        // Ascending real track IDs make the anchor deterministic across every source page.
                    }
                    if (rows.size<100) break
                    val next = rows.last().header.localId
                    if (next==cursor) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                    cursor = next
                } while (true)
            }
            admit()
            AccountCatalogSearchSnapshot(lease,revision,kind,documents.values.sortedBy { it.key })
        } }
    }
    override suspend fun readPlaylist(lease:AccountLease,playlistId:AccountUuid):AccountLocalDomainRow? = owner.read(lease){sql,_->
        AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.PLAYLIST,null,1,playlistId).singleOrNull()
    }
    override suspend fun readTrackContext(lease:AccountLease,trackId:AccountUuid):AccountCatalogTrackContext? = owner.read(lease){sql,_->
        val track=AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.TRACK,null,1,trackId).singleOrNull()
            ?:return@read null
        val libraryIds=sql.query("""SELECT entry_id FROM (SELECT scope_id,entry_id,track_ref_id FROM j_library_entry
            UNION ALL SELECT scope_id,entry_id,track_ref_id FROM j_library_relation)
            WHERE scope_id=? AND track_ref_id=? ORDER BY entry_id LIMIT 101""",lease.scopeId,trackId){AccountUuid(it.getText(0))}
        AccountCatalogTrackContext(track,libraryIds.take(100).flatMap{
            AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.LIBRARY,null,1,it)
        },libraryIds.size>100)
    }
    override suspend fun readArtists(lease:AccountLease,after:AccountUuid?,limit:Int):List<AccountCatalogArtist> {
        require(limit in 1..100)
        return owner.read(lease){sql,_->
            val result=mutableListOf<AccountCatalogArtist>();var cursor=after
            repeat(10){
                val page=artistPage(sql,lease.scopeId,cursor,limit-result.size);result.addAll(page.items)
                if(page.exhausted || result.size==limit)return@read result
                cursor=page.scannedThrough
            }
            fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
    }
    override suspend fun readAppearances(lease:AccountLease,artistId:AccountUuid,afterSubject:AccountUuid?,limit:Int):List<AccountCatalogAppearance> {
        require(limit in 1..100);return owner.read(lease){sql,_->
            val result=mutableListOf<AccountCatalogAppearance>();var cursor=afterSubject?.let{AccountCatalogSubjectCursor(it,"~")}
            repeat(10){
                val page=appearancePage(sql,lease.scopeId,artistId,cursor,limit-result.size);result.addAll(page.items)
                if(page.exhausted)return@read result
                cursor=page.scannedThrough
                if(result.size==limit){
                    // The old UUID-only cursor cannot represent a Recording/Release ID tie.
                    val last=result.last();val next=appearancePage(sql,lease.scopeId,artistId,cursor,1)
                    if(next.scannedThrough?.subjectId==last.subjectId)fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
                    return@read result
                }
            }
            fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
    }
    override suspend fun readArtistPage(lease:AccountLease,after:AccountUuid?,limit:Int):AccountCatalogArtistPage {
        require(limit in 1..100);return owner.read(lease){sql,_->artistPage(sql,lease.scopeId,after,limit)}
    }
    override suspend fun readAppearancePage(lease:AccountLease,artistId:AccountUuid,after:AccountCatalogSubjectCursor?,limit:Int):AccountCatalogAppearancePage {
        require(limit in 1..100 && (after==null || after.subjectType in AccountCatalogProjectionWriter.families))
        return owner.read(lease){sql,_->appearancePage(sql,lease.scopeId,artistId,after,limit)}
    }
    override suspend fun readArtist(lease:AccountLease,artistId:AccountUuid):AccountCatalogArtist? = owner.read(lease){sql,_->
        val artist=sql.query("""SELECT name,sort_name,artist_type,disambiguation,country_code,identity_status FROM j_catalog_artist a
            WHERE scope_id=? AND artist_id=? AND deleted_at IS NULL
            AND NOT EXISTS(SELECT 1 FROM j_snapshot_head h WHERE h.scope_id=a.scope_id AND (a.snapshot_id IS NULL OR a.snapshot_id<>h.snapshot_id) AND coalesce(a.source_sequence,0)<=h.high_water)""",
            lease.scopeId,artistId){AccountCatalogArtist(artistId,it.getText(0),sql.nullable(it,1),sql.nullable(it,2),sql.nullable(it,3),sql.nullable(it,4),sql.nullable(it,5))}.singleOrNull()
            ?:return@read null
        var cursor:AccountCatalogSubjectCursor?=null
        repeat(10){
            val page=appearancePage(sql,lease.scopeId,artistId,cursor,100)
            if(page.items.isNotEmpty())return@read artist
            if(page.exhausted)return@read null
            cursor=page.scannedThrough?:fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
        fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
    }
    override suspend fun readSubject(lease:AccountLease,subject:AccountCatalogSubjectCursor):AccountCatalogAppearance? {
        require(subject.subjectType in setOf("RECORDING_ARTIST_CREDIT","RELEASE_ARTIST_CREDIT"))
        return owner.read(lease){sql,_->appearancePage(sql,lease.scopeId,null,null,1,subject.subjectType,subject.subjectId).items.singleOrNull()}
    }
    override suspend fun readSubjectPage(lease:AccountLease,subjectType:String,after:AccountCatalogSubjectCursor?,limit:Int):AccountCatalogAppearancePage {
        require(limit in 1..100 && subjectType in setOf("RECORDING_ARTIST_CREDIT","RELEASE_ARTIST_CREDIT") &&
            (after==null || after.subjectType==subjectType))
        return owner.read(lease){sql,_->appearancePage(sql,lease.scopeId,null,after,limit,subjectType)}
    }
    private fun artistPage(sql:AccountPageSql,scope:AccountUuid,after:AccountUuid?,limit:Int):AccountCatalogArtistPage {
        val rows=sql.query("""SELECT artist_id,name,sort_name,artist_type,disambiguation,country_code,identity_status FROM j_catalog_artist a
            WHERE scope_id=? AND artist_id>? AND deleted_at IS NULL
            AND NOT EXISTS(SELECT 1 FROM j_snapshot_head h WHERE h.scope_id=a.scope_id AND (a.snapshot_id IS NULL OR a.snapshot_id<>h.snapshot_id) AND coalesce(a.source_sequence,0)<=h.high_water)
            ORDER BY artist_id LIMIT ?""",scope,after?.value?:"",limit){
            AccountCatalogArtist(AccountUuid(it.getText(0)),it.getText(1),sql.nullable(it,2),sql.nullable(it,3),sql.nullable(it,4),sql.nullable(it,5),sql.nullable(it,6))}
        val visible=rows.filter{artist->
            var cursor:AccountCatalogSubjectCursor?=null;var found=false;var complete=false
            repeat(10){if(!found && !complete){val page=appearancePage(sql,scope,artist.artistId,cursor,100)
                found=page.items.isNotEmpty();complete=page.exhausted;cursor=page.scannedThrough}}
            if(!found && !complete)fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            found
        }
        return AccountCatalogArtistPage(visible,rows.lastOrNull()?.artistId?:after,rows.size<limit)
    }
    private fun appearancePage(sql:AccountPageSql,scope:AccountUuid,artist:AccountUuid?,after:AccountCatalogSubjectCursor?,limit:Int,
        subjectType:String?=null,subjectId:AccountUuid?=null):AccountCatalogAppearancePage {
        val subjects=sql.query("""SELECT s.subject_type,s.subject_id,s.credit_id,s.version,s.owner_scope_id,s.page_count,c.display_name,c.version,c.member_count
            FROM j_catalog_subject_credit s JOIN j_catalog_credit c ON c.scope_id=s.scope_id AND c.credit_id=s.credit_id
            WHERE s.scope_id=? AND (s.subject_id>? OR (s.subject_id=? AND s.subject_type>?)) AND s.deleted_at IS NULL AND c.deleted_at IS NULL
            AND (? IS NULL OR EXISTS(SELECT 1 FROM j_catalog_credit_member m WHERE m.scope_id=c.scope_id AND m.credit_id=c.credit_id AND m.version=c.version AND m.artist_id=?))
            AND (? IS NULL OR s.subject_type=?) AND (? IS NULL OR s.subject_id=?)
            AND NOT EXISTS(SELECT 1 FROM j_snapshot_head h WHERE h.scope_id=s.scope_id AND (s.snapshot_id IS NULL OR s.snapshot_id<>h.snapshot_id) AND coalesce(s.source_sequence,0)<=h.high_water)
            AND NOT EXISTS(SELECT 1 FROM j_snapshot_head h WHERE h.scope_id=c.scope_id AND (c.snapshot_id IS NULL OR c.snapshot_id<>h.snapshot_id) AND coalesce(c.source_sequence,0)<=h.high_water)
            ORDER BY s.subject_id,s.subject_type LIMIT ?""",scope,after?.subjectId?.value?:"",after?.subjectId?.value?:"",after?.subjectType?:"",artist,artist,
            subjectType,subjectType,subjectId,subjectId,limit){(0..8).map{col->it.getText(col)}}
        val visible=subjects.mapNotNull{s->
            val args=arrayOf<Any?>(scope,s[0],s[1],s[2],s[3].toLong(),s[4])
            val pages=sql.number("SELECT count(*) FROM j_catalog_owner_page WHERE scope_id=? AND subject_type=? AND subject_id=? AND credit_id=? AND version=? AND owner_scope_id=? AND page_count=?",*args,s[5].toLong())
            if(pages!=s[5].toLong())return@mapNotNull null
            val digest=MessageDigest.getInstance("SHA-256");var afterId="";var seen=false;var reachable=false
            while(true){
                val ids=sql.query("""SELECT o.recording_id,EXISTS(SELECT 1 FROM j_track_detail d JOIN j_account_projection_visibility p ON p.scope_id=d.scope_id AND p.local_id=d.track_ref_id
                    AND p.aggregate_type='USER_TRACK_REF' WHERE d.scope_id=o.scope_id AND d.recording_id=o.recording_id AND p.effective_deleted=0
                    AND p.state='COMPLETE' AND (p.snapshot_confirmed IS NULL OR p.snapshot_confirmed=1)) FROM j_catalog_owner_recording o
                    WHERE o.scope_id=? AND o.subject_type=? AND o.subject_id=? AND o.credit_id=? AND o.version=? AND o.owner_scope_id=? AND o.recording_id>? ORDER BY o.recording_id LIMIT 100""",*args,afterId){it.getText(0) to (it.getLong(1)==1L)}
                if(ids.isEmpty())break
                for((id,owned) in ids){if(seen)digest.update(0.toByte());digest.update(id.toByteArray(Charsets.UTF_8));seen=true;reachable=reachable||owned;afterId=id}
            }
            if(!reachable || digest.digest().joinToString(""){(it.toInt()and 255).toString(16).padStart(2,'0')}!=s[4])return@mapNotNull null
            val members=sql.query("""SELECT m.artist_id,m.position,m.credited_name,m.join_phrase,m.role FROM j_catalog_credit_member m
                JOIN j_catalog_artist a ON a.scope_id=m.scope_id AND a.artist_id=m.artist_id WHERE m.scope_id=? AND m.credit_id=? AND m.version=?
                AND a.deleted_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM j_snapshot_head h WHERE h.scope_id=a.scope_id AND (a.snapshot_id IS NULL OR a.snapshot_id<>h.snapshot_id) AND coalesce(a.source_sequence,0)<=h.high_water)
                ORDER BY m.position LIMIT 1000""",scope,s[2],s[7].toLong()){
                AccountCatalogCreditMember(AccountUuid(it.getText(0)),it.getLong(1).toInt(),it.getText(2),it.getText(3),it.getText(4))}
            if(members.size!=s[8].toInt())null else AccountCatalogAppearance(s[0],AccountUuid(s[1]),AccountUuid(s[2]),s[6],members)
        }
        return AccountCatalogAppearancePage(visible,subjects.lastOrNull()?.let{AccountCatalogSubjectCursor(AccountUuid(it[1]),it[0])}?:after,subjects.size<limit)
    }
    override suspend fun readMetadata(lease:AccountLease,trackId:AccountUuid):AccountTrackMetadata?=owner.read(lease){sql,_->sql.query("SELECT revision,state,exact_document FROM j_track_metadata WHERE scope_id=? AND track_id=?",lease.scopeId,trackId){
        val p=AccountSyncWireV1.document(it.getBlob(2));AccountTrackMetadata(trackId,it.getLong(0),it.getText(1),p.obj("fields"),p.obj("provenance"),decodeAlbum(p),p)
    }.singleOrNull()}
    override suspend fun readAttribution(lease:AccountLease,sourceId:AccountUuid):List<AccountAttribution> = owner.read(lease){sql,_->sql.query("SELECT aggregate_type,aggregate_id,exact_document,observation_kind FROM j_attribution WHERE scope_id=? AND source_id=? LIMIT 100",lease.scopeId,sourceId){
        AccountAttribution(sourceId,it.getText(0),AccountUuid(it.getText(1)),AccountSyncWireV1.document(it.getBlob(2)),it.getText(3))}}
}

/** Same validated provider/edition evidence as Android; never a canonical Release or Artist identity. */
internal fun decodeAlbum(metadata:JsonObject):AccountMetadataAlbum?=runCatching {
    require(metadata.string("state",100) in setOf("MISSING","LOCAL","QUEUED","READY","REVIEW","NOT_FOUND","RETRY","FAILED"))
    val p=metadata["album_group_v1"] as? JsonObject?:return null;val fields=metadata.obj("fields")
    require(p.number("schema_version",1,1)==1L)
    fun text(k:String,max:Int):String=p.string(k,max).also{require(it==it.trim() && it.isNotBlank() && it.none{c->c.code<32||c.code in 127..159})}
    fun nullable(k:String,max:Int):String?=if(p[k]==JsonNull)null else text(k,max)
    val provider=text("provider",100);val release=text("release_id",128);val key=text("key",300)
    if(provider=="MUSICBRAINZ"){AccountUuid(release);require(key=="musicbrainz:release:$release")}
    else{require(provider in setOf("JAMENDO","YANDEX","BANDCAMP","SOUNDCLOUD","YOUTUBE","HITMO") && release.matches(Regex("[A-Za-z0-9._-]{1,128}")) && key=="native:${provider.lowercase(Locale.ROOT)}:album:$release")}
    val title=text("title",500);val artist=nullable("album_artist",500);val date=nullable("release_date",10)
    if(date!=null){require(date.matches(Regex("[0-9]{4}(-[0-9]{2})?(-[0-9]{2})?")));LocalDate.parse(when(date.length){4->"$date-01-01";7->"$date-01";else->date})}
    fun number(k:String):Int?=if(p[k]==JsonNull)null else p.number(k,1,9_999).toInt()
    val disc=number("disc_number");val track=number("track_number");val evidence=p.obj("evidence")
    require(evidence.string("source",100)==if(provider=="MUSICBRAINZ")"MUSICBRAINZ" else "SOURCE_NATIVE")
    val sourceId=evidence.string("source_id",500)
    require(!sourceId.contains("://") && !sourceId.contains('\\') && !sourceId.startsWith('/') && !sourceId.startsWith("file:") && !sourceId.startsWith("data:") && !sourceId.matches(Regex("^[A-Za-z]:/.*")) && sourceId.none{it in "?&#=@"})
    require(provider!="MUSICBRAINZ" || sourceId==key);evidence.bool("locked");evidence.string("normalization_version",100)
    val observed=evidence.string("observed_at",100);require(runCatching{OffsetDateTime.parse(observed)}.isSuccess||runCatching{LocalDateTime.parse(observed)}.isSuccess)
    fun normalized(v:JsonElement?):String?=(v as? JsonPrimitive)?.takeIf{it.isString}?.content?.let{
        Normalizer.normalize(it,Normalizer.Form.NFKC).split('\u0131').joinToString("\u0131"){part->part.uppercase(Locale.ROOT).lowercase(Locale.ROOT)}
            .replace("\u00df","ss").replace('\u03c2','\u03c3').replace(Regex("[^\\p{L}\\p{N}_]+")," ").trim().replace(Regex("\\s+")," ")}
    require("mb_release_id" !in fields || if(provider=="MUSICBRAINZ")fields["mb_release_id"]==JsonPrimitive(release) else fields["mb_release_id"]==JsonNull)
    require("album" !in fields || normalized(fields["album"])==normalized(JsonPrimitive(title)))
    require(artist==null || "album_artist" !in fields || normalized(fields["album_artist"])==normalized(JsonPrimitive(artist)))
    require("release_date" !in fields || fields["release_date"]==(date?.let(::JsonPrimitive)?:JsonNull))
    for((k,v)in listOf("disc_number" to disc,"track_number" to track))require(k !in fields || fields[k]==(v?.let(::JsonPrimitive)?:JsonNull))
    AccountMetadataAlbum("metadata-album:$key",provider,release,title,artist,date,disc,track)
}.getOrNull()
