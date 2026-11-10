package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import java.time.Instant
import kotlinx.serialization.json.*

internal class AccountDomainRepository(private val owner: AccountRepositoryOwner) : DesktopAccountDomain {
    override suspend fun readExact(lease: AccountLease, kind: AccountProjectionKind, id: AccountUuid): AccountLocalDomainRow? =
        owner.read(lease) { sql, _ -> AccountLocalDomainReader.read(sql, lease.scopeId, kind, null, 1, id).singleOrNull() }

    override suspend fun readTrackPreferences(lease: AccountLease, trackId: AccountUuid): List<AccountLocalDomainRow> =
        owner.read(lease) { sql, _ ->
            val ids = sql.query("SELECT preference_id FROM j_preference WHERE scope_id=? AND track_ref_id=? ORDER BY preference_id LIMIT 2",
                lease.scopeId, trackId) { AccountUuid(it.getText(0)) }
            ids.map { AccountLocalDomainReader.read(sql, lease.scopeId, AccountProjectionKind.PREFERENCE, null, 1, it).single() }
        }

    override suspend fun readMutationOutcome(lease: AccountLease, change: AccountDomainChange,
        review: AccountDomainReview): AccountDomainReceipt? {
        val expected = reviewedInput(change, review)
        return owner.read(lease) { sql, _ ->
            val marker = sql.query("SELECT kind,exact_input FROM j_repository_commit WHERE scope_id=? AND operation_id=?",
                lease.scopeId, change.operationId) { it.getText(0) to it.getBlob(1) }.singleOrNull()
            if (marker == null) null else {
                if (marker.first != "DOMAIN" || !marker.second.contentEquals(expected)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                domainReceipt(sql, lease, change.operationId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
        }
    }

    override suspend fun readEditablePlaylist(lease: AccountLease, playlistId: AccountUuid): AccountPlaylistPage =
        owner.read(lease) { sql, _ ->
            val ids = editablePlaylistIds(sql, lease.scopeId, playlistId)
            val items = ids.take(100).map { id ->
                val occurrence = AccountLocalDomainReader.read(sql, lease.scopeId, AccountProjectionKind.PLAYLIST_ENTRY, null, 1, id).single()
                val entry = occurrence.detail as AccountLocalDomainDetail.Entry
                val track = AccountLocalDomainReader.read(sql, lease.scopeId, AccountProjectionKind.TRACK, null, 1, entry.trackId).singleOrNull()
                AccountPlaylistOccurrence(occurrence, track)
            }
            val last = items.lastOrNull()?.occurrence
            AccountPlaylistPage(items, last?.takeIf { ids.size > 100 }?.let {
                AccountPlaylistCursor((it.detail as AccountLocalDomainDetail.Entry).positionKey, it.header.localId)
            })
        }

    private fun editablePlaylistIds(sql: AccountPageSql, scope: AccountUuid, playlist: AccountUuid): List<AccountUuid> =
        sql.query("""SELECT e.entry_id FROM j_playlist_entry e
            LEFT JOIN j_domain_state s ON s.scope_id=e.scope_id AND s.local_id=e.entry_id AND s.aggregate_type='PLAYLIST_ENTRY'
            LEFT JOIN j_account_projection_visibility p ON p.scope_id=e.scope_id AND p.local_id=e.entry_id AND p.aggregate_type='PLAYLIST_ENTRY'
            WHERE e.scope_id=? AND e.playlist_id=? AND
            CASE WHEN coalesce(p.pending,0)=1 THEN coalesce(s.removed,p.effective_deleted,0)
                ELSE coalesce(p.effective_deleted,s.removed,0) END=0
            ORDER BY e.position_key,e.entry_id LIMIT 101""", scope, playlist) { AccountUuid(it.getText(0)) }

    private fun reviewedInput(change: AccountDomainChange, review: AccountDomainReview): ByteArray {
        val commandBytes = domainInput(change)
        return AccountPortableV1.canonical(buildJsonObject {
            put("command", AccountSyncWireV1.document(commandBytes))
            put("expected", JsonArray(review.expected.map { expected -> buildJsonObject {
                put("kind", expected.kind.name); put("id", expected.localId.value)
                put("header", expected.header?.let { h -> buildJsonObject {
                    put("revision", h.localRevision); put("server", h.serverId?.value?.let(::JsonPrimitive) ?: JsonNull)
                    put("version", h.observedVersion?.let(::JsonPrimitive) ?: JsonNull); put("pending", h.pending)
                    put("removed", h.removed); put("state", h.rawProjectionState?.let(::JsonPrimitive) ?: JsonNull)
                    put("confirmed", h.snapshotConfirmed?.let(::JsonPrimitive) ?: JsonNull); put("conflict", h.hasConflict)
                } } ?: JsonNull)
            } }))
            put("playlist", review.playlistId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("order", review.occurrenceOrder?.let { JsonArray(it.map { id -> JsonPrimitive(id.value) }) } ?: JsonNull)
        })
    }

    override suspend fun mutateReviewed(lease: AccountLease, change: AccountDomainChange,
        review: AccountDomainReview): AccountDomainReceipt {
        require(review.expected.size in 1..104 && review.expected.map { it.kind to it.localId }.distinct().size == review.expected.size)
        require(review.expected.all { item -> item.header?.let { it.localId == item.localId } != false })
        require((review.playlistId == null) == (review.occurrenceOrder == null))
        review.occurrenceOrder?.let { require(it.size <= 100 && it.distinct().size == it.size) }
        val input = reviewedInput(change, review)
        return owner.write(lease, change.operationId, "DOMAIN", input) { sql, witness, replay ->
            if (!replay) {
                review.expected.forEach { expected ->
                    val row = AccountLocalDomainReader.read(sql, lease.scopeId, expected.kind, null, 1, expected.localId).singleOrNull()
                    if (row?.header != expected.header) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                }
                val command = change.command
                if (command is AccountDomainCommand.Preference) {
                    val ids = sql.query("SELECT preference_id FROM j_preference WHERE scope_id=? AND track_ref_id=? LIMIT 2",
                        lease.scopeId, command.trackId) { AccountUuid(it.getText(0)) }
                    val expected = review.expected.singleOrNull { it.kind == AccountProjectionKind.PREFERENCE && it.localId == command.localId }
                        ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                    if (ids != (if (expected.header == null) emptyList<AccountUuid>() else listOf(command.localId)))
                        fail(AccountFailure.ACCOUNT_LEASE_STALE)
                }
                val manualId = when (command) {
                    is AccountDomainCommand.Playlist -> command.localId.takeUnless { command.create }
                    is AccountDomainCommand.Entry -> command.playlistId
                    is AccountDomainCommand.Delete -> when (command.aggregateType) {
                        "PLAYLIST" -> command.localId
                        "PLAYLIST_ENTRY" -> (AccountLocalDomainReader.read(sql, lease.scopeId,
                            AccountProjectionKind.PLAYLIST_ENTRY, null, 1, command.localId).singleOrNull()?.detail
                            as? AccountLocalDomainDetail.Entry)?.playlistId ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                        else -> null
                    }
                    else -> null
                }
                manualId?.let { id ->
                    val row = AccountLocalDomainReader.read(sql, lease.scopeId, AccountProjectionKind.PLAYLIST, null, 1, id).singleOrNull()
                        ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                    if ((row.detail as AccountLocalDomainDetail.Playlist).rawType != "MANUAL" || row.header.removed || row.header.hasConflict)
                        fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                }
                val orderingPlaylist = review.playlistId ?: (command as? AccountDomainCommand.Entry)?.takeIf { it.serverOrdering }?.playlistId
                if (orderingPlaylist != null) {
                    val order = editablePlaylistIds(sql, lease.scopeId, orderingPlaylist)
                    if (order.size > 100) fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                    if (review.playlistId != null && order != review.occurrenceOrder) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                    val entry = change.command as? AccountDomainCommand.Entry
                    if (entry != null && entry.serverOrdering) {
                        if (review.playlistId == null && (entry.move || entry.beforeEntryId != null)) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                        if (entry.playlistId != orderingPlaylist || entry.beforeEntryId == entry.localId ||
                            (entry.beforeEntryId != null && entry.beforeEntryId !in order)) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                        val reordered = order.filter { it != entry.localId }.toMutableList()
                        val index = entry.beforeEntryId?.let { reordered.indexOf(it) } ?: reordered.size
                        reordered.add(index, entry.localId)
                        // Existing local ordering format, with one existing v1 before-entry Journal command.
                        reordered.forEachIndexed { position, id ->
                            if (id != entry.localId) {
                                val old = AccountLocalDomainReader.read(sql, lease.scopeId, AccountProjectionKind.PLAYLIST_ENTRY,
                                    null, 1, id).single()
                                if (old.header.hasConflict || old.header.snapshotConfirmed == false ||
                                    old.header.rawProjectionState.let { it != null && it != "COMPLETE" })
                                    fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                                val detail = old.detail as AccountLocalDomainDetail.Entry
                                val key = position.toString().padStart(20, '0')
                                if (detail.positionKey != key) {
                                    sql.mutate("j_playlist_entry", listOf(lease.scopeId, id),
                                        "UPDATE j_playlist_entry SET position_key=? WHERE scope_id=? AND entry_id=?", key, lease.scopeId, id)
                                    sql.mutate("j_domain_state", listOf(lease.scopeId, "PLAYLIST_ENTRY", id), """INSERT INTO j_domain_state
                                        (scope_id,aggregate_type,local_id,revision,removed) VALUES(?,'PLAYLIST_ENTRY',?,?,0)
                                        ON CONFLICT(scope_id,aggregate_type,local_id) DO UPDATE SET revision=excluded.revision""",
                                        lease.scopeId, id, next(old.header.localRevision))
                                    // Rebalanced neighbors are portable data in the same transaction,
                                    // without inventing extra wire events or lineage sequence numbers.
                                    AccountDomainPortableCapture.capture(sql, lease, witness,
                                        change.copy(command = AccountDomainCommand.Entry(id, detail.playlistId, detail.trackId, key, true)), position + 1)
                                }
                            }
                        }
                        val actual = change.copy(command = entry.copy(positionKey = index.toString().padStart(20, '0')))
                        AccountDomainMutationWriter(sql, lease, witness).mutate(actual)
                    } else AccountDomainMutationWriter(sql, lease, witness).mutate(change)
                } else AccountDomainMutationWriter(sql, lease, witness).mutate(change)
            }
            domainReceipt(sql, lease, change.operationId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }
    override suspend fun readProjectionPage(lease:AccountLease,kind:AccountProjectionKind,after:AccountUuid?,limit:Int):List<AccountLocalDomainRow> =
        owner.read(lease){sql,_->AccountLocalDomainReader.read(sql,lease.scopeId,kind,after,limit)}
    override suspend fun readPlaylistPage(lease:AccountLease,playlistId:AccountUuid,
        after:AccountPlaylistCursor?,limit:Int):AccountPlaylistPage = owner.read(lease){sql,_->
        require(limit in 1..100)
        val cursors=sql.query("""SELECT entry_id,position_key FROM j_playlist_entry
            WHERE scope_id=? AND playlist_id=? AND
                (? IS NULL OR position_key>? OR (position_key=? AND entry_id>?))
            ORDER BY position_key,entry_id LIMIT ?""",lease.scopeId,playlistId,after?.positionKey,
            after?.positionKey,after?.positionKey,after?.occurrenceId,limit+1){
            AccountPlaylistCursor(it.getText(1),AccountUuid(it.getText(0)))
        }
        val page=cursors.take(limit)
        val items=page.map{cursor->
            val occurrence=AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.PLAYLIST_ENTRY,
                null,1,cursor.occurrenceId).singleOrNull() ?: fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            val entry=occurrence.detail as AccountLocalDomainDetail.Entry
            if(entry.playlistId!=playlistId || entry.positionKey!=cursor.positionKey)
                fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            val track=AccountLocalDomainReader.read(sql,lease.scopeId,AccountProjectionKind.TRACK,
                null,1,entry.trackId).singleOrNull()
            AccountPlaylistOccurrence(occurrence,track)
        }
        AccountPlaylistPage(items,if(cursors.size>limit)page.last() else null)
    }
    override suspend fun mutate(lease: AccountLease, change: AccountDomainChange): AccountDomainReceipt {
        val input = domainInput(change)
        return owner.write(lease, change.operationId, "DOMAIN", input) { sql, witness, replay ->
            if (!replay) AccountDomainMutationWriter(sql, lease, witness).mutate(change)
            domainReceipt(sql, lease, change.operationId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }
    override suspend fun saveQueue(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long, queue: AccountQueueSnapshot): Unit {
        require(queue.revision == next(expectedRevision) && queue.entries.size <= 1_000 && queue.repeat in setOf("OFF", "ONE", "ALL"))
        require(queue.entries.map { it.entryId }.distinct().size == queue.entries.size && queue.entries.map { it.position }.sorted() == queue.entries.indices.toList())
        require(queue.currentEntryId == null || queue.entries.any { it.entryId == queue.currentEntryId })
        val input = AccountPortableV1.canonical(buildJsonObject {
            put("queue_id", queue.queueId.value); put("revision", queue.revision); put("repeat_mode", queue.repeat)
            put("shuffle", queue.shuffle); put("session_id", queue.sessionId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("current_entry_id", queue.currentEntryId?.value?.let(::JsonPrimitive) ?: JsonNull)
            put("entries", JsonArray(queue.entries.map { buildJsonObject { put("entry_id", it.entryId.value); put("track_id", it.trackId.value); put("position", it.position) } }))
        })
        owner.write(lease, operationId, "QUEUE", input) { sql, _, replay ->
            if (!replay) {
                val prior = sql.query("SELECT revision FROM j_queue WHERE scope_id=? AND queue_id=?", lease.scopeId, queue.queueId) { it.getLong(0) }.singleOrNull() ?: 0
                if (prior != expectedRevision) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                sql.mutate("j_queue", listOf(lease.scopeId, queue.queueId), """INSERT INTO j_queue
                    (scope_id,queue_id,revision,repeat_mode,shuffle,session_id,current_entry_id) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(scope_id,queue_id) DO UPDATE SET revision=excluded.revision,repeat_mode=excluded.repeat_mode,
                    shuffle=excluded.shuffle,session_id=excluded.session_id,current_entry_id=excluded.current_entry_id""",
                    lease.scopeId, queue.queueId, queue.revision, queue.repeat, if (queue.shuffle) 1 else 0, queue.sessionId, queue.currentEntryId)
                for (entry in queue.entries) sql.mutate("j_queue_entry", listOf(lease.scopeId, queue.queueId, queue.revision, entry.entryId),
                    "INSERT INTO j_queue_entry(scope_id,queue_id,revision,entry_id,track_id,position) VALUES(?,?,?,?,?,?)",
                    lease.scopeId, queue.queueId, queue.revision, entry.entryId, entry.trackId, entry.position)
            }
        }
    }
    override suspend fun readQueue(lease: AccountLease, queueId: AccountUuid): AccountQueueSnapshot? = owner.read(lease) { sql, _ ->
        sql.query("SELECT revision,repeat_mode,shuffle,session_id,current_entry_id FROM j_queue WHERE scope_id=? AND queue_id=?", lease.scopeId, queueId) {
            AccountQueueSnapshot(queueId, it.getLong(0), emptyList(), it.getText(1), it.getLong(2) == 1L,
                sql.nullable(it, 3)?.let(::AccountUuid), sql.nullable(it, 4)?.let(::AccountUuid))
        }.singleOrNull()?.let { queue -> queue.copy(entries = sql.query("""SELECT entry_id,track_id,position FROM j_queue_entry
            WHERE scope_id=? AND queue_id=? AND revision=? ORDER BY position LIMIT 1000""", lease.scopeId, queueId, queue.revision) {
            AccountQueueEntry(AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), it.getLong(2).toInt())
        }) }
    }
    override suspend fun checkpoint(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long,
        checkpoint: AccountListeningCheckpoint, finalPayload: JsonObject?): AccountDomainReceipt? {
        val c = checkpoint
        require(c.revision == next(expectedRevision) && c.startedAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS &&
            c.playedMs in 0..604_800_000 && c.positionMs in 0..604_800_000 && c.finalized == (finalPayload != null))
        if (finalPayload != null) {
            AccountDomainWire.listening(finalPayload)
            require(operationId == c.listeningId && finalPayload.id("local_user_track_ref_id") == c.trackId &&
                finalPayload.number("played_ms", 0) == c.playedMs && finalPayload.bool("excluded_from_taste") == c.excludedFromTaste &&
                finalPayload.string("event_origin", 100) == c.origin && finalPayload.string("context", 50) == c.context)
        }
        val input = AccountPortableV1.canonical(buildJsonObject {
            put("listening_id", c.listeningId.value); put("queue_id", c.queueId.value); put("entry_id", c.queueEntryId.value)
            put("session_id", c.sessionId.value); put("track_id", c.trackId.value); put("started_ms", c.startedAtMs)
            put("played_ms", c.playedMs); put("position_ms", c.positionMs); put("revision", c.revision)
            put("origin", c.origin); put("context", c.context); put("excluded", c.excludedFromTaste); put("finalized", c.finalized)
            put("final_payload", finalPayload ?: JsonNull)
        })
        return owner.write(lease, operationId, "CHECKPOINT", input) { sql, witness, replay ->
            if (!replay) {
                val old = checkpoint(sql, lease, c.listeningId)
                if ((old?.revision ?: 0) != expectedRevision || old?.finalized == true ||
                    old?.let { it.copy(playedMs=c.playedMs, positionMs=c.positionMs, revision=c.revision,
                        excludedFromTaste=c.excludedFromTaste, finalized=c.finalized) != c } == true || (old != null && c.playedMs < old.playedMs)) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                if (sql.number("""SELECT count(*) FROM j_queue q JOIN j_queue_entry e ON e.scope_id=q.scope_id AND e.queue_id=q.queue_id
                    AND e.revision=q.revision WHERE q.scope_id=? AND q.queue_id=? AND q.session_id=? AND e.entry_id=? AND e.track_id=?""",
                        lease.scopeId, c.queueId, c.sessionId, c.queueEntryId, c.trackId) != 1L) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                sql.mutate("j_playback_checkpoint", listOf(lease.scopeId, c.listeningId), """INSERT INTO j_playback_checkpoint
                    (scope_id,listening_id,queue_id,entry_id,session_id,track_id,started_ms,played_ms,position_ms,revision,origin,context,excluded,finalized)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,listening_id) DO UPDATE SET played_ms=excluded.played_ms,
                    position_ms=excluded.position_ms,revision=excluded.revision,excluded=excluded.excluded,finalized=excluded.finalized""",
                    lease.scopeId, c.listeningId, c.queueId, c.queueEntryId, c.sessionId, c.trackId, c.startedAtMs, c.playedMs,
                    c.positionMs, c.revision, c.origin, c.context, if (c.excludedFromTaste) 1 else 0, if (c.finalized) 1 else 0)
                if (finalPayload != null) AccountDomainMutationWriter(sql, lease, witness).mutate(
                    AccountDomainChange(operationId, c.startedAtMs, AccountDomainCommand.Listen(c.listeningId, finalPayload)))
            }
            if (c.finalized) domainReceipt(sql, lease, operationId) else null
        }
    }
    override suspend fun readCheckpoint(lease: AccountLease, listeningId: AccountUuid): AccountListeningCheckpoint? =
        owner.read(lease) { sql, _ -> checkpoint(sql, lease, listeningId) }
    private fun checkpoint(sql: AccountPageSql, lease: AccountLease, id: AccountUuid): AccountListeningCheckpoint? = sql.query("""SELECT
        queue_id,entry_id,session_id,track_id,started_ms,played_ms,position_ms,revision,origin,context,excluded,finalized
        FROM j_playback_checkpoint WHERE scope_id=? AND listening_id=?""", lease.scopeId, id) {
        AccountListeningCheckpoint(id, AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), AccountUuid(it.getText(2)),
            AccountUuid(it.getText(3)), it.getLong(4), it.getLong(5), it.getLong(6), it.getLong(7), it.getText(8), it.getText(9), it.getLong(10)==1L, it.getLong(11)==1L)
    }.singleOrNull()
    override suspend fun confirmPolicy(lease: AccountLease, policy: AccountConfirmedPolicy): Unit {
        require(policy.revision in 0..DesktopAccountLimits.MAX_REVISION && policy.confirmedAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS &&
            policy.rawState.matches(Regex("[A-Z][A-Z0-9_]{0,99}")))
        require(policy.enabled == (policy.rawState == if(policy.kind==AccountPolicyKind.TRAINING_CONSENT)"GRANTED" else "ENABLED"))
        if(policy.kind==AccountPolicyKind.TRAINING_CONSENT && policy.rawState in setOf("UNKNOWN","GRANTED","DENIED","WITHDRAWN"))
            require(if(policy.rawState=="UNKNOWN")policy.revision==0L else policy.revision>0)
        owner.write(lease, policy.operationId, "CONFIRMED_POLICY", bytes("kind" to policy.kind.name, "revision" to policy.revision.toString(),
            "enabled" to policy.enabled.toString(), "confirmed_at" to policy.confirmedAtMs.toString(), "pending_k_ref" to (policy.pendingKReference?.value ?: ""),"raw_state" to policy.rawState)) { sql, _, replay ->
            if (!replay) {
                val old = sql.query("SELECT revision,raw_state FROM j_confirmed_policy WHERE scope_id=? AND kind=?", lease.scopeId, policy.kind.name) { it.getLong(0) to it.getText(1) }.singleOrNull()
                if (old!=null && (policy.revision < old.first || policy.revision==old.first && policy.rawState!=old.second)) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                sql.mutate("j_confirmed_policy", listOf(lease.scopeId, policy.kind.name), """INSERT INTO j_confirmed_policy
                    (scope_id,kind,revision,enabled,confirmed_at_ms,operation_id,pending_k_ref,raw_state) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,kind)
                    DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,confirmed_at_ms=excluded.confirmed_at_ms,
                    operation_id=excluded.operation_id,pending_k_ref=excluded.pending_k_ref,raw_state=excluded.raw_state""", lease.scopeId, policy.kind.name, policy.revision,
                    if (policy.enabled) 1 else 0, policy.confirmedAtMs, policy.operationId, policy.pendingKReference,policy.rawState)
            }
        }
    }
    override suspend fun readPolicies(lease: AccountLease): List<AccountConfirmedPolicy> = owner.read(lease) { sql, _ ->
        sql.query("SELECT kind,revision,enabled,confirmed_at_ms,operation_id,pending_k_ref,raw_state FROM j_confirmed_policy WHERE scope_id=? LIMIT 100", lease.scopeId) {
            val kind = AccountPolicyKind.entries.singleOrNull { kind -> kind.name == it.getText(0) }
            kind?.let { k -> AccountConfirmedPolicy(k, it.getLong(1), it.getLong(2)==1L, it.getLong(3), AccountUuid(it.getText(4)), sql.nullable(it,5)?.let(::AccountUuid),it.getText(6)) }
        }.filterNotNull()
    }
    override suspend fun retainImportReview(lease: AccountLease, review: AccountImportReview): Unit {
        require(review.rank in 1..100)
        owner.write(lease, review.operationId, "IMPORT_REVIEW", bytes("review" to review.reviewId.value, "job" to review.localJobId.value,
            "entry" to review.localEntryId.value, "candidate" to review.candidateId.value, "server_job" to review.serverJobId.value,
            "server_entry" to review.serverEntryId.value, "rank" to review.rank.toString(), "predecessor" to (review.predecessor?.value ?: ""))) { sql, _, replay ->
            if (!replay) sql.mutate("j_import_review", listOf(lease.scopeId, review.reviewId), """INSERT INTO j_import_review
                (scope_id,review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,rank,predecessor,operation_id)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", lease.scopeId, review.reviewId, review.localJobId, review.localEntryId, review.candidateId,
                review.serverJobId, review.serverEntryId, review.rank, review.predecessor, review.operationId)
        }
    }
    override suspend fun readImportReviews(lease: AccountLease, after: AccountUuid?, limit: Int): List<AccountImportReview> {
        require(limit in 1..100)
        return owner.read(lease) { sql, _ -> sql.query("""SELECT review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,rank,predecessor,operation_id
            FROM j_import_review WHERE scope_id=? AND review_id>? ORDER BY review_id LIMIT ?""", lease.scopeId, after?.value ?: "", limit) {
            AccountImportReview(AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),AccountUuid(it.getText(2)),AccountUuid(it.getText(3)),
                AccountUuid(it.getText(4)),AccountUuid(it.getText(5)),it.getLong(6).toInt(),sql.nullable(it,7)?.let(::AccountUuid),AccountUuid(it.getText(8)))
        } }
    }
    override suspend fun enqueueWork(lease: AccountLease, operationId: AccountUuid, work: AccountWorkDescriptor): Unit =
        owner.write(lease, operationId, "WORK_ENQUEUE", workBytes(work)) { sql, _, replay ->
            // I1 publishes its command and work atomically through the named repository.
            if(work.kind==AccountWorkKind.IMPORT_REVIEW)fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
            if (!replay) {
                require(work.generation in 1..DesktopAccountLimits.MAX_REVISION && work.nextAttemptAtMs in 0..DesktopAccountLimits.MAX_REVISION)
                if (sql.number("SELECT count(*) FROM j_work WHERE state NOT IN ('COMPLETED','CANCELED')") >= DesktopAccountLimits.WORK) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                sql.mutate("j_work", listOf(lease.scopeId, work.workId), """INSERT INTO j_work
                    (scope_id,work_id,kind,subject_id,generation,next_attempt_ms,state) VALUES(?,?,?,?,?,?,'PENDING')""",
                    lease.scopeId, work.workId, work.kind.name, work.subjectId, work.generation, work.nextAttemptAtMs)
            }
        }
    override suspend fun claimWork(lease: AccountLease, operationId: AccountUuid, workId: AccountUuid): AccountWorkClaim? =
        owner.write(lease, operationId, "WORK_CLAIM", bytes("work_id" to workId.value)) { sql, _, replay ->
            val row = work(sql, lease, workId) ?: return@write null
            if(row.first.kind==AccountWorkKind.IMPORT_REVIEW)fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
            if (!replay) {
                if (sql.number("SELECT count(*) FROM j_work WHERE state='RUNNING'") != 0L || row.second != "PENDING" || row.first.nextAttemptAtMs > System.currentTimeMillis()) fail(AccountFailure.ACCOUNT_BUSY)
                val oldRevision = sql.number("SELECT lease_revision FROM j_work WHERE scope_id=? AND work_id=?", lease.scopeId, workId)
                sql.mutate("j_work", listOf(lease.scopeId, workId), """UPDATE j_work SET state='RUNNING',boot_id=?,lease_token=?,lease_revision=?,expires_ms=?
                    WHERE scope_id=? AND work_id=?""", lease.bootId, operationId, next(oldRevision), System.currentTimeMillis()+DesktopAccountLimits.LEASE_MS, lease.scopeId, workId)
                val committed=sql.query("SELECT boot_id,lease_token,lease_revision,expires_ms FROM j_work WHERE scope_id=? AND work_id=?",lease.scopeId,workId){
                    AccountWorkClaim(row.first,AccountUuid(it.getText(0)),AccountUuid(it.getText(1)),it.getLong(2),it.getLong(3))}.single()
                retainWorkOutcome(sql,lease,operationId,committed)
            }
            workOutcome(sql,lease,operationId)
        }
    override suspend fun renewWork(lease:AccountLease,operationId:AccountUuid,claim:AccountWorkClaim):AccountWorkClaim =
        owner.write(lease,operationId,"WORK_RENEW",bytes("token" to claim.leaseToken.value,"revision" to claim.leaseRevision.toString(),"work" to claim.descriptor.workId.value)){sql,_,replay->
            if(claim.descriptor.kind==AccountWorkKind.IMPORT_REVIEW)fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
            if(!replay){
                val id=claim.descriptor.workId
                if(claim.bootId!=lease.bootId || claim.expiresAtMs<=System.currentTimeMillis() || sql.number("""SELECT count(*) FROM j_work WHERE scope_id=? AND work_id=?
                    AND state='RUNNING' AND boot_id=? AND lease_token=? AND lease_revision=? AND expires_ms=? AND kind<>'IMPORT_REVIEW'""",lease.scopeId,id,claim.bootId,claim.leaseToken,claim.leaseRevision,claim.expiresAtMs)!=1L)fail(AccountFailure.ACCOUNT_LEASE_STALE)
                val renewed=claim.copy(leaseRevision=next(claim.leaseRevision),expiresAtMs=System.currentTimeMillis()+DesktopAccountLimits.LEASE_MS)
                sql.mutate("j_work",listOf(lease.scopeId,id),"UPDATE j_work SET lease_revision=?,expires_ms=? WHERE scope_id=? AND work_id=?",renewed.leaseRevision,renewed.expiresAtMs,lease.scopeId,id)
                retainWorkOutcome(sql,lease,operationId,renewed)
            }
            workOutcome(sql,lease,operationId)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    override suspend fun recoverWorkOutcome(lease:AccountLease,operationId:AccountUuid):AccountWorkClaim? =
        owner.settle(lease,AccountPortableV1.stableId(operationId,"work-outcome:v1"),"WORK_OUTCOME",bytes("operation" to operationId.value)){sql,_->workOutcome(sql,lease,operationId)}
    override suspend fun settleWork(lease: AccountLease, operationId: AccountUuid, claim: AccountWorkClaim, state: String, nextAttemptAtMs: Long): Unit {
        if(claim.descriptor.kind==AccountWorkKind.IMPORT_REVIEW)fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        require(state in setOf("PENDING", "COMPLETED", "FAILED", "CANCELED", "UNCERTAIN") && nextAttemptAtMs in 0..DesktopAccountLimits.MAX_REVISION)
        owner.settle(lease, operationId, "WORK_SETTLE", bytes("work_id" to claim.descriptor.workId.value, "token" to claim.leaseToken.value,
            "revision" to claim.leaseRevision.toString(), "state" to state, "next" to nextAttemptAtMs.toString())) { sql, replay ->
            if (!replay) {
                val id = claim.descriptor.workId
                if (sql.number("""SELECT count(*) FROM j_work WHERE scope_id=? AND work_id=? AND state='RUNNING' AND boot_id=?
                    AND lease_token=? AND lease_revision=? AND expires_ms=? AND generation=? AND kind<>'IMPORT_REVIEW'""", lease.scopeId, id, claim.bootId,
                    claim.leaseToken, claim.leaseRevision, claim.expiresAtMs, claim.descriptor.generation) != 1L) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                sql.mutate("j_work", listOf(lease.scopeId,id), "UPDATE j_work SET state=?,next_attempt_ms=? WHERE scope_id=? AND work_id=?",state,nextAttemptAtMs,lease.scopeId,id)
            }
        }
    }
    private fun work(sql: AccountPageSql, lease: AccountLease, id: AccountUuid): Pair<AccountWorkDescriptor,String>? = sql.query(
        "SELECT kind,subject_id,generation,next_attempt_ms,state FROM j_work WHERE scope_id=? AND work_id=?",lease.scopeId,id) {
        val kind = AccountWorkKind.entries.singleOrNull { k -> k.name == it.getText(0) } ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        AccountWorkDescriptor(id,kind,AccountUuid(it.getText(1)),it.getLong(2),it.getLong(3)) to it.getText(4)
    }.singleOrNull()
    private fun retainWorkOutcome(sql:AccountPageSql,lease:AccountLease,operation:AccountUuid,c:AccountWorkClaim):Unit {
        val w=c.descriptor
        sql.mutate("j_work_outcome",listOf(lease.scopeId,operation),"""INSERT INTO j_work_outcome
            (scope_id,operation_id,work_id,kind,subject_id,generation,next_attempt_ms,boot_id,lease_token,lease_revision,expires_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            lease.scopeId,operation,w.workId,w.kind.name,w.subjectId,w.generation,w.nextAttemptAtMs,c.bootId,c.leaseToken,c.leaseRevision,c.expiresAtMs)
    }
    private fun workOutcome(sql:AccountPageSql,lease:AccountLease,operation:AccountUuid):AccountWorkClaim? = sql.query("""SELECT work_id,kind,subject_id,generation,next_attempt_ms,
        boot_id,lease_token,lease_revision,expires_ms FROM j_work_outcome WHERE scope_id=? AND operation_id=?""",lease.scopeId,operation){
        val kind=AccountWorkKind.entries.singleOrNull{k->k.name==it.getText(1)}?:fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        AccountWorkClaim(AccountWorkDescriptor(AccountUuid(it.getText(0)),kind,AccountUuid(it.getText(2)),it.getLong(3),it.getLong(4)),
            AccountUuid(it.getText(5)),AccountUuid(it.getText(6)),it.getLong(7),it.getLong(8))}.singleOrNull()
    private fun workBytes(w: AccountWorkDescriptor): ByteArray = bytes("work_id" to w.workId.value,"kind" to w.kind.name,
        "subject" to w.subjectId.value,"generation" to w.generation.toString(),"next" to w.nextAttemptAtMs.toString()).also { require(it.size <= 1_024) }
}

internal fun domainInput(change: AccountDomainChange): ByteArray = AccountPortableV1.canonical(buildJsonObject {
    val command = change.command
    require(change.occurredAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS)
    val (event,type)=AccountDomainWire.type(change.command)
    put("event_type",event);put("aggregate_type",type);put("local_id",change.command.localId.value);put("occurred_ms",change.occurredAtMs)
    put("payload",AccountDomainWire.payload(change.command))
    if(command is AccountDomainCommand.Listen)put("started_at_ms",command.startedAtMs?.let(::JsonPrimitive)?:JsonNull)
    if(command is AccountDomainCommand.Entry)put("local_position_key",command.positionKey)
}).also { AccountSyncWireV1.document(it) }
internal fun domainReceipt(sql:AccountPageSql,lease:AccountLease,operation:AccountUuid):AccountDomainReceipt? = sql.query(
    "SELECT aggregate_id,event_id,intent_id FROM j_domain_receipt WHERE scope_id=? AND operation_id=?",lease.scopeId,operation) {
    AccountDomainReceipt(operation,AccountUuid(it.getText(0)),sql.nullable(it,1)?.let(::AccountUuid),sql.nullable(it,2)?.let(::AccountUuid))
}.singleOrNull()

internal class AccountDomainMutationWriter(private val sql:AccountPageSql,private val lease:AccountLease,private val witness:AccountProtectedWitness) {
    fun mutate(change:AccountDomainChange):AccountDomainReceipt {
        domainReceipt(sql,lease,change.operationId)?.let { return it }
        val c=change.command;val (eventType,type)=AccountDomainWire.type(c);val p=AccountDomainWire.payload(c)
        val scope=lease.scopeId;val local=c.localId
        fun track(id:AccountUuid):Unit { if(sql.number("SELECT count(*) FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",scope,id)!=1L) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT) }
        when(c) {
            is AccountDomainCommand.PatchTrack -> {
                track(local)
                for(key in c.fields.keys) {
                    val value=if(c.fields[key]==JsonNull)null else c.fields.getValue(key).jsonPrimitive.content
                    if(key=="title" || key=="artist") sql.mutate("j_track_ref",listOf(scope,local),"UPDATE j_track_ref SET $key=? WHERE scope_id=? AND track_ref_id=?",value?:"",scope,local)
                    val old=sql.number("SELECT count(*) FROM j_track_detail WHERE scope_id=? AND track_ref_id=?",scope,local)
                    if(old==0L) sql.mutate("j_track_detail",listOf(scope,local),"INSERT INTO j_track_detail(scope_id,track_ref_id) VALUES(?,?)",scope,local)
                    val column=if(key=="album")"album" else "${key}_is_null"
                    sql.mutate("j_track_detail",listOf(scope,local),"UPDATE j_track_detail SET $column=? WHERE scope_id=? AND track_ref_id=?",if(key=="album")value else if(value==null)1 else 0,scope,local)
                }
            }
            is AccountDomainCommand.Library -> {
                track(c.trackId)
                val old=sql.text("SELECT track_ref_id FROM j_library_entry WHERE scope_id=? AND entry_id=? UNION ALL SELECT track_ref_id FROM j_library_relation WHERE scope_id=? AND entry_id=?",scope,local,scope,local)
                if(old!=null && old!=c.trackId.value) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                if(c.removed && old==null)fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                if(old==null) sql.mutate("j_library_relation",listOf(scope,local),"INSERT INTO j_library_relation(scope_id,entry_id,track_ref_id) VALUES(?,?,?)",scope,local,c.trackId)
            }
            is AccountDomainCommand.Preference -> {
                track(c.trackId)
                sql.text("SELECT track_ref_id FROM j_preference WHERE scope_id=? AND preference_id=?",scope,local)?.let{if(it!=c.trackId.value)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)}
                sql.mutate("j_preference",listOf(scope,local),"""INSERT INTO j_preference(scope_id,preference_id,track_ref_id,preference,excluded_from_taste)
                    VALUES(?,?,?,?,?) ON CONFLICT(scope_id,preference_id) DO UPDATE SET preference=excluded.preference,excluded_from_taste=excluded.excluded_from_taste""",scope,local,c.trackId,c.value,if(c.excludedFromTaste)1 else 0)
            }
            is AccountDomainCommand.Playlist -> {
                val exists=sql.number("SELECT count(*) FROM j_playlist WHERE scope_id=? AND playlist_id=?",scope,local)==1L
                if(exists==c.create) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                sql.mutate("j_playlist",listOf(scope,local),"""INSERT INTO j_playlist(scope_id,playlist_id,name,description,visibility,playlist_type)
                    VALUES(?,?,?,?,'PRIVATE','MANUAL') ON CONFLICT(scope_id,playlist_id) DO UPDATE SET name=excluded.name,description=excluded.description""",scope,local,c.name,c.description)
            }
            is AccountDomainCommand.Entry -> {
                track(c.trackId)
                if(sql.number("SELECT count(*) FROM j_playlist WHERE scope_id=? AND playlist_id=?",scope,c.playlistId)!=1L) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                val old=sql.query("SELECT playlist_id,track_ref_id FROM j_playlist_entry WHERE scope_id=? AND entry_id=?",scope,local){it.getText(0) to it.getText(1)}.singleOrNull()
                if(old!=null && old!=(c.playlistId.value to c.trackId.value) || c.move && old==null) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                sql.mutate("j_playlist_entry",listOf(scope,local),"""INSERT INTO j_playlist_entry(scope_id,entry_id,playlist_id,track_ref_id,position_key) VALUES(?,?,?,?,?)
                    ON CONFLICT(scope_id,entry_id) DO UPDATE SET position_key=excluded.position_key""",scope,local,c.playlistId,c.trackId,c.positionKey)
            }
            is AccountDomainCommand.Delete -> {
                val exists=when(c.aggregateType){
                    "USER_TRACK_REF"->sql.number("SELECT count(*) FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",scope,local)
                    "PLAYLIST"->sql.number("SELECT count(*) FROM j_playlist WHERE scope_id=? AND playlist_id=?",scope,local)
                    "PLAYLIST_ENTRY"->sql.number("SELECT count(*) FROM j_playlist_entry WHERE scope_id=? AND entry_id=?",scope,local)
                    "LIBRARY_ENTRY"->sql.number("SELECT (SELECT count(*) FROM j_library_entry WHERE scope_id=? AND entry_id=?)+(SELECT count(*) FROM j_library_relation WHERE scope_id=? AND entry_id=?)",scope,local,scope,local)
                    else->fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                }
                if(exists!=1L)fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                // Pending removal stays in j_domain_state until an authoritative response.
            }
            is AccountDomainCommand.Listen -> {
                val trackId=p.id("local_user_track_ref_id");track(trackId)
                sql.mutate("j_listening",listOf(scope,local),"""INSERT INTO j_listening(scope_id,listening_id,track_ref_id,source_local_track_id,recording_id,played_ms,
                    duration_ms,completion_ratio,event_origin,context,explicit_feedback,excluded_from_taste,recommendation,source_created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",scope,local,trackId,trackId,p.nullId("recording_id"),p.number("played_ms",0,604_800_000),
                    p.nullNumber("track_duration_ms",1,604_800_000),if(p["completion_ratio"]==JsonNull)null else p.getValue("completion_ratio").jsonPrimitive.double,
                    p.string("event_origin",100),p.string("context",50),p.string("explicit_feedback",50),if(p.bool("excluded_from_taste"))1 else 0,
                    p["recommendation"]?.takeIf{it!=JsonNull}?.let{AccountPortableV1.canonical(it.jsonObject)},"")
            }
            is AccountDomainCommand.Interaction -> AccountInteractionProjectionWriter.local(sql,lease,witness,c)
        }
        val attribution=p["attribution"]?.takeIf{it!=JsonNull}?:p["recommendation"]?.takeIf{it!=JsonNull}
        attribution?.let{AccountCatalogProjectionWriter.attribution(sql,scope,change.operationId,type,local,it.jsonObject,"LOCAL")}
        val mapped=sql.query("SELECT server_id,observed_version FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?",scope,type,local){
            AccountUuid(it.getText(0)) to if(it.isNull(1))null else it.getLong(1)
        }.singleOrNull()
        val event=journal(change,type,eventType,p,mapped?.first,if(c is AccountDomainCommand.Listen || c is AccountDomainCommand.Interaction)null else mapped?.second)
        val receipt=AccountDomainReceipt(change.operationId,local,event,if(event==null)change.operationId else null)
        sql.mutate("j_domain_receipt",listOf(scope,change.operationId),"INSERT INTO j_domain_receipt(scope_id,operation_id,aggregate_id,event_id,intent_id) VALUES(?,?,?,?,?)",scope,change.operationId,local,receipt.eventId,receipt.localIntentId)
        val revision=sql.query("SELECT revision FROM j_domain_state WHERE scope_id=? AND aggregate_type=? AND local_id=?",scope,type,local){it.getLong(0)}.singleOrNull()?:0
        sql.mutate("j_domain_state",listOf(scope,type,local),"""INSERT INTO j_domain_state(scope_id,aggregate_type,local_id,revision,removed) VALUES(?,?,?,?,?)
            ON CONFLICT(scope_id,aggregate_type,local_id) DO UPDATE SET revision=excluded.revision,removed=excluded.removed""",scope,type,local,next(revision),if(c is AccountDomainCommand.Delete || c is AccountDomainCommand.Library && c.removed)1 else 0)
        AccountMediaProjectionFence.changed(sql,scope,local,type)
        AccountDomainPortableCapture.capture(sql,lease,witness,change)
        return receipt
    }
    private fun journal(change:AccountDomainChange,type:String,eventType:String,p:JsonObject,server:AccountUuid?,base:Long?):AccountUuid? {
        val scope=lease.scopeId;val plan=witness.preparation;val payload=AccountPortableV1.canonical(p)
        val remote = plan.scope.remote
        val binding = plan.binding
        require(payload.size<=DesktopAccountLimits.PAYLOAD_BYTES);AccountSyncWireV1.document(payload)
        accountEventCapacity(sql,payload.size.toLong()+4_096)
        if(remote==null || binding==null) {
            sql.mutate("j_local_intent",listOf(scope,change.operationId),"""INSERT INTO j_local_intent
                (scope_id,change_id,profile_id,aggregate_local_id,event_type,aggregate_type,schema_version,occurred_at_ms,canonical_payload,state)
                VALUES(?,?,?,?,?,?,1,?,?,'LOCAL')""",scope,change.operationId,lease.profileId,change.command.localId,eventType,type,change.occurredAtMs,payload)
            retainAccountIntentOrder(sql,scope,change.operationId)
            return null
        }
        val lineage=AccountUuid(sql.text("SELECT lineage_id FROM j_profile_alias WHERE scope_id=? AND profile_id=?",scope,lease.profileId)?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))
        val sequence=sql.number("SELECT next_sequence FROM j_lineage WHERE scope_id=? AND lineage_id=?",scope,lineage)
        val c=change.command
        fun known(type:String,local:AccountUuid):AccountUuid? {
            var current=sql.text("SELECT server_id FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND local_id=?",scope,type,local)?.let(::AccountUuid)?:return null
            val seen=HashSet<AccountUuid>();repeat(100){
                if(!seen.add(current))fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                val next=sql.text("SELECT canonical_server_id FROM j_redirect WHERE scope_id=? AND aggregate_type=? AND alias_server_id=?",scope,type,current)?.let(::AccountUuid)?:return current
                current=next
            };fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
        val references=when(c){
            is AccountDomainCommand.Library->AccountDomainReferences(trackServerId=known("USER_TRACK_REF",c.trackId))
            is AccountDomainCommand.Preference->AccountDomainReferences(trackServerId=known("USER_TRACK_REF",c.trackId))
            is AccountDomainCommand.Entry->AccountDomainReferences(known("USER_TRACK_REF",c.trackId),known("PLAYLIST",c.playlistId),c.beforeEntryId?.let{known("PLAYLIST_ENTRY",it)})
            is AccountDomainCommand.Listen->{
                val track=known("USER_TRACK_REF",c.payload.id("local_user_track_ref_id"));val supplied=c.payload.nullId("server_user_track_ref_id")
                if(supplied!=null && track!=null && supplied!=track)fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                AccountDomainReferences(trackServerId=track?:supplied)
            }
            else->AccountDomainReferences()
        }
        val event=AccountEventV1.createDomain(change,remote,binding,lease.profileId,sequence,server,base,references)
        sql.mutate("j_lineage",listOf(scope,lineage),"UPDATE j_lineage SET next_sequence=? WHERE scope_id=? AND lineage_id=?",next(sequence),scope,lineage)
        sql.mutate("j_event",listOf(scope,event.eventId),"""INSERT INTO j_event(scope_id,event_id,lineage_id,profile_id,user_id,device_id,sequence,idempotency_key,
            event_type,aggregate_type,aggregate_local_id,canonical_bytes,request_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",scope,event.eventId,lineage,event.profileId,
            event.userId,event.deviceId,event.sequence,event.idempotencyKey,event.eventType,event.aggregateType,event.aggregateLocalId,event.canonicalBytes,event.requestHash)
        sql.mutate("j_event_delivery",listOf(scope,event.eventId),"INSERT INTO j_event_delivery(scope_id,event_id,state,attempt,next_retry_ms) VALUES(?,?,'PENDING',0,0)",scope,event.eventId)
        return event.eventId
    }
}
internal fun accountEventCapacity(sql:AccountPageSql,additional:Long):Unit {
    val count=sql.number("SELECT count(*) FROM j_event_delivery WHERE state NOT IN ('ACKNOWLEDGED','REJECTED')") + sql.number("""SELECT count(*) FROM j_local_intent i
        WHERE i.materialized_event_id IS NULL AND NOT EXISTS(SELECT 1 FROM j_account_intent_link m WHERE m.source_scope_id=i.scope_id AND m.change_id=i.change_id)""")
    val bytes=accountEventCopyBytes(sql)
    if(count>=DesktopAccountLimits.PENDING_EVENTS || additional>DesktopAccountLimits.EVENT_COPY_BYTES-bytes) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
}
internal fun accountEventCopyBytes(sql:AccountPageSql):Long=sql.number("""SELECT
    (SELECT coalesce(sum(length(canonical_bytes)),0) FROM j_event)+
    (SELECT coalesce(sum(length(canonical_payload)),0) FROM j_local_intent)+
    (SELECT coalesce(sum(length(exact_fact)),0) FROM j_source_copy)+
    (SELECT coalesce(sum(length(exact_selection)),0) FROM j_source_selection)+
    (SELECT coalesce(sum(length(exact_selection)),0) FROM j_account_intent_selection)+
    (SELECT coalesce(sum(length(exact_input)),0) FROM j_repository_commit WHERE kind IN ('DOMAIN','CHECKPOINT','SOURCE_COPY','SOURCE_MATERIALIZE','ROOT_INTENT_PREPARE','ROOT_INTENT_MATERIALIZE','PRESENTED','CONFLICT_REPAIR'))""")
