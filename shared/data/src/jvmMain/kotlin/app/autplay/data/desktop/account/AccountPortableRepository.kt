package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import java.time.Instant
import kotlinx.serialization.json.*

internal class AccountPortableRepository(private val owner: AccountRepositoryOwner) : DesktopAccountPortableData {
    override suspend fun ownerAccountId(lease:AccountLease):AccountUuid=owner.read(lease){_,w->account(w)}
    private fun account(witness: AccountProtectedWitness): AccountUuid = witness.preparation.scope.portableAccountId
        ?: fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
    private fun ownership(sql: AccountPageSql, lease: AccountLease, witness: AccountProtectedWitness): AccountUuid {
        val account = account(witness)
        val old = sql.query("SELECT account_id,scope_id FROM j_portable_owner WHERE profile_id=?", lease.profileId) {
            it.getText(0) to it.getText(1)
        }.singleOrNull()
        if (old != null && old != (account.value to lease.scopeId.value)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        if (old == null) sql.mutate("j_portable_owner", listOf(lease.profileId), """INSERT INTO j_portable_owner
            (profile_id,account_id,scope_id,binding_commit,authority_revision,device_key_thumbprint,cutoff_ms) VALUES(?,?,?,?,?,?,0)""",
            lease.profileId, account, lease.scopeId, lease.bindingCommit, lease.authorityRevision, witness.preparation.binding?.deviceKeyThumbprint)
        return account
    }
    private fun cursor(sql: AccountPageSql, account: AccountUuid, profile: AccountUuid, source: String): AccountPortableCursor =
        sql.query("""SELECT cursor_time,cursor_id,revision,complete,pass_id,uploaded_revision FROM j_portable_cursor
            WHERE account_id=? AND profile_id=? AND source=?""", account, profile, source) {
            AccountPortableCursor(sql.nullable(it, 0), sql.nullable(it, 1)?.let(::AccountUuid), it.getLong(2),
                it.getLong(3) == 1L, sql.nullable(it, 4)?.let(::AccountUuid), it.getLong(5))
        }.singleOrNull() ?: AccountPortableCursor(null, null, 0, false, null)
    private fun putCursor(sql: AccountPageSql, account: AccountUuid, profile: AccountUuid, source: String, c: AccountPortableCursor): Unit {
        require(c.revision in 0..DesktopAccountLimits.MAX_REVISION)
        sql.mutate("j_portable_cursor", listOf(account, profile, source), """INSERT INTO j_portable_cursor
            (account_id,profile_id,source,cursor_time,cursor_id,revision,complete,pass_id,uploaded_revision) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id,profile_id,source) DO UPDATE SET cursor_time=excluded.cursor_time,cursor_id=excluded.cursor_id,
            revision=excluded.revision,complete=excluded.complete,pass_id=excluded.pass_id,uploaded_revision=excluded.uploaded_revision""",
            account, profile, source, c.time, c.id, c.revision, if (c.complete) 1 else 0, c.passId, c.uploadedRevision)
    }
    override suspend fun beginPass(lease: AccountLease, operationId: AccountUuid, passId: AccountUuid): Unit =
        owner.write(lease, operationId, "PORTABLE_BEGIN", bytes("pass_id" to passId.value)) { sql, witness, replay ->
            if (!replay) {
                val account = ownership(sql, lease, witness)
                val prior=cursor(sql,account,lease.profileId,"records")
                if(prior.passId==passId)return@write
                if(prior.passId!=null && AccountPortableV1.sources.any{!cursor(sql,account,lease.profileId,it).complete})fail(AccountFailure.ACCOUNT_BUSY)
                for (source in AccountPortableV1.sources + "upload") {
                    val c = cursor(sql, account, lease.profileId, source)
                    putCursor(sql, account, lease.profileId, source, c.copy(revision = next(c.revision), complete = false, passId = passId))
                }
            }
        }
    override suspend fun cursor(lease: AccountLease, source: String): AccountPortableCursor {
        require(source in AccountPortableV1.sources + "upload")
        return owner.read(lease) { sql, witness -> cursor(sql, account(witness), lease.profileId, source) }
    }
    override suspend fun applyPortablePage(lease: AccountLease, operationId: AccountUuid, source: String,
        expectedCursor: AccountPortableCursor, exactResponse: ByteArray): AccountPortableCursor {
        require(source in AccountPortableV1.sources && exactResponse.size in 1..DesktopAccountLimits.PORTABLE_PAGE_BYTES)
        val raw = exactResponse.copyOf(); val root = AccountSyncWireV1.document(raw)
        require(root.keys == setOf("account_id", "records", "cursor", "next"))
        val account = root.id("account_id")
        val values = root.array("records", 32).map { it as? JsonObject ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID) }
        val records = values.map(AccountPortableV1::decode)
        fun position(key: String): Pair<String, AccountUuid>? {
            if (root[key] == JsonNull) return null
            val c = root.obj(key); require(c.keys == setOf("time", "id"))
            return c.string("time", 40).also { require(it.length >= 20); Instant.parse(it) } to c.id("id")
        }
        val position = position("cursor"); val nextPosition = position("next")
        require(records.isEmpty() == (position == null) && (nextPosition == null || records.size == 32 && nextPosition == position))
        position?.let {
            val oldTime = Instant.parse(expectedCursor.time ?: "1970-01-01T00:00:00Z")
            val comparison = Instant.parse(it.first).compareTo(oldTime)
            require(comparison > 0 || comparison == 0 && it.second.value > (expectedCursor.id?.value ?: "00000000-0000-0000-0000-000000000000"))
        }
        val input = bytes("source" to source, "expected_revision" to expectedCursor.revision.toString()) + raw
        return owner.write(lease, operationId, "PORTABLE_PAGE", input) { sql, witness, replay ->
            require(account(witness) == account && witness.preparation.scope.remote != null)
            if (!replay) {
                ownership(sql, lease, witness)
                val current = cursor(sql, account, lease.profileId, source)
                if (current != expectedCursor || current.complete || current.passId == null) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                records.forEachIndexed { index, record ->
                    original(sql, account, operationId, index, record, AccountPortableV1.canonical(values[index]))
                    merge(sql, account, record)
                }
                putCursor(sql, account, lease.profileId, source, current.copy(time = position?.first ?: current.time,
                    id = position?.second ?: current.id, revision = next(current.revision), complete = nextPosition == null))
                updateStatistics(sql, account, lease.profileId)
            }
            cursor(sql, account, lease.profileId, source)
        }
    }
    override suspend fun mergeRecords(lease: AccountLease, operationId: AccountUuid, records: List<AccountPortableRecord>): Unit {
        require(records.size in 1..32)
        val checked = records.map { AccountPortableV1.decode(it.wire()) }
        val input = AccountPortableV1.canonical(buildJsonObject { put("records", JsonArray(checked.map { it.wire() })) })
        require(input.size <= DesktopAccountLimits.PORTABLE_PAGE_BYTES)
        owner.write(lease, operationId, "PORTABLE_MERGE", input) { sql, witness, replay ->
            if (!replay) {
                val account = ownership(sql, lease, witness)
                checked.forEachIndexed { index, record -> original(sql, account, operationId, index, record,
                    AccountPortableV1.canonical(records[index].wire())); merge(sql, account, record) }
                updateStatistics(sql, account, lease.profileId)
            }
        }
    }
    override suspend fun recordUpload(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long, uploadedRevision: Long): Unit =
        owner.write(lease, operationId, "PORTABLE_UPLOAD", bytes("expected" to expectedRevision.toString(), "uploaded" to uploadedRevision.toString())) { sql, witness, replay ->
            if (!replay) {
                val account = ownership(sql, lease, witness); val c = cursor(sql, account, lease.profileId, "upload")
                if (c.revision != expectedRevision || uploadedRevision < c.uploadedRevision || uploadedRevision > latest(sql, account)) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                putCursor(sql, account, lease.profileId, "upload", c.copy(revision = next(c.revision), uploadedRevision = uploadedRevision,
                    complete = uploadedRevision == latest(sql, account)))
            }
        }
    override suspend fun readUploadPage(lease:AccountLease,afterRevision:Long,limit:Int):List<AccountPortableUploadRecord> {
        require(afterRevision in 0..DesktopAccountLimits.MAX_REVISION && limit in 1..32)
        return owner.read(lease){sql,w->sql.query("""SELECT record_id,kind,document,revision FROM j_portable_record
            WHERE account_id=? AND canonical_record_id=record_id AND revision>? ORDER BY revision,record_id LIMIT ?""",account(w),afterRevision,limit){
            AccountPortableUploadRecord(decode(it.getText(0),it.getText(1),it.getBlob(2)),it.getLong(3))}}
    }
    override suspend fun readPage(lease: AccountLease, kind: String, after: AccountUuid?, limit: Int): List<AccountPortableRecord> {
        require(kind in AccountPortableV1.kinds && limit in 1..32)
        return owner.read(lease) { sql, witness -> sql.query("""SELECT record_id,kind,document FROM j_portable_record
            WHERE account_id=? AND kind=? AND canonical_record_id=record_id AND record_id>? ORDER BY record_id LIMIT ?""",
            account(witness), kind, after?.value ?: "", limit) { decode(it.getText(0), it.getText(1), it.getBlob(2)) } }
    }
    override suspend fun statistics(lease: AccountLease): AccountPortableStatistics = owner.read(lease) { sql, witness ->
        val account = account(witness)
        val revision = latest(sql, account)
        // ROOT-only mutations are captured atomically above; ROOT has no six remote sources.
        val boundOwners=sql.number("SELECT count(*) FROM j_portable_owner o JOIN j_scope p ON p.scope_id=o.scope_id WHERE o.account_id=? AND p.server_instance_id IS NOT NULL",account)
        val complete = boundOwners>0 && sql.number("""SELECT count(*) FROM j_portable_owner o JOIN j_scope p ON p.scope_id=o.scope_id WHERE o.account_id=? AND p.server_instance_id IS NOT NULL AND
            (SELECT count(*) FROM j_portable_cursor c WHERE c.account_id=o.account_id AND c.profile_id=o.profile_id
            AND c.source IN ('records','history','preferences','playlists','playlist_entries','tastes') AND c.complete=1)=6""", account) ==
            boundOwners
        val cached = if(complete) sql.query("""SELECT min(s.checked_through_ms) FROM j_portable_owner o JOIN j_portable_statistics s
            ON s.account_id=o.account_id AND s.profile_id=o.profile_id JOIN j_scope p ON p.scope_id=o.scope_id
            WHERE o.account_id=? AND p.server_instance_id IS NOT NULL
            HAVING count(*)=? AND count(s.checked_through_ms)=count(*)""", account,boundOwners) {
            if (it.isNull(0)) null else it.getLong(0)
        }.singleOrNull() else null
        // Partial records are available, but a partial aggregate is never represented as a confirmed total.
        val available=complete && cached!=null
        val total = if (available) sql.query("""SELECT count(*),coalesce(sum(l.played_ms),0) FROM j_portable_listening l
            JOIN j_portable_record r ON r.account_id=l.account_id AND r.record_id=l.record_id
            WHERE r.account_id=? AND r.canonical_record_id=r.record_id""", account) { it.getLong(0) to it.getLong(1) }.single() else null
        AccountPortableStatistics(revision, cached, available, available, total?.first, total?.second)
    }
    private fun original(sql: AccountPageSql, account: AccountUuid, operation: AccountUuid, index: Int, record: AccountPortableRecord, bytes: ByteArray): Unit {
        capacity(sql, account, bytes.size.toLong())
        sql.mutate("j_portable_original", listOf(account, operation, index), """INSERT INTO j_portable_original
            (account_id,operation_id,item_index,record_id,kind,exact_record) VALUES(?,?,?,?,?,?)""", account, operation, index, record.recordId, record.kind, bytes)
    }
    private fun merge(sql:AccountPageSql,account:AccountUuid,r:AccountPortableRecord):Unit=AccountPortableMerge.merge(sql,account,r)
    private fun decode(id:String,kind:String,bytes:ByteArray):AccountPortableRecord=AccountPortableMerge.decode(id,kind,bytes)
    private fun latest(sql:AccountPageSql,account:AccountUuid):Long=AccountPortableMerge.latest(sql,account)
    private fun capacity(sql:AccountPageSql,account:AccountUuid,bytes:Long):Unit=AccountPortableMerge.capacity(sql,account,bytes)
    private fun updateStatistics(sql:AccountPageSql,account:AccountUuid,profile:AccountUuid):Unit=AccountPortableMerge.updateStatistics(sql,account,profile)
}

/** Shared by domain/materialization writers INSIDE their existing J transaction, never nested SQL. */
internal object AccountPortableMerge {
    fun merge(sql: AccountPageSql, account: AccountUuid, incoming: AccountPortableRecord): Unit {
        val ids = linkedSetOf(incoming.recordId)
        if (incoming.kind == "LISTENING") incoming.document.getValue("event_ids").jsonArray.forEach {
            sql.text("SELECT record_id FROM j_listening_alias WHERE account_id=? AND event_id=?", account, AccountUuid(it.jsonPrimitive.content))?.let { id -> ids.add(AccountUuid(id)) }
        }
        var merged = incoming
        for (id in ids.sortedBy { it.value }) record(sql, account, id)?.let { merged = AccountPortableV1.merge(it, merged) }
        val current = record(sql, account, merged.recordId)
        if (current == merged && ids.size <= 1) return
        val document = AccountPortableV1.canonical(merged.document); capacity(sql, account, document.size.toLong(),if(current==null)1 else 0)
        val revision = next(latest(sql, account))
        sql.mutate("j_portable_record", listOf(account, merged.recordId), """INSERT INTO j_portable_record
            (account_id,record_id,kind,document,content_digest,revision,canonical_record_id) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account_id,record_id) DO UPDATE SET document=excluded.document,content_digest=excluded.content_digest,
            revision=excluded.revision,canonical_record_id=excluded.canonical_record_id""", account, merged.recordId, merged.kind,
            document, AccountPortableV1.hash(merged.document), revision, merged.recordId)
        if (merged.kind == "LISTENING") {
            sql.mutate("j_portable_listening", listOf(account, merged.recordId), """INSERT INTO j_portable_listening
                (account_id,record_id,played_ms,started_ms,excluded) VALUES(?,?,?,?,?) ON CONFLICT(account_id,record_id)
                DO UPDATE SET played_ms=excluded.played_ms,started_ms=excluded.started_ms,excluded=excluded.excluded""", account, merged.recordId,
                merged.document.getValue("played_ms").jsonPrimitive.long, merged.document.getValue("started_at_ms").jsonPrimitive.long,
                if (merged.document.getValue("excluded_from_taste").jsonPrimitive.boolean) 1 else 0)
            // No DELETE: old originals and merged rows remain, but only the closure's canonical row is counted.
            for (id in ids.filter { it != merged.recordId }) if (record(sql, account, id) != null) sql.mutate("j_portable_record", listOf(account, id),
                "UPDATE j_portable_record SET canonical_record_id=?,revision=? WHERE account_id=? AND record_id=?", merged.recordId, revision, account, id)
            for (alias in merged.document.getValue("event_ids").jsonArray) {
                val id = AccountUuid(alias.jsonPrimitive.content)
                sql.mutate("j_listening_alias", listOf(account, id), """INSERT INTO j_listening_alias(account_id,event_id,record_id) VALUES(?,?,?)
                    ON CONFLICT(account_id,event_id) DO UPDATE SET record_id=excluded.record_id""", account, id, merged.recordId)
            }
        }
    }
    private fun record(sql: AccountPageSql, account: AccountUuid, id: AccountUuid): AccountPortableRecord? = sql.query(
        "SELECT record_id,kind,document FROM j_portable_record WHERE account_id=? AND record_id=?", account, id) {
        decode(it.getText(0), it.getText(1), it.getBlob(2))
    }.singleOrNull()
    fun decode(id: String, kind: String, bytes: ByteArray): AccountPortableRecord = AccountPortableV1.decode(
        AccountPortableRecord(AccountUuid(id), kind, AccountSyncWireV1.document(bytes)).wire())
    fun latest(sql: AccountPageSql, account: AccountUuid): Long = sql.number("SELECT coalesce(max(revision),0) FROM j_portable_record WHERE account_id=?", account)
    fun capacity(sql: AccountPageSql, account: AccountUuid, bytes: Long, additionalRecords:Long=0): Unit {
        val count = sql.number("SELECT count(*) FROM j_portable_record WHERE account_id=?", account)
        val total = sql.number("""SELECT (SELECT coalesce(sum(length(document)),0) FROM j_portable_record WHERE account_id=?) +
            (SELECT coalesce(sum(length(exact_record)),0) FROM j_portable_original WHERE account_id=?) +
            (SELECT coalesce(sum(length(c.exact_input)),0) FROM j_repository_commit c JOIN j_scope s ON s.scope_id=c.scope_id
                WHERE s.account_id=? AND c.kind IN ('PORTABLE_PAGE','PORTABLE_MERGE'))""", account, account, account)
        if (count > DesktopAccountLimits.PORTABLE_RECORDS-additionalRecords || bytes > DesktopAccountLimits.PORTABLE_BYTES - total) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
    }
    fun updateStatistics(sql: AccountPageSql, account: AccountUuid, profile: AccountUuid): Unit {
        val complete = sql.number("""SELECT count(*) FROM j_portable_cursor WHERE account_id=? AND profile_id=? AND complete=1
            AND source IN ('records','history','preferences','playlists','playlist_entries','tastes')""", account, profile) == 6L
        sql.mutate("j_portable_statistics", listOf(account, profile), """INSERT INTO j_portable_statistics
            (account_id,profile_id,revision,checked_through_ms,complete,available,listens,played_ms) VALUES(?,?,?,?,?,?,NULL,NULL)
            ON CONFLICT(account_id,profile_id) DO UPDATE SET revision=excluded.revision,checked_through_ms=excluded.checked_through_ms,
            complete=excluded.complete,available=excluded.available,listens=NULL,played_ms=NULL""", account, profile, latest(sql, account),
            if (complete) System.currentTimeMillis() else null, if (complete) 1 else 0, if (complete) 1 else 0)
    }
}

internal fun next(revision: Long): Long {
    if (revision !in 0 until DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
    return revision + 1
}
internal fun bytes(vararg fields: Pair<String, String>): ByteArray = AccountPortableV1.canonical(
    JsonObject(fields.associate { it.first to JsonPrimitive(it.second) }))
