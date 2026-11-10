package app.autplay.data.desktop.account

/** Static unpublished schema1 additions. Table shape also supplies the common mutation ledger. */
internal data class AccountTableShape(val name: String, val keys: String, val columns: String, val trees: Int, val ddl: String)
internal object AccountRepositorySchema {
    const val COMPONENT: String = "account-repositories-v1"
    private fun table(name: String, keys: String, columns: String, trees: Int = 2, body: String): AccountTableShape =
        AccountTableShape(name, keys, columns, trees, "CREATE TABLE $name ($body)")
    val tables: List<AccountTableShape> = listOf(
        table("j_domain_state", "scope_id,aggregate_type,local_id", "scope_id,aggregate_type,local_id,revision,removed", body = """
            scope_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, local_id TEXT NOT NULL, revision INTEGER NOT NULL,
            removed INTEGER NOT NULL, PRIMARY KEY(scope_id,aggregate_type,local_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_repository_commit", "scope_id,operation_id", "scope_id,operation_id,kind,exact_input", body = """
            scope_id TEXT NOT NULL, operation_id TEXT NOT NULL, kind TEXT NOT NULL,
            exact_input BLOB NOT NULL CHECK(length(exact_input)<=8388608), PRIMARY KEY(scope_id,operation_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_domain_receipt", "scope_id,operation_id", "scope_id,operation_id,aggregate_id,event_id,intent_id", body = """
            scope_id TEXT NOT NULL, operation_id TEXT NOT NULL, aggregate_id TEXT NOT NULL, event_id TEXT, intent_id TEXT,
            PRIMARY KEY(scope_id,operation_id),
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,intent_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT"""),
        table("j_conflict_repair", "scope_id,profile_id,source_id,aggregate_type,local_id,operation_id", "scope_id,profile_id,source_id,aggregate_type,local_id,operation_id,event_id", 3, """
            scope_id TEXT NOT NULL,profile_id TEXT NOT NULL,source_id TEXT NOT NULL,aggregate_type TEXT NOT NULL,local_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,event_id TEXT NOT NULL,PRIMARY KEY(scope_id,profile_id,source_id,aggregate_type,local_id,operation_id),UNIQUE(scope_id,event_id),
            FOREIGN KEY(scope_id,profile_id,source_id,aggregate_type,local_id) REFERENCES j_conflict(scope_id,profile_id,source_id,aggregate_type,local_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT"""),
        table("j_queue", "scope_id,queue_id", "scope_id,queue_id,revision,repeat_mode,shuffle,session_id,current_entry_id", body = """
            scope_id TEXT NOT NULL, queue_id TEXT NOT NULL, revision INTEGER NOT NULL, repeat_mode TEXT NOT NULL,
            shuffle INTEGER NOT NULL, session_id TEXT, current_entry_id TEXT, PRIMARY KEY(scope_id,queue_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_queue_entry", "scope_id,queue_id,revision,entry_id", "scope_id,queue_id,revision,entry_id,track_id,position", body = """
            scope_id TEXT NOT NULL, queue_id TEXT NOT NULL, revision INTEGER NOT NULL, entry_id TEXT NOT NULL,
            track_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 999),
            PRIMARY KEY(scope_id,queue_id,revision,entry_id), UNIQUE(scope_id,queue_id,revision,position),
            FOREIGN KEY(scope_id,queue_id) REFERENCES j_queue(scope_id,queue_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,track_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT"""),
        table("j_playback_checkpoint", "scope_id,listening_id", "scope_id,listening_id,queue_id,entry_id,session_id,track_id,started_ms,played_ms,position_ms,revision,origin,context,excluded,finalized", body = """
            scope_id TEXT NOT NULL, listening_id TEXT NOT NULL, queue_id TEXT NOT NULL, entry_id TEXT NOT NULL,
            session_id TEXT NOT NULL, track_id TEXT NOT NULL, started_ms INTEGER NOT NULL, played_ms INTEGER NOT NULL,
            position_ms INTEGER NOT NULL, revision INTEGER NOT NULL, origin TEXT NOT NULL, context TEXT NOT NULL,
            excluded INTEGER NOT NULL, finalized INTEGER NOT NULL, PRIMARY KEY(scope_id,listening_id),
            FOREIGN KEY(scope_id,queue_id) REFERENCES j_queue(scope_id,queue_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,track_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT"""),
        table("j_confirmed_policy", "scope_id,kind", "scope_id,kind,revision,enabled,confirmed_at_ms,operation_id,pending_k_ref,raw_state", body = """
            scope_id TEXT NOT NULL, kind TEXT NOT NULL, revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
            confirmed_at_ms INTEGER NOT NULL, operation_id TEXT NOT NULL, pending_k_ref TEXT,raw_state TEXT NOT NULL,
            PRIMARY KEY(scope_id,kind), FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_work", "scope_id,work_id", "scope_id,work_id,kind,subject_id,generation,next_attempt_ms,state,boot_id,lease_token,lease_revision,expires_ms", body = """
            scope_id TEXT NOT NULL, work_id TEXT NOT NULL, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
            generation INTEGER NOT NULL, next_attempt_ms INTEGER NOT NULL, state TEXT NOT NULL,
            boot_id TEXT, lease_token TEXT, lease_revision INTEGER NOT NULL DEFAULT 0, expires_ms INTEGER,
            PRIMARY KEY(scope_id,work_id), FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_work_outcome", "scope_id,operation_id", "scope_id,operation_id,work_id,kind,subject_id,generation,next_attempt_ms,boot_id,lease_token,lease_revision,expires_ms", body = """
            scope_id TEXT NOT NULL,operation_id TEXT NOT NULL,work_id TEXT NOT NULL,kind TEXT NOT NULL,subject_id TEXT NOT NULL,
            generation INTEGER NOT NULL,next_attempt_ms INTEGER NOT NULL,boot_id TEXT NOT NULL,lease_token TEXT NOT NULL,lease_revision INTEGER NOT NULL,expires_ms INTEGER NOT NULL,
            PRIMARY KEY(scope_id,operation_id),FOREIGN KEY(scope_id,work_id) REFERENCES j_work(scope_id,work_id) ON DELETE RESTRICT"""),
        table("j_import_review", "scope_id,review_id", "scope_id,review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,rank,predecessor,operation_id", body = """
            scope_id TEXT NOT NULL, review_id TEXT NOT NULL, local_job_id TEXT NOT NULL, local_entry_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL, server_job_id TEXT NOT NULL, server_entry_id TEXT NOT NULL, rank INTEGER NOT NULL,
            predecessor TEXT, operation_id TEXT NOT NULL, PRIMARY KEY(scope_id,review_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_source_registration", "source_store_id", "source_store_id,owner_ref", 3, """
            source_store_id TEXT PRIMARY KEY, owner_ref TEXT NOT NULL UNIQUE"""),
        table("j_source_selection", "selection_id", "selection_id,source_store_id,scope_id,account_id,review_revision,cutoff_ms,binding_commit,authority_revision,state,exact_selection", body = """
            selection_id TEXT PRIMARY KEY, source_store_id TEXT NOT NULL, scope_id TEXT NOT NULL, account_id TEXT,
            review_revision INTEGER NOT NULL, cutoff_ms INTEGER NOT NULL, binding_commit TEXT NOT NULL,
            authority_revision INTEGER NOT NULL, state TEXT NOT NULL, exact_selection BLOB NOT NULL CHECK(length(exact_selection)<=8388608),
            FOREIGN KEY(source_store_id) REFERENCES j_source_registration(source_store_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_source_copy", "source_store_id,change_id", "source_store_id,change_id,selection_id,aggregate_id,kind,version,occurred_ms,exact_fact", body = """
            source_store_id TEXT NOT NULL, change_id TEXT NOT NULL, selection_id TEXT NOT NULL, aggregate_id TEXT NOT NULL,
            kind TEXT NOT NULL, version INTEGER NOT NULL, occurred_ms INTEGER NOT NULL,
            exact_fact BLOB NOT NULL CHECK(length(exact_fact)<=262144), PRIMARY KEY(source_store_id,change_id),
            FOREIGN KEY(selection_id) REFERENCES j_source_selection(selection_id) ON DELETE RESTRICT"""),
        table("j_identity_map", "source_store_id,source_id,aggregate_type", "source_store_id,source_id,aggregate_type,scope_id,target_id", 3, """
            source_store_id TEXT NOT NULL, source_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, scope_id TEXT NOT NULL,
            target_id TEXT NOT NULL, PRIMARY KEY(source_store_id,source_id,aggregate_type), UNIQUE(scope_id,aggregate_type,target_id),
            FOREIGN KEY(source_store_id) REFERENCES j_source_registration(source_store_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT"""),
        table("j_materialization", "source_store_id,change_id", "source_store_id,change_id,scope_id,account_id,aggregate_id,lineage_id,event_id,intent_id", 3, """
            source_store_id TEXT NOT NULL, change_id TEXT NOT NULL, scope_id TEXT NOT NULL, account_id TEXT,
            aggregate_id TEXT NOT NULL, lineage_id TEXT, event_id TEXT, intent_id TEXT,
            PRIMARY KEY(source_store_id,change_id), UNIQUE(scope_id,event_id),
            FOREIGN KEY(source_store_id,change_id) REFERENCES j_source_copy(source_store_id,change_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,intent_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT"""),
        table("j_materialization_dependency", "selection_id,source_id,aggregate_type", "selection_id,source_id,aggregate_type,scope_id,target_id,event_id,intent_id", body = """
            selection_id TEXT NOT NULL,source_id TEXT NOT NULL,aggregate_type TEXT NOT NULL,scope_id TEXT NOT NULL,target_id TEXT NOT NULL,event_id TEXT,intent_id TEXT,
            PRIMARY KEY(selection_id,source_id,aggregate_type),FOREIGN KEY(selection_id) REFERENCES j_source_selection(selection_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,intent_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT"""),
        table("j_materialization_event", "source_store_id,change_id,item_index", "source_store_id,change_id,item_index,scope_id,event_id,intent_id", body = """
            source_store_id TEXT NOT NULL,change_id TEXT NOT NULL,item_index INTEGER NOT NULL,scope_id TEXT NOT NULL,event_id TEXT,intent_id TEXT,
            PRIMARY KEY(source_store_id,change_id,item_index),FOREIGN KEY(source_store_id,change_id) REFERENCES j_source_copy(source_store_id,change_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,intent_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT"""),
        table("j_source_outcome", "selection_id,change_id", "selection_id,change_id,state,reason", body = """
            selection_id TEXT NOT NULL,change_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT,
            PRIMARY KEY(selection_id,change_id),FOREIGN KEY(selection_id) REFERENCES j_source_selection(selection_id) ON DELETE RESTRICT"""),
        table("j_portable_owner", "profile_id", "profile_id,account_id,scope_id,binding_commit,authority_revision,device_key_thumbprint,cutoff_ms", body = """
            profile_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, scope_id TEXT NOT NULL, binding_commit TEXT NOT NULL,
            authority_revision INTEGER NOT NULL, device_key_thumbprint TEXT, cutoff_ms INTEGER NOT NULL,
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT"""),
        table("j_portable_record", "account_id,record_id", "account_id,record_id,kind,document,content_digest,revision,canonical_record_id", 3, """
            account_id TEXT NOT NULL, record_id TEXT NOT NULL, kind TEXT NOT NULL, document BLOB NOT NULL CHECK(length(document)<=16384),
            content_digest TEXT NOT NULL, revision INTEGER NOT NULL, canonical_record_id TEXT NOT NULL,
            PRIMARY KEY(account_id,record_id)"""),
        table("j_portable_original", "account_id,operation_id,item_index", "account_id,operation_id,item_index,record_id,kind,exact_record", body = """
            account_id TEXT NOT NULL, operation_id TEXT NOT NULL, item_index INTEGER NOT NULL, record_id TEXT NOT NULL,
            kind TEXT NOT NULL, exact_record BLOB NOT NULL CHECK(length(exact_record)<=17000), PRIMARY KEY(account_id,operation_id,item_index)"""),
        table("j_listening_alias", "account_id,event_id", "account_id,event_id,record_id", body = """
            account_id TEXT NOT NULL, event_id TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(account_id,event_id),
            FOREIGN KEY(account_id,record_id) REFERENCES j_portable_record(account_id,record_id) ON DELETE RESTRICT"""),
        table("j_portable_listening", "account_id,record_id", "account_id,record_id,played_ms,started_ms,excluded", body = """
            account_id TEXT NOT NULL, record_id TEXT NOT NULL, played_ms INTEGER NOT NULL, started_ms INTEGER NOT NULL,
            excluded INTEGER NOT NULL, PRIMARY KEY(account_id,record_id),
            FOREIGN KEY(account_id,record_id) REFERENCES j_portable_record(account_id,record_id) ON DELETE RESTRICT"""),
        table("j_source_listen_alias", "source_store_id,source_listen_id", "source_store_id,source_listen_id,scope_id,account_id,event_id,intent_id", body = """
            source_store_id TEXT NOT NULL, source_listen_id TEXT NOT NULL, scope_id TEXT NOT NULL, account_id TEXT,
            event_id TEXT,intent_id TEXT,CHECK((event_id IS NULL)<>(intent_id IS NULL)),PRIMARY KEY(source_store_id,source_listen_id),
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,intent_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT"""),
        table("j_portable_cursor", "account_id,profile_id,source", "account_id,profile_id,source,cursor_time,cursor_id,revision,complete,pass_id,uploaded_revision", body = """
            account_id TEXT NOT NULL, profile_id TEXT NOT NULL, source TEXT NOT NULL, cursor_time TEXT, cursor_id TEXT,
            revision INTEGER NOT NULL, complete INTEGER NOT NULL, pass_id TEXT, uploaded_revision INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(account_id,profile_id,source), FOREIGN KEY(profile_id) REFERENCES j_portable_owner(profile_id) ON DELETE RESTRICT"""),
        table("j_portable_statistics", "account_id,profile_id", "account_id,profile_id,revision,checked_through_ms,complete,available,listens,played_ms", body = """
            account_id TEXT NOT NULL, profile_id TEXT NOT NULL, revision INTEGER NOT NULL, checked_through_ms INTEGER,
            complete INTEGER NOT NULL, available INTEGER NOT NULL, listens INTEGER, played_ms INTEGER,
            PRIMARY KEY(account_id,profile_id), FOREIGN KEY(profile_id) REFERENCES j_portable_owner(profile_id) ON DELETE RESTRICT"""),
    )
    private val immutable = setOf("j_repository_commit", "j_domain_receipt", "j_conflict_repair", "j_queue_entry", "j_import_review", "j_work_outcome",
        "j_source_registration", "j_source_copy", "j_identity_map", "j_materialization", "j_portable_owner",
        "j_portable_original", "j_source_listen_alias", "j_materialization_dependency", "j_materialization_event", "j_source_outcome")
    val create: List<String> = tables.map { it.ddl } + tables.map {
        "CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"
    } + immutable.map {
        "CREATE TRIGGER ${it}_immutable BEFORE UPDATE ON $it BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"
    } + listOf("""CREATE VIEW j_account_projection_visibility AS WITH retained AS (SELECT p.*,
        EXISTS(SELECT 1 FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
            WHERE e.scope_id=p.scope_id AND e.aggregate_type=p.aggregate_type AND e.aggregate_local_id=p.local_id AND d.state<>'ACKNOWLEDGED'
            AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.source_id
                WHERE r.scope_id=e.scope_id AND r.source_id=e.event_id AND a.terminal=1))
        OR EXISTS(SELECT 1 FROM j_local_intent i WHERE i.scope_id=p.scope_id AND i.aggregate_type=p.aggregate_type
            AND i.aggregate_local_id=p.local_id AND i.materialized_event_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM j_account_intent_link m WHERE m.source_scope_id=i.scope_id AND m.change_id=i.change_id)) AS pending,
        CASE WHEN h.scope_id IS NULL OR p.aggregate_type NOT IN ('USER_TRACK_REF','LIBRARY_ENTRY','PLAYLIST','PLAYLIST_ENTRY') THEN NULL
            WHEN p.ack_snapshot=h.snapshot_id OR coalesce(p.source_sequence,0)>h.high_water OR EXISTS(SELECT 1 FROM j_bootstrap_member m
                WHERE m.scope_id=p.scope_id AND m.profile_id=h.profile_id AND m.snapshot_id=h.snapshot_id
                AND m.aggregate_type=p.aggregate_type AND m.server_id=p.server_id) THEN 1 ELSE 0 END AS snapshot_confirmed,
        h.profile_id AS head_profile,h.snapshot_id AS head_snapshot
        FROM j_projection_state p LEFT JOIN j_snapshot_head h ON h.scope_id=p.scope_id)
        SELECT r.*,CASE WHEN r.deleted=1 THEN 1 WHEN r.pending=1 OR r.snapshot_confirmed=1 THEN 0
            WHEN EXISTS(SELECT 1 FROM j_bootstrap_tombstone b JOIN j_tombstone t
                ON t.scope_id=b.scope_id AND t.tombstone_id=b.tombstone_id
                WHERE b.scope_id=r.scope_id AND b.profile_id=r.head_profile AND b.snapshot_id=r.head_snapshot
                AND t.aggregate_type=r.aggregate_type AND t.server_id=r.server_id) THEN 1 ELSE 0 END AS effective_deleted
        FROM retained r""")
}

/** All children execute on the one actual store connection, authority capture and budget. */
internal interface AccountRepositoryOwner {
    suspend fun <T> read(lease: app.autplay.application.desktop.account.AccountLease,
        action: (AccountPageSql, app.autplay.application.desktop.account.AccountProtectedWitness) -> T): T
    suspend fun <T> write(lease: app.autplay.application.desktop.account.AccountLease,
        operation: app.autplay.application.desktop.account.AccountUuid, kind: String, input: ByteArray,
        action: (AccountPageSql, app.autplay.application.desktop.account.AccountProtectedWitness, Boolean) -> T): T
    suspend fun <T> settle(lease: app.autplay.application.desktop.account.AccountLease,
        operation: app.autplay.application.desktop.account.AccountUuid, kind: String, input: ByteArray,
        action: (AccountPageSql, Boolean) -> T): T
}
