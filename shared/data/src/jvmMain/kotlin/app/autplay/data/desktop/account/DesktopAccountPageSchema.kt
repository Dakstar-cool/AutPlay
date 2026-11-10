package app.autplay.data.desktop.account

/**
 * Additive completion of UNPUBLISHED J1 schema1, not a deployed migration or schema2.
 * The foundation DDL/columns remain byte-identical. Typed extensions retain all known/unknown rows.
 * Remaining portable/catalog/materialization/work/M1 DDL belongs to those still-open owners.
 */
internal object DesktopAccountPageSchema {
    const val COMPONENT: String = "sync-pages-v1"
    val create: List<String> = listOf(
        """CREATE TABLE j_schema_component (
            name TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version=1))""",
        """CREATE TABLE j_sync_cursor (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, lineage_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 9007199254740991),
            opaque_cursor TEXT, server_sequence INTEGER NOT NULL CHECK(server_sequence>=0), state TEXT NOT NULL,
            PRIMARY KEY(scope_id,profile_id),
            FOREIGN KEY(scope_id,profile_id,lineage_id) REFERENCES j_profile_alias(scope_id,profile_id,lineage_id) ON DELETE RESTRICT,
            CHECK(opaque_cursor IS NULL OR length(CAST(opaque_cursor AS BLOB))<=8192))""",
        """CREATE TABLE j_bootstrap (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, snapshot_id TEXT, page_token TEXT,
            final_cursor TEXT, high_water INTEGER, state TEXT NOT NULL,
            PRIMARY KEY(scope_id,profile_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_sync_cursor(scope_id,profile_id) ON DELETE RESTRICT,
            CHECK(page_token IS NULL OR length(CAST(page_token AS BLOB))<=8192),
            CHECK(final_cursor IS NULL OR length(CAST(final_cursor AS BLOB))<=8192))""",
        """CREATE TABLE j_bootstrap_member (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL, server_id TEXT NOT NULL, safe_record BLOB NOT NULL CHECK(length(safe_record)<=8388608),
            PRIMARY KEY(scope_id,profile_id,snapshot_id,aggregate_type,server_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_sync_cursor(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_bootstrap_tombstone (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, tombstone_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,profile_id,snapshot_id,tombstone_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_sync_cursor(scope_id,profile_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,tombstone_id) REFERENCES j_tombstone(scope_id,tombstone_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_snapshot_head (
            scope_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, high_water INTEGER NOT NULL,
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_page_result (
            operation_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, profile_id TEXT NOT NULL,
            lineage_id TEXT NOT NULL, activation_revision INTEGER NOT NULL, kind TEXT NOT NULL,
            state TEXT NOT NULL, has_more INTEGER NOT NULL CHECK(has_more IN (0,1)), ack_through INTEGER,
            safe_wire BLOB CHECK(safe_wire IS NULL OR length(safe_wire)<=8388608),
            UNIQUE(scope_id,operation_id), UNIQUE(scope_id,profile_id,operation_id),
            FOREIGN KEY(scope_id,profile_id,lineage_id) REFERENCES j_profile_alias(scope_id,profile_id,lineage_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_applied_event (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, event_id TEXT NOT NULL,
            server_sequence INTEGER NOT NULL, exact_event BLOB NOT NULL CHECK(length(exact_event)<=8388608),
            PRIMARY KEY(scope_id,profile_id,event_id), UNIQUE(scope_id,profile_id,server_sequence),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_sync_cursor(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_deferred_page (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL, reason TEXT NOT NULL,
            state TEXT NOT NULL, PRIMARY KEY(scope_id,profile_id),
            FOREIGN KEY(scope_id,profile_id,operation_id) REFERENCES j_page_result(scope_id,profile_id,operation_id)
                ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED)""",
        """CREATE TABLE j_ack (
            scope_id TEXT NOT NULL, event_id TEXT NOT NULL, outcome TEXT NOT NULL,
            terminal INTEGER NOT NULL CHECK(terminal IN (0,1)), server_id TEXT, observed_version INTEGER, raw_outcome TEXT NOT NULL,
            page_operation_id TEXT NOT NULL, ack_index INTEGER NOT NULL CHECK(ack_index BETWEEN 0 AND 99),
            PRIMARY KEY(scope_id,event_id),
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,page_operation_id) REFERENCES j_page_result(scope_id,operation_id)
                ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED)""",
        """CREATE TABLE j_projection_state (
            scope_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, local_id TEXT NOT NULL,
            server_id TEXT NOT NULL, profile_id TEXT NOT NULL, observed_version INTEGER,
            state TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)), source_sequence INTEGER, ack_snapshot TEXT,
            PRIMARY KEY(scope_id,aggregate_type,local_id), UNIQUE(scope_id,aggregate_type,server_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_track_detail (
            scope_id TEXT NOT NULL, track_ref_id TEXT NOT NULL, title_is_null INTEGER NOT NULL DEFAULT 0,
            artist_is_null INTEGER NOT NULL DEFAULT 0, album TEXT, duration_ms INTEGER, recording_id TEXT,
            resolution_status TEXT, PRIMARY KEY(scope_id,track_ref_id),
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        // Preserve the foundation's UNIQUE(track_ref_id) table exactly. Remote retained/re-added
        // entries have distinct IDs; the typed owner reads the disjoint union without recreating it.
        """CREATE TABLE j_library_relation (
            scope_id TEXT NOT NULL, entry_id TEXT NOT NULL, track_ref_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,entry_id),
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_library_detail (
            scope_id TEXT NOT NULL, entry_id TEXT NOT NULL, source TEXT, availability_status TEXT,
            PRIMARY KEY(scope_id,entry_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_preference (
            scope_id TEXT NOT NULL, preference_id TEXT NOT NULL, track_ref_id TEXT NOT NULL,
            preference TEXT NOT NULL, excluded_from_taste INTEGER NOT NULL CHECK(excluded_from_taste IN (0,1)),
            PRIMARY KEY(scope_id,preference_id), UNIQUE(scope_id,track_ref_id),
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_playlist (
            scope_id TEXT NOT NULL, playlist_id TEXT NOT NULL, name TEXT, description TEXT,
            visibility TEXT, playlist_type TEXT, PRIMARY KEY(scope_id,playlist_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_playlist_entry (
            scope_id TEXT NOT NULL, entry_id TEXT NOT NULL, playlist_id TEXT NOT NULL, track_ref_id TEXT NOT NULL,
            position_key TEXT NOT NULL, PRIMARY KEY(scope_id,entry_id),
            FOREIGN KEY(scope_id,playlist_id) REFERENCES j_playlist(scope_id,playlist_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_listening (
            scope_id TEXT NOT NULL, listening_id TEXT NOT NULL, track_ref_id TEXT NOT NULL,
            source_local_track_id TEXT NOT NULL, recording_id TEXT, played_ms INTEGER NOT NULL,
            duration_ms INTEGER, completion_ratio REAL, event_origin TEXT NOT NULL, context TEXT NOT NULL,
            explicit_feedback TEXT NOT NULL, excluded_from_taste INTEGER NOT NULL,
            recommendation BLOB, source_created_at TEXT NOT NULL,
            PRIMARY KEY(scope_id,listening_id),
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_conflict (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL, source_id TEXT NOT NULL, aggregate_type TEXT NOT NULL,
            local_id TEXT NOT NULL, server_id TEXT, kind TEXT NOT NULL, resolution_state TEXT NOT NULL,
            base_version INTEGER, observed_version INTEGER, safe_evidence BLOB NOT NULL CHECK(length(safe_evidence)<=8388608),
            PRIMARY KEY(scope_id,profile_id,source_id,aggregate_type,local_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_redirect (
            scope_id TEXT NOT NULL, aggregate_type TEXT NOT NULL, alias_server_id TEXT NOT NULL,
            canonical_server_id TEXT NOT NULL, alias_local_id TEXT, canonical_local_id TEXT,
            server_sequence INTEGER, PRIMARY KEY(scope_id,aggregate_type,alias_server_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)""",
        "CREATE INDEX j_redirect_target ON j_redirect(scope_id,aggregate_type,canonical_server_id)",
        """CREATE TABLE j_tombstone (
            scope_id TEXT NOT NULL, tombstone_id TEXT NOT NULL, aggregate_type TEXT NOT NULL,
            server_id TEXT NOT NULL, deleted_by_event_id TEXT NOT NULL, deleted_at TEXT NOT NULL,
            retain_until TEXT NOT NULL, PRIMARY KEY(scope_id,tombstone_id),
            UNIQUE(scope_id,aggregate_type,server_id,deleted_by_event_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)""",
        """CREATE VIEW j_projection_visibility AS WITH retained AS (SELECT p.*,
            EXISTS(SELECT 1 FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                WHERE e.scope_id=p.scope_id AND e.aggregate_type=p.aggregate_type AND e.aggregate_local_id=p.local_id AND d.state<>'ACKNOWLEDGED')
            OR EXISTS(SELECT 1 FROM j_local_intent i WHERE i.scope_id=p.scope_id AND i.aggregate_type=p.aggregate_type
                AND i.aggregate_local_id=p.local_id AND i.materialized_event_id IS NULL) AS pending,
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
            FROM retained r""",
    ) + listOf("j_schema_component", "j_sync_cursor", "j_bootstrap", "j_bootstrap_member", "j_bootstrap_tombstone", "j_snapshot_head", "j_page_result", "j_applied_event",
        "j_deferred_page", "j_ack", "j_projection_state", "j_track_detail", "j_library_relation", "j_library_detail", "j_preference",
        "j_playlist", "j_playlist_entry", "j_listening", "j_conflict", "j_redirect", "j_tombstone").map { table ->
        "CREATE TRIGGER ${table}_retain BEFORE DELETE ON $table BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"
    } + listOf("j_schema_component", "j_bootstrap_member", "j_bootstrap_tombstone", "j_page_result", "j_applied_event", "j_ack", "j_tombstone", "j_redirect").map { table ->
        "CREATE TRIGGER ${table}_immutable BEFORE UPDATE ON $table BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"
    }
}
