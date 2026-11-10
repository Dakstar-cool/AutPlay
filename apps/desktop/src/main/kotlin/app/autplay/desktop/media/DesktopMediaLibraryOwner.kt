package app.autplay.desktop.media

import app.autplay.application.desktop.*
import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

/** Path-free bounded playback presentation. These display rows never grant byte authority. */
internal class DesktopMediaLibraryOwner(private val standalone: DesktopLibraryStore,
    private val domain: DesktopAccountDomain, private val catalog: DesktopAccountCatalog,
    work: DesktopMediaWorkOwner) {
    private val selected = AtomicReference<AccountLease?>(null)
    private val gate = Any()
    private val mutable = MutableStateFlow(standalone.state.value)
    val state = mutable.asStateFlow()
    fun hasAccountContext(): Boolean = selected.get() != null
    init { work.scope.launch { standalone.state.collect { snapshot ->
        synchronized(gate) { if (selected.get() == null) mutable.value = snapshot }
    } } }

    suspend fun select(lease: AccountLease, queueId: MediaId?): Unit {
        val saved = queueId?.let { domain.readQueue(lease, it) }
        mediaCheck((saved?.entries?.size ?: 0) <= DesktopLocalLimits.MAX_QUEUE_ENTRIES,
            MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
        val tracks = ArrayList<DesktopTrack>()
        for (id in saved?.entries.orEmpty().map { it.trackId }.distinct()) tracks.add(track(lease, id))
        val checkpoint = saved?.sessionId?.let { domain.readCheckpoint(lease, it) }
        val queue = saved?.let { row ->
            val ordered = row.entries.sortedBy { it.position }.map { DesktopQueueEntry(it.entryId.value, it.trackId.value) }
            val live = checkpoint?.takeIf { !it.finalized && it.queueId == row.queueId && it.queueEntryId == row.currentEntryId }
            DesktopQueue(row.queueId.value, ordered, row.currentEntryId?.value,
                queueType = if (live?.origin == "PLAYLIST") "PLAYLIST" else "LIBRARY",
                positionMs = checkpoint?.positionMs ?: 0, shuffleEnabled = row.shuffle,
                shuffleOrder = ordered.map { it.id }, repeatMode = row.repeat,
                sessionId = live?.listeningId?.value, sessionStartedAtMs = live?.startedAtMs, playedMs = live?.playedMs ?: 0,
                listenTasteState = if (live?.excludedFromTaste == true) "EXCLUDED" else "INCLUDED")
        }
        // This is the selected playback projection, never a truncated whole account library.
        // Browsing/playlist occurrence pages remain owned by the actual J catalog.
        val view = DesktopLibrarySnapshot(tracks, emptyList(), queue)
        synchronized(gate) { selected.set(lease); mutable.value = view }
    }

    suspend fun includeTarget(lease: AccountLease, id: AccountUuid, occurrenceId: AccountUuid?, retainQueue: Boolean): Unit {
        mediaCheck(selected.get() == lease, MediaCode.OWNER_STALE, MediaStage.RESOLVE)
        occurrenceId?.let { requireOccurrence(lease, id, it) }
        val target = track(lease, id)
        mediaCheck(target.collectionState == "ACTIVE", MediaCode.INTENT_STALE, MediaStage.RESOLVE)
        synchronized(gate) {
            mediaCheck(selected.get() == lease, MediaCode.OWNER_STALE, MediaStage.RESOLVE)
            val current = mutable.value
            val protected = current.queue?.entries.orEmpty().filter { retainQueue || it.id == current.queue?.currentEntryId }
                .map { it.trackId }.toSet() + id.value
            val retained = current.tracks.filter { it.id != id.value }.toMutableList()
            while (retained.size >= DesktopLocalLimits.MAX_QUEUE_ENTRIES) {
                val index = retained.indexOfFirst { it.id !in protected }
                mediaCheck(index >= 0, MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
                retained.removeAt(index)
            }
            retained.add(target)
            mutable.value = current.copy(tracks = retained)
        }
    }

    fun adoptRenewal(expected: AccountLease, fresh: AccountLease): Unit = synchronized(gate) {
        mediaCheck(selected.compareAndSet(expected, fresh), MediaCode.OWNER_STALE, MediaStage.AUTH)
    }

    fun committed(lease: AccountLease?, queue: DesktopQueue): Unit = synchronized(gate) {
        val actual = selected.get()
        if (lease != null && actual == lease) mutable.value = mutable.value.copy(queue = queue)
    }
    fun standalone(): Unit = synchronized(gate) { selected.set(null); mutable.value = standalone.state.value }
    private suspend fun requireOccurrence(lease: AccountLease, track: AccountUuid, id: AccountUuid): Unit {
        var after: AccountUuid? = null
        repeat(100) {
            val rows = domain.readProjectionPage(lease, AccountProjectionKind.PLAYLIST_ENTRY, after, 100)
            val actual = rows.singleOrNull { it.header.localId == id }
            if (actual != null) {
                val entry = actual.detail as? AccountLocalDomainDetail.Entry
                    ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
                val playlist = catalog.readPlaylist(lease, entry.playlistId)
                    ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
                mediaCheck(!actual.header.removed && !actual.header.hasConflict && entry.trackId == track &&
                    !playlist.header.removed && playlist.header.localId == entry.playlistId,
                    MediaCode.INTENT_STALE, MediaStage.RESOLVE)
                return
            }
            mediaCheck(rows.size == 100, MediaCode.INTENT_STALE, MediaStage.RESOLVE)
            val next = rows.last().header.localId
            mediaCheck(next != after, MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            after = next
        }
        throw mediaFailure(MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
    }

    private suspend fun track(lease: AccountLease, id: AccountUuid): DesktopTrack {
        val context = catalog.readTrackContext(lease, id)
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        val row = context.track
        val detail = row.detail as? AccountLocalDomainDetail.Track
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        mediaCheck(row.header.localId == id, MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        val member = context.library.any { !it.header.removed &&
            (it.detail as? AccountLocalDomainDetail.Library)?.trackId == id }
        // Empty/zero byte facts are unobserved; resolver obtains independent J/K/file evidence.
        return DesktopTrack(id.value, detail.title.orEmpty(), detail.artist.orEmpty(), detail.durationMs,
            "", 0, "NEUTRAL", if (!row.header.removed && member) "ACTIVE" else "REMOVED")
    }
}
