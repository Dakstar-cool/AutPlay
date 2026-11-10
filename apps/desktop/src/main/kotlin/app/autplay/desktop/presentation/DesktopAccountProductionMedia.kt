package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import app.autplay.desktop.media.DesktopMediaRuntime
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.ui.desktop.*
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** Actual J/native/file facts. Display IDs are one-page capabilities, never media locators. */
internal class DesktopAccountProductionMedia(
    private val runtime: DesktopMediaRuntime,
    private val account: DesktopAccountStore,
    private val controller: DesktopPlaybackController,
    private val currentLease: () -> AccountLease?,
    private val readMeteredPreference: () -> Boolean,
    private val saveMeteredPreference: suspend (Boolean) -> Unit,
    private val prepareNetworkLease: suspend (AccountLease) -> AccountLease,
) : DesktopAccountHostMedia {
    private val journal = account.media ?: unavailable()
    private val serial = Mutex()
    private val retired = AtomicBoolean(false)
    @Volatile private var mediaClosed = false
    private val mutable = MutableStateFlow(empty())
    override val downloads: StateFlow<DesktopDownloadsView> = mutable.asStateFlow()
    private sealed interface Cursor {
        data class Intents(val after: AccountUuid?) : Cursor
        data class Copies(val scope: AccountUuid?, val copy: AccountUuid?) : Cursor
    }
    private class Page(val lease: AccountLease, val after: Cursor?, val history: List<Cursor?> = emptyList())
    private class Copy(val lease: AccountLease, val value: AccountMediaCopyView)
    private class Intent(val lease: AccountLease, val track: AccountUuid, val id: AccountUuid)
    private val pages = LinkedHashMap<String, Page>()
    private val copies = LinkedHashMap<String, Copy>()
    private val intents = LinkedHashMap<String, Intent>()
    private var active: Intent? = null
    private var page: Page? = null
    private val observations = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val refreshes = Channel<Unit>(Channel.CONFLATED)

    init {
        observations.launch { runtime.downloads.state.drop(1).collect { refreshes.trySend(Unit) } }
        observations.launch { runtime.copies.progress.drop(1).collect { refreshes.trySend(Unit) } }
        observations.launch { runtime.network.drop(1).collect { refreshes.trySend(Unit) } }
        observations.launch {
            for (ignored in refreshes) {
                if (!retired.get()) try { refreshDownloads(null) }
                catch (_: Exception) {
                    // A native progress event supplies no missing J/LOCAL authority.
                    mutable.value = empty()
                }
            }
        }
    }

    override suspend fun play(target: DesktopAccountTrackTarget, enqueue: Boolean): Unit = serial.withLock {
        requireLease(target.lease)
        // Completed original-owner LOCAL copies and queue edits never require a server trip.
        if (!enqueue && target.lease.sessionFamily != null &&
            journal.readTrackCopies(target.lease, target.trackId, null, 1).isEmpty() &&
            prepareNetworkLease(target.lease) != target.lease) changed()
        select(target.lease)
        runtime.library.includeTarget(target.lease, target.trackId, target.occurrenceId, retainQueue = enqueue)
        // The controller validates collection membership and owns every queue/listen identity.
        if (enqueue) controller.enqueue(target.trackId.value) else controller.playTracks(listOf(target.trackId.value),
            queueType = if (target.occurrenceId == null) "LIBRARY" else "PLAYLIST")
    }

    override suspend fun download(target: DesktopAccountTrackTarget): Unit = serial.withLock {
        requireLease(target.lease)
        if (prepareNetworkLease(target.lease) != target.lease) changed()
        select(target.lease)
        settleActive(target.lease)
        if (active != null) throw AccountStoreException(AccountFailure.ACCOUNT_BUSY)
        val id = runtime.downloads.download(target.trackId, "USER_DOWNLOAD")
        val request = Intent(target.lease, target.trackId, id)
        intents[UUID.randomUUID().toString()] = request
        active = request
        readPage(Page(target.lease, null))
    }

    override suspend fun cancelDownload(id: String): Unit = serial.withLock {
        val request = intents[id] ?: changed()
        // Stopping the exact retained running request grants no J/network authority. It wins
        // even while rotation temporarily fences publication of the successor lease.
        if (runtime.downloads.ownsIntent(request.id)) {
            runtime.downloads.cancelCurrent()
            runtime.downloads.awaitIdle()
            active = null
            val fresh = currentLease()
            if (fresh != null) readPage(Page(fresh, null)) else mutable.value = empty()
            return@withLock
        }
        requireLease(request.lease)
        val actual = journal.readIntent(request.lease, request.id) ?: changed()
        if (actual.coarseState == "COMPLETED") changed()
        journal.setDesiredState(request.lease, AccountUuid.random(), request.id, actual.record.revision, "CANCEL")
        requireLease(request.lease)
        // This adapter admits one transfer at a time; cancellation cannot target another request.
        if (active?.id == request.id) {
            runtime.downloads.cancelCurrent()
            runtime.downloads.awaitIdle()
            active = null
        }
        readPage(page?.takeIf { it.lease == request.lease } ?: Page(request.lease, null))
    }

    override suspend fun retryDownload(id: String): Unit = serial.withLock {
        val request = intents[id] ?: changed()
        requireLease(request.lease)
        if (prepareNetworkLease(request.lease) != request.lease) changed()
        select(request.lease)
        settleActive(request.lease)
        if (active != null) throw AccountStoreException(AccountFailure.ACCOUNT_BUSY)
        val actual = journal.readIntent(request.lease, request.id) ?: changed()
        if (actual.coarseState == "COMPLETED") changed()
        requireLease(request.lease)
        runtime.downloads.resume(request.track, request.id)
        active = request
        readPage(page?.takeIf { it.lease == request.lease } ?: Page(request.lease, null))
    }

    override suspend fun playOffline(id: String): Unit = serial.withLock {
        val selected = copies[id] ?: changed()
        select(selected.lease)
        val old = selected.value
        val actual = journal.readCopy(selected.lease, old.copy.copyId, old.copy.record.scopeId) ?: changed()
        if (actual != old || !playable(actual)) changed()
        requireLease(selected.lease)
        // ROOT/DETACHED playback is an explicit original-owner copy. No UTR or Journal is fabricated.
        controller.playAccountCopy(actual.copy.localTrackRefId.value, actual.copy.copyId.value, actual.copy.record.scopeId.value)
    }

    override suspend fun setMetered(allowed: Boolean): Unit = serial.withLock {
        val lease = currentLease() ?: changed()
        requireLease(lease)
        saveMeteredPreference(allowed)
        requireLease(lease)
        readPage(page?.takeIf { it.lease == lease } ?: Page(lease, null))
    }

    /** Notification only: the settings owner already finished persistence and policy dispatch. */
    fun preferencesChanged(): Unit { if (!retired.get()) refreshes.trySend(Unit) }

    override suspend fun refreshDownloads(target: String?): Unit = serial.withLock {
        val lease = currentLease()
        if (lease == null || retired.get()) {
            pages.clear(); copies.clear()
            intents.entries.removeAll { retired.get() || !runtime.downloads.ownsIntent(it.value.id) }
            page = null; mutable.value = empty(); return@withLock
        }
        val requested = if (target == null) page?.takeIf { it.lease == lease } ?: Page(lease, null)
            else pages[target]?.takeIf { it.lease == lease } ?: changed()
        readPage(requested)
    }

    private suspend fun select(lease: AccountLease): Unit {
        requireLease(lease)
        if (runtime.admission.selectedLeaseOrNull() != lease || runtime.admission.isRetired()) {
            val fence = controller.fenceSelectedContext()
            runtime.selectAccount(lease)
            requireLease(lease)
            controller.restoreSelectedContextAfterDrain(fence) { !retired.get() && currentLease() == lease }
        }
        requireLease(lease)
    }

    /** Explicit return to the complete local library, after actual previous-owner settlement. */
    suspend fun selectStandalone(restoreAllowed: () -> Boolean): Unit = serial.withLock {
        if (retired.get() || !restoreAllowed()) changed()
        if (!runtime.library.hasAccountContext() && !runtime.admission.hasSelectedLease() && !controller.isContextFenced()) return@withLock
        val fence = controller.fenceSelectedContext()
        runtime.selectStandalone()
        if (retired.get() || !restoreAllowed()) { runtime.retireAdmissions(); changed() }
        controller.restoreSelectedContextAfterDrain(fence) { !retired.get() && restoreAllowed() }
    }

    private suspend fun settleActive(lease: AccountLease): Unit {
        val request = active ?: return
        if (request.lease != lease) changed()
        val actual = journal.readIntent(lease, request.id)
        if (actual != null && actual.coarseState in setOf("COMPLETED", "CANCELED", "FAILED", "PAUSED")) {
            runtime.downloads.awaitIdle()
            active = null
        }
        requireLease(lease)
    }

    private suspend fun readPage(requested: Page): Unit {
        val lease = requested.lease
        requireLease(lease)
        val prior = active
        if (prior != null && prior.lease != lease) {
            val retainedActive = if (runtime.admission.selectedLeaseOrNull() == lease &&
                prior.lease.storeId == lease.storeId && prior.lease.scopeId == lease.scopeId &&
                prior.lease.profileId == lease.profileId && prior.lease.bindingCommit == lease.bindingCommit &&
                prior.lease.sessionFamily == lease.sessionFamily && runtime.downloads.ownsIntent(prior.id))
                journal.readIntent(lease, prior.id) else null
            if (retainedActive?.localTrackRefId == prior.track) active = Intent(lease, prior.track, prior.id)
            else { runtime.downloads.awaitIdle(); active = null }
        }
        val cursor = requested.after ?: Cursor.Intents(null)
        val retained = if (cursor is Cursor.Intents) journal.readIntentPage(lease, cursor.after, 51) else emptyList()
        val copyCursor = cursor as? Cursor.Copies ?: Cursor.Copies(null, null)
        val rows = if (cursor is Cursor.Copies || retained.isEmpty())
            journal.readOwnedCopies(lease, copyCursor.scope, copyCursor.copy, 51) else emptyList()
        val shown = rows.take(50)
        val nextCopies = LinkedHashMap<String, Copy>()
        val nextIntents = LinkedHashMap<String, Intent>()
        val views = shown.map { value ->
            val id = UUID.randomUUID().toString()
            nextCopies[id] = Copy(lease, value)
            DesktopDownloadView(id, value.copy.localTrackRefId.value, null, storage(value.copy.storageClass),
                if (playable(value)) DesktopAccountCondition.Ready else DesktopAccountCondition.Attention,
                value.copy.totalBytes, value.copy.totalBytes, playable(value),
                retainedCopy = true)
        }.toMutableList()
        for (value in retained.take(50)) {
            val id = UUID.randomUUID().toString()
            nextIntents[id] = Intent(lease, value.localTrackRefId, value.intentId)
            val attempt = if (value.currentAttempt > 0) journal.readAttempt(lease, value.intentId, value.currentAttempt) else null
            views.add(DesktopDownloadView(id, value.localTrackRefId.value, null, storage(value.storageClass),
                when (if (value.desiredState == "CANCEL") "CANCELED" else value.coarseState) {
                    "COMPLETED" -> DesktopAccountCondition.Ready
                    "CANCELED" -> DesktopAccountCondition.Empty
                    "PAUSED", "FAILED", "UNCERTAIN" -> DesktopAccountCondition.Attention
                    else -> DesktopAccountCondition.Pending
                }, attempt?.confirmedPrefixBytes, attempt?.totalBytes, false,
                issue = if (attempt?.state == "UNCERTAIN") DesktopAccountIssue.Conflict else DesktopAccountIssue.None))
        }
        val nextPages = LinkedHashMap<String, Page>()
        val previous = if (requested.history.isNotEmpty()) UUID.randomUUID().toString().also { id ->
            nextPages[id] = Page(lease, requested.history.last(), requested.history.dropLast(1))
        } else null
        val successor: Cursor? = when {
            retained.size > 50 -> Cursor.Intents(retained[49].intentId)
            retained.isNotEmpty() && journal.readOwnedCopies(lease, null, null, 1).isNotEmpty() -> Cursor.Copies(null, null)
            rows.size > 50 -> Cursor.Copies(shown.last().copy.record.scopeId, shown.last().copy.copyId)
            else -> null
        }
        val next = successor?.let { after -> UUID.randomUUID().toString().also { id ->
            nextPages[id] = Page(lease, after, (requested.history + listOf(requested.after)).takeLast(32))
        } }
        requireLease(lease)
        copies.clear(); copies.putAll(nextCopies); intents.clear(); intents.putAll(nextIntents)
        pages.clear(); pages.putAll(nextPages); page = requested
        mutable.value = DesktopDownloadsView(if (views.isEmpty()) DesktopAccountCondition.Empty else DesktopAccountCondition.Ready,
            views, readMeteredPreference(), next != null, previous, next)
    }

    private fun requireLease(lease: AccountLease): Unit {
        if (retired.get() || currentLease() != lease) changed()
    }
    override fun retire(): Unit {
        if (retired.compareAndSet(false, true)) {
            runtime.retireAdmissions()
            refreshes.close()
            mutable.value = empty()
        }
    }
    /** Root calls only after the actual complete media runtime close has returned successfully. */
    fun confirmMediaClosed(): Unit { check(retired.get()); mediaClosed = true }
    override suspend fun drain(): Unit = withContext(NonCancellable) {
        serial.withLock {
            if (!mediaClosed) runtime.playback.awaitQuiescent()
            runtime.downloads.awaitIdle(); runtime.playback.awaitPersistence()
        }
        if (retired.get()) observations.coroutineContext[Job]?.cancelAndJoin()
    }
    private fun playable(value: AccountMediaCopyView): Boolean = value.copy.publicationState == "COMMITTED" &&
        value.copy.integrityState == "VERIFIED" && value.ownership.state == "OWNED"
    private fun storage(value: String): DesktopMediaStorage = when (value) {
        "CACHE" -> DesktopMediaStorage.Cache
        "PINNED" -> DesktopMediaStorage.Pinned
        "USER_DOWNLOAD" -> DesktopMediaStorage.UserDownload
        else -> DesktopMediaStorage.Unknown
    }
    private companion object {
        fun empty(): DesktopDownloadsView = DesktopDownloadsView(DesktopAccountCondition.Unavailable, emptyList(), null, false)
        fun unavailable(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        fun changed(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_LEASE_STALE)
    }
}
