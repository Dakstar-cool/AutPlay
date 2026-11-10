package app.autplay.application.desktop

import kotlinx.coroutines.flow.StateFlow

object DesktopLocalLimits {
    const val MAX_QUEUE_ENTRIES: Int = 1_000
    const val MAX_SELECTION_ROOTS: Int = 256
}

/** Local collection identity; file capabilities never appear in the UI projection. */
data class DesktopTrack(
    val id: String,
    val title: String,
    val artist: String,
    val durationMs: Long?,
    val sha256: String,
    val byteSize: Long,
    /** Track-level local choice; unknown persisted values are retained, never normalized on read. */
    val preference: String = "NEUTRAL",
    /** Local membership only; removal retains bytes and every existing reference. */
    val collectionState: String = "ACTIVE",
)

data class DesktopPlaylistEntry(val id: String, val trackId: String)
data class DesktopPlaylist(
    val id: String,
    val name: String,
    val entries: List<DesktopPlaylistEntry>,
    val description: String? = null,
)
data class DesktopQueueEntry(val id: String, val trackId: String)

/** Unknown persisted modes must survive reads; the playback owner rejects unsupported modes. */
data class DesktopQueue(
    val id: String,
    val entries: List<DesktopQueueEntry>,
    val currentEntryId: String?,
    val queueType: String = "LIBRARY",
    val positionMs: Long = 0,
    val shuffleEnabled: Boolean = false,
    val shuffleOrder: List<String> = entries.map { it.id },
    val repeatMode: String = "OFF",
    val sessionId: String? = null,
    val sessionStartedAtMs: Long? = null,
    val playedMs: Long = 0,
    /** Queue-wide and current-listen choices are independent; future strings remain opaque. */
    val sessionTasteState: String = "INCLUDED",
    val listenTasteState: String = "INCLUDED",
)

data class DesktopListeningEvent(
    val id: String,
    val queueEntryId: String,
    val trackId: String,
    val startedAtMs: Long,
    val playedMs: Long,
    val positionMs: Long,
    val finalized: Boolean,
    /** Captured choices for this listen; finalized history is never edited retroactively. */
    val sessionTasteState: String = "INCLUDED",
    val listenTasteState: String = "INCLUDED",
)

data class DesktopLibrarySnapshot(
    val tracks: List<DesktopTrack> = emptyList(),
    val playlists: List<DesktopPlaylist> = emptyList(),
    val queue: DesktopQueue? = null,
    /** Bounded newest-event preview; use loadHistoryPage for the full persisted history. */
    val history: List<DesktopListeningEvent> = emptyList(),
)

/** Verified managed copy produced by the explicit import adapter; never an original path. */
data class DesktopImportedTrack(
    val id: String,
    val title: String,
    val artist: String,
    val durationMs: Long?,
    val sha256: String,
    val byteSize: Long,
    val managedFileName: String,
)

/** Transactions commit mutations and local-only outbox facts together. No server binding is invented. */
interface DesktopLibraryStore {
    val state: StateFlow<DesktopLibrarySnapshot>
    /** Read-only stable keyset page, bounded to 1..PAGE_SIZE; never refreshes or mutates the library. */
    suspend fun loadHistoryPage(
        before: DesktopHistoryCursor? = null, limit: Int = DesktopHistoryLimits.PAGE_SIZE,
    ): DesktopHistoryPage
    /** Explicit read of all saved local history in one transaction; never refreshes state or writes facts.
     * The caller captures one inclusive cutoff per refresh. Failure throws; an empty history returns zero.
     */
    suspend fun readLocalStatistics(throughInclusiveMs: Long): DesktopLocalStatistics
    suspend fun addImportedTrack(track: DesktopImportedTrack): DesktopTrack
    /** Frozen path-free manifest; same job ID and exact manifest replay is a fact no-op. */
    suspend fun createImportJob(manifest: DesktopImportJobManifest): DesktopImportJob
    suspend fun loadImportJob(jobId: String): DesktopImportJob?
    suspend fun loadUnfinishedImportJobs(
        after: DesktopImportJobCursor? = null, limit: Int = DesktopImportJobLimits.JOB_PAGE,
    ): DesktopImportJobPage
    /** Explicit confirmed NEW preview and candidate subset. Never call from automatic restart.
     * Exact replay does not mint a lost permit; a new revision/attempt is needed after interruption.
     */
    suspend fun confirmImportJobAttempt(attempt: DesktopImportJobAttempt): DesktopImportJobAuthorization
    /** Verifies managed bytes, then commits track/deduplication and settled outcome in ONE transaction.
     * Copying alone is not success. Exact command replay is read-only even after cancellation/restart.
     */
    suspend fun commitImportJobCandidate(
        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
    ): DesktopImportJob
    suspend fun failImportJobCandidate(
        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, failure: DesktopImportJobFailure,
    ): DesktopImportJob
    /** Revokes this process's permit and appends a CAS-fenced stop. Committed candidates survive.
     * Once a fresh stop passes its fence, local permission stays revoked even if its fact fails.
     * An exact old stop replay is read-only and cannot revoke a newer attempt.
     */
    suspend fun stopImportJob(stop: DesktopImportJobStop): DesktopImportJob
    suspend fun updateTrackMetadata(trackId: String, title: String, artist: String, durationMs: Long?): Unit
    /** Edits display text while retaining the current persisted duration and byte identity. */
    suspend fun updateTrackTextMetadata(trackId: String, title: String, artist: String): Unit
    /** Read-only evidence and exact baseline/revision from one transaction; null only for a missing track. */
    suspend fun loadTrackTextProvenance(trackId: String): DesktopTrackTextProvenance?
    /** Compatibility preview application; changed fields have UNKNOWN origin without format/source evidence.
     * Compares raw labels and byte identity transactionally; unchanged fields retain their origin.
     */
    suspend fun applyEmbeddedTrackText(baseline: DesktopTrackTextBaseline, title: String, artist: String): Unit
    /** Additive frozen-preview API with explicit per-field intent and a metadata revision fence. */
    suspend fun applyEmbeddedTrackText(application: DesktopEmbeddedTrackTextApplication): Unit
    /** Native duration facts retain current display text; no stale metadata snapshot is replayed. */
    suspend fun updateTrackDuration(trackId: String, durationMs: Long): Unit
    /** Explicit track-level choice only; never changes a listen, queue or native playback. */
    suspend fun setTrackPreference(trackId: String, preference: String): Unit
    /** Reversible, confirmed collection removal; never deletes bytes or existing references. */
    suspend fun removeTrackFromCollection(trackId: String): Unit
    /** Explicit restoration only. Duplicate import must preserve the current membership. */
    suspend fun restoreTrackToCollection(trackId: String): Unit
    /** Returns null for a retired queue/entry/listen, with no row or fact changes. */
    suspend fun setCurrentListenTasteExcluded(
        expectedQueueId: String, expectedEntryId: String, expectedListeningEventId: String, excluded: Boolean,
    ): DesktopQueue?
    /** Applies to the unfinished current listen and future listens within this queue ID only. */
    suspend fun setQueueSessionTasteExcluded(expectedQueueId: String, excluded: Boolean): DesktopQueue?
    suspend fun createPlaylist(name: String): DesktopPlaylist
    /** Preserves the playlist/entry identities; an unchanged name creates no intent. */
    suspend fun renamePlaylist(playlistId: String, name: String): Unit
    /** Exact optional text, up to 500 UTF-16 units; empty editor text is passed as null.
     * Rechecks the expected persisted description transactionally; no stale overwrite or queue effect.
     */
    suspend fun updatePlaylistDescription(
        playlistId: String, expectedDescription: String?, description: String?,
    ): Unit
    /** User-confirmed aggregate removal; retains media, tracks, queue, history and previous facts.
     * A missing playlist is a no-op without a new intent.
     */
    suspend fun deletePlaylist(playlistId: String): Unit
    suspend fun appendToPlaylist(playlistId: String, trackId: String): Unit
    /** At most MAX_QUEUE_ENTRIES references, in exact input order including duplicates.
     * Validates the whole batch and commits every entry and existing per-entry fact atomically.
     * Empty is a no-op; missing/non-ACTIVE references or overflow refuse the entire batch.
     */
    suspend fun appendTracksToPlaylist(playlistId: String, trackIds: List<String>): Unit
    suspend fun removePlaylistEntry(playlistId: String, entryId: String): Unit
    /** Moves one entry by -1/+1 in current persisted order; absent/boundary targets are no-ops.
     * Other directions are no-ops. This never edits an already selected playback queue/session.
     */
    suspend fun movePlaylistEntry(playlistId: String, entryId: String, direction: Int): Unit
    /** New entry ID/track mappings require ACTIVE membership at transaction time.
     * Existing persisted mappings remain usable after collection removal.
     * Same-queue checkpoints retain taste choices; only new listen identity resets a known listen choice.
     */
    suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent? = null): Unit
    /** Returned capability is runtime-only, checked against the owned media directory. */
    suspend fun resolveManagedFile(trackId: String): String?
    /** Read-only metadata capability; never repairs a locator or publishes playback facts. */
    suspend fun resolveMetadataFile(trackId: String): String?
    /** Attribute-only, path-free observation. Never hashes/repairs media or refreshes a snapshot. */
    suspend fun inspectManagedCopy(trackId: String): DesktopCopyInspection?
    suspend fun close(): Unit
}

data class DesktopImportResult(
    val imported: Int,
    val duplicates: Int,
    val failed: Int,
    val errorCode: String? = null,
    /** Partial imports remain committed. Counts on cancellation are the last settled progress. */
    val cancelled: Boolean = false,
)

interface DesktopImportPort {
    /** Only user-selected sources. Implementations bound traversal, bytes and cancellation. */
    suspend fun importSelected(paths: List<String>): DesktopImportResult
}

data class DesktopAudioEvent(
    val generation: Long,
    val status: String,
    val positionMs: Long = 0,
    val durationMs: Long? = null,
    val isPlaying: Boolean = false,
    val errorCode: String? = null,
    val title: String? = null,
    val artist: String? = null,
)

/** One native execution owner; a generation identifies a single selected queue entry. */
interface DesktopAudioEngine {
    val state: StateFlow<DesktopAudioEvent>
    fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit
    fun play(generation: Long): Unit
    fun pause(generation: Long): Unit
    fun seek(generation: Long, positionMs: Long): Unit
    fun stop(generation: Long): Unit
    suspend fun close(): Unit
}
