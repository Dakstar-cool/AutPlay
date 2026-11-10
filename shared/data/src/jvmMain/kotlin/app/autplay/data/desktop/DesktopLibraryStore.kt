package app.autplay.data.desktop

import androidx.room3.Room
import androidx.room3.RoomDatabase
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopHistoryCursor
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryPage
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopLocalStatistics
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopPlaylistEntry
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopEmbeddedTrackTextApplication
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobAuthorization
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobLimits
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobPage
import app.autplay.application.desktop.DesktopImportJobPermit
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.ByteBuffer
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardOpenOption.CREATE
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.PosixFilePermissions
import java.nio.file.attribute.BasicFileAttributes
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import app.autplay.application.desktop.account.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull

/** The message is a stable code; platform exceptions and raw paths never escape this adapter. */
class DesktopStorageException(val code: String) : IllegalStateException(code)

/** Opens only the dedicated desktop database, on IO. No destructive migration fallback exists. */
suspend fun openDesktopLibraryStore(dataRoot: Path): DesktopLibraryStore {
    var channel: FileChannel? = null
    var lock: FileLock? = null
    var database: DesktopDatabase? = null
    try {
        return withContext(Dispatchers.IO) {
            val root = dataRoot.toAbsolutePath().normalize()
            rejectSymlinkAncestors(root)
            createPrivateDirectory(root)
            val ownedRoot = root.toRealPath()
            val media = ownedRoot.resolve("media")
            check(!Files.isSymbolicLink(media)) { "desktop_root_unsafe" }
            createPrivateDirectory(media)
            check(media.toRealPath().parent == ownedRoot) { "desktop_root_unsafe" }
            val databasePath = ownedRoot.resolve("desktop-library-v1.db")
            listOf(databasePath, ownedRoot.resolve("desktop-library-v1.db-wal"),
                ownedRoot.resolve("desktop-library-v1.db-shm"), ownedRoot.resolve("desktop-library.lock")).forEach {
                check(!Files.isSymbolicLink(it)) { "desktop_root_unsafe" }
            }
            val lockPath = ownedRoot.resolve("desktop-library.lock")
            if (!Files.exists(lockPath, NOFOLLOW_LINKS)) createPrivateFile(lockPath)
            val openedChannel = FileChannel.open(lockPath, CREATE, WRITE, NOFOLLOW_LINKS)
            channel = openedChannel
            val acquiredLock = try {
                openedChannel.tryLock() ?: throw DesktopStorageException("desktop_store_in_use")
            } catch (failure: java.nio.channels.OverlappingFileLockException) {
                throw DesktopStorageException("desktop_store_in_use")
            }
            lock = acquiredLock
            if (!Files.exists(databasePath, NOFOLLOW_LINKS)) createPrivateFile(databasePath)
            val openedDatabase = Room.databaseBuilder<DesktopDatabase>(name = databasePath.toString())
                .addMigrations(DesktopPreferenceMigration, DesktopCollectionMigration, DesktopTasteMigration,
                    DesktopPlaylistDescriptionMigration)
                .setDriver(BundledSQLiteDriver())
                .setJournalMode(RoomDatabase.JournalMode.WRITE_AHEAD_LOGGING)
                .setQueryCoroutineContext(Dispatchers.IO)
                .build()
            database = openedDatabase
            val store = PersistentDesktopLibraryStore(openedDatabase, ownedRoot, media.toRealPath(), openedChannel, acquiredLock)
            // Force schema verification before exposing a successfully opened store.
            store.initialize()
            store
        }
    } catch (failure: Throwable) {
        withContext(Dispatchers.IO + NonCancellable) {
            runCatching { database?.close() }
            runCatching { lock?.release() }
            runCatching { channel?.close() }
        }
        if (failure is CancellationException) throw failure
        if (failure is LinkageError) throw DesktopStorageException("desktop_native_driver_unavailable")
        if (failure !is Exception) throw failure
        throw sanitizedStorageFailure(failure, "desktop_database_open_failed")
    }
}

private class PersistentDesktopLibraryStore(
    private val database: DesktopDatabase,
    private val root: Path,
    private val media: Path,
    private val lockChannel: FileChannel,
    private val fileLock: FileLock,
) : DesktopLibraryStore, StandaloneAccountSourceOwner {
    private val dao: DesktopDao = database.desktopDao()
    private val mutex: Mutex = Mutex()
    private val mutableState: MutableStateFlow<DesktopLibrarySnapshot> = MutableStateFlow(DesktopLibrarySnapshot())
    override val state: StateFlow<DesktopLibrarySnapshot> = mutableState.asStateFlow()
    private var closed: Boolean = false
    // Guarded by mutex. A failed projection read requires a new store, never an implicit retry.
    private var snapshotUnavailable: Boolean = false
    // Mutex guarded, bounded and intentionally empty on every reopen. No durable source authority.
    private val importJobPermits: MutableMap<String, DesktopImportJobPermit> = LinkedHashMap()

    suspend fun initialize(): Unit { mutableState.value = dao.snapshot().toDomain() }

    override fun requireCanonicalAccountOwner(): Unit = ensureCanonicalAccountSource()

    override fun accountReview(): DesktopStandaloneAccountReview = object : DesktopStandaloneAccountReview {
        override suspend fun readPage(afterChangeId: String?, limit: Int): StandaloneAccountFactPage = mutex.withLock {
            ensureCanonicalAccountSource()
            require(limit in 1..100 && (afterChangeId == null || afterChangeId.length in 1..1_024))
            val rows = dao.accountFactPage(afterChangeId, limit + 1)
            require(rows.all { it.changeId.length in 1..1_024 && it.kind.length in 1..100 })
            val page = rows.take(limit)
            StandaloneAccountFactPage(page.map {
                StandaloneAccountFactChoice(it.changeId, it.kind, it.occurredAtMs, standaloneFactEligible(it))
            }, page.lastOrNull()?.changeId?.takeIf { rows.size > limit })
        }
        override suspend fun readSelected(selectedChangeIds: List<String>): StandaloneAccountReviewSnapshot = mutex.withLock {
            ensureCanonicalAccountSource()
            val rows = dao.accountSelectedRows(selectedChangeIds.toList())
            StandaloneAccountReviewSnapshot(rows.facts.map {
                AccountSelectedFact(it.changeId, it.aggregateId, it.kind, it.version.toLong(),
                    it.occurredAtMs, it.payloadJson.toByteArray(Charsets.UTF_8))
            }, rows.dependencies)
        }
    }

    override fun accountSource(registration: AccountSourceRegistration): DesktopStandaloneAccountSource =
        object : DesktopStandaloneAccountSource {
            override val source: AccountSourceRegistration = registration

            override suspend fun readPage(afterChangeId: String?, limit: Int): StandaloneAccountFactPage =
                mutex.withLock {
                    ensureCanonicalAccountSource()
                    require(limit in 1..100 && (afterChangeId == null || afterChangeId.length in 1..1_024))
                    val rows = dao.accountFactPage(afterChangeId, limit + 1)
                    require(rows.all { it.changeId.length in 1..1_024 && it.kind.length in 1..100 })
                    val page = rows.take(limit)
                    StandaloneAccountFactPage(page.map {
                        StandaloneAccountFactChoice(it.changeId, it.kind, it.occurredAtMs, standaloneFactEligible(it))
                    }, page.lastOrNull()?.changeId?.takeIf { rows.size > limit })
                }

            override suspend fun capture(source: AccountSourceRegistration,
                selectedChangeIds: List<String>): AccountSelectedSourceCapture {
                require(source == registration) { "desktop_account_source_changed" }
                val admission = Any()
                mutex.lock(admission)
                var returned = false
                try {
                    ensureCanonicalAccountSource()
                    currentCoroutineContext().ensureActive()
                    val rows = dao.accountSelectedRows(selectedChangeIds.toList())
                    val frozenSnapshot = AccountSelectedSourceSnapshot(registration, rows.facts.map {
                        AccountSelectedFact(it.changeId, it.aggregateId, it.kind, it.version.toLong(),
                            it.occurredAtMs, it.payloadJson.toByteArray(Charsets.UTF_8))
                    }, rows.dependencies)
                    val live = AtomicBoolean(true)
                    val capture = object : AccountSelectedSourceCapture {
                        override val snapshot: AccountSelectedSourceSnapshot = frozenSnapshot
                        override fun stillExact(): Boolean = live.get() && !closed && !snapshotUnavailable && fileLock.isValid
                        override fun close(): Unit {
                            if (live.compareAndSet(true, false)) mutex.unlock(admission)
                        }
                    }
                    // No dispatcher handoff follows ownership publication. The caller must close it.
                    returned = true
                    return capture
                } finally {
                    if (!returned) mutex.unlock(admission)
                }
            }
        }

    private fun ensureCanonicalAccountSource(): Unit {
        ensureUsable()
        requireCode(System.getProperty("os.name") == "Mac OS X" && fileLock.isValid,
            "desktop_account_source_unavailable")
        val canonical = Path.of(System.getProperty("user.home")).toRealPath()
            .resolve("Library/Application Support/AutPlay")
        requireCode(root == canonical && root == root.toRealPath(), "desktop_account_source_unavailable")
    }

    override suspend fun loadHistoryPage(before: DesktopHistoryCursor?, limit: Int): DesktopHistoryPage = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                currentCoroutineContext().ensureActive()
                requireCode(limit in 1..DesktopHistoryLimits.PAGE_SIZE, "desktop_history_limit_invalid")
                requireCode(before == null || (before.startedAtMs >= 0 &&
                    before.eventId.matches(Regex("[A-Za-z0-9_-]{1,128}"))), "desktop_history_cursor_invalid")
                val rows = dao.historyPage(before?.startedAtMs, before?.eventId, limit + 1)
                val events = rows.take(limit).map { it.toDomain() }
                val next = if (rows.size > limit) events.last().let { DesktopHistoryCursor(it.startedAtMs, it.id) } else null
                DesktopHistoryPage(events, next)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                // A transient page read does not change the published snapshot or its sticky latch.
                throw sanitizedStorageFailure(failure, "desktop_history_read_failed")
            }
        }
    }

    override suspend fun readLocalStatistics(throughInclusiveMs: Long): DesktopLocalStatistics = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                currentCoroutineContext().ensureActive()
                requireCode(throughInclusiveMs >= 0, "desktop_statistics_cutoff_invalid")
                dao.readLocalStatistics(throughInclusiveMs)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                // A read failure neither refreshes state nor changes the sticky snapshot latch.
                throw sanitizedStorageFailure(failure, "desktop_statistics_read_failed")
            }
        }
    }

    override suspend fun addImportedTrack(track: DesktopImportedTrack): DesktopTrack = mutate {
        validId(track.id)
        validMetadata(track.title, track.artist, track.durationMs)
        requireCode(track.sha256.matches(Regex("[0-9a-f]{64}")) && track.byteSize in 1..MAX_TRACK_BYTES, "desktop_import_invalid")
        requireCode(DesktopManagedMedia.hasExpectedHash(track.managedFileName, track.sha256), "desktop_import_invalid")
        val file = managedFile(track.managedFileName) ?: throw DesktopStorageException("desktop_media_unavailable")
        verifyBytes(file, track.sha256, track.byteSize)
        val persisted = dao.addTrack(TrackRow(track.id, track.title, track.artist, track.durationMs, track.sha256, track.byteSize),
            LocatorRow(track.id, track.managedFileName))
        persisted.toDomain(dao.trackPreference(persisted.id)?.preference ?: "NEUTRAL",
            dao.trackCollectionState(persisted.id)?.collectionState ?: "ACTIVE")
    }

    override suspend fun createImportJob(manifest: DesktopImportJobManifest): DesktopImportJob {
        val frozen = DesktopImportJobProjection.freeze(manifest)
        return mutate { dao.createImportJob(frozen) }
    }

    override suspend fun loadImportJob(jobId: String): DesktopImportJob? = readImportJobs { dao.loadImportJob(jobId) }

    override suspend fun loadUnfinishedImportJobs(after: DesktopImportJobCursor?, limit: Int): DesktopImportJobPage =
        readImportJobs { dao.loadUnfinishedImportJobs(after, limit) }

    private suspend fun <T> readImportJobs(action: suspend () -> T): T = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                currentCoroutineContext().ensureActive()
                action()
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_import_job_read_failed")
            }
        }
    }

    override suspend fun confirmImportJobAttempt(attempt: DesktopImportJobAttempt): DesktopImportJobAuthorization {
        val frozen = DesktopImportJobProjection.freeze(attempt)
        return mutate {
            val history = dao.loadImportJobHistory(frozen.jobId)
            val replayed = DesktopImportJobProjection.replay(history, frozen.commandId,
                DesktopImportJobProjection.attemptSignature(frozen))
            if (!replayed) requireCode(frozen.jobId in importJobPermits ||
                importJobPermits.size < DesktopImportJobLimits.ACTIVE_PERMITS, "desktop_import_job_permit_limit")
            val job = dao.confirmImportJobAttempt(frozen)
            val permit = if (replayed) importJobPermits[frozen.jobId]?.takeIf { it.attemptId == frozen.attemptId }
                else DesktopImportJobPermit(frozen.jobId, frozen.attemptId, frozen.previewId, newId()).also {
                    importJobPermits[frozen.jobId] = it
                }
            DesktopImportJobAuthorization(job, permit)
        }
    }

    override suspend fun commitImportJobCandidate(
        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
    ): DesktopImportJob = settleImportJob(settlement, permit, track, null)

    override suspend fun failImportJobCandidate(
        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, failure: DesktopImportJobFailure,
    ): DesktopImportJob = settleImportJob(settlement, permit, null, failure)

    private suspend fun settleImportJob(
        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit,
        track: DesktopImportedTrack?, failure: DesktopImportJobFailure?,
    ): DesktopImportJob = mutate {
        val request = DesktopImportJobProjection.settlementRequest(settlement, track, failure)
        val history = dao.loadImportJobHistory(settlement.jobId)
        // A lost response can be reconciled without a source grant or another filesystem read.
        if (DesktopImportJobProjection.replay(history, settlement.commandId, listOf(request))) return@mutate history.job
        requireCode(importJobPermits[settlement.jobId] == permit && permit.jobId == settlement.jobId &&
            permit.attemptId == settlement.attemptId, "desktop_import_job_reselection_required")
        requireCode(history.job.revision == settlement.expectedRevision &&
            history.job.phase == DesktopImportJobPhase.RESELECTION_REQUIRED && history.attempt?.attemptId == settlement.attemptId,
            "desktop_import_job_conflict")
        val mapping = history.attempt?.selections?.singleOrNull { it.candidateId == settlement.candidateId }
        requireCode(mapping != null && mapping.selectionId == settlement.selectionId && (track == null || track.byteSize == mapping.byteSize),
            "desktop_import_job_conflict")
        if (track != null) {
            validId(track.id)
            validMetadata(track.title, track.artist, track.durationMs)
            requireCode(DesktopManagedMedia.hasExpectedHash(track.managedFileName, track.sha256), "desktop_import_invalid")
            val file = managedFile(track.managedFileName) ?: throw DesktopStorageException("desktop_media_unavailable")
            verifyBytes(file, track.sha256, track.byteSize)
        }
        dao.settleImportJobCandidate(settlement, track, failure).also {
            val current = it.confirmedAttempt
            if (it.phase == DesktopImportJobPhase.COMPLETED || (current != null && current.selections.all { selection ->
                    it.candidates.single { candidate -> candidate.candidate.id == selection.candidateId }.settledAttemptId == current.attemptId
                })) importJobPermits.remove(settlement.jobId)
        }
    }

    override suspend fun stopImportJob(stop: DesktopImportJobStop): DesktopImportJob = mutate {
        // A stale stop cannot revoke a newer attempt's process-local permit.
        val signature = DesktopImportJobProjection.stopSignature(stop)
        val history = dao.loadImportJobHistory(stop.jobId)
        if (DesktopImportJobProjection.replay(history, stop.commandId, signature)) return@mutate history.job
        requireCode(history.job.revision == stop.expectedRevision && history.job.phase != DesktopImportJobPhase.COMPLETED,
            "desktop_import_job_conflict")
        // Local admission closes even if the subsequent stop fact cannot commit. Reauthorization
        // is then required; no failed/cancelled stop may silently leave permission to copy/settle.
        importJobPermits.remove(stop.jobId)
        dao.stopImportJob(stop)
    }

    override suspend fun updateTrackMetadata(trackId: String, title: String, artist: String, durationMs: Long?): Unit = mutate {
        validId(trackId)
        validMetadata(title, artist, durationMs)
        val row = dao.track(trackId) ?: throw DesktopStorageException("desktop_track_missing")
        if (row.title != title || row.artist != artist || row.durationMs != durationMs) {
            dao.updateMetadata(row.copy(title = title, artist = artist, durationMs = durationMs))
        }
    }

    override suspend fun updateTrackTextMetadata(trackId: String, title: String, artist: String): Unit = mutate {
        validId(trackId)
        // Validate new text only; persisted duration is not a caller-owned edit or normalization.
        validMetadata(title, artist, null)
        dao.updateTextMetadata(trackId, title, artist)
    }

    override suspend fun loadTrackTextProvenance(trackId: String): DesktopTrackTextProvenance? = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                currentCoroutineContext().ensureActive()
                validId(trackId)
                dao.loadTextProvenance(trackId)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_text_provenance_read_failed")
            }
        }
    }

    override suspend fun updateTrackDuration(trackId: String, durationMs: Long): Unit = mutate {
        validId(trackId)
        requireCode(durationMs >= 0, "desktop_metadata_invalid")
        dao.updateDuration(trackId, durationMs)
    }

    override suspend fun applyEmbeddedTrackText(baseline: DesktopTrackTextBaseline, title: String, artist: String): Unit = mutate {
        validId(baseline.trackId)
        validMetadata(title, artist, null)
        // Baseline labels are exact persisted before-images, not new values to normalize.
        dao.applyEmbeddedText(baseline, title, artist)
    }

    override suspend fun applyEmbeddedTrackText(application: DesktopEmbeddedTrackTextApplication): Unit = mutate {
        val baseline = application.expected.baseline
        validId(baseline.trackId)
        validMetadata(application.title ?: baseline.title, application.artist ?: baseline.artist, null)
        requireCode(application.expected.metadataRevision >= 0, "desktop_metadata_invalid")
        dao.applyEmbeddedText(application)
    }

    override suspend fun setTrackPreference(trackId: String, preference: String): Unit = mutate {
        validId(trackId)
        // Exact bounded input only; persisted unknown values are not passed through this gate.
        requireCode(preference == "NEUTRAL" || preference == "LIKED" || preference == "DISLIKED",
            "desktop_preference_invalid")
        dao.updateTrackPreference(trackId, preference)
    }

    override suspend fun removeTrackFromCollection(trackId: String): Unit = mutate {
        validId(trackId)
        dao.updateTrackCollectionState(trackId, "REMOVED")
    }

    override suspend fun restoreTrackToCollection(trackId: String): Unit = mutate {
        validId(trackId)
        dao.updateTrackCollectionState(trackId, "ACTIVE")
    }

    override suspend fun createPlaylist(name: String): DesktopPlaylist = mutate {
        requireCode(name.isNotBlank() && validText(name), "desktop_playlist_name_invalid")
        val row = PlaylistRow(newId(), name)
        dao.addPlaylist(row)
        DesktopPlaylist(row.id, row.name, emptyList(), row.description)
    }

    override suspend fun renamePlaylist(playlistId: String, name: String): Unit = mutate {
        validId(playlistId)
        requireCode(name.isNotBlank() && validText(name), "desktop_playlist_name_invalid")
        dao.renamePlaylist(playlistId, name)
    }

    override suspend fun updatePlaylistDescription(playlistId: String, expectedDescription: String?, description: String?): Unit = mutate {
        validId(playlistId)
        requirePlaylistDescription(description)
        dao.updatePlaylistDescription(playlistId, expectedDescription, description)
    }

    override suspend fun deletePlaylist(playlistId: String): Unit = mutate {
        validId(playlistId)
        dao.deletePlaylist(playlistId)
    }

    override suspend fun appendToPlaylist(playlistId: String, trackId: String): Unit = mutate {
        validId(playlistId)
        validId(trackId)
        val entry = PlaylistEntryRow(newId(), playlistId, trackId, 0)
        dao.appendEntry(entry)
    }

    override suspend fun appendTracksToPlaylist(playlistId: String, trackIds: List<String>): Unit {
        // Bound and freeze caller-owned references before the first dispatch or suspension.
        val frozenTrackIds = trackIds.take(DesktopLocalLimits.MAX_QUEUE_ENTRIES + 1)
        requireCode(frozenTrackIds.size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES, "desktop_playlist_batch_limit")
        validId(playlistId)
        frozenTrackIds.forEach(::validId)
        val entries = frozenTrackIds.map { PlaylistEntryRow(newId(), playlistId, it, 0) }
        mutate { dao.appendEntries(playlistId, entries) }
    }

    override suspend fun removePlaylistEntry(playlistId: String, entryId: String): Unit = mutate {
        validId(playlistId)
        validId(entryId)
        dao.removeEntry(playlistId, entryId)
    }

    override suspend fun movePlaylistEntry(playlistId: String, entryId: String, direction: Int): Unit = mutate {
        validId(playlistId)
        validId(entryId)
        dao.moveEntry(playlistId, entryId, direction)
    }

    override suspend fun setCurrentListenTasteExcluded(
        expectedQueueId: String, expectedEntryId: String, expectedListeningEventId: String, excluded: Boolean,
    ): DesktopQueue? = mutate {
        validId(expectedQueueId)
        validId(expectedEntryId)
        validId(expectedListeningEventId)
        dao.updateTaste(expectedQueueId, expectedEntryId, expectedListeningEventId, excluded, currentListen = true)
            ?.let { it.queue.toDomain(it.entries) }
    }

    override suspend fun setQueueSessionTasteExcluded(expectedQueueId: String, excluded: Boolean): DesktopQueue? = mutate {
        validId(expectedQueueId)
        dao.updateTaste(expectedQueueId, null, null, excluded, currentListen = false)
            ?.let { it.queue.toDomain(it.entries) }
    }

    override suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent?): Unit {
        val copy = queue.copy(entries = queue.entries.toList(), shuffleOrder = queue.shuffleOrder.toList())
        mutate {
            val startedAt = copy.sessionStartedAtMs
            validId(copy.id)
            requireCode(copy.entries.size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES, "desktop_queue_limit")
            copy.entries.forEach { validId(it.id); validId(it.trackId) }
            val ids = copy.entries.map { it.id }.toSet()
            requireCode(ids.size == copy.entries.size && (copy.currentEntryId == null || copy.currentEntryId in ids) &&
                copy.shuffleOrder.size == ids.size && copy.shuffleOrder.toSet() == ids, "desktop_queue_invalid")
            requireCode(copy.queueType.isNotBlank() && validText(copy.queueType) &&
                copy.repeatMode.isNotBlank() && validText(copy.repeatMode) &&
                copy.positionMs >= 0 && copy.playedMs >= 0 &&
                (startedAt == null || startedAt >= 0) &&
                (copy.sessionId == null) == (startedAt == null), "desktop_queue_invalid")
            copy.sessionId?.let(::validId)
            event?.let {
                validId(it.id); validId(it.queueEntryId); validId(it.trackId)
                requireCode(it.startedAtMs >= 0 && it.playedMs >= 0 && it.positionMs >= 0, "desktop_listening_invalid")
            }
            val shuffled = copy.shuffleOrder.withIndex().associate { it.value to it.index }
            val rows = copy.entries.mapIndexed { index, entry -> QueueEntryRow(entry.id, entry.trackId, index, shuffled.getValue(entry.id)) }
            dao.saveQueue(QueueRow(id = copy.id, currentEntryId = copy.currentEntryId, queueType = copy.queueType,
                positionMs = copy.positionMs, shuffleEnabled = copy.shuffleEnabled, repeatMode = copy.repeatMode,
                sessionId = copy.sessionId, sessionStartedAtMs = startedAt, playedMs = copy.playedMs,
                sessionTasteState = copy.sessionTasteState, listenTasteState = copy.listenTasteState), rows,
                event?.let { ListeningRow(it.id, it.queueEntryId, it.trackId, it.startedAtMs, it.playedMs, it.positionMs, it.finalized,
                    it.sessionTasteState, it.listenTasteState) })
        }
    }

    override suspend fun resolveManagedFile(trackId: String): String? = withContext(Dispatchers.IO) {
        DesktopManagedMedia.withPublicationLock {
            mutex.withLock {
                ensureUsable()
                try {
                    validId(trackId)
                    val locator = dao.locator(trackId) ?: return@withLock null
                    val file = managedFile(locator.fileName) ?: return@withLock null
                    val track = dao.track(trackId) ?: throw DesktopStorageException("desktop_track_missing")
                    requireCode(DesktopManagedMedia.hasExpectedHash(locator.fileName, track.sha256), "desktop_media_integrity_failed")
                    if (locator.fileName.endsWith(".media")) {
                        val repaired = withTimeoutOrNull(LEGACY_REPAIR_TIMEOUT_MS) { repairLegacyCopy(track, file, locator) }
                            ?: throw DesktopStorageException("desktop_legacy_media_time_limit")
                        repaired.toString()
                    } else {
                        requireCode(track.byteSize in 1..MAX_TRACK_BYTES && Files.size(file) == track.byteSize,
                            "desktop_media_integrity_failed")
                        file.toString()
                    }
                } catch (failure: Exception) {
                    if (failure is CancellationException) throw failure
                    throw sanitizedStorageFailure(failure, "desktop_media_lookup_failed")
                }
            }
        }
    }

    override suspend fun resolveMetadataFile(trackId: String): String? = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                validId(trackId)
                val locator = dao.locator(trackId) ?: return@withLock null
                val track = dao.track(trackId) ?: return@withLock null
                val file = managedFile(locator.fileName) ?: return@withLock null
                requireCode(DesktopManagedMedia.hasExpectedHash(locator.fileName, track.sha256), "desktop_media_integrity_failed")
                requireCode(track.byteSize in 1..MAX_TRACK_BYTES && Files.size(file) == track.byteSize,
                    "desktop_media_integrity_failed")
                // A read capability never repairs a legacy locator, hashes audio, or refreshes state.
                file.toString()
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_media_lookup_failed")
            }
        }
    }

    override suspend fun inspectManagedCopy(trackId: String): DesktopCopyInspection? = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                currentCoroutineContext().ensureActive()
                ensureThreadActive()
                validId(trackId)
                val track = dao.track(trackId) ?: return@withLock null
                val locator = dao.locator(trackId)
                // Read current persisted identity, never the possibly stale display snapshot.
                val present = locator != null && track.byteSize in 1..MAX_TRACK_BYTES &&
                    DesktopManagedMedia.hasExpectedHash(locator.fileName, track.sha256) &&
                    withTimeout(COPY_INSPECTION_TIMEOUT_MS) {
                        runInterruptible {
                            try {
                                ensureThreadActive()
                                val file = managedFile(locator.fileName) ?: return@runInterruptible false
                                val before = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                                ensureThreadActive()
                                if (!before.isRegularFile || before.size() != track.byteSize) return@runInterruptible false
                                if (ownedRegularFile(file) != file) return@runInterruptible false
                                val after = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                                ensureThreadActive()
                                after.isRegularFile && after.size() == track.byteSize &&
                                    after.fileKey() == before.fileKey() && after.lastModifiedTime() == before.lastModifiedTime()
                            } catch (failure: java.io.IOException) {
                                ensureThreadActive()
                                false
                            } catch (failure: SecurityException) {
                                ensureThreadActive()
                                false
                            }
                        }
                    }
                currentCoroutineContext().ensureActive()
                ensureThreadActive()
                // Advisory attributes only: no bytes, hash/decoder proof, repair, fact or refresh.
                DesktopCopyInspection(track.id, track.sha256, track.byteSize, if (present) "PRESENT" else "UNAVAILABLE")
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_copy_inspection_failed")
            }
        }
    }

    override suspend fun close(): Unit = withContext(Dispatchers.IO + NonCancellable) {
        try {
            mutex.withLock {
                if (!closed) {
                    closed = true
                    importJobPermits.clear()
                    try {
                        database.close()
                    } finally {
                        try { fileLock.release() } finally { lockChannel.close() }
                    }
                }
            }
        } catch (failure: Exception) {
            throw sanitizedStorageFailure(failure, "desktop_store_close_failed")
        }
    }

    private suspend fun <T> mutate(action: suspend () -> T): T = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureUsable()
            try {
                var actionFailure: Throwable? = null
                try {
                    action()
                } catch (failure: Throwable) {
                    actionFailure = failure
                    throw failure
                } finally {
                    // A cancelled caller may already have committed. Refresh from DB, never guess rollback.
                    try {
                        withContext(NonCancellable) { mutableState.value = dao.snapshot().toDomain() }
                    } catch (failure: Throwable) {
                        // The DB may have committed while state still holds the previous snapshot.
                        // Reject all later writes and capabilities before releasing the mutex.
                        snapshotUnavailable = true
                        // Preserve cancellation/transaction failure if refresh also fails. Do not attach
                        // raw platform exceptions (which may contain paths) to the public failure.
                        if (actionFailure == null) throw DesktopStorageException("desktop_snapshot_unavailable")
                    }
                }
            } catch (failure: Exception) {
                val outgoing = if (failure is CancellationException) failure
                    else sanitizedStorageFailure(failure, "desktop_mutation_failed")
                if (snapshotUnavailable &&
                    !(outgoing is DesktopStorageException && outgoing.code == "desktop_snapshot_unavailable")) {
                    // Preserve the primary failure while giving callers a path-free recovery marker.
                    outgoing.addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
                }
                throw outgoing
            }
        }
    }

    private fun ensureUsable(): Unit {
        requireCode(!closed, "desktop_store_closed")
        requireCode(!snapshotUnavailable, "desktop_snapshot_unavailable")
    }

    private fun managedFile(fileName: String): Path? {
        if (!DesktopManagedMedia.isSafeFileName(fileName)) return null
        return ownedRegularFile(media.resolve(fileName))
    }

    private fun ownedRegularFile(candidate: Path): Path? {
        if (!Files.isDirectory(root, NOFOLLOW_LINKS) || !Files.isDirectory(media, NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(root) || Files.isSymbolicLink(media) || root.toRealPath() != root || media.toRealPath() != media) return null
        if (candidate != candidate.normalize() || candidate.parent != media || Files.isSymbolicLink(candidate) ||
            !Files.isRegularFile(candidate, NOFOLLOW_LINKS) || !Files.isReadable(candidate)) return null
        val real = candidate.toRealPath()
        return real.takeIf { it.parent == media }
    }

    private suspend fun verifyBytes(file: Path, expectedHash: String, expectedSize: Long): Unit = runInterruptible {
        verifyBytesBlocking(file, expectedHash, expectedSize)
    }

    private fun verifyBytesBlocking(file: Path, expectedHash: String, expectedSize: Long): Unit {
        requireCode(ownedRegularFile(file) == file, "desktop_media_integrity_failed")
        val before = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        requireCode(before.isRegularFile && before.size() == expectedSize, "desktop_media_integrity_failed")
        val digest = MessageDigest.getInstance("SHA-256")
        var bytes = 0L
        Files.newInputStream(file, READ, NOFOLLOW_LINKS).use { input ->
            val buffer = ByteArray(128 * 1024)
            while (true) {
                ensureThreadActive()
                val count = input.read(buffer)
                if (count < 0) break
                bytes += count
                requireCode(bytes <= expectedSize, "desktop_media_integrity_failed")
                digest.update(buffer, 0, count)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        ensureThreadActive()
        val after = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        requireCode(bytes == expectedSize && actual == expectedHash && ownedRegularFile(file) == file &&
            after.isRegularFile && after.size() == before.size() && after.fileKey() == before.fileKey() &&
            after.lastModifiedTime() == before.lastModifiedTime(), "desktop_media_integrity_failed")
    }

    private suspend fun repairLegacyCopy(track: TrackRow, source: Path, locator: LocatorRow): Path {
        requireCode(track.byteSize in 1..MAX_TRACK_BYTES, "desktop_legacy_media_size_limit")
        var temporary: Path? = null
        try {
            val extension = runInterruptible {
                requireCode(ownedRegularFile(source) == source, "desktop_media_unavailable")
                DesktopAudioHeaderProbe.detectExtension(source)
                    ?: throw DesktopStorageException("desktop_legacy_media_format_unknown")
            }
            val repairedName = "${track.sha256}.$extension"
            requireCode(DesktopManagedMedia.hasExpectedHash(repairedName, track.sha256), "desktop_legacy_media_format_unknown")
            val destination = media.resolve(repairedName)
            runInterruptible {
                if (Files.exists(destination, NOFOLLOW_LINKS)) {
                    // Never overwrite an unsafe or corrupt sibling; only verified bytes grant a capability.
                    verifyBytesBlocking(destination, track.sha256, track.byteSize)
                    requireCode(DesktopAudioHeaderProbe.detectExtension(destination) == extension, "desktop_media_integrity_failed")
                    FileChannel.open(destination, WRITE, NOFOLLOW_LINKS).use { it.force(true) }
                } else {
                    requireCode(ownedRegularFile(source) == source, "desktop_media_unavailable")
                    val before = Files.readAttributes(source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                    requireCode(before.isRegularFile && before.size() == track.byteSize, "desktop_media_integrity_failed")
                    requireRepairSpace(track.byteSize)
                    val attributes = if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                        arrayOf(PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
                    } else emptyArray()
                    val part = Files.createTempFile(media, ".repair-", ".part", *attributes)
                    temporary = part
                    val digest = MessageDigest.getInstance("SHA-256")
                    var copied = 0L
                    FileChannel.open(source, READ, NOFOLLOW_LINKS).use { input ->
                        FileChannel.open(part, WRITE, NOFOLLOW_LINKS).use { output ->
                            val buffer = ByteBuffer.allocate(128 * 1024)
                            while (true) {
                                ensureThreadActive()
                                buffer.clear()
                                val count = input.read(buffer)
                                if (count < 0) break
                                if (count == 0) continue
                                copied += count
                                requireCode(copied <= track.byteSize, "desktop_media_integrity_failed")
                                requireRepairSpace(0)
                                buffer.flip()
                                digest.update(buffer.asReadOnlyBuffer())
                                while (buffer.hasRemaining()) { ensureThreadActive(); output.write(buffer) }
                            }
                            output.force(true)
                        }
                    }
                    val after = Files.readAttributes(source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                    requireCode(after.isRegularFile && after.size() == before.size() &&
                        after.lastModifiedTime() == before.lastModifiedTime() && after.fileKey() == before.fileKey() &&
                        copied == track.byteSize && ownedRegularFile(source) == source, "desktop_media_integrity_failed")
                    val actualHash = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
                    requireCode(actualHash == track.sha256, "desktop_media_integrity_failed")
                    verifyBytesBlocking(part, track.sha256, track.byteSize)
                    requireCode(DesktopAudioHeaderProbe.detectExtension(part) == extension, "desktop_media_integrity_failed")
                    ensureThreadActive()
                    try {
                        Files.move(part, destination, ATOMIC_MOVE)
                    } catch (failure: AtomicMoveNotSupportedException) {
                        throw DesktopStorageException("desktop_legacy_media_atomic_publish_unavailable")
                    }
                    verifyBytesBlocking(destination, track.sha256, track.byteSize)
                }
                if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    try {
                        FileChannel.open(media, READ, NOFOLLOW_LINKS).use { it.force(true) }
                    } catch (failure: java.io.IOException) {
                        throw DesktopStorageException("desktop_legacy_media_publish_sync_failed")
                    }
                }
                ensureThreadActive()
            }
            // Old bytes are retained even if cancellation or a DB failure follows publication.
            currentCoroutineContext().ensureActive()
            dao.repairManagedLocator(track.id, locator.fileName, repairedName)
            return managedFile(repairedName) ?: throw DesktopStorageException("desktop_media_unavailable")
        } finally {
            temporary?.let { part ->
                withContext(Dispatchers.IO + NonCancellable) {
                    runCatching { if (ownedRegularFile(part) == part) Files.deleteIfExists(part) }
                }
            }
        }
    }

    private fun requireRepairSpace(bytes: Long): Unit {
        requireCode(Files.getFileStore(media).usableSpace >= bytes + LEGACY_REPAIR_RESERVE_BYTES, "desktop_legacy_media_insufficient_space")
    }
}

private const val MAX_TEXT_LENGTH: Int = 4096
private const val MAX_TRACK_BYTES: Long = 2L * 1024 * 1024 * 1024
private const val COPY_INSPECTION_TIMEOUT_MS: Long = 5_000
private const val LEGACY_REPAIR_TIMEOUT_MS: Long = 15L * 60 * 1000
private const val LEGACY_REPAIR_RESERVE_BYTES: Long = 128L * 1024 * 1024

private fun ensureThreadActive(): Unit {
    if (Thread.currentThread().isInterrupted) throw CancellationException("desktop_media_cancelled")
}

private fun rejectSymlinkAncestors(path: Path): Unit {
    var current = path.root
    for (component in path) {
        current = current.resolve(component)
        requireCode(!Files.isSymbolicLink(current), "desktop_root_unsafe")
    }
}
private fun createPrivateDirectory(path: Path): Unit {
    if (path.fileSystem.supportedFileAttributeViews().contains("posix")) {
        Files.createDirectories(path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
    } else Files.createDirectories(path)
}
private fun createPrivateFile(path: Path): Unit {
    try {
        if (path.fileSystem.supportedFileAttributeViews().contains("posix")) {
            Files.createFile(path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
        } else Files.createFile(path)
    } catch (failure: java.nio.file.FileAlreadyExistsException) {
        requireCode(!Files.isSymbolicLink(path), "desktop_root_unsafe")
    }
}
private fun newId(): String = UUID.randomUUID().toString()
private fun requireCode(condition: Boolean, code: String): Unit { if (!condition) throw DesktopStorageException(code) }
private fun validId(id: String): Unit = requireCode(id.matches(Regex("[A-Za-z0-9_-]{1,128}")), "desktop_identity_invalid")
private fun validMetadata(title: String, artist: String, duration: Long?): Unit = requireCode(
    title.isNotBlank() && validText(title) && validText(artist) && (duration == null || duration >= 0), "desktop_metadata_invalid",
)
private fun validText(value: String): Boolean {
    if (value.length > MAX_TEXT_LENGTH) return false
    var offset = 0
    while (offset < value.length) {
        val character = value[offset++]
        if (character == '\u0000') return false
        if (character.isHighSurrogate()) {
            if (offset >= value.length || !value[offset++].isLowSurrogate()) return false
        } else if (character.isLowSurrogate()) return false
    }
    return true
}
/** Validate new commands only, never reinterpret a persisted legacy before-image. */
internal fun requirePlaylistDescription(value: String?): Unit = requireCode(
    value == null || (value.isNotEmpty() && value.length <= 500 && validText(value)),
    "desktop_playlist_description_invalid",
)
private fun sanitizedStorageFailure(failure: Exception, fallback: String): DesktopStorageException {
    if (failure is DesktopStorageException) return failure
    val code = failure.message?.takeIf { it.matches(Regex("desktop_[a-z_]+")) } ?: fallback
    return DesktopStorageException(code)
}
private fun TrackRow.toDomain(preference: String = "NEUTRAL", collectionState: String = "ACTIVE"): DesktopTrack =
    DesktopTrack(id, title, artist, durationMs, sha256, byteSize, preference, collectionState)
private fun DatabaseSnapshot.toDomain(): DesktopLibrarySnapshot {
    val byTrack = preferences.associate { it.trackId to it.preference }
    val collectionByTrack = collection.associate { it.trackId to it.collectionState }
    return DesktopLibrarySnapshot(
        tracks = tracks.map { it.toDomain(byTrack[it.id] ?: "NEUTRAL", collectionByTrack[it.id] ?: "ACTIVE") },
        playlists = playlists.map { playlist -> DesktopPlaylist(playlist.id, playlist.name,
            playlistEntries.filter { it.playlistId == playlist.id }.map { DesktopPlaylistEntry(it.id, it.trackId) },
            playlist.description) },
        queue = queue?.toDomain(queueEntries),
        history = history.map { it.toDomain() },
    )
}

private fun ListeningRow.toDomain(): DesktopListeningEvent = DesktopListeningEvent(
    id, queueEntryId, trackId, startedAtMs, playedMs, positionMs, finalized, sessionTasteState, listenTasteState,
)

private fun QueueRow.toDomain(entries: List<QueueEntryRow>): DesktopQueue = DesktopQueue(
    id, entries.sortedBy { it.ordinal }.map { DesktopQueueEntry(it.id, it.trackId) },
    currentEntryId, queueType, positionMs, shuffleEnabled, entries.sortedBy { it.shuffleOrdinal }.map { it.id },
    repeatMode, sessionId, sessionStartedAtMs, playedMs, sessionTasteState, listenTasteState,
)
