package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*

internal object AccountLocalDomainReader {
    fun read(sql:AccountPageSql,scope:AccountUuid,kind:AccountProjectionKind,after:AccountUuid?,limit:Int,
        exactId:AccountUuid?=null):List<AccountLocalDomainRow> {
        require(limit in 1..100)
        val (table,key,type)=when(kind){
            AccountProjectionKind.TRACK->Triple("j_track_ref","track_ref_id","USER_TRACK_REF")
            AccountProjectionKind.LIBRARY->Triple("(SELECT * FROM j_library_entry UNION ALL SELECT * FROM j_library_relation)","entry_id","LIBRARY_ENTRY")
            AccountProjectionKind.PREFERENCE->Triple("j_preference","preference_id","USER_TRACK_PREFERENCE")
            AccountProjectionKind.PLAYLIST->Triple("j_playlist","playlist_id","PLAYLIST")
            AccountProjectionKind.PLAYLIST_ENTRY->Triple("j_playlist_entry","entry_id","PLAYLIST_ENTRY")
            AccountProjectionKind.LISTENING->Triple("j_listening","listening_id","LISTENING_EVENT")
        }
        val headers=sql.query("""SELECT t.$key,p.server_id,p.observed_version,coalesce(s.revision,1),
            CASE WHEN coalesce(p.pending,0)=1 THEN coalesce(s.removed,p.effective_deleted,0) ELSE coalesce(p.effective_deleted,s.removed,0) END,
            EXISTS(SELECT 1 FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                WHERE e.scope_id=t.scope_id AND e.aggregate_type=? AND e.aggregate_local_id=t.$key AND d.state<>'ACKNOWLEDGED'
                AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.source_id
                    WHERE r.scope_id=e.scope_id AND r.source_id=e.event_id AND a.terminal=1))
            OR EXISTS(SELECT 1 FROM j_local_intent i WHERE i.scope_id=t.scope_id AND i.aggregate_type=? AND i.aggregate_local_id=t.$key AND i.materialized_event_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM j_account_intent_link m WHERE m.source_scope_id=i.scope_id AND m.change_id=i.change_id)),p.state,p.snapshot_confirmed,
            EXISTS(SELECT 1 FROM j_conflict c WHERE c.scope_id=t.scope_id AND c.aggregate_type=? AND c.local_id=t.$key AND c.resolution_state<>'RESOLVED'
                AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.event_id
                    WHERE r.scope_id=c.scope_id AND r.profile_id=c.profile_id AND r.source_id=c.source_id AND r.aggregate_type=c.aggregate_type
                    AND r.local_id=c.local_id AND a.terminal=1 AND a.outcome='APPLIED'))
            FROM $table t LEFT JOIN j_account_projection_visibility p ON p.scope_id=t.scope_id AND p.local_id=t.$key AND p.aggregate_type=?
            LEFT JOIN j_domain_state s ON s.scope_id=t.scope_id AND s.local_id=t.$key AND s.aggregate_type=?
            WHERE t.scope_id=? AND t.$key${if(exactId==null) ">" else "="}? ORDER BY t.$key LIMIT ?""",
            type,type,type,type,type,scope,exactId?.value?:after?.value?:"",limit){
            AccountLocalDomainHeader(AccountUuid(it.getText(0)),sql.nullable(it,1)?.let(::AccountUuid),if(it.isNull(2))null else it.getLong(2),it.getLong(3),it.getLong(5)==1L,it.getLong(4)==1L,
                sql.nullable(it,6),if(it.isNull(7))null else it.getLong(7)==1L,it.getLong(8)==1L)
        }
        return headers.map{h->
            val id=h.localId
            val detail:AccountLocalDomainDetail=when(kind){
                AccountProjectionKind.TRACK->sql.query("""SELECT t.title,t.artist,d.title_is_null,d.artist_is_null,d.album,d.duration_ms,d.recording_id
                    FROM j_track_ref t LEFT JOIN j_track_detail d ON d.scope_id=t.scope_id AND d.track_ref_id=t.track_ref_id WHERE t.scope_id=? AND t.track_ref_id=?""",scope,id){
                    AccountLocalDomainDetail.Track(if(!it.isNull(2)&&it.getLong(2)==1L)null else it.getText(0),if(!it.isNull(3)&&it.getLong(3)==1L)null else it.getText(1),
                        sql.nullable(it,4),if(it.isNull(5))null else it.getLong(5),sql.nullable(it,6)?.let(::AccountUuid))}.single()
                AccountProjectionKind.LIBRARY->sql.query("""SELECT l.track_ref_id,d.source,d.availability_status FROM $table l LEFT JOIN j_library_detail d
                    ON d.scope_id=l.scope_id AND d.entry_id=l.entry_id WHERE l.scope_id=? AND l.entry_id=?""",scope,id){
                    AccountLocalDomainDetail.Library(AccountUuid(it.getText(0)),sql.nullable(it,1),sql.nullable(it,2))}.single()
                AccountProjectionKind.PREFERENCE->sql.query("SELECT track_ref_id,preference,excluded_from_taste FROM j_preference WHERE scope_id=? AND preference_id=?",scope,id){
                    AccountLocalDomainDetail.Preference(AccountUuid(it.getText(0)),it.getText(1),it.getLong(2)==1L)}.single()
                AccountProjectionKind.PLAYLIST->sql.query("SELECT name,description,visibility,playlist_type FROM j_playlist WHERE scope_id=? AND playlist_id=?",scope,id){
                    AccountLocalDomainDetail.Playlist(sql.nullable(it,0),sql.nullable(it,1),sql.nullable(it,2),sql.nullable(it,3))}.single()
                AccountProjectionKind.PLAYLIST_ENTRY->sql.query("SELECT playlist_id,track_ref_id,position_key FROM j_playlist_entry WHERE scope_id=? AND entry_id=?",scope,id){
                    AccountLocalDomainDetail.Entry(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),it.getText(2))}.single()
                AccountProjectionKind.LISTENING->sql.query("""SELECT track_ref_id,source_local_track_id,recording_id,played_ms,duration_ms,completion_ratio,event_origin,context,
                    explicit_feedback,excluded_from_taste,recommendation,source_created_at FROM j_listening WHERE scope_id=? AND listening_id=?""",scope,id){
                    AccountLocalDomainDetail.Listening(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),sql.nullable(it,2)?.let(::AccountUuid),it.getLong(3),
                        if(it.isNull(4))null else it.getLong(4),if(it.isNull(5))null else it.getDouble(5),it.getText(6),it.getText(7),it.getText(8),it.getLong(9)==1L,
                        if(it.isNull(10))null else AccountSyncWireV1.document(it.getBlob(10)),it.getText(11).takeIf{value->value.isNotEmpty()})}.single()
            }
            AccountLocalDomainRow(h,detail)
        }
    }
}
