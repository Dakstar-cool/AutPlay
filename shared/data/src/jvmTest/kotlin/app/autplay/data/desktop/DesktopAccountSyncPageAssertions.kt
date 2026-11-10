package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import app.autplay.application.desktop.account.*
import app.autplay.data.desktop.account.AccountPrivateFiles
import app.autplay.data.desktop.account.DesktopAccountPageSchema
import app.autplay.data.desktop.account.DesktopAccountSqliteStore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.*
import org.junit.Assert.*

/**
 * FUTURE real synthetic SQLite instructions, deliberately UNCALLED. No @Test or composite call.
 * No vectors/hashes/native calls were executed to author this file. POSIX disposable volume only.
 * All helpers live here. Ambiguous retained-lock cuts below are ISOLATED-PROCESS ONLY and uncalled.
 * Source fixtures cannot qualify real Keychain, HTTP, B1, macOS packaging, fsync or privacy rollout.
 */
internal suspend fun desktopAccountSyncPageAssertions(): Unit {
    fun uuid(): AccountUuid = AccountUuid.random()
    val homes = ArrayList<Path>()
    val stores = ArrayList<DesktopAccountStore>()
    val deliveries = LinkedHashMap<DesktopAccountStore, AccountDeliveryLease>()
    val captures = LinkedHashMap<DesktopAccountStore, AccountPageCapture>()
    var clock = 1_800_000_000_000L
    suspend fun refused(code: AccountFailure, action: suspend () -> Unit): Unit {
        try { action(); fail("expected ${code.name}") }
        catch (failure: AccountStoreException) { assertEquals(code, failure.failure) }
    }
    fun string(value: String): JsonPrimitive = JsonPrimitive(value)
    fun id(value: AccountUuid): JsonPrimitive = string(value.value)
    fun bytes(value: JsonObject): ByteArray = value.toString().toByteArray(Charsets.UTF_8)
    fun objectOf(vararg values: Pair<String, JsonElement>): JsonObject = JsonObject(mapOf(*values))
    class Protected : DesktopAccountAuthorityPort {
        var storeState: AccountProtectedStoreState = AccountProtectedStoreState.Reserved(uuid())
        var plan: AccountScopePreparation? = null
        var stage = AccountProtectedStage.PREPARING
        var allowed = true
        var failClose = false
        val family = uuid()
        val held = AtomicBoolean(false)
        val gate = AtomicBoolean(false)
        override fun tryAcquireLifetime(): AccountProtectedLifetime? {
            if (!held.compareAndSet(false, true)) return null
            return object : AccountProtectedLifetime {
                override val storeState: AccountProtectedStoreState get() = this@Protected.storeState
                override fun completeInstallation(storeId: AccountUuid): Boolean {
                    if (!held.get() || !gate.compareAndSet(false, true)) return false
                    return try {
                        when (val captured = this@Protected.storeState) {
                            is AccountProtectedStoreState.Reserved -> if (captured.storeId == storeId) {
                                this@Protected.storeState = AccountProtectedStoreState.Existing(storeId); true
                            } else false
                            is AccountProtectedStoreState.Existing -> captured.storeId == storeId
                            else -> false
                        }
                    } finally { assertTrue(gate.compareAndSet(true, false)) }
                }
                override fun tryCapture(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture? {
                    if (plan != preparation || storeState != AccountProtectedStoreState.Existing(expectedStoreId) || !gate.compareAndSet(false, true)) return null
                    val observed = AccountProtectedWitness(expectedStoreId, preparation, stage, family, 1, 1, allowed, true)
                    return object : AccountProtectedCapture {
                        override val witness: AccountProtectedWitness = observed
                        override fun completeReconciliation(readback: AccountActivationReadback): AccountProtectedWitness {
                            assertEquals(preparation, readback.preparation)
                            stage = AccountProtectedStage.READY
                            return observed.copy(stage = stage)
                        }
                        override fun close(): Unit {
                            assertTrue(gate.compareAndSet(true, false))
                            if (failClose) { failClose = false; throw IllegalStateException("FIXTURE_CAPTURE_CLOSE") }
                        }
                    }
                }
                override fun close(): Unit { assertFalse(gate.get()); assertTrue(held.compareAndSet(true, false)) }
            }
        }
    }
    class Faults {
        var cut = "NONE"
        var failPrefix: String? = null
        var unreadableMarker = false
        var contradictoryMarker = false
        var unavailableFence = false
        var commits = 0
        var statements = 0
        var observeCommit: ((SQLiteConnection) -> Unit)? = null
        var reportedPageCount: Long? = null // Admission seam only; never claims a native FULL fault.
        fun wrap(sql: SQLiteConnection): SQLiteConnection = object : SQLiteConnection by sql {
            override fun prepare(query: String): SQLiteStatement {
                statements++
                val delegate = sql.prepare(query)
                return object : SQLiteStatement by delegate {
                    override fun step(): Boolean {
                        if (unreadableMarker && query.startsWith("SELECT scope_id,profile_id,lineage_id,activation_revision,kind,state,has_more,ack_through,safe_wire")) {
                            throw IllegalStateException("FIXTURE_READBACK")
                        }
                        if (unavailableFence && query in setOf("BEGIN IMMEDIATE", "ROLLBACK")) throw IllegalStateException("FIXTURE_FENCE")
                        if (failPrefix?.let(query::startsWith) == true) {
                            failPrefix = null
                            throw IllegalStateException("FIXTURE_STATEMENT")
                        }
                        if (query == "COMMIT") {
                            commits++
                            val fault = cut
                            cut = "NONE"
                            if (fault == "BEFORE") throw IllegalStateException("FIXTURE_COMMIT")
                            val value = delegate.step()
                            observeCommit?.invoke(sql) // Read only, on the SAME settled serial writer.
                            unreadableMarker = fault == "AFTER_UNREADABLE"
                            contradictoryMarker = fault == "AFTER_CONTRADICTORY"
                            unavailableFence = fault == "AFTER_FENCE"
                            if (fault.startsWith("AFTER")) throw IllegalStateException("FIXTURE_COMMIT")
                            return value
                        }
                        return delegate.step()
                    }
                    override fun getLong(index: Int): Long {
                        val value = delegate.getLong(index)
                        if (query == "PRAGMA page_count" && index == 0) reportedPageCount?.let { return it }
                        return if (contradictoryMarker && index == 3 && query.startsWith("SELECT scope_id,profile_id,lineage_id,activation_revision,kind,state,has_more,ack_through,safe_wire")) value + 1 else value
                    }
                }
            }
        }
    }
    fun home(): Path = Files.createTempDirectory("autplay-j1-page-fixture-",
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"))).toRealPath().also(homes::add)
    fun owner(root: Path, k: Protected, faults: Faults = Faults()): DesktopAccountStore = DesktopAccountSqliteStore(
        k, AccountPrivateFiles(root), wallMs = { clock }, monotonicNanos = { clock * 1_000_000L },
        openConnection = { faults.wrap(BundledSQLiteDriver().open(it)) },
    ).also(stores::add)
    suspend fun select(store: DesktopAccountStore, k: Protected, plan: AccountScopePreparation): AccountLease {
        k.plan = plan; k.stage = AccountProtectedStage.PREPARING
        store.prepareScope(plan)
        k.stage = AccountProtectedStage.BINDING_COMMIT_PENDING
        return store.activateScope(plan.activationId)
    }
    data class Fixture(val root: Path, val k: Protected, val faults: Faults, val store: DesktopAccountStore,
        val plan: AccountScopePreparation, val lease: AccountLease)
    suspend fun fixture(): Fixture {
        val root = home(); val k = Protected(); val faults = Faults(); val store = owner(root, k, faults)
        val reserved = (k.storeState as AccountProtectedStoreState.Reserved).storeId
        assertEquals(reserved, store.initialize().storeId)
        assertEquals(AccountProtectedStoreState.Existing(reserved), k.storeState)
        val plan = AccountScopePreparation(uuid(), AccountScopeIdentity(uuid(), uuid(),
            AccountRemoteOwner(uuid(), uuid())), uuid(),
            AccountRemoteBinding(uuid(), uuid(), 1, "b".repeat(64), "c".repeat(64)), 1, 0, activationId = uuid())
        return Fixture(root, k, faults, store, plan, select(store, k, plan))
    }
    fun pages(f: Fixture): DesktopAccountSyncPages = checkNotNull(f.store.syncPages)
    fun header(f: Fixture): Map<String, JsonElement> = mapOf("protocol_version" to JsonPrimitive(1),
        "device_id" to id(checkNotNull(f.plan.binding).deviceId), "server_profile_id" to id(f.plan.profileId),
        "journal_epoch" to id(checkNotNull(f.plan.binding).journalEpoch), "server_time" to string("2026-10-16T10:00:00Z"))
    fun change(): AccountCreateTrack = AccountCreateTrack(uuid(), uuid(), uuid(), "Local title", "Local artist", clock)
    suspend fun claim(f: Fixture): AccountDeliveryClaim = checkNotNull(f.store.claimPending(f.lease).awaitResult()).also { deliveries[f.store] = it.lease }
    suspend fun capture(f: Fixture, kind: AccountPageKind): AccountPageCapture = pages(f).capturePage(f.lease, kind).awaitResult().also { captures[f.store] = it }
    suspend fun release(f: Fixture, capture: AccountPageCapture): Unit { pages(f).settlePage(capture); captures.remove(f.store) }
    fun ack(event: AccountEventV1, outcome: String = "APPLIED", extra: Map<String, JsonElement> = emptyMap()): JsonObject = JsonObject(mapOf(
        "event_id" to id(event.eventId), "device_sequence" to JsonPrimitive(event.sequence),
        "aggregate_type" to string(event.aggregateType), "aggregate_local_id" to id(event.aggregateLocalId),
        "aggregate_server_id" to id(event.aggregateLocalId), "outcome" to string(outcome), "server_row_version" to JsonPrimitive(1),
    ) + extra)
    fun ackPage(f: Fixture, through: Long, items: List<JsonObject>): ByteArray = bytes(JsonObject(header(f) + mapOf(
        "acknowledged_through_device_sequence" to JsonPrimitive(through), "acks" to JsonArray(items))))
    suspend fun applyAck(f: Fixture, claim: AccountDeliveryClaim, through: Long, items: List<JsonObject>): AccountPageReceipt =
        pages(f).applyAckPage(claim.lease, ackPage(f, through, items)).awaitResult().also { deliveries.remove(f.store) }
    fun aggregate(type: String, server: AccountUuid, payload: JsonObject, version: Long = 1): JsonObject = objectOf(
        "aggregate_type" to string(type), "aggregate_server_id" to id(server), "server_row_version" to JsonPrimitive(version), "payload" to payload)
    fun event(type: String, server: AccountUuid, eventType: String, sequence: Long, payload: JsonObject,
        eventId: AccountUuid = uuid(), operation: String = "UPSERT", extra: Map<String, JsonElement> = emptyMap()): JsonObject =
        JsonObject(aggregate(type, server, payload) + mapOf("event_id" to id(eventId), "server_sequence" to JsonPrimitive(sequence),
            "origin_device_id" to JsonNull, "event_type" to string(eventType), "schema_version" to JsonPrimitive(1),
            "operation" to string(operation), "created_at" to string("2026-10-16T10:00:00Z")) + extra)
    fun bootstrap(f: Fixture, snapshot: AccountUuid, items: List<JsonObject>, more: Boolean = false,
        next: String? = null, final: String = "final-cursor-00000001", high: Long = 20,
        tombstones: List<JsonObject> = emptyList(), redirects: List<JsonObject> = emptyList()): ByteArray = bytes(JsonObject(header(f) + mapOf(
        "snapshot_id" to id(snapshot), "snapshot_high_water_server_sequence" to JsonPrimitive(high),
        "aggregates" to JsonArray(items), "tombstones" to JsonArray(tombstones), "redirects" to JsonArray(redirects),
        "next_page_token" to (next?.let(::string) ?: JsonNull), "has_more" to JsonPrimitive(more),
        "snapshot_cursor" to string(final), "pending_event_directive" to string("PRESERVE_REBASE_RETRY"))))
    fun pull(f: Fixture, captured: AccountPageCapture, items: List<JsonObject>, next: String = "next-cursor-00000001"): ByteArray =
        bytes(JsonObject(header(f) + mapOf("from_cursor" to (captured.original.cursor?.let(::string) ?: JsonNull),
            "next_cursor" to string(next), "has_more" to JsonPrimitive(false), "events" to JsonArray(items))))
    suspend fun boot(f: Fixture, response: ByteArray): AccountPageReceipt {
        val captured = capture(f, AccountPageKind.BOOTSTRAP)
        return pages(f).applyBootstrapPage(captured, response).awaitResult().also { captures.remove(f.store) }
    }
    suspend fun ready(f: Fixture): Unit { assertEquals("APPLIED", boot(f, bootstrap(f, uuid(), emptyList())).state) }
    suspend fun reopen(f: Fixture): Fixture {
        f.store.close()
        val next = owner(f.root, f.k, f.faults)
        next.initialize()
        val activation = f.plan.copy(activationId = uuid(), expectedAuthorityRevision = f.lease.authorityRevision + 1,
            expectedActivationRevision = f.lease.activationRevision)
        return f.copy(store = next, plan = activation, lease = select(next, f.k, activation))
    }
    suspend fun applyPull(f: Fixture, items: List<JsonObject>): AccountPageReceipt {
        val c = capture(f, AccountPageKind.PULL)
        return pages(f).applyPullPage(c, pull(f, c, items)).awaitResult().also { captures.remove(f.store) }
    }
    suspend fun restartSnapshot(f: Fixture): Unit {
        val state = pages(f).readSyncState(f.lease)
        val c = capture(f, if (state.bootstrapState == "READY") AccountPageKind.PULL else AccountPageKind.BOOTSTRAP)
        pages(f).resetPage(c, AccountPageReset.USER_REQUEST).awaitResult(); captures.remove(f.store)
    }
    suspend fun projections(f: Fixture): List<AccountDomainProjection> {
        val result = ArrayList<AccountDomainProjection>()
        for (kind in AccountProjectionKind.entries) {
            var after: AccountUuid? = null
            var finished = false
            repeat(20) { // This helper's controls contain <=1000 rows/family, never the expansion seed.
                if (!finished) {
                    val batch = pages(f).readProjectionPage(f.lease, kind, after)
                    result.addAll(batch); after = batch.lastOrNull()?.header?.localId
                    finished = batch.size < 100
                }
            }
            check(finished)
        }
        return result
    }
    fun listeningPayload(track: AccountUuid): JsonObject = objectOf(
        "interaction_type" to string("LISTENING_EVENT_RECORDED"), "local_user_track_ref_id" to id(uuid()),
        "server_user_track_ref_id" to id(track), "recording_id" to JsonNull, "played_ms" to JsonPrimitive(1200),
        "track_duration_ms" to JsonNull, "completion_ratio" to JsonNull, "event_origin" to string("ORGANIC"),
        "context" to string("GENERAL"), "explicit_feedback" to string("NONE"),
        "excluded_from_taste" to JsonPrimitive(false), "recommendation" to JsonNull)
    fun closedSql(root: Path, action: (SQLiteConnection) -> Unit): Unit =
        BundledSQLiteDriver().open(AccountPrivateFiles(root).database.toString()).use(action)
    fun scalar(sql: SQLiteConnection, query: String): Long = sql.prepare(query).use {
        assertTrue(it.step()); val value = it.getLong(0); assertFalse(it.step()); value
    }
    suspend fun loseResult(ticket: AccountPageOperation<*>): Unit {
        try { withTimeout(1) { ticket.awaitOutcome(); awaitCancellation() } }
        catch (_: TimeoutCancellationException) { /* The SAME ticket survives. */ }
    }

    // UNCALLED ISOLATED-PROCESS obligation: no in-process finally may close/delete this owner.
    suspend fun ambiguousPageWorkerOnly(f: Fixture, phase: String, cut: String): Unit {
        require(cut in setOf("AFTER_UNREADABLE", "AFTER_CONTRADICTORY", "AFTER_FENCE"))
        val captured = if (phase == "ACK") null else capture(f, AccountPageKind.BOOTSTRAP)
        val delivery = if (phase == "ACK") { f.store.createTrack(f.lease, change()); claim(f) } else null
        val before = f.faults.commits
        f.faults.cut = cut
        val ticket = if (delivery != null) pages(f).applyAckPage(delivery.lease, ackPage(f, 1, listOf(ack(delivery.events.single()))))
            else pages(f).applyBootstrapPage(checkNotNull(captured), bootstrap(f, uuid(), emptyList()))
        refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
        assertTrue(ticket.awaitOutcome() is AccountPageOutcome.Failed)
        assertEquals(before + 1, f.faults.commits)
        refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { f.store.close() }
        assertTrue(f.k.held.get())
        assertSame(ticket.awaitOutcome(), ticket.awaitOutcome())
        // Reviewed child harness must terminate this process, retaining locks until actual exit.
    }

    try {
        suspend fun ackReplayAndAtomicCases(): Unit {
            // Terminal suffix cannot jump a missing ACK. A duplicate replay closes the exact gap.
            val gap = fixture()
            gap.store.createTrack(gap.lease, change()); gap.store.createTrack(gap.lease, change())
            val initial = claim(gap)
            val originals = initial.events.map { it.canonicalBytes }
            assertEquals(0L, applyAck(gap, initial, 2, listOf(ack(initial.events[1]))).acknowledgedThrough)
            assertEquals(listOf("PENDING", "ACKNOWLEDGED"), gap.store.readDelivery(gap.lease, null).sortedBy { it.sequence }.map { it.rawState })
            clock += 60_001
            val retry = claim(gap)
            assertEquals(listOf(1L), retry.events.map { it.sequence })
            assertArrayEquals(originals[0], retry.events.single().canonicalBytes)
            assertEquals(2L, applyAck(gap, retry, 2, listOf(ack(retry.events.single(), "DUPLICATE", mapOf("original_outcome" to string("APPLIED"))))).acknowledgedThrough)
            gap.store.createTrack(gap.lease, change())
            val third = claim(gap)
            assertEquals(3L, third.events.single().sequence)
            for (bad in listOf(
                listOf(ack(third.events.single()), ack(third.events.single())),
                listOf(JsonObject(ack(third.events.single()) + ("event_id" to id(uuid())))),
                listOf(JsonObject(ack(third.events.single()) + ("device_sequence" to JsonPrimitive(4)))),
                listOf(JsonObject(ack(third.events.single()) + ("aggregate_local_id" to id(uuid())))),
            )) refused(AccountFailure.ACCOUNT_INPUT_INVALID) { pages(gap).applyAckPage(third.lease, ackPage(gap, 3, bad)).awaitResult() }
            val unknown = JsonObject(ack(third.events.single(), "FUTURE_OUTCOME") - "server_row_version")
            assertEquals(2L, applyAck(gap, third, 2, listOf(unknown)).acknowledgedThrough)
            clock += 60_001
            val finalThird = claim(gap)
            assertArrayEquals(third.events.single().canonicalBytes, finalThird.events.single().canonicalBytes)
            applyAck(gap, finalThird, 3, listOf(ack(finalThird.events.single())))
            gap.store.close()
            closedSql(gap.root) { sql ->
                assertEquals(4L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                assertEquals(3L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
                assertEquals(3L, scalar(sql, "SELECT count(*) FROM j_ack"))
                sql.prepare("SELECT canonical_bytes FROM j_event ORDER BY sequence").use {
                    originals.forEach { original -> assertTrue(it.step()); assertArrayEquals(original, it.getBlob(0)) }
                }
            }

            // ID/version/redirect writes roll back with delivery and checkpoint at the intended cut.
            val atomic = fixture(); atomic.store.createTrack(atomic.lease, change()); val a = claim(atomic)
            val redirect = objectOf("alias_server_id" to id(a.events.single().aggregateLocalId), "canonical_server_id" to id(uuid()))
            val correlated = ack(a.events.single(), extra = mapOf("server_row_version" to JsonPrimitive(7), "redirect" to redirect))
            atomic.faults.failPrefix = "INSERT INTO j_ack"
            refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { applyAck(atomic, a, 1, listOf(correlated)) }
            assertNull(atomic.store.readTracks(atomic.lease, null).single().serverTrackRefId)
            assertTrue(pages(atomic).readProjectionPage(atomic.lease, AccountProjectionKind.TRACK, null).isEmpty())
            assertEquals("IN_FLIGHT", atomic.store.readDelivery(atomic.lease, null).single().rawState)
            applyAck(atomic, a, 1, listOf(correlated))
            val projected = pages(atomic).readProjectionPage(atomic.lease, AccountProjectionKind.TRACK, null).single() as AccountRemoteTrack
            assertEquals(7L, projected.header.observedVersion)
            assertEquals(redirect["canonical_server_id"]!!.jsonPrimitive.content, projected.header.canonicalServerId.value)
            assertFalse(projected.header.pending)
        }
        ackReplayAndAtomicCases()

        suspend fun bootstrapAndFactCases(): Unit {
            // Multiple bootstrap pages, dependencies before parents, explicit null, dirty adopted create.
            val multi = fixture(); val localChange = change(); multi.store.createTrack(multi.lease, localChange)
            val remoteTrack = uuid(); val remoteLibrary = uuid(); val snapshot = uuid()
            val library = aggregate("LIBRARY_ENTRY", remoteLibrary, objectOf("server_user_track_ref_id" to id(remoteTrack),
                "source" to string("IMPORT"), "availability_status" to string("PENDING")))
            assertTrue(boot(multi, bootstrap(multi, snapshot, listOf(library), true, "page-token-00000001")).hasMore)
            assertNull(pages(multi).readSyncState(multi.lease).cursor)
            val incomplete = pages(multi).readProjectionPage(multi.lease, AccountProjectionKind.TRACK, null).single() as AccountRemoteTrack
            assertEquals("DEPENDENCY", incomplete.header.rawState); assertNull(incomplete.title)
            val next = capture(multi, AccountPageKind.BOOTSTRAP)
            assertEquals(snapshot, next.requestSnapshotId); assertEquals("page-token-00000001", next.requestPageToken)
            val remote = aggregate("USER_TRACK_REF", remoteTrack, objectOf("title" to JsonNull, "artist" to string("Remote artist")))
            val dirty = aggregate("USER_TRACK_REF", localChange.trackRefId, objectOf("title" to string("Server title"), "artist" to string("Server artist")))
            assertEquals("APPLIED", pages(multi).applyBootstrapPage(next, bootstrap(multi, snapshot, listOf(remote, dirty))).awaitResult().state)
            captures.remove(multi.store)
            assertEquals("READY", pages(multi).readSyncState(multi.lease).bootstrapState)
            assertEquals("final-cursor-00000001", pages(multi).readSyncState(multi.lease).cursor)
            assertEquals("Local title", multi.store.readTracks(multi.lease, null).single { it.trackRefId == localChange.trackRefId }.title)
            assertEquals("PENDING", multi.store.readDelivery(multi.lease, null).single().rawState)

            // Pull dedupe/dependency/unknown page, immutable cursor on deferral and bounded exact bytes.
            val p = capture(multi, AccountPageKind.PULL)
            val playlistId = uuid(); val entryId = uuid()
            val playlist = event("PLAYLIST", playlistId, "PLAYLIST_CREATED", 21, objectOf("name" to string("Playlist"), "description" to JsonNull))
            val entry = event("PLAYLIST_ENTRY", entryId, "PLAYLIST_ENTRY_UPSERTED", 22, objectOf("server_playlist_id" to id(playlistId),
                "server_user_track_ref_id" to id(remoteTrack), "position_key" to string("a0")))
            val pullBytes = pull(multi, p, listOf(playlist, entry))
            assertEquals("APPLIED", pages(multi).applyPullPage(p, pullBytes).awaitResult().state); captures.remove(multi.store)
            val duplicateCapture = capture(multi, AccountPageKind.PULL)
            assertEquals("APPLIED", pages(multi).applyPullPage(duplicateCapture, pull(multi, duplicateCapture, listOf(playlist, entry), "next-cursor-00000002")).awaitResult().state)
            captures.remove(multi.store)
            assertEquals(1, pages(multi).readProjectionPage(multi.lease, AccountProjectionKind.PLAYLIST_ENTRY, null).size)
            val blocked = capture(multi, AccountPageKind.PULL)
            val future = event("FUTURE_AGGREGATE", uuid(), "FUTURE_EVENT", 23, objectOf("future_value" to string("retain")))
            val safeFuture = pull(multi, blocked, listOf(future), "next-cursor-00000003")
            assertEquals("DEFERRED", pages(multi).applyPullPage(blocked, safeFuture).awaitResult().state); captures.remove(multi.store)
            assertEquals(blocked.original.cursor, pages(multi).readSyncState(multi.lease).cursor)
            val refusedPage = capture(multi, AccountPageKind.PULL)
            refused(AccountFailure.ACCOUNT_DELIVERY_BLOCKED) { pages(multi).applyPullPage(refusedPage, pull(multi, refusedPage, emptyList())).awaitResult() }
            release(multi, refusedPage)
            multi.store.close()
            closedSql(multi.root) { sql ->
                assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event"))
                assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                assertEquals(2L, scalar(sql, "SELECT count(*) FROM j_applied_event"))
                assertTrue(scalar(sql, "SELECT count(*) FROM j_conflict") > 0)
                sql.prepare("SELECT r.safe_wire FROM j_deferred_page d JOIN j_page_result r ON r.operation_id=d.operation_id").use {
                    assertTrue(it.step()); assertArrayEquals(safeFuture, it.getBlob(0))
                }
            }

            // Preference/listening projections use actual canonical ref identity; no guessed start time.
            val facts = fixture(); ready(facts)
            val fc = capture(facts, AccountPageKind.PULL); val trackId = uuid()
            val track = event("USER_TRACK_REF", trackId, "USER_TRACK_REF_CREATED", 21, objectOf("title" to string("T"), "artist" to string("A")))
            val preference = event("USER_TRACK_PREFERENCE", trackId, "USER_TRACK_PREFERENCE_SET", 22, objectOf("preference" to string("LIKED"), "excluded_from_taste" to JsonPrimitive(false)))
            val listeningId = uuid()
            val listen = event("LISTENING_EVENT", listeningId, "LISTENING_EVENT_RECORDED", 23, objectOf(
                "interaction_type" to string("LISTENING_EVENT_RECORDED"), "local_user_track_ref_id" to id(uuid()),
                "server_user_track_ref_id" to id(trackId), "recording_id" to JsonNull, "played_ms" to JsonPrimitive(1200),
                "track_duration_ms" to JsonNull, "completion_ratio" to JsonNull, "event_origin" to string("ORGANIC"),
                "context" to string("GENERAL"), "explicit_feedback" to string("NONE"), "excluded_from_taste" to JsonPrimitive(false), "recommendation" to JsonNull), eventId = listeningId)
            pages(facts).applyPullPage(fc, pull(facts, fc, listOf(track, preference, listen))).awaitResult(); captures.remove(facts.store)
            assertEquals("LIKED", (pages(facts).readProjectionPage(facts.lease, AccountProjectionKind.PREFERENCE, null).single() as AccountRemotePreference).rawPreference)
            assertEquals(1200L, (pages(facts).readProjectionPage(facts.lease, AccountProjectionKind.LISTENING, null).single() as AccountRemoteListening).playedMs)

            val deletedEvent = uuid(); val tombstone = uuid(); val deleteCapture = capture(facts, AccountPageKind.PULL)
            val deletion = event("USER_TRACK_REF", trackId, "AGGREGATE_DELETED", 24, objectOf(), deletedEvent, "DELETE", mapOf(
                "tombstone" to objectOf("tombstone_id" to id(tombstone), "deleted_by_event_id" to id(deletedEvent),
                    "deleted_at" to string("2026-10-16T11:00:00Z"), "retain_until" to string("2026-11-16T11:00:00Z"))))
            pages(facts).applyPullPage(deleteCapture, pull(facts, deleteCapture, listOf(deletion), "facts-cursor-00000002")).awaitResult(); captures.remove(facts.store)
            assertTrue(pages(facts).readProjectionPage(facts.lease, AccountProjectionKind.TRACK, null).single().header.deleted)
            val staleEdit = capture(facts, AccountPageKind.PULL)
            pages(facts).applyPullPage(staleEdit, pull(facts, staleEdit, listOf(event("USER_TRACK_REF", trackId,
                "USER_TRACK_REF_PATCHED", 25, objectOf("title" to string("Old edit")))), "facts-cursor-00000003")).awaitResult(); captures.remove(facts.store)
            assertEquals("T", (pages(facts).readProjectionPage(facts.lease, AccountProjectionKind.TRACK, null).single() as AccountRemoteTrack).title)

            // A retained tombstone may coexist with a live restored aggregate in the SAME snapshot.
            // A later snapshot lacking the live member exposes deletion without destroying the cached row.
            val restored = fixture(); val restoredTrack = uuid(); val restoredEntry = uuid()
            val oldTombstone = objectOf("tombstone_id" to id(uuid()), "aggregate_type" to string("LIBRARY_ENTRY"),
                "aggregate_server_id" to id(restoredEntry), "deleted_by_event_id" to id(uuid()),
                "deleted_at" to string("2026-10-01T00:00:00Z"), "retain_until" to string("2026-11-01T00:00:00Z"))
            val liveTrack = aggregate("USER_TRACK_REF", restoredTrack, objectOf("title" to string("Restored"), "artist" to string("A")), 3)
            val liveEntry = aggregate("LIBRARY_ENTRY", restoredEntry, objectOf("server_user_track_ref_id" to id(restoredTrack)), 3)
            boot(restored, bootstrap(restored, uuid(), listOf(liveEntry, liveTrack), tombstones = listOf(oldTombstone)))
            assertFalse(pages(restored).readProjectionPage(restored.lease, AccountProjectionKind.LIBRARY, null).single().header.deleted)
            val requestSnapshot = capture(restored, AccountPageKind.PULL)
            pages(restored).resetPage(requestSnapshot, AccountPageReset.USER_REQUEST).awaitResult(); captures.remove(restored.store)
            boot(restored, bootstrap(restored, uuid(), listOf(liveTrack), tombstones = listOf(oldTombstone)))
            assertTrue(pages(restored).readProjectionPage(restored.lease, AccountProjectionKind.LIBRARY, null).single().header.deleted)
            val restoreCapture = capture(restored, AccountPageKind.PULL)
            val restoreEvent = JsonObject(event("LIBRARY_ENTRY", restoredEntry, "LIBRARY_ENTRY_UPSERTED", 21,
                objectOf("removed_at_ms" to JsonNull)) + ("server_row_version" to JsonPrimitive(4)))
            pages(restored).applyPullPage(restoreCapture, pull(restored, restoreCapture, listOf(restoreEvent))).awaitResult(); captures.remove(restored.store)
            assertFalse(pages(restored).readProjectionPage(restored.lease, AccountProjectionKind.LIBRARY, null).single().header.deleted)

            val redirects = fixture(); ready(redirects); val alias = uuid(); val canonical = uuid()
            val redirectCapture = capture(redirects, AccountPageKind.PULL)
            val firstRedirect = event("RECORDING", alias, "AGGREGATE_REDIRECTED", 21, objectOf(), operation = "REDIRECT",
                extra = mapOf("redirect" to objectOf("alias_server_id" to id(alias), "canonical_server_id" to id(canonical))))
            pages(redirects).applyPullPage(redirectCapture, pull(redirects, redirectCapture, listOf(firstRedirect))).awaitResult(); captures.remove(redirects.store)
            val cycleCapture = capture(redirects, AccountPageKind.PULL)
            val cycle = event("RECORDING", canonical, "AGGREGATE_REDIRECTED", 22, objectOf(), operation = "REDIRECT",
                extra = mapOf("redirect" to objectOf("alias_server_id" to id(canonical), "canonical_server_id" to id(alias))))
            assertEquals("DEFERRED", pages(redirects).applyPullPage(cycleCapture, pull(redirects, cycleCapture, listOf(cycle))).awaitResult().state)
            captures.remove(redirects.store)
            assertEquals(cycleCapture.original.cursor, pages(redirects).readSyncState(redirects.lease).cursor)
        }
        bootstrapAndFactCases()

        // Exact wrong owner/profile/device/epoch cannot change cursor/projections; stale capture cannot settle a successor.
        val guards = fixture(); ready(guards)
        for (field in listOf("device_id", "journal_epoch", "server_profile_id", "user_id")) {
            val captured = capture(guards, AccountPageKind.PULL)
            val wrong = JsonObject(Json.parseToJsonElement(pull(guards, captured, emptyList()).toString(Charsets.UTF_8)).jsonObject + (field to id(uuid())))
            refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { pages(guards).applyPullPage(captured, bytes(wrong)).awaitResult() }
            assertEquals(captured.original.cursor, pages(guards).readSyncState(guards.lease).cursor)
            release(guards, captured)
        }
        val old = capture(guards, AccountPageKind.PULL); release(guards, old)
        val newer = capture(guards, AccountPageKind.PULL)
        refused(AccountFailure.ACCOUNT_LEASE_STALE) { pages(guards).settlePage(old) }
        refused(AccountFailure.ACCOUNT_LEASE_STALE) { pages(guards).applyPullPage(old, pull(guards, old, emptyList())).awaitResult() }
        guards.store.retire()
        refused(AccountFailure.ACCOUNT_CLOSED) { pages(guards).applyPullPage(newer, pull(guards, newer, emptyList())).awaitResult() }
        refused(AccountFailure.ACCOUNT_BUSY) { guards.store.close() }
        assertTrue(guards.k.held.get()); release(guards, newer); guards.store.close()

        // Lost authority admits neither a late ACK nor a late page; exact existing settlement drains it.
        val late = fixture(); late.store.createTrack(late.lease, change()); val sent = claim(late)
        late.k.allowed = false
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { applyAck(late, sent, 1, listOf(ack(sent.events.single()))) }
        late.store.failDelivery(sent.lease, AccountDeliveryFailure.AUTHORITY_UNAVAILABLE); deliveries.remove(late.store)
        late.store.close()
        closedSql(late.root) { sql ->
            assertEquals(0L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
            assertEquals(0L, scalar(sql, "SELECT count(*) FROM j_ack"))
            assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event_delivery WHERE state='AUTHORITY_BLOCKED'"))
        }
        val latePage = fixture(); ready(latePage); val oldOwner = capture(latePage, AccountPageKind.PULL)
        latePage.k.allowed = false
        refused(AccountFailure.ACCOUNT_SCOPE_CHANGED) { pages(latePage).applyPullPage(oldOwner, pull(latePage, oldOwner, emptyList())).awaitResult() }
        release(latePage, oldOwner); latePage.store.close()

        // A confirmed NEW APPLIED effect after an older snapshot stays visible without a pull round trip.
        val afterSnapshot = fixture(); ready(afterSnapshot)
        afterSnapshot.store.createTrack(afterSnapshot.lease, change()); val newCreate = claim(afterSnapshot)
        applyAck(afterSnapshot, newCreate, 1, listOf(ack(newCreate.events.single())))
        assertEquals(1, afterSnapshot.store.readTracks(afterSnapshot.lease, null).size)
        assertEquals(true, pages(afterSnapshot).readProjectionPage(afterSnapshot.lease, AccountProjectionKind.TRACK, null).single().header.snapshotConfirmed)

        // Snapshot expiry/reset preserves old snapshot evidence, cursor, pending outbox and allocator.
        val reset = fixture(); reset.store.createTrack(reset.lease, change())
        val expiredId = uuid()
        boot(reset, bootstrap(reset, expiredId, listOf(aggregate("LIBRARY_ENTRY", uuid(),
            objectOf("server_user_track_ref_id" to id(uuid())))), true, "expired-page-000001"))
        val expired = capture(reset, AccountPageKind.BOOTSTRAP)
        pages(reset).resetPage(expired, AccountPageReset.BOOTSTRAP_SNAPSHOT_INVALID).awaitResult(); captures.remove(reset.store)
        val state = pages(reset).readSyncState(reset.lease)
        assertEquals("RESET_REQUIRED", state.state); assertEquals(expiredId, state.snapshotId); assertNull(state.cursor)
        val restart = capture(reset, AccountPageKind.BOOTSTRAP)
        assertNull(restart.requestSnapshotId); assertNull(restart.requestPageToken)
        pages(reset).applyBootstrapPage(restart, bootstrap(reset, uuid(), emptyList())).awaitResult(); captures.remove(reset.store)
        val retainedDependency = pages(reset).readProjectionPage(reset.lease, AccountProjectionKind.TRACK, null).single()
        assertEquals("DEPENDENCY", retainedDependency.header.rawState); assertEquals(false, retainedDependency.header.snapshotConfirmed)
        assertEquals("PENDING", reset.store.readDelivery(reset.lease, null).single().rawState)
        val invalidCursor = capture(reset, AccountPageKind.PULL)
        pages(reset).resetPage(invalidCursor, AccountPageReset.CURSOR_INVALID).awaitResult(); captures.remove(reset.store)
        assertEquals(invalidCursor.original.cursor, pages(reset).readSyncState(reset.lease).cursor)

        suspend fun commitAndProjectionCases(): Unit {
            // COMMIT before/after, result loss, and capture cleanup have controls that reach each cut.
            for (phase in listOf("ACK", "BOOTSTRAP", "PULL", "DEFER", "RESET")) for (cut in listOf("BEFORE", "AFTER")) {
                val f = fixture()
                if (phase in listOf("PULL", "DEFER", "RESET")) ready(f)
                val delivery = if (phase == "ACK") { f.store.createTrack(f.lease, change()); claim(f) } else null
                val captured = if (delivery == null) capture(f, if (phase == "BOOTSTRAP") AccountPageKind.BOOTSTRAP else AccountPageKind.PULL) else null
                val before = f.faults.commits
                f.faults.cut = cut
                val ticket = when (phase) {
                    "ACK" -> pages(f).applyAckPage(checkNotNull(delivery).lease, ackPage(f, 1, listOf(ack(delivery.events.single()))))
                    "BOOTSTRAP" -> pages(f).applyBootstrapPage(checkNotNull(captured), bootstrap(f, uuid(), emptyList()))
                    "RESET" -> pages(f).resetPage(checkNotNull(captured), AccountPageReset.CURSOR_INVALID)
                    "DEFER" -> pages(f).applyPullPage(checkNotNull(captured), pull(f, captured, listOf(event("UNKNOWN", uuid(), "UNKNOWN", 21, objectOf()))))
                    else -> pages(f).applyPullPage(checkNotNull(captured), pull(f, captured, emptyList()))
                }
                loseResult(ticket)
                if (cut == "BEFORE") {
                    refused(AccountFailure.ACCOUNT_DATA_UNAVAILABLE) { ticket.awaitResult() }
                    if (delivery != null) { f.store.failDelivery(delivery.lease, AccountDeliveryFailure.RETRYABLE); deliveries.remove(f.store) }
                    else release(f, checkNotNull(captured))
                } else {
                    assertTrue(ticket.awaitOutcome() is AccountPageOutcome.Completed)
                    if (delivery != null) deliveries.remove(f.store) else captures.remove(f.store)
                }
                val count = f.faults.statements
                assertSame(ticket.awaitOutcome(), ticket.awaitOutcome()); assertEquals(count, f.faults.statements)
                assertEquals(before + if (cut == "BEFORE" && delivery != null) 2 else 1, f.faults.commits)
                f.store.close()
            }
            for (phase in listOf("CAPTURE", "ACK", "BOOTSTRAP")) {
                val f = fixture()
                val delivery = if (phase == "ACK") { f.store.createTrack(f.lease, change()); claim(f) } else null
                val captured = if (phase == "BOOTSTRAP") capture(f, AccountPageKind.BOOTSTRAP) else null
                f.k.failClose = true
                val ticket: AccountPageOperation<*> = when (phase) {
                    "ACK" -> pages(f).applyAckPage(checkNotNull(delivery).lease, ackPage(f, 1, listOf(ack(delivery.events.single()))))
                    "BOOTSTRAP" -> pages(f).applyBootstrapPage(checkNotNull(captured), bootstrap(f, uuid(), emptyList()))
                    else -> pages(f).capturePage(f.lease, AccountPageKind.BOOTSTRAP)
                }
                refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ticket.awaitResult() }
                val result = ticket.awaitOutcome() as AccountPageOutcome.Retained<*>
                assertFalse(f.k.gate.get())
                if (phase == "CAPTURE") {
                    val exact = result.result as AccountPageCapture
                    captures[f.store] = exact
                    refused(AccountFailure.ACCOUNT_BUSY) { f.store.close() }
                    release(f, exact)
                } else { deliveries.remove(f.store); captures.remove(f.store); assertTrue(result.result is AccountPageReceipt) }
                f.store.close(); assertFalse(f.k.held.get())
            }

            // Known foundation rows/unknown states survive additive schema1 completion, no destructive recreation.
            val foundation = fixture(); foundation.store.createTrack(foundation.lease, change()); foundation.store.close()
            var foundationBytes: ByteArray? = null
            closedSql(foundation.root) { sql ->
                sql.prepare("SELECT canonical_bytes FROM j_event").use { assertTrue(it.step()); foundationBytes = it.getBlob(0) }
                sql.execSQL("PRAGMA foreign_keys=OFF")
                for (ddl in DesktopAccountPageSchema.create.asReversed()) {
                    val match = Regex("^CREATE (?:UNIQUE )?(TABLE|INDEX|TRIGGER|VIEW) ([a-z0-9_]+)").find(ddl)!!
                    sql.execSQL("DROP ${match.groupValues[1]} ${match.groupValues[2]}")
                }
                sql.execSQL("CREATE TABLE future_preserved(value TEXT NOT NULL)")
                sql.execSQL("INSERT INTO future_preserved(value) VALUES('retain')")
                sql.execSQL("UPDATE j_event_delivery SET state='FUTURE_DELIVERY'")
            }
            val reopened = owner(foundation.root, foundation.k)
            val identity = reopened.initialize()
            assertEquals(foundation.lease.storeId, identity.storeId)
            val activation = foundation.plan.copy(activationId = uuid(), expectedAuthorityRevision = foundation.lease.authorityRevision + 1,
                expectedActivationRevision = foundation.lease.activationRevision)
            val selected = select(reopened, foundation.k, activation)
            assertEquals("FUTURE_DELIVERY", reopened.readDelivery(selected, null).single().rawState)
            assertEquals("NOT_STARTED", checkNotNull(reopened.syncPages).readSyncState(selected).state)
            reopened.close()
            closedSql(foundation.root) { sql ->
                assertEquals(1L, scalar(sql, "SELECT count(*) FROM future_preserved WHERE value='retain'"))
                assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event"))
                assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                assertEquals(1L, scalar(sql, "PRAGMA user_version"))
                sql.prepare("SELECT canonical_bytes FROM j_event").use {
                    assertTrue(it.step()); assertArrayEquals(checkNotNull(foundationBytes), it.getBlob(0))
                }
            }
            // Every supported reference family: missing explicit canonical parents must roll back.
            // Preference/listening are PULL facts; actual bootstrap never publishes those families.
            for (family in listOf("USER_TRACK_PREFERENCE", "LISTENING_EVENT")) for (laterParent in listOf(false, true)) {
                val f = fixture(); ready(f)
                val pending = change(); f.store.createTrack(f.lease, pending)
                val originalClaim = claim(f); val immutable = originalClaim.events.single().canonicalBytes
                val immutableHash = originalClaim.events.single().requestHash
                f.store.failDelivery(originalClaim.lease, AccountDeliveryFailure.RETRYABLE); deliveries.remove(f.store)
                val track = uuid(); val subject = if (family == "LISTENING_EVENT") uuid() else track
                val fact = if (family == "LISTENING_EVENT") event(family, subject, "LISTENING_EVENT_RECORDED", 21,
                    listeningPayload(track), subject) else event(family, subject, "USER_TRACK_PREFERENCE_SET", 21,
                    objectOf("server_user_track_ref_id" to id(track), "preference" to string("LIKED"), "excluded_from_taste" to JsonPrimitive(false)))
                val parent = event("USER_TRACK_REF", track, "USER_TRACK_REF_CREATED", 22, objectOf("title" to string("T")))
                val before = projections(f); val c = capture(f, AccountPageKind.PULL)
                val receipt = pages(f).applyPullPage(c, pull(f, c, if (laterParent) listOf(fact, parent) else listOf(fact))).awaitResult()
                captures.remove(f.store)
                assertEquals(if (laterParent) "APPLIED" else "DEFERRED", receipt.state)
                if (!laterParent) {
                    assertEquals(before, projections(f)); assertEquals(c.original.cursor, pages(f).readSyncState(f.lease).cursor)
                    assertEquals(c.original.serverSequence, pages(f).readSyncState(f.lease).serverSequence)
                    assertEquals("DEFERRED", pages(f).readSyncState(f.lease).state)
                }
                f.store.close()
                closedSql(f.root) { sql ->
                    assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                    sql.prepare("SELECT canonical_bytes,request_hash FROM j_event").use {
                        assertTrue(it.step()); assertArrayEquals(immutable, it.getBlob(0)); assertArrayEquals(immutableHash, it.getBlob(1)); assertFalse(it.step())
                    }
                }
            }
            for (family in listOf("LIBRARY_ENTRY", "PLAYLIST_ENTRY")) for (existingParents in listOf(false, true)) for (finalParent in listOf(false, true)) {
                val f = fixture(); val track = uuid(); val playlist = uuid(); val snapshot = uuid()
                // Both new placeholders and old COMPLETE rows need current snapshot evidence.
                if (existingParents) boot(f, bootstrap(f, uuid(), listOf(aggregate("USER_TRACK_REF", track, objectOf("title" to string("Old"))),
                    aggregate("PLAYLIST", playlist, objectOf("name" to string("Old")))))) else ready(f)
                restartSnapshot(f)
                val child = aggregate(family, uuid(), if (family == "LIBRARY_ENTRY") objectOf("server_user_track_ref_id" to id(track)) else
                    objectOf("server_user_track_ref_id" to id(track), "server_playlist_id" to id(playlist), "position_key" to string("a0")))
                assertEquals("APPLIED", boot(f, bootstrap(f, snapshot, listOf(child), true, "member-later-000001", high = 30)).state)
                val before = projections(f); val c = capture(f, AccountPageKind.BOOTSTRAP)
                val parents = if (!finalParent) emptyList() else listOf(aggregate("USER_TRACK_REF", track, objectOf("title" to string("Current")), 2),
                    aggregate("PLAYLIST", playlist, objectOf("name" to string("Current")), 2))
                val receipt = pages(f).applyBootstrapPage(c, bootstrap(f, snapshot, parents, high = 30)).awaitResult(); captures.remove(f.store)
                assertEquals(if (finalParent) "APPLIED" else "DEFERRED", receipt.state)
                if (!finalParent) {
                    assertEquals(before, projections(f)); assertEquals(c.original.cursor, pages(f).readSyncState(f.lease).cursor)
                    assertEquals(c.original.pageToken, pages(f).readSyncState(f.lease).pageToken)
                    assertEquals("BOOTSTRAPPING", pages(f).readSyncState(f.lease).bootstrapState)
                }
            }
            // A current UTR tombstone proves a historical parent, even on a fresh store; no live
            // metadata is invented. A historical PLAYLIST tombstone cannot parent a live snapshot entry.
            for (deletedType in listOf("USER_TRACK_REF", "PLAYLIST")) {
                val f = fixture(); val track = uuid(); val playlist = uuid(); val deleted = if (deletedType == "PLAYLIST") playlist else track
                val tomb = objectOf("tombstone_id" to id(uuid()), "aggregate_type" to string(deletedType), "aggregate_server_id" to id(deleted),
                    "deleted_by_event_id" to id(uuid()), "deleted_at" to string("2026-10-01T00:00:00Z"), "retain_until" to string("2026-11-01T00:00:00Z"))
                val parents = if (deletedType == "PLAYLIST") listOf(aggregate("USER_TRACK_REF", track, objectOf("title" to string("T")))) else
                    listOf(aggregate("PLAYLIST", playlist, objectOf("name" to string("P"))))
                val child = aggregate("PLAYLIST_ENTRY", uuid(), objectOf("server_user_track_ref_id" to id(track),
                    "server_playlist_id" to id(playlist), "position_key" to string("a0")))
                assertEquals(if (deletedType == "PLAYLIST") "DEFERRED" else "APPLIED",
                    boot(f, bootstrap(f, uuid(), parents + child, tombstones = listOf(tomb))).state)
            }

            // Versioned library restore: omitted/null restore, non-null removes, obsolete/equal cannot
            // revive. Keep the historical tombstone AND immutable local intent through dirty refusal.
            for (field in listOf("ABSENT", "NULL", "REMOVED", "STALE", "EQUAL", "DIRTY", "SNAPSHOT_STALE", "SNAPSHOT_EQUAL")) {
                var f = fixture(); val track = uuid(); val entry = uuid()
                boot(f, bootstrap(f, uuid(), listOf(aggregate("USER_TRACK_REF", track, objectOf("title" to string("T")), 3),
                    aggregate("LIBRARY_ENTRY", entry, objectOf("server_user_track_ref_id" to id(track)), 3))))
                val deletedBy = uuid()
                val tomb = objectOf("tombstone_id" to id(uuid()), "deleted_by_event_id" to id(deletedBy),
                    "deleted_at" to string("2026-10-16T11:00:00Z"), "retain_until" to string("2026-11-16T11:00:00Z"))
                val deletion = JsonObject(event("LIBRARY_ENTRY", entry, "AGGREGATE_DELETED", 21, objectOf(), deletedBy, "DELETE",
                    mapOf("tombstone" to tomb)) + ("server_row_version" to JsonPrimitive(4)))
                if (field.startsWith("SNAPSHOT_")) {
                    restartSnapshot(f)
                    boot(f, bootstrap(f, uuid(), listOf(aggregate("USER_TRACK_REF", track, objectOf("title" to string("T")), 3)), high = 30,
                        tombstones = listOf(JsonObject(tomb + mapOf("aggregate_type" to string("LIBRARY_ENTRY"), "aggregate_server_id" to id(entry))))))
                } else assertEquals("APPLIED", applyPull(f, listOf(deletion)).state)
                val local = pages(f).readProjectionPage(f.lease, AccountProjectionKind.LIBRARY, null).single().header.localId
                val intent = "{\"removed_at_ms\":null}".toByteArray(Charsets.UTF_8)
                if (field == "DIRTY") {
                    f.store.close()
                    closedSql(f.root) { sql ->
                        sql.prepare("""INSERT INTO j_local_intent(scope_id,change_id,profile_id,aggregate_local_id,event_type,
                            aggregate_type,schema_version,occurred_at_ms,canonical_payload,state)
                            VALUES(?,?,?,?,'LIBRARY_ENTRY_UPSERTED','LIBRARY_ENTRY',1,?,?,'LOCAL')""").use {
                            it.bindText(1, f.lease.scopeId.value); it.bindText(2, uuid().value); it.bindText(3, f.lease.profileId.value)
                            it.bindText(4, local.value); it.bindLong(5, clock); it.bindBlob(6, intent); assertFalse(it.step())
                        }
                    }
                    f = reopen(f)
                }
                val payload = when (field) { "NULL" -> objectOf("removed_at_ms" to JsonNull)
                    "REMOVED" -> objectOf("removed_at_ms" to JsonPrimitive(clock)); else -> objectOf() }
                val version = when (field) { "STALE", "SNAPSHOT_EQUAL" -> 3L; "SNAPSHOT_STALE" -> 2L; "EQUAL" -> 4L; else -> 5L }
                val restore = JsonObject(event("LIBRARY_ENTRY", entry, "LIBRARY_ENTRY_UPSERTED", if (field.startsWith("SNAPSHOT_")) 31 else 22, payload) + ("server_row_version" to JsonPrimitive(version)))
                val c = capture(f, AccountPageKind.PULL); val reply = pull(f, c, listOf(restore))
                val result = pages(f).applyPullPage(c, reply).awaitResult(); captures.remove(f.store)
                assertEquals(if (field.endsWith("EQUAL")) "DEFERRED" else "APPLIED", result.state)
                assertEquals(field !in setOf("ABSENT", "NULL"), pages(f).readProjectionPage(f.lease, AccountProjectionKind.LIBRARY, null).single().header.deleted)
                if (field in setOf("ABSENT", "NULL")) {
                    // Exact replay and a later-delivered obsolete version preserve the restored row.
                    assertEquals("APPLIED", applyPull(f, listOf(restore)).state)
                    val obsolete = JsonObject(event("LIBRARY_ENTRY", entry, "LIBRARY_ENTRY_UPSERTED", 23, objectOf()) + ("server_row_version" to JsonPrimitive(3)))
                    applyPull(f, listOf(obsolete))
                    assertFalse(pages(f).readProjectionPage(f.lease, AccountProjectionKind.LIBRARY, null).single().header.deleted)
                }
                if (field.endsWith("EQUAL")) assertEquals(c.original.cursor, pages(f).readSyncState(f.lease).cursor)
                f.store.close()
                closedSql(f.root) { sql ->
                    assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_tombstone"))
                    assertEquals(if (field == "DIRTY") 1L else 0L, scalar(sql, "SELECT count(*) FROM j_conflict"))
                    if (field == "DIRTY") sql.prepare("SELECT canonical_payload FROM j_local_intent").use {
                        assertTrue(it.step()); assertArrayEquals(intent, it.getBlob(0)); assertFalse(it.step())
                    }
                }
            }
        }
        commitAndProjectionCases()
        suspend fun pageBudgetAndCapacityCases(): Unit {
            // Shared page budget: a normal maximum-size 500-entry snapshot remains processable over
            // real parent/member pages. No extra transport page-size member or queue/checkpoint wire.
            val ordinary = fixture(); val ordinarySnapshot = uuid()
            val ordinaryTracks = List(500) { uuid() }; val ordinaryPlaylists = List(500) { uuid() }
            val ordinaryEntries = List(500) { uuid() }
            assertEquals("APPLIED", boot(ordinary, bootstrap(ordinary, ordinarySnapshot,
                ordinaryTracks.map { aggregate("USER_TRACK_REF", it, objectOf("title" to string("T"), "artist" to string("A"))) },
                true, "ordinary-tracks-0001")).state)
            assertNull(pages(ordinary).readSyncState(ordinary.lease).cursor)
            assertEquals("APPLIED", boot(ordinary, bootstrap(ordinary, ordinarySnapshot,
                ordinaryPlaylists.map { aggregate("PLAYLIST", it, objectOf("name" to string("P"))) },
                true, "ordinary-lists-00001")).state)
            val ordinaryPage = ordinaryEntries.mapIndexed { index, entry -> aggregate("PLAYLIST_ENTRY", entry,
                objectOf("server_playlist_id" to id(ordinaryPlaylists[index]), "server_user_track_ref_id" to id(ordinaryTracks[index]),
                    "position_key" to string("a0"))) }
            assertEquals("APPLIED", boot(ordinary, bootstrap(ordinary, ordinarySnapshot, ordinaryPage)).state)
            assertEquals("READY", pages(ordinary).readSyncState(ordinary.lease).state)
            assertEquals(500, projections(ordinary).count { it is AccountRemotePlaylistEntry })
            val largeOrdinary = fixture()
            val largePage = bootstrap(largeOrdinary, uuid(), List(500) { aggregate("USER_TRACK_REF", uuid(),
                objectOf("title" to string("T".repeat(12_000)), "artist" to string("A".repeat(4_000)))) })
            assertTrue(largePage.size < DesktopAccountLimits.PAGE_BYTES)
            assertEquals("APPLIED", boot(largeOrdinary, largePage).state)
            // Existing projections are reused across snapshots; each new snapshot retains its own members.
            restartSnapshot(ordinary)
            val replaySnapshot = uuid()
            boot(ordinary, bootstrap(ordinary, replaySnapshot, ordinaryTracks.map { aggregate("USER_TRACK_REF", it,
                objectOf("title" to string("T"), "artist" to string("A"))) }, true, "replay-tracks-00001"))
            boot(ordinary, bootstrap(ordinary, replaySnapshot, ordinaryPlaylists.map { aggregate("PLAYLIST", it,
                objectOf("name" to string("P"))) }, true, "replay-lists-000001"))
            boot(ordinary, bootstrap(ordinary, replaySnapshot, ordinaryPage))

            // 500 small entries can bind entries + tracks + playlists. The first 50 subjects alone
            // have 15000 possible redirect conflicts (all 500 would have 150000). Seed retained valid
            // aliases only on a CLOSED disposable database; source application must stop globally,
            // before SQLite_FULL, without dropping conflicts or retaining half a projection page.
            data class Subject(val entry: AccountUuid, val track: AccountUuid, val playlist: AccountUuid)
            val subjects = List(500) { Subject(uuid(), uuid(), uuid()) }
            var expansion = fixture(); val baseTrack = uuid(); val basePlaylist = uuid()
            boot(expansion, bootstrap(expansion, uuid(), listOf(aggregate("USER_TRACK_REF", baseTrack, objectOf("title" to string("Base"))),
                aggregate("PLAYLIST", basePlaylist, objectOf("name" to string("Base"))))))
            val baseTrackLocal = pages(expansion).readProjectionPage(expansion.lease, AccountProjectionKind.TRACK, null).single().header.localId
            val basePlaylistLocal = pages(expansion).readProjectionPage(expansion.lease, AccountProjectionKind.PLAYLIST, null).single().header.localId
            expansion.store.createTrack(expansion.lease, change())
            expansion.store.close()
            closedSql(expansion.root) { sql ->
                fun insert(query: String, vararg values: String): Unit = sql.prepare(query).use { s ->
                    values.forEachIndexed { index, value -> s.bindText(index + 1, value) }; assertFalse(s.step())
                }
                sql.execSQL("PRAGMA foreign_keys=ON")
                sql.execSQL("BEGIN IMMEDIATE")
                for (subject in subjects.take(50)) for ((type, canonicalId) in listOf("PLAYLIST_ENTRY" to subject.entry,
                    "USER_TRACK_REF" to subject.track, "PLAYLIST" to subject.playlist)) repeat(100) {
                    val localId = uuid(); val aliasId = uuid(); val scope = expansion.lease.scopeId.value; val profile = expansion.lease.profileId.value
                    when (type) {
                        "USER_TRACK_REF" -> insert("""INSERT INTO j_track_ref(scope_id,track_ref_id,operation_id,profile_id,title,artist,
                            occurred_at_ms,server_track_ref_id,observed_version) VALUES(?,?,?,?,'Alias','A',0,?,1)""",
                            scope, localId.value, uuid().value, profile, aliasId.value)
                        "PLAYLIST" -> insert("INSERT INTO j_playlist(scope_id,playlist_id,name) VALUES(?,?,'Alias')", scope, localId.value)
                        else -> insert("""INSERT INTO j_playlist_entry(scope_id,entry_id,playlist_id,track_ref_id,position_key)
                            VALUES(?,?,?,?,'a0')""", scope, localId.value, basePlaylistLocal.value, baseTrackLocal.value)
                    }
                    insert("""INSERT INTO j_projection_state(scope_id,aggregate_type,local_id,server_id,profile_id,observed_version,state)
                        VALUES(?,?,?,?,?,1,'COMPLETE')""", scope, type, localId.value, aliasId.value, profile)
                    insert("""INSERT INTO j_redirect(scope_id,aggregate_type,alias_server_id,canonical_server_id,alias_local_id)
                        VALUES(?,?,?,?,?)""", scope, type, aliasId.value, canonicalId.value, localId.value)
                }
                sql.execSQL("COMMIT"); sql.execSQL("PRAGMA wal_checkpoint(TRUNCATE)")
            }
            expansion = reopen(expansion)
            data class Frozen(val rows: List<Long>, val next: Long, val checkpoint: Long, val event: ByteArray, val hash: ByteArray)
            fun frozen(sql: SQLiteConnection): Frozen {
                val rows = listOf("j_projection_state", "j_track_ref", "j_track_detail", "j_library_relation", "j_library_detail",
                    "j_preference", "j_playlist", "j_playlist_entry", "j_listening", "j_conflict", "j_redirect", "j_tombstone",
                    "j_bootstrap_member", "j_bootstrap_tombstone", "j_snapshot_head", "j_applied_event", "j_ack").map {
                    scalar(sql, "SELECT count(*) FROM $it")
                }
                return sql.prepare("SELECT canonical_bytes,request_hash FROM j_event").use {
                    assertTrue(it.step()); val eventBytes = it.getBlob(0); val hash = it.getBlob(1); assertFalse(it.step())
                    Frozen(rows, scalar(sql, "SELECT next_sequence FROM j_lineage"), scalar(sql, "SELECT ack_checkpoint FROM j_lineage"), eventBytes, hash)
                }
            }
            var observed: Frozen? = null
            expansion.faults.observeCommit = { sql -> observed = frozen(sql) }
            restartSnapshot(expansion)
            val oldRows = checkNotNull(observed); val complexCapture = capture(expansion, AccountPageKind.BOOTSTRAP)
            val complexPage = bootstrap(expansion, uuid(), subjects.map { aggregate("PLAYLIST_ENTRY", it.entry,
                objectOf("server_user_track_ref_id" to id(it.track), "server_playlist_id" to id(it.playlist), "position_key" to string("a0"))) },
                true, "expansion-next-00001")
            val complexityTicket = pages(expansion).applyBootstrapPage(complexCapture, complexPage)
            assertEquals("DEFERRED", complexityTicket.awaitResult().state); captures.remove(expansion.store)
            val statementsAfter = expansion.faults.statements
            assertSame(complexityTicket.awaitOutcome(), complexityTicket.awaitOutcome()); assertEquals(statementsAfter, expansion.faults.statements)
            val afterRows = checkNotNull(observed)
            assertEquals(oldRows.rows, afterRows.rows); assertEquals(oldRows.next, afterRows.next); assertEquals(oldRows.checkpoint, afterRows.checkpoint)
            assertArrayEquals(oldRows.event, afterRows.event); assertArrayEquals(oldRows.hash, afterRows.hash)
            val blockedState = pages(expansion).readSyncState(expansion.lease)
            assertEquals("DEFERRED", blockedState.state); assertEquals(complexCapture.original.cursor, blockedState.cursor)
            assertEquals(complexCapture.original.serverSequence, blockedState.serverSequence)
            assertEquals(complexCapture.original.pageToken, blockedState.pageToken)
            expansion.store.close()
            closedSql(expansion.root) { sql ->
                assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_deferred_page WHERE reason='PAGE_MUTATION_CAPACITY' AND state='BLOCKED'"))
                sql.prepare("SELECT r.safe_wire FROM j_deferred_page d JOIN j_page_result r ON r.operation_id=d.operation_id").use {
                    assertTrue(it.step()); assertArrayEquals(complexPage, it.getBlob(0)); assertFalse(it.step())
                }
                assertTrue(scalar(sql, "PRAGMA page_count") * 4096 < DesktopAccountLimits.MAIN_BYTES - DesktopAccountLimits.PROGRESS_BYTES)
            }
            // A bounded redirect expansion is admitted with EVERY conflict. Repeated binds within
            // this one page dedupe the entry's same conflict key instead of spending/dropping it twice.
            expansion = reopen(expansion); restartSnapshot(expansion)
            val single = subjects.first()
            assertEquals("APPLIED", boot(expansion, bootstrap(expansion, uuid(), listOf(aggregate("PLAYLIST_ENTRY", single.entry,
                objectOf("server_user_track_ref_id" to id(single.track), "server_playlist_id" to id(single.playlist), "position_key" to string("a0")))),
                true, "bounded-next-000001")).state)
            expansion.store.close()
            closedSql(expansion.root) { sql -> assertEquals(300L, scalar(sql, "SELECT count(*) FROM j_conflict")) }

            // Actual known-capacity exhaustion on a closed synthetic database, bounded to 64 pad rows.
            // Checkpoint each pad transaction so this control reaches MAIN admission, not incidental WAL.
            var full = fixture(); full.store.close()
            closedSql(full.root) { sql ->
                sql.execSQL("CREATE TABLE fixture_capacity_pad(bytes BLOB NOT NULL)")
                repeat(64) {
                    if (scalar(sql, "PRAGMA page_count") * 4096 <= DesktopAccountLimits.MAIN_BYTES -
                        DesktopAccountLimits.PROGRESS_BYTES - DesktopAccountLimits.PAGE_GROWTH_BYTES) {
                        sql.execSQL("INSERT INTO fixture_capacity_pad(bytes) VALUES(zeroblob(8388608))")
                        sql.execSQL("PRAGMA wal_checkpoint(TRUNCATE)")
                    }
                }
            }
            full = reopen(full)
            val fullState = pages(full).readSyncState(full.lease)
            refused(AccountFailure.ACCOUNT_STORAGE_FULL) { pages(full).capturePage(full.lease, AccountPageKind.BOOTSTRAP).awaitResult() }
            assertEquals(fullState, pages(full).readSyncState(full.lease)); full.store.close()

            // Admission exhaustion after dispatch must retain the exact ACK lease, including when
            // capture cleanup fails AFTER the deferral COMMIT. No uncertain lock cut enters this loop.
            for (cleanupFailure in listOf(false, true)) {
                val f = fixture(); ready(f); f.store.createTrack(f.lease, change())
                val claimTicket = f.store.claimPending(f.lease)
                val sent = checkNotNull(claimTicket.awaitResult()); deliveries[f.store] = sent.lease
                val immutable = sent.events.single().canonicalBytes; val immutableHash = sent.events.single().requestHash
                f.faults.reportedPageCount = (DesktopAccountLimits.MAIN_BYTES - DesktopAccountLimits.PROGRESS_BYTES) / 4096 + 1
                f.k.failClose = cleanupFailure
                val ackTicket = pages(f).applyAckPage(sent.lease, ackPage(f, 1, listOf(ack(sent.events.single()))))
                val outcome = ackTicket.awaitOutcome()
                val receipt = if (cleanupFailure) {
                    refused(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) { ackTicket.awaitResult() }
                    (outcome as AccountPageOutcome.Retained<AccountPageReceipt>).result
                } else ackTicket.awaitResult()
                assertEquals("DEFERRED", receipt.state)
                val statements = f.faults.statements
                assertSame(outcome, ackTicket.awaitOutcome()); assertEquals(statements, f.faults.statements)
                refused(AccountFailure.ACCOUNT_BUSY) { f.store.close() }
                assertTrue(f.k.held.get())
                f.store.retire()
                f.store.settleDelivery(AccountDeliverySettlement(claimTicket.operationId, sent.lease), AccountDeliveryFailure.UNSUPPORTED)
                deliveries.remove(f.store)
                f.store.close(); assertFalse(f.k.held.get())
                closedSql(f.root) { sql ->
                    assertEquals(0L, scalar(sql, "SELECT count(*) FROM j_ack")); assertEquals(0L, scalar(sql, "SELECT ack_checkpoint FROM j_lineage"))
                    assertEquals(2L, scalar(sql, "SELECT next_sequence FROM j_lineage"))
                    assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_event_delivery WHERE state='UNSUPPORTED'"))
                    assertEquals(1L, scalar(sql, "SELECT count(*) FROM j_deferred_page WHERE kind='ACK' AND reason='ACCOUNT_STORAGE_FULL'"))
                    sql.prepare("SELECT canonical_bytes,request_hash FROM j_event").use {
                        assertTrue(it.step()); assertArrayEquals(immutable, it.getBlob(0)); assertArrayEquals(immutableHash, it.getBlob(1))
                    }
                }
            }

            // Capacity: exact 100-event boundary accepted, 101 is deferred with the old cursor.
            val capacity = fixture(); ready(capacity); val cap = capture(capacity, AccountPageKind.PULL)
            val hundred = (21L..120L).map { event("PLAYLIST", uuid(), "PLAYLIST_CREATED", it, objectOf("name" to string("P"))) }
            pages(capacity).applyPullPage(cap, pull(capacity, cap, hundred)).awaitResult(); captures.remove(capacity.store)
            val tooMany = capture(capacity, AccountPageKind.PULL)
            assertEquals("DEFERRED", pages(capacity).applyPullPage(tooMany, pull(capacity, tooMany,
                (121L..221L).map { event("PLAYLIST", uuid(), "PLAYLIST_CREATED", it, objectOf("name" to string("P"))) })).awaitResult().state)
            captures.remove(capacity.store)
            assertEquals(tooMany.original.cursor, pages(capacity).readSyncState(capacity.lease).cursor)
            refused(AccountFailure.ACCOUNT_INPUT_INVALID) { pages(capacity).readProjectionPage(capacity.lease, AccountProjectionKind.PLAYLIST, null, 101) }
            // Full capacity/fsync/foreign schema/unknown component and isolated ambiguity gates remain UNVERIFIED.
        }
        pageBudgetAndCapacityCases()
    } finally {
        for ((store, captured) in captures.toMap()) checkNotNull(store.syncPages).settlePage(captured)
        for ((store, delivery) in deliveries.toMap()) store.failDelivery(delivery, AccountDeliveryFailure.RETRYABLE)
        for (store in stores.asReversed()) store.close()
        for (root in homes) {
            val absolute = root.toAbsolutePath().normalize()
            check(absolute.fileName.toString().startsWith("autplay-j1-page-fixture-"))
            Files.walk(absolute).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { path ->
                check(path.toAbsolutePath().normalize().startsWith(absolute)); Files.delete(path)
            } }
        }
    }
}
