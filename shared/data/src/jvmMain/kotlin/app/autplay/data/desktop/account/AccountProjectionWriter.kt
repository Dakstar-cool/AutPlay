package app.autplay.data.desktop.account

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.execSQL
import app.autplay.application.desktop.account.*
import kotlinx.serialization.json.*

internal class AccountPageDeferred(val reason: String) : IllegalStateException(reason)

/**
 * One ledger for the WHOLE page, including its commit marker, dependencies and redirect fan-in.
 * Research: sqlite.org/{limits,lang_transaction,fileformat,pragma}.html (2026-10-16).
 * Compared shared row admission, streamed rollback, smaller transport pages, per-bind caps and
 * larger reserves. Use the first two together. Bootstrap v1 has no requested page-size member;
 * only pull supports a smaller requested limit, for a future coordinator. A per-bind cap cannot
 * bound a page's sum; raising reserves/growth would break J1. Neither is the admission solution.
 *
 * Count actual existing rows before each write, with all retained column lengths (no BLOB copy).
 * New rows pay encoded record bytes plus 1024 bytes per table/PK/index tree (more than twice
 * these fixed UUID/discriminator index cells). Existing/repeated rows pay old + upper-bound new
 * bytes and 512 bytes/tree for rewrites. Shared physical page observations handle packing/splits:
 * charging an entire new tree page for every packed row would needlessly refuse ordinary 500-row
 * pages. Duplicate facts return before mutation. The bounded touched-key set holds no payloads.
 * These are conservative admission estimates, NOT measured pinned-binary sizing.
 * A separate physical guard leaves 256 pages before EACH single-row statement for B-tree splits
 * (<=5 trees, <=18 binary levels at 131072 pages, two new pages/level), plus record overflow.
 * Observe page_count after every write. No bulk writes, unbounded plan or accumulated row list.
 * Main/WAL/SHM/free-volume checks still apply; I/O/fsync/SQLite implementation faults remain open.
 * max_page_count bounds only the main file. Pinned 2.7.0 WAL/cache/packing/native failure measurements
 * and the future synthetic assertions remain UNVERIFIED; no new native limit/threading API is used.
 */
internal class AccountPageMutationBudget(private val sql: AccountPageSql, private val ceiling: Long) {
    private data class Shape(val keys: String, val columns: String, val trees: Int)
    private val shapes = mapOf(
        "j_projection_state" to Shape("scope_id,aggregate_type,local_id", "scope_id,aggregate_type,local_id,server_id,profile_id,observed_version,state,deleted,source_sequence,ack_snapshot", 3),
        "j_track_ref" to Shape("scope_id,track_ref_id", "scope_id,track_ref_id,operation_id,profile_id,title,artist,occurred_at_ms,server_track_ref_id,observed_version", 3),
        "j_track_detail" to Shape("scope_id,track_ref_id", "scope_id,track_ref_id,title_is_null,artist_is_null,album,duration_ms,recording_id,resolution_status", 2),
        "j_library_relation" to Shape("scope_id,entry_id", "scope_id,entry_id,track_ref_id", 2),
        "j_library_entry" to Shape("scope_id,entry_id", "scope_id,entry_id,track_ref_id", 3),
        "j_library_detail" to Shape("scope_id,entry_id", "scope_id,entry_id,source,availability_status", 2),
        "j_playlist" to Shape("scope_id,playlist_id", "scope_id,playlist_id,name,description,visibility,playlist_type", 2),
        "j_playlist_entry" to Shape("scope_id,entry_id", "scope_id,entry_id,playlist_id,track_ref_id,position_key", 2),
        "j_preference" to Shape("scope_id,preference_id", "scope_id,preference_id,track_ref_id,preference,excluded_from_taste", 3),
        "j_listening" to Shape("scope_id,listening_id", "scope_id,listening_id,track_ref_id,source_local_track_id,recording_id,played_ms,duration_ms,completion_ratio,event_origin,context,explicit_feedback,excluded_from_taste,recommendation,source_created_at", 2),
        "j_conflict" to Shape("scope_id,profile_id,source_id,aggregate_type,local_id", "scope_id,profile_id,source_id,aggregate_type,local_id,server_id,kind,resolution_state,base_version,observed_version,safe_evidence", 2),
        "j_redirect" to Shape("scope_id,aggregate_type,alias_server_id", "scope_id,aggregate_type,alias_server_id,canonical_server_id,alias_local_id,canonical_local_id,server_sequence", 3),
        "j_tombstone" to Shape("scope_id,tombstone_id", "scope_id,tombstone_id,aggregate_type,server_id,deleted_by_event_id,deleted_at,retain_until", 3),
        "j_bootstrap_member" to Shape("scope_id,profile_id,snapshot_id,aggregate_type,server_id", "scope_id,profile_id,snapshot_id,aggregate_type,server_id,safe_record", 2),
        "j_bootstrap_tombstone" to Shape("scope_id,profile_id,snapshot_id,tombstone_id", "scope_id,profile_id,snapshot_id,tombstone_id", 2),
        "j_applied_event" to Shape("scope_id,profile_id,event_id", "scope_id,profile_id,event_id,server_sequence,exact_event", 3),
        "j_bootstrap" to Shape("scope_id,profile_id", "scope_id,profile_id,snapshot_id,page_token,final_cursor,high_water,state", 2),
        "j_snapshot_head" to Shape("scope_id", "scope_id,profile_id,snapshot_id,high_water", 2),
        "j_sync_cursor" to Shape("scope_id,profile_id", "scope_id,profile_id,lineage_id,revision,opaque_cursor,server_sequence,state", 2),
        "j_deferred_page" to Shape("scope_id,profile_id", "scope_id,profile_id,operation_id,kind,reason,state", 3),
        "j_ack" to Shape("scope_id,event_id", "scope_id,event_id,outcome,terminal,server_id,observed_version,raw_outcome,page_operation_id,ack_index", 2),
        "j_event_delivery" to Shape("scope_id,event_id", "scope_id,event_id,state,attempt,next_retry_ms,lease_boot,lease_token,lease_revision,lease_expires_ms,last_error", 3),
        "j_lineage" to Shape("scope_id,lineage_id", "scope_id,lineage_id,server_instance_id,user_id,device_id,journal_epoch,next_sequence,ack_checkpoint", 5),
        "j_store" to Shape("singleton", "singleton,store_id,format,schema_version,last_wall_ms", 2),
        "j_event" to Shape("scope_id,event_id", "scope_id,event_id,lineage_id,profile_id,user_id,device_id,sequence,idempotency_key,event_type,aggregate_type,aggregate_local_id,canonical_bytes,request_hash", 5),
        "j_local_intent" to Shape("scope_id,change_id", "scope_id,change_id,profile_id,aggregate_local_id,event_type,aggregate_type,schema_version,occurred_at_ms,canonical_payload,state,materialized_event_id", 3),
    ) + (AccountRepositorySchema.tables + AccountCatalogSchema.tables + AccountMediaSchema.tables + AccountIntentSchema.tables + AccountInteractionSchema.tables + AccountImportReviewSchema.tables)
        .associate { it.name to Shape(it.keys, it.columns, it.trees) }
    private val initialPages: Long = sql.number("PRAGMA page_count")
    private var charged: Long = 0
    private var mutations: Int = 0
    private var visits: Int = 0
    private var markerAllocation: Long = 0
    private val touched: MutableSet<List<Any?>> = HashSet()
    fun visit(): Unit { if (++visits > 100_000) exceeded() }
    private fun exceeded(): Nothing = throw AccountPageDeferred("PAGE_MUTATION_CAPACITY")
    private fun rounded(bytes: Long): Long = ((bytes + 3_999) / 4_000) * 4_096
    private fun charge(bytes: Long): Unit {
        if (bytes > ceiling - charged) exceeded()
        charged += bytes
    }
    /** Held before any derived writes; final marker insertion uses this exact reserved slot. */
    fun marker(bytes: Int): Unit {
        markerAllocation = rounded(bytes.toLong() + 2_048) + 4 * 4_096
        charge(markerAllocation)
    }
    fun beforeMarker(): Unit {
        if ((sql.number("PRAGMA page_count") - initialPages) * 4_096 + markerAllocation + 1_048_576 > ceiling) exceeded()
        markerAllocation = 0 // The reserved slot is now being written; do not count it twice after.
    }
    fun before(table: String, keys: List<Any?>, values: Array<out Any?>): Unit {
        if (++mutations > 8_000) exceeded()
        val shape = shapes[table] ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        val columns = shape.columns.split(',')
        val keyColumns = shape.keys.split(',')
        if (keyColumns.size != keys.size) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        val length = columns.joinToString("+") { "coalesce(length(CAST($it AS BLOB)),0)+9" }
        val prior = sql.query("SELECT $length FROM $table WHERE ${keyColumns.joinToString(" AND ") { "$it=?" }}",
            *keys.toTypedArray()) { it.getLong(0) }.singleOrNull()
        val supplied = values.sumOf { value -> when (value) {
            null -> 1L
            is ByteArray -> value.size.toLong()
            is String -> value.toByteArray(Charsets.UTF_8).size.toLong()
            is AccountUuid -> 36L
            else -> 9L
        } }
        // INSERT/UPSERT/CASE can only retain old fields or use supplied values/static bounded labels.
        val record = (prior ?: 0) + supplied + 256 // Header/varints and fixed SQL labels/defaults.
        val first = touched.add(listOf(table) + keys)
        val cost = if (prior == null && first) record + shape.trees * 1_024L
            else record + (prior ?: 0) + shape.trees * 512L
        charge(cost)
        val allocated = sql.number("PRAGMA page_count")
        // Independent of the logical estimate: abort BEFORE a split could cross the page ceiling.
        if ((allocated - initialPages) * 4_096 + rounded(record) + 1_048_576 + markerAllocation > ceiling) exceeded()
    }
    fun after(): Unit {
        val growth = (sql.number("PRAGMA page_count") - initialPages) * 4_096
        if (growth > ceiling) exceeded()
        // Never let optimistic packing estimates credit back already allocated physical pages.
        charged = maxOf(charged, growth + markerAllocation)
        if (charged > ceiling) exceeded()
    }
}

/** Only used by the existing serial connection owner, inside its transaction/lifetime. */
internal class AccountPageSql(private val snapshotUncertain: () -> Unit = {}, private val connection: () -> SQLiteConnection) {
    private var budget: AccountPageMutationBudget? = null
    /** Called only by the serialized J owner inside its actual authority capture. No writes. */
    fun <T> readSnapshot(action: () -> T): T {
        check(budget == null)
        connection().execSQL("BEGIN DEFERRED")
        try {
            val value = action()
            connection().execSQL("COMMIT")
            return value
        } catch (failure: Throwable) {
            try { connection().execSQL("ROLLBACK") } catch (cleanup: Throwable) {
                snapshotUncertain(); failure.addSuppressed(cleanup)
            }
            throw failure
        }
    }
    fun <T> budgeted(ceiling: Long, markerBytes: Int, action: () -> T): T {
        check(budget == null)
        val ledger = AccountPageMutationBudget(this, ceiling)
        budget = ledger
        try { ledger.marker(markerBytes); return action() } finally { budget = null }
    }
    fun mutate(table: String, keys: List<Any?>, sql: String, vararg values: Any?): Unit {
        val ledger = budget ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        ledger.before(table, keys, values)
        execute(sql, *values)
        ledger.after()
    }
    fun commitMarker(sql: String, vararg values: Any?): Unit {
        val ledger = budget ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        ledger.beforeMarker()
        execute(sql, *values)
        ledger.after()
    }
    private fun execute(sql: String, vararg values: Any?): Unit = connection().prepare(sql).use {
        bind(it, values)
        if (it.step()) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }
    fun <T> query(sql: String, vararg values: Any?, read: (SQLiteStatement) -> T): List<T> = connection().prepare(sql).use {
        budget?.visit()
        bind(it, values)
        val rows = ArrayList<T>()
        while (it.step()) {
            budget?.visit()
            if (budget != null && rows.size >= 512) throw AccountPageDeferred("PAGE_MUTATION_CAPACITY")
            rows.add(read(it))
        }
        rows
    }
    fun number(sql: String, vararg values: Any?): Long = query(sql, *values) { it.getLong(0) }.single()
    fun text(sql: String, vararg values: Any?): String? = query(sql, *values) { nullable(it, 0) }.singleOrNull()
    fun nullable(statement: SQLiteStatement, column: Int): String? = if (statement.isNull(column)) null else statement.getText(column)
    private fun bind(statement: SQLiteStatement, values: Array<out Any?>): Unit {
        values.forEachIndexed { index, value -> when (value) {
            null -> statement.bindNull(index + 1)
            is AccountUuid -> statement.bindText(index + 1, value.value)
            is String -> statement.bindText(index + 1, value)
            is ByteArray -> statement.bindBlob(index + 1, value)
            is Long -> statement.bindLong(index + 1, value)
            is Int -> statement.bindLong(index + 1, value.toLong())
            is Double -> statement.bindDouble(index + 1, value)
            else -> fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        } }
    }
}

/** Six actual v1 families. Unknown catalog/metadata/interaction/queue semantics defer the whole page. */
internal class AccountProjectionWriter(
    private val sql: AccountPageSql,
    private val scope: AccountUuid,
    private val profile: AccountUuid,
    private val lineage: AccountUuid,
    private val source: AccountUuid,
    private val snapshotId: AccountUuid? = null,
) {
    private data class Table(val name: String, val key: String)
    private val tables = mapOf(
        "USER_TRACK_REF" to Table("j_track_ref", "track_ref_id"),
        "LIBRARY_ENTRY" to Table("j_library_entry", "entry_id"),
        "USER_TRACK_PREFERENCE" to Table("j_preference", "preference_id"),
        "PLAYLIST" to Table("j_playlist", "playlist_id"),
        "PLAYLIST_ENTRY" to Table("j_playlist_entry", "entry_id"),
        "LISTENING_EVENT" to Table("j_listening", "listening_id"),
        "USER_INTERACTION_EVENT" to Table("j_user_interaction", "interaction_id"),
    )
    private val events = mapOf(
        "USER_TRACK_REF_CREATED" to "USER_TRACK_REF", "USER_TRACK_REF_PATCHED" to "USER_TRACK_REF",
        "LIBRARY_ENTRY_UPSERTED" to "LIBRARY_ENTRY", "USER_TRACK_PREFERENCE_SET" to "USER_TRACK_PREFERENCE",
        "PLAYLIST_CREATED" to "PLAYLIST", "PLAYLIST_METADATA_PATCHED" to "PLAYLIST",
        "PLAYLIST_ENTRY_UPSERTED" to "PLAYLIST_ENTRY", "PLAYLIST_ENTRY_MOVED" to "PLAYLIST_ENTRY",
        "LISTENING_EVENT_RECORDED" to "LISTENING_EVENT",
        "RECOMMENDATION_IMPRESSION_RECORDED" to "USER_INTERACTION_EVENT", "RECOMMENDATION_FEEDBACK_RECORDED" to "USER_INTERACTION_EVENT",
    )
    private fun defer(reason: String): Nothing = throw AccountPageDeferred(reason)
    private fun table(type: String): Table = tables[type] ?: defer("UNSUPPORTED_FAMILY")
    private fun exists(type: String, local: AccountUuid): Boolean {
        if (type == "LIBRARY_ENTRY") {
            val count = sql.number("""SELECT (SELECT count(*) FROM j_library_entry WHERE scope_id=? AND entry_id=?) +
                (SELECT count(*) FROM j_library_relation WHERE scope_id=? AND entry_id=?)""", scope, local, scope, local)
            if (count > 1) defer("CONTRADICTORY_LIBRARY_ID")
            return count == 1L
        }
        val table = table(type)
        return sql.number("SELECT count(*) FROM ${table.name} WHERE scope_id=? AND ${table.key}=?", scope, local) == 1L
    }
    private fun mapped(type: String, server: AccountUuid): AccountUuid? = sql.text(
        "SELECT local_id FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND server_id=?", scope, type, server)?.let(::AccountUuid)

    fun dirty(type: String, local: AccountUuid): Boolean = sql.number("""SELECT count(*) FROM j_event e
        JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
        WHERE e.scope_id=? AND e.aggregate_type=? AND e.aggregate_local_id=? AND d.state<>'ACKNOWLEDGED'
        AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.source_id
            WHERE r.scope_id=e.scope_id AND r.source_id=e.event_id AND a.terminal=1)""",
        scope, type, local) > 0 || sql.number("""SELECT count(*) FROM j_local_intent i
        WHERE scope_id=? AND aggregate_type=? AND aggregate_local_id=? AND materialized_event_id IS NULL
        AND NOT EXISTS(SELECT 1 FROM j_account_intent_link m WHERE m.source_scope_id=i.scope_id AND m.change_id=i.change_id)""", scope, type, local) > 0

    private fun pendingBase(type: String, local: AccountUuid): Long? = sql.query("""SELECT e.canonical_bytes FROM j_event e
        JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id WHERE e.scope_id=?
        AND e.aggregate_type=? AND e.aggregate_local_id=? AND d.state<>'ACKNOWLEDGED'
        AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.source_id
            WHERE r.scope_id=e.scope_id AND r.source_id=e.event_id AND a.terminal=1) ORDER BY e.sequence LIMIT 1""",
        scope, type, local) { AccountEventV1.fromCanonical(it.getBlob(0)).baseServerRowVersion }.singleOrNull()

    fun conflict(type: String, local: AccountUuid, server: AccountUuid?, kind: String, evidence: JsonObject,
        version: Long?, base: Long? = null, resolution: String = "REVIEW_REQUIRED", sourceId: AccountUuid = source): Unit {
        val bytes = evidence.toString().toByteArray(Charsets.UTF_8)
        val existing = sql.query("""SELECT kind,resolution_state,safe_evidence FROM j_conflict
            WHERE scope_id=? AND profile_id=? AND source_id=? AND aggregate_type=? AND local_id=?""",
            scope, profile, sourceId, type, local) { Triple(it.getText(0), it.getText(1), it.getBlob(2)) }.singleOrNull()
        if (existing != null) {
            if (existing.first != kind || existing.second != resolution || !existing.third.contentEquals(bytes)) defer("CONTRADICTORY_CONFLICT")
            return
        }
        sql.mutate("j_conflict", listOf(scope, profile, sourceId, type, local), """INSERT INTO j_conflict(scope_id,profile_id,source_id,aggregate_type,local_id,server_id,
            kind,resolution_state,base_version,observed_version,safe_evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            scope, profile, sourceId, type, local, server, kind, resolution, base, version, bytes)
    }

    /** Binds IDs/observed versions, never rewrites an immutable event or unrelated dirty fields. */
    fun acknowledge(event: AccountEventV1, ack: AccountWireAck): Unit {
        val server = ack.server ?: defer("ACK_MAPPING_MISSING")
        val local = event.aggregateLocalId
        if (!exists(ack.type, local)) defer("ACK_PROJECTION_MISSING")
        val other = mapped(ack.type, server)
        if (other != null && other != local) defer("ACK_ID_COLLISION")
        val prior = sql.text("SELECT server_id FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?",
            scope, ack.type, local)
        if (prior != null && prior != server.value) defer("ACK_ID_COLLISION")
        bind(ack.type, local, server, ack.version, "COMPLETE")
        if (ack.outcome == "APPLIED" && ack.effective == "APPLIED") {
            // No bootstrap can run during this exact delivery lease. A newly APPLIED create
            // remains visible after the older snapshot; an old DUPLICATE cannot resurrect it.
            sql.mutate("j_projection_state", listOf(scope, ack.type, local), """UPDATE j_projection_state SET ack_snapshot=(SELECT snapshot_id FROM j_snapshot_head WHERE scope_id=?)
                WHERE scope_id=? AND aggregate_type=? AND local_id=?""", scope, scope, ack.type, local)
        }
        if (ack.type == "USER_TRACK_REF") {
            val old = sql.text("SELECT server_track_ref_id FROM j_track_ref WHERE scope_id=? AND track_ref_id=?", scope, local)
            if (old != null && old != server.value) defer("ACK_ID_COLLISION")
            sql.mutate("j_track_ref", listOf(scope, local), """UPDATE j_track_ref SET server_track_ref_id=?,observed_version=CASE
                WHEN observed_version IS NULL THEN ? ELSE max(observed_version,coalesce(?,observed_version)) END
                WHERE scope_id=? AND track_ref_id=?""", server, ack.version, ack.version, scope, local)
        }
        ack.redirect?.let { redirect(it, null) }
        AccountMediaProjectionFence.changed(sql, scope, local, ack.type)
    }

    private fun bind(type: String, local: AccountUuid, server: AccountUuid, version: Long?, state: String, sequence: Long? = null): Unit {
        val prior = sql.query("SELECT server_id,state FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?", scope, type, local) {
            it.getText(0) to it.getText(1)
        }.singleOrNull()
        if (prior != null && (prior.first != server.value || prior.second !in setOf("COMPLETE", "DEPENDENCY"))) defer("UNKNOWN_OR_CONTRADICTORY_PROJECTION")
        sql.mutate("j_projection_state", listOf(scope, type, local), """INSERT INTO j_projection_state(scope_id,aggregate_type,local_id,server_id,profile_id,observed_version,state,source_sequence)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,aggregate_type,local_id) DO UPDATE SET
            observed_version=CASE WHEN observed_version IS NULL THEN excluded.observed_version
                ELSE max(observed_version,coalesce(excluded.observed_version,observed_version)) END,
            source_sequence=CASE WHEN source_sequence IS NULL THEN excluded.source_sequence
                ELSE max(source_sequence,coalesce(excluded.source_sequence,source_sequence)) END,
            state=CASE WHEN state='DEPENDENCY' THEN excluded.state ELSE state END""", scope, type, local, server, profile, version, state, sequence)
        val related = sql.query("""SELECT alias_server_id,canonical_server_id FROM j_redirect WHERE scope_id=?
            AND aggregate_type=? AND (alias_server_id=? OR canonical_server_id=?) LIMIT 101""", scope, type, server, server) {
            AccountUuid(it.getText(0)) to AccountUuid(it.getText(1))
        }
        if (related.size > 100) defer("REDIRECT_FAN_IN_CAPACITY")
        for ((alias, canonical) in related) {
            val aliasLocal = mapped(type, alias); val canonicalLocal = mapped(type, canonical)
            if (aliasLocal != null && canonicalLocal != null && aliasLocal != canonicalLocal) conflict(type, aliasLocal, canonical,
                "REDIRECT_COLLISION", JsonObject(mapOf("alias_server_id" to JsonPrimitive(alias.value),
                    "canonical_server_id" to JsonPrimitive(canonical.value))), version)
        }
    }

    /** Exact client-create evidence closes lost-create-ACK bootstrap adoption, including nested library. */
    private fun proposed(type: String, local: AccountUuid): Boolean {
        val bytes = if (type == "LIBRARY_ENTRY") sql.query("""SELECT e.canonical_bytes FROM j_library_entry l
            JOIN j_track_ref t ON t.scope_id=l.scope_id AND t.track_ref_id=l.track_ref_id
            JOIN j_event e ON e.scope_id=t.scope_id AND e.event_id=t.operation_id
            WHERE l.scope_id=? AND l.entry_id=? AND e.lineage_id=? AND e.event_type='USER_TRACK_REF_CREATED' LIMIT 2""",
            scope, local, lineage) { it.getBlob(0) }.singleOrNull()
        else sql.query("""SELECT canonical_bytes FROM j_event WHERE scope_id=? AND lineage_id=?
            AND aggregate_type=? AND aggregate_local_id=? AND event_type IN ('USER_TRACK_REF_CREATED','PLAYLIST_CREATED',
                'LIBRARY_ENTRY_UPSERTED','PLAYLIST_ENTRY_UPSERTED') ORDER BY sequence LIMIT 1""", scope, lineage, type, local) { it.getBlob(0) }.singleOrNull()
        if (bytes == null) return false
        val event = AccountEventV1.fromCanonical(bytes)
        if (type == "LIBRARY_ENTRY" && event.aggregateType == "USER_TRACK_REF") {
            val doc = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as JsonObject
            return doc.obj("payload").optionalId("library_entry_local_id") == local
        }
        return event.aggregateType == type && event.aggregateLocalId == local
    }

    private fun local(type: String, server: AccountUuid): AccountUuid {
        mapped(type, server)?.let { return it }
        val candidate = if (exists(type, server)) {
            if (!proposed(type, server)) defer("ID_COLLISION")
            server
        } else AccountUuid.random()
        bind(type, candidate, server, null, "DEPENDENCY")
        return candidate
    }

    private fun trackDependency(server: AccountUuid): AccountUuid {
        val local = local("USER_TRACK_REF", server)
        if (!exists("USER_TRACK_REF", local)) {
            // Explicit DEPENDENCY and null flags: empty foundation fields are never claimed metadata.
            sql.mutate("j_track_ref", listOf(scope, local), """INSERT INTO j_track_ref(scope_id,track_ref_id,operation_id,profile_id,title,artist,
                occurred_at_ms,server_track_ref_id) VALUES(?,?,?,?,? ,?,0,?)""", scope, local, AccountUuid.random(), profile, "", "", server)
            sql.mutate("j_track_detail", listOf(scope, local), "INSERT INTO j_track_detail(scope_id,track_ref_id,title_is_null,artist_is_null) VALUES(?,?,1,1)", scope, local)
        }
        return local
    }
    private fun playlistDependency(server: AccountUuid): AccountUuid {
        val local = local("PLAYLIST", server)
        if (!exists("PLAYLIST", local)) sql.mutate("j_playlist", listOf(scope, local), "INSERT INTO j_playlist(scope_id,playlist_id) VALUES(?,?)", scope, local)
        return local
    }

    fun apply(change: AccountWireChange): Unit {
        if (change.schema != 1L) defer("UNSUPPORTED_SCHEMA_VERSION")
        if (change.type in AccountCatalogProjectionWriter.families) {
            AccountCatalogProjectionWriter.apply(sql, scope, snapshotId, change)
            return
        }
        if (change.eventType != null) {
            val expected = events[change.eventType]
            if (change.operation == "UPSERT" && expected != change.type) defer("UNSUPPORTED_EVENT_TYPE")
            if (change.operation == "DELETE" && change.eventType !in setOf("AGGREGATE_DELETED", "AGGREGATE_TOMBSTONED")) defer("UNSUPPORTED_EVENT_TYPE")
            if (change.operation == "REDIRECT" && change.eventType !in setOf("AGGREGATE_REDIRECT", "AGGREGATE_REDIRECTED")) defer("UNSUPPORTED_EVENT_TYPE")
        }
        if (change.operation == "REDIRECT") {
            val value = change.redirect ?: defer("REDIRECT_MISSING")
            if (value.alias != change.server) defer("REDIRECT_ID_MISMATCH")
            redirect(value, change.sequence)
            return
        }
        table(change.type)
        if (change.operation == "DELETE") {
            if (change.type !in setOf("USER_TRACK_REF", "LIBRARY_ENTRY", "PLAYLIST", "PLAYLIST_ENTRY")) defer("UNSUPPORTED_DELETE_FAMILY")
            tombstone(change)
            return
        }
        if (change.operation != "UPSERT" || change.version == null) defer("UNSUPPORTED_OPERATION")
        // Actual sync.py bootstrap publishes no preference/listening aggregate snapshots.
        if (snapshotId != null && change.type in setOf("USER_TRACK_PREFERENCE", "LISTENING_EVENT","USER_INTERACTION_EVENT")) defer("UNSUPPORTED_SNAPSHOT_FAMILY")
        if(change.type in setOf("LISTENING_EVENT","USER_INTERACTION_EVENT") && change.version!=1L)defer("UNSUPPORTED_APPEND_ONLY_VERSION")
        if(change.type=="USER_INTERACTION_EVENT" && change.event!=change.server)defer("INTERACTION_EVENT_ID_MISMATCH")
        if(change.type=="USER_INTERACTION_EVENT")mapped(change.type,change.server)?.takeIf{exists(change.type,it)}?.let{
            AccountInteractionProjectionWriter.persist(sql,scope,it,change.original.nullId("origin_device_id"),change.payload,change.createdAt)
            AccountCatalogProjectionWriter.attribution(sql,scope,change.event?:source,change.type,it,change.payload.obj("recommendation"))
        }
        if(change.type=="LISTENING_EVENT" && change.event==change.server && change.createdAt!=null)mapped(change.type,change.server)?.takeIf{exists(change.type,it)}?.let{
            if(sql.number("SELECT played_ms FROM j_listening WHERE scope_id=? AND listening_id=?",scope,it)!=change.payload.number("played_ms",0,604_800_000))defer("LISTENING_IDENTITY_CONFLICT")
            sql.mutate("j_listening",listOf(scope,it),"UPDATE j_listening SET source_created_at=? WHERE scope_id=? AND listening_id=? AND source_created_at=''",change.createdAt,scope,it)
            change.payload["recommendation"]?.takeIf{value->value!=JsonNull}?.let{value->AccountCatalogProjectionWriter.attribution(sql,scope,change.event?:source,change.type,it,value.jsonObject)}
        }
        if (change.type == "USER_TRACK_REF" && "metadata_v1" in change.payload) {
            // Metadata has its own revision; a publication can retain the UTR row version.
            mapped(change.type, change.server)?.takeIf { exists(change.type, it) }?.let {
                AccountCatalogProjectionWriter.metadata(sql, scope, it, change.payload.obj("metadata_v1"))
            }
        }
        // sync.py clears removed_at for BOTH omitted and explicit-null fields on an existing
        // versioned library upsert. Non-null marks removal; neither form erases old tombstones.
        val libraryUpsert = change.type == "LIBRARY_ENTRY" && change.eventType == "LIBRARY_ENTRY_UPSERTED"
        val restoring = snapshotId != null || (libraryUpsert &&
            ("removed_at_ms" !in change.payload || change.payload["removed_at_ms"] == JsonNull))
        val prior = sql.query("""SELECT local_id,observed_version,effective_deleted FROM j_account_projection_visibility
            WHERE scope_id=? AND aggregate_type=? AND server_id=?""", scope, change.type, change.server) {
            Triple(AccountUuid(it.getText(0)), if (it.isNull(1)) null else it.getLong(1), it.getLong(2))
        }.singleOrNull()
        val priorVersion = prior?.second
        // Obsolete events cannot restore OR manufacture a new conflict from retained delete evidence.
        // Do not stamp their sequence: that could falsely confirm a row absent from snapshot membership.
        if (priorVersion != null && (change.version < priorVersion ||
                (change.version == priorVersion && snapshotId == null && !(checkNotNull(prior).third == 1L && restoring)))) {
            return
        }
        if (prior != null && prior.third == 1L && restoring && change.version == priorVersion) defer("RESTORE_VERSION_CONTRADICTION")
        val knownLive = sql.number("""SELECT count(*) FROM j_account_projection_visibility WHERE scope_id=? AND aggregate_type=?
            AND server_id=? AND snapshot_confirmed=1 AND effective_deleted=0""", scope, change.type, change.server) != 0L
        if (!restoring && !libraryUpsert && !knownLive && sql.number("SELECT count(*) FROM j_tombstone WHERE scope_id=? AND aggregate_type=? AND server_id=?",
                scope, change.type, change.server) != 0L) {
            conflict(change.type, mapped(change.type, change.server) ?: change.server, change.server,
                "EDIT_VS_DELETE", change.original, change.version, sourceId = change.event ?: source)
            return
        }
        val local = local(change.type, change.server)
        change.payload["attribution"]?.takeIf { it != JsonNull }?.let {
            AccountCatalogProjectionWriter.attribution(sql, scope, change.event ?: source, change.type, local, it.jsonObject)
        }
        if (dirty(change.type, local)) {
            conflict(change.type, local, change.server, "STALE_VERSION", change.original, change.version,
                base = pendingBase(change.type, local), sourceId = change.event ?: source)
            if (exists(change.type, local)) {
                bind(change.type, local, change.server, change.version, "COMPLETE", change.sequence)
                if (change.type == "USER_TRACK_REF") sql.mutate("j_track_ref", listOf(scope, local), """UPDATE j_track_ref SET server_track_ref_id=?,
                    observed_version=CASE WHEN observed_version IS NULL THEN ? ELSE max(observed_version,?) END
                    WHERE scope_id=? AND track_ref_id=?""", change.server, change.version, change.version, scope, local)
                AccountMediaProjectionFence.changed(sql, scope, local, change.type)
            }
            return
        }
        val version = sql.query("""SELECT observed_version,deleted,state FROM j_projection_state
            WHERE scope_id=? AND aggregate_type=? AND local_id=?""", scope, change.type, local) {
            Triple(if (it.isNull(0)) null else it.getLong(0), it.getLong(1), it.getText(2))
        }.single()
        if (version.second == 1L && !restoring && !libraryUpsert) {
            conflict(change.type, local, change.server, "EDIT_VS_DELETE", change.original, change.version,
                sourceId = change.event ?: source)
            return
        }
        if (restoring && version.second == 1L) sql.mutate("j_projection_state", listOf(scope, change.type, local), "UPDATE j_projection_state SET deleted=0 WHERE scope_id=? AND aggregate_type=? AND local_id=?", scope, change.type, local)
        when (change.type) {
            "USER_TRACK_REF" -> track(change, local, version.third)
            "LIBRARY_ENTRY" -> library(change, local)
            "PLAYLIST" -> playlist(change, local, version.third)
            "PLAYLIST_ENTRY" -> entry(change, local)
            "USER_TRACK_PREFERENCE" -> preference(change, local)
            "LISTENING_EVENT" -> listening(change, local)
            "USER_INTERACTION_EVENT" -> {
                AccountInteractionProjectionWriter.persist(sql,scope,local,change.original.nullId("origin_device_id"),change.payload,change.createdAt)
                AccountCatalogProjectionWriter.attribution(sql,scope,change.event?:source,change.type,local,change.payload.obj("recommendation"))
            }
        }
        bind(change.type, local, change.server, change.version, "COMPLETE", change.sequence)
        sql.mutate("j_domain_state", listOf(scope, change.type, local), """UPDATE j_domain_state SET removed=
            (SELECT deleted FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?)
            WHERE scope_id=? AND aggregate_type=? AND local_id=?""", scope, change.type, local, scope, change.type, local)
        AccountMediaProjectionFence.changed(sql, scope, local, change.type)
    }

    private fun nullableText(payload: JsonObject, key: String, max: Int = DesktopAccountLimits.PAYLOAD_BYTES): String? =
        if (payload[key] == JsonNull) null else payload.string(key, max, 0)
    private fun patchText(payload: JsonObject, key: String, old: String?): String? =
        if (key in payload) nullableText(payload, key) else old

    private fun track(change: AccountWireChange, local: AccountUuid, state: String): Unit {
        val p = change.payload
        if (change.eventType == "USER_TRACK_REF_PATCHED" && state == "DEPENDENCY") defer("MISSING_TRACK_BASE")
        trackDependency(change.server)
        val old = sql.query("""SELECT t.title,t.artist,d.title_is_null,d.artist_is_null,d.album,d.duration_ms,
            d.recording_id,d.resolution_status FROM j_track_ref t LEFT JOIN j_track_detail d
            ON d.scope_id=t.scope_id AND d.track_ref_id=t.track_ref_id WHERE t.scope_id=? AND t.track_ref_id=?""", scope, local) {
            listOf(if (!it.isNull(2) && it.getLong(2) == 1L) null else it.getText(0),
                if (!it.isNull(3) && it.getLong(3) == 1L) null else it.getText(1), sql.nullable(it, 4),
                if (it.isNull(5)) null else it.getLong(5).toString(), sql.nullable(it, 6), sql.nullable(it, 7))
        }.single()
        val title = patchText(p, "title", old[0]); val artist = patchText(p, "artist", old[1])
        val album = patchText(p, "album", old[2])
        val duration = if ("duration_ms" in p) p.nullNumber("duration_ms", 0) else old[3]?.toLong()
        val recording = if ("recording_id" in p) p.nullId("recording_id")?.value else old[4]
        val resolution = if ("resolution_status" in p) p.string("resolution_status", 100) else old[5]
        sql.mutate("j_track_ref", listOf(scope, local), "UPDATE j_track_ref SET title=?,artist=?,server_track_ref_id=?,observed_version=? WHERE scope_id=? AND track_ref_id=?",
            title ?: "", artist ?: "", change.server, change.version, scope, local)
        sql.mutate("j_track_detail", listOf(scope, local), """INSERT INTO j_track_detail(scope_id,track_ref_id,title_is_null,artist_is_null,album,duration_ms,recording_id,resolution_status)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,track_ref_id) DO UPDATE SET title_is_null=excluded.title_is_null,
            artist_is_null=excluded.artist_is_null,album=excluded.album,duration_ms=excluded.duration_ms,
            recording_id=excluded.recording_id,resolution_status=excluded.resolution_status""", scope, local,
            if (title == null) 1 else 0, if (artist == null) 1 else 0, album, duration, recording, resolution)
        // This is the originating client's requested nested ID. sync.py can skip that nested
        // insert when the ID already exists; only an actual LIBRARY_ENTRY projection confirms it.
        // Keep its exact bytes in the page record instead of manufacturing a remote relation/version.
        p.optionalId("library_entry_local_id")
        if ("metadata_v1" in p) AccountCatalogProjectionWriter.metadata(sql, scope, local, p.obj("metadata_v1"))
    }
    private fun ref(p: JsonObject, vararg keys: String): AccountUuid? {
        val values = keys.filter { it in p }.mapNotNull { p.nullId(it) }.distinct()
        if (values.size > 1) defer("CONTRADICTORY_DEPENDENCY")
        return values.singleOrNull()
    }
    private fun library(change: AccountWireChange, local: AccountUuid): Unit {
        val p = change.payload
        val previous = sql.text("""SELECT track_ref_id FROM j_library_entry WHERE scope_id=? AND entry_id=?
            UNION ALL SELECT track_ref_id FROM j_library_relation WHERE scope_id=? AND entry_id=?""", scope, local, scope, local)?.let(::AccountUuid)
        val server = ref(p, "server_user_track_ref_id", "user_track_ref_id", "local_user_track_ref_id")
        val track = server?.let(::trackDependency) ?: previous ?: defer("MISSING_TRACK_DEPENDENCY")
        if (previous != null && previous != track) defer("CHANGED_TRACK_DEPENDENCY")
        if (previous == null) sql.mutate("j_library_relation", listOf(scope, local), "INSERT INTO j_library_relation(scope_id,entry_id,track_ref_id) VALUES(?,?,?)", scope, local, track)
        val removed = if ("removed_at_ms" in p) p.nullNumber("removed_at_ms", 0) else null
        // sync.py's initial (row_version=1) INSERT ignores removed_at_ms; only an existing
        // versioned upsert uses it to mark removal. Omitted/null existing upserts clear above.
        if (removed != null && change.version != 1L) sql.mutate("j_projection_state", listOf(scope, "LIBRARY_ENTRY", local), "UPDATE j_projection_state SET deleted=1 WHERE scope_id=? AND aggregate_type='LIBRARY_ENTRY' AND local_id=?", scope, local)
        val old = sql.query("SELECT source,availability_status FROM j_library_detail WHERE scope_id=? AND entry_id=?", scope, local) {
            sql.nullable(it, 0) to sql.nullable(it, 1)
        }.singleOrNull()
        sql.mutate("j_library_detail", listOf(scope, local), """INSERT INTO j_library_detail(scope_id,entry_id,source,availability_status) VALUES(?,?,?,?)
            ON CONFLICT(scope_id,entry_id) DO UPDATE SET source=excluded.source,availability_status=excluded.availability_status""", scope, local,
            if ("source" in p) p.string("source", 100) else old?.first,
            if ("availability_status" in p) p.string("availability_status", 100) else old?.second)
    }
    private fun playlist(change: AccountWireChange, local: AccountUuid, state: String): Unit {
        val p = change.payload
        if (change.eventType == "PLAYLIST_METADATA_PATCHED" && state == "DEPENDENCY") defer("MISSING_PLAYLIST_BASE")
        val old = sql.query("SELECT name,description,visibility,playlist_type FROM j_playlist WHERE scope_id=? AND playlist_id=?", scope, local) {
            (0..3).map { column -> sql.nullable(it, column) }
        }.singleOrNull()
        val name = if ("name" in p) p.string("name", 500) else old?.get(0) ?: defer("PLAYLIST_NAME_MISSING")
        sql.mutate("j_playlist", listOf(scope, local), """INSERT INTO j_playlist(scope_id,playlist_id,name,description,visibility,playlist_type) VALUES(?,?,?,?,?,?)
            ON CONFLICT(scope_id,playlist_id) DO UPDATE SET name=excluded.name,description=excluded.description,
            visibility=excluded.visibility,playlist_type=excluded.playlist_type""", scope, local, name,
            patchText(p, "description", old?.get(1)), if ("visibility" in p) p.string("visibility", 100) else old?.get(2),
            if ("playlist_type" in p) p.string("playlist_type", 100) else old?.get(3))
    }
    private fun entry(change: AccountWireChange, local: AccountUuid): Unit {
        val p = change.payload
        // Server publishes these canonical dependencies, even when client request used local IDs.
        val playlist = playlistDependency(p.id("server_playlist_id"))
        val track = trackDependency(p.id("server_user_track_ref_id"))
        val position = p.string("position_key", 128)
        val prior = sql.query("SELECT playlist_id,track_ref_id FROM j_playlist_entry WHERE scope_id=? AND entry_id=?", scope, local) {
            it.getText(0) to it.getText(1)
        }.singleOrNull()
        if (prior != null && prior != (playlist.value to track.value)) defer("CHANGED_ENTRY_DEPENDENCY")
        sql.mutate("j_playlist_entry", listOf(scope, local), """INSERT INTO j_playlist_entry(scope_id,entry_id,playlist_id,track_ref_id,position_key) VALUES(?,?,?,?,?)
            ON CONFLICT(scope_id,entry_id) DO UPDATE SET position_key=excluded.position_key""", scope, local, playlist, track, position)
    }
    private fun preference(change: AccountWireChange, local: AccountUuid): Unit {
        val p = change.payload
        // Canonical preference aggregate identity IS its canonical UTR (sync.py _resolve_track_ref).
        val explicit = p.optionalId("server_user_track_ref_id")
        if (explicit != null && explicit != change.server) defer("PREFERENCE_ID_MISMATCH")
        if (explicit == null && mapped("USER_TRACK_REF", change.server) == null && !proposed("USER_TRACK_REF", change.server)) defer("UNRESOLVED_PREFERENCE_TRACK")
        val track = trackDependency(change.server)
        val value = p.string("preference", 100)
        if (value !in setOf("NEUTRAL", "LIKED", "DISLIKED")) defer("UNSUPPORTED_ENUM_VALUE")
        sql.mutate("j_preference", listOf(scope, local), """INSERT INTO j_preference(scope_id,preference_id,track_ref_id,preference,excluded_from_taste) VALUES(?,?,?,?,?)
            ON CONFLICT(scope_id,preference_id) DO UPDATE SET preference=excluded.preference,excluded_from_taste=excluded.excluded_from_taste""",
            scope, local, track, value, if (p.bool("excluded_from_taste")) 1 else 0)
    }
    private fun listening(change: AccountWireChange, local: AccountUuid): Unit {
        val p = change.payload
        if (change.event != change.server) defer("LISTENING_EVENT_ID_MISMATCH")
        if (p.string("interaction_type", 100) != "LISTENING_EVENT_RECORDED") defer("UNSUPPORTED_INTERACTION")
        val sourceLocal = p.id("local_user_track_ref_id")
        val server = p.nullId("server_user_track_ref_id") ?: if (exists("USER_TRACK_REF", sourceLocal) &&
            proposed("USER_TRACK_REF", sourceLocal)) sourceLocal else defer("UNRESOLVED_LISTENING_TRACK")
        val track = trackDependency(server)
        val origin = p.string("event_origin", 100); val context = p.string("context", 50); val feedback = p.string("explicit_feedback", 50)
        if (origin !in setOf("ORGANIC", "RECOMMENDED", "PLAYLIST", "SEARCH", "WAVE") ||
            context !in setOf("GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY") || feedback !in setOf("NONE", "LIKE", "DISLIKE")) defer("UNSUPPORTED_ENUM_VALUE")
        AccountDomainWire.listening(p)
        val recommendation = p["recommendation"]?.takeIf { it != JsonNull }?.jsonObject
        recommendation?.let { AccountCatalogProjectionWriter.attribution(sql, scope, change.event ?: source, change.type, local, it) }
        val ratio = if (p["completion_ratio"] == JsonNull) null else {
            val value = p["completion_ratio"] as? JsonPrimitive ?: defer("INVALID_LISTENING_RATIO")
            value.doubleOrNull?.takeIf { !value.isString && it.isFinite() && it in 0.0..1.0 } ?: defer("INVALID_LISTENING_RATIO")
        }
        val duration = if (p["track_duration_ms"] == JsonNull) null else p.number("track_duration_ms", 1, 604_800_000)
        sql.mutate("j_listening", listOf(scope, local), """INSERT INTO j_listening(scope_id,listening_id,track_ref_id,source_local_track_id,recording_id,played_ms,
            duration_ms,completion_ratio,event_origin,context,explicit_feedback,excluded_from_taste,recommendation,source_created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", scope, local, track, sourceLocal, p.nullId("recording_id"),
            p.number("played_ms", 0, 604_800_000), duration, ratio, origin, context, feedback,
            if (p.bool("excluded_from_taste")) 1 else 0, recommendation?.let(AccountPortableV1::canonical), change.createdAt ?: defer("LISTENING_TIME_MISSING"))
    }

    private fun tombstone(change: AccountWireChange): Unit {
        val value = change.tombstone ?: defer("TOMBSTONE_MISSING")
        val prior = sql.query("""SELECT aggregate_type,server_id,deleted_by_event_id,deleted_at,retain_until
            FROM j_tombstone WHERE scope_id=? AND tombstone_id=?""", scope, value.id) { (0..4).map(it::getText) }.singleOrNull()
        val expected = listOf(value.type, value.server.value, value.deletedBy.value, value.deletedAt, value.retainUntil)
        if (prior != null && prior != expected) defer("CONTRADICTORY_TOMBSTONE")
        if (prior == null) sql.mutate("j_tombstone", listOf(scope, value.id), """INSERT INTO j_tombstone(scope_id,tombstone_id,aggregate_type,server_id,
            deleted_by_event_id,deleted_at,retain_until) VALUES(?,?,?,?,?,?,?)""", scope, value.id, value.type, value.server,
            value.deletedBy, value.deletedAt, value.retainUntil)
        val local = mapped(change.type, change.server) ?: return
        // A fixed snapshot may carry a retained historical tombstone AND a live restored row.
        // Its membership/head decides visibility at final cutover, independent of page ordering.
        val state = sql.text("SELECT state FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?", scope, change.type, local)
        if (state !in setOf("COMPLETE", "DEPENDENCY")) defer("UNKNOWN_PROJECTION_STATE")
        if (snapshotId != null) {
            val liveAlreadySeen = sql.number("""SELECT count(*) FROM j_bootstrap_member WHERE scope_id=? AND profile_id=?
                AND snapshot_id=? AND aggregate_type=? AND server_id=?""", scope, profile, snapshotId, change.type, change.server) != 0L
            if (!liveAlreadySeen && dirty(change.type, local)) conflict(change.type, local, change.server, "DELETE_VS_EDIT", change.original,
                change.version, base = pendingBase(change.type, local), sourceId = change.event ?: source)
            return
        }
        sql.mutate("j_projection_state", listOf(scope, change.type, local), """UPDATE j_projection_state SET observed_version=CASE WHEN observed_version IS NULL THEN ?
            ELSE max(observed_version,coalesce(?,observed_version)) END,source_sequence=CASE WHEN source_sequence IS NULL THEN ?
            ELSE max(source_sequence,coalesce(?,source_sequence)) END WHERE scope_id=? AND aggregate_type=? AND local_id=?""",
            change.version, change.version, change.sequence, change.sequence, scope, change.type, local)
        if (dirty(change.type, local)) conflict(change.type, local, change.server, "DELETE_VS_EDIT", change.original,
            change.version, base = pendingBase(change.type, local), sourceId = change.event ?: source)
        else sql.mutate("j_projection_state", listOf(scope, change.type, local), "UPDATE j_projection_state SET deleted=1 WHERE scope_id=? AND aggregate_type=? AND local_id=?", scope, change.type, local)
        AccountMediaProjectionFence.changed(sql, scope, local, change.type)
    }

    fun redirect(value: AccountWireRedirect, sequence: Long?): Unit {
        if (value.type != "RECORDING") table(value.type)
        var cursor = value.canonical
        val visited = hashSetOf(value.alias)
        repeat(100) {
            if (!visited.add(cursor)) defer("REDIRECT_CYCLE")
            val next = sql.text("SELECT canonical_server_id FROM j_redirect WHERE scope_id=? AND aggregate_type=? AND alias_server_id=?",
                scope, value.type, cursor)
            if (next == null) {
                val previous = sql.text("SELECT canonical_server_id FROM j_redirect WHERE scope_id=? AND aggregate_type=? AND alias_server_id=?",
                    scope, value.type, value.alias)
                if (previous != null && previous != value.canonical.value) defer("CONTRADICTORY_REDIRECT")
                if (previous == null) sql.mutate("j_redirect", listOf(scope, value.type, value.alias), """INSERT INTO j_redirect(scope_id,aggregate_type,alias_server_id,canonical_server_id,
                    alias_local_id,canonical_local_id,server_sequence) VALUES(?,?,?,?,?,?,?)""", scope, value.type, value.alias,
                    value.canonical, mapped(value.type, value.alias), mapped(value.type, value.canonical), sequence)
                AccountMediaProjectionFence.redirected(sql, scope, value.type, value.alias)
                val alias = mapped(value.type, value.alias); val canonical = mapped(value.type, value.canonical)
                if (alias != null && canonical != null && alias != canonical) conflict(value.type, alias, value.canonical,
                    "REDIRECT_COLLISION", JsonObject(mapOf("alias_server_id" to JsonPrimitive(value.alias.value),
                        "canonical_server_id" to JsonPrimitive(value.canonical.value))), null)
                return
            }
            cursor = AccountUuid(next)
        }
        defer("REDIRECT_CHAIN_CAPACITY")
    }
}
