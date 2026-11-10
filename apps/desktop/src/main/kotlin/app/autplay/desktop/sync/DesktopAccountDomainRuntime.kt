package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.coroutines.coroutineContext

public data class DesktopAccountDomainState(public val kind: AccountProjectionKind = AccountProjectionKind.TRACK,
    public val rows: List<AccountLocalDomainRow> = emptyList(), public val nextAfter: AccountUuid? = null,
    public val revision: Long = 0, public val available: Boolean = false, public val failure: AccountFailure? = null)
public data class DesktopAccountCatalogState(public val artists: List<AccountCatalogArtist> = emptyList(),
    public val afterArtist: AccountUuid? = null, public val available: Boolean = false,
    public val metadata: AccountTrackMetadata? = null, public val appearances: List<AccountCatalogAppearance> = emptyList())

/** Actual local-first commands. J commits each mutation with its immutable Journal/local intent. */
public class DesktopAccountDomainRuntime(private val store: DesktopAccountStore) {
    private val domain = store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    private val retired = AtomicBoolean(false)
    private val mutable = MutableStateFlow(DesktopAccountDomainState())
    private val catalogMutable = MutableStateFlow(DesktopAccountCatalogState())
    public val state: StateFlow<DesktopAccountDomainState> = mutable.asStateFlow()
    public val catalogState: StateFlow<DesktopAccountCatalogState> = catalogMutable.asStateFlow()
    private fun admit() { if (retired.get()) throw AccountStoreException(AccountFailure.ACCOUNT_CLOSED) }
    public suspend fun readPage(lease: AccountLease, kind: AccountProjectionKind, after: AccountUuid? = null): DesktopAccountDomainState {
        admit(); coroutineContext.ensureActive()
        return try {
            val rows = domain.readProjectionPage(lease, kind, after, 100)
            admit(); coroutineContext.ensureActive()
            DesktopAccountDomainState(kind, rows, rows.lastOrNull()?.header?.localId?.takeIf { rows.size == 100 },
                mutable.value.revision + 1, true).also { mutable.value = it }
        } catch (failure: AccountStoreException) {
            DesktopAccountDomainState(kind, revision = mutable.value.revision + 1, failure = failure.failure).also { mutable.value = it }
        }
    }
    public suspend fun createTrack(lease: AccountLease, change: AccountCreateTrack): AccountMutationReceipt {
        admit(); coroutineContext.ensureActive(); return store.createTrack(lease, change)
    }
    public suspend fun mutate(lease: AccountLease, change: AccountDomainChange): AccountDomainReceipt {
        admit(); coroutineContext.ensureActive(); return domain.mutate(lease, change)
    }
    public suspend fun saveQueue(lease: AccountLease, operation: AccountUuid, expectedRevision: Long, queue: AccountQueueSnapshot) {
        admit(); coroutineContext.ensureActive(); domain.saveQueue(lease, operation, expectedRevision, queue)
    }
    public suspend fun queue(lease: AccountLease, id: AccountUuid): AccountQueueSnapshot? { admit(); return domain.readQueue(lease, id) }
    public suspend fun listening(lease: AccountLease, operation: AccountUuid, expectedRevision: Long,
        checkpoint: AccountListeningCheckpoint, finalPayload: kotlinx.serialization.json.JsonObject? = null): AccountDomainReceipt? {
        admit(); coroutineContext.ensureActive(); return domain.checkpoint(lease, operation, expectedRevision, checkpoint, finalPayload)
    }
    /** Reads actual catalog Artist IDs, ordered names/roles and complete owner proofs populated by v1 sync. */
    public suspend fun artists(lease: AccountLease, after: AccountUuid? = null): List<AccountCatalogArtist> {
        admit(); coroutineContext.ensureActive()
        val catalog = store.catalog ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        return catalog.readArtists(lease, after, 100).also { rows ->
            admit(); catalogMutable.value = catalogMutable.value.copy(artists = rows, afterArtist = after, available = true)
        }
    }
    public suspend fun appearances(lease: AccountLease, artist: AccountUuid, afterSubject: AccountUuid? = null): List<AccountCatalogAppearance> {
        admit(); coroutineContext.ensureActive()
        val catalog = store.catalog ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        return catalog.readAppearances(lease, artist, afterSubject, 100).also { rows ->
            admit(); catalogMutable.value = catalogMutable.value.copy(appearances = rows, available = true)
        }
    }
    public suspend fun metadata(lease: AccountLease, track: AccountUuid): AccountTrackMetadata? {
        admit(); coroutineContext.ensureActive()
        val catalog = store.catalog ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        return catalog.readMetadata(lease, track).also { value -> admit(); catalogMutable.value = catalogMutable.value.copy(metadata = value) }
    }
    public suspend fun copySelected(lease: AccountLease, consent: AccountSelectionConsent,
        source: AccountSourceRegistration, standalone: DesktopSelectedSourcePort) {
        admit(); coroutineContext.ensureActive()
        (store.materialization ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION))
            .copySelected(lease, consent, source, standalone)
    }
    public suspend fun materializeSelected(lease: AccountLease, selection: AccountUuid, revision: Long,
        standalone: DesktopSelectedSourcePort): List<AccountMaterializationLink> {
        admit(); coroutineContext.ensureActive()
        return (store.materialization ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION))
            .materializeSelected(lease, selection, revision, standalone)
    }
    public suspend fun selectionStatus(lease: AccountLease, selection: AccountUuid): AccountSelectionStatus? {
        admit(); coroutineContext.ensureActive()
        return (store.materialization ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION))
            .readSelection(lease, selection)
    }
    public suspend fun freezeAccountIntents(lease: AccountLease, sourceScope: AccountUuid, selection: AccountUuid,
        reviewRevision: Long, changes: List<AccountUuid>): AccountIntentSelection {
        admit(); coroutineContext.ensureActive()
        return (store.accountIntents ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION))
            .freeze(lease, sourceScope, selection, reviewRevision, changes)
    }
    public suspend fun materializeAccountIntents(lease: AccountLease, selection: AccountUuid,
        reviewRevision: Long): AccountIntentMaterializationResult {
        admit(); coroutineContext.ensureActive()
        return (store.accountIntents ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION))
            .materialize(lease, selection, reviewRevision)
    }
    public fun retire() { retired.set(true); mutable.value = DesktopAccountDomainState(); catalogMutable.value = DesktopAccountCatalogState() }
}

/** Host wiring uses these concrete owners after the actual K-first/J activation yields a real lease. */
public class DesktopAccountServices(public val account: app.autplay.desktop.account.DesktopAccountRuntime,
    public val store: DesktopAccountStore, authority: app.autplay.desktop.account.DesktopAccountAuthority,
    guardPublication: DesktopSyncGuardPublication) {
    private val retired = AtomicBoolean(false)
    public val http: DesktopDomainHttp = DesktopDomainHttp()
    public val session: DesktopSyncSession = DesktopSyncSession(account, http, authority::currentLease)
    public val transport: DesktopSyncTransport = DesktopSyncTransport(session)
    public val binding: DesktopSyncBindingCoordinator = DesktopSyncBindingCoordinator(store, transport, guardPublication)
    public val sync: DesktopSyncRuntime = DesktopSyncRuntime(store, transport, binding)
    public val portable: DesktopPortableDataRuntime = DesktopPortableDataRuntime(store, transport)
    public val statistics: DesktopAccountStatisticsRuntime = DesktopAccountStatisticsRuntime(store, transport)
    public val domain: DesktopAccountDomainRuntime = DesktopAccountDomainRuntime(store)
    public val matching: DesktopImportMatchingRuntime = DesktopImportMatchingRuntime(store, transport)
    public val canonicalCatalog: DesktopCanonicalCatalog = DesktopCanonicalCatalog(transport)
    public val catalogSearch: app.autplay.data.desktop.account.AccountCatalogFtsSearch = app.autplay.data.desktop.account.AccountCatalogFtsSearch()
    /** The root supplies its canonical already-open local store; no new store or file scan is opened. */
    public fun importLinks(local: app.autplay.application.desktop.DesktopLibraryStore): DesktopOwnedImportLinkAdmission =
        DesktopOwnedImportLinkAdmission(local,store,transport,matching.decisions)
    /** One connected delivery cycle, called in the host's existing lifecycle scope. */
    public suspend fun synchronize(lease: AccountLease): DesktopSyncViewState {
        check(!retired.get()) { "ACCOUNT_SERVICES_RETIRED" }; coroutineContext.ensureActive()
        val outcome = sync.runPass(lease)
        if (!retired.get() && outcome.phase in setOf(DesktopSyncPhase.SYNCED, DesktopSyncPhase.IDLE) && outcome.failure == null) {
            portable.runPass(lease)
            if (!retired.get()) { statistics.refresh(lease); domain.readPage(lease, AccountProjectionKind.TRACK); domain.artists(lease) }
        }
        return outcome
    }
    /** Call synchronously before switching account, sleep or Quit, then await drain on this exact owner. */
    public fun retire() { retired.set(true); catalogSearch.retire(); canonicalCatalog.retire(); domain.retire(); matching.retire(); portable.retire(); statistics.invalidate(); sync.retire(); store.retire() }
    public suspend fun drain(lease: AccountLease) { catalogSearch.close(); sync.drain(lease); session.drain().settledAwait(); matching.drainRetained() }
    /** Also settles an index when initialization never issued an account lease. */
    public suspend fun closeCatalogSearch(): Unit = catalogSearch.close()
    /** Only after the host has joined all pass callers and drain has returned. */
    public fun closeHttpAfterDrain() { check(session.outstandingIoCount() == 0); http.close() }
}
