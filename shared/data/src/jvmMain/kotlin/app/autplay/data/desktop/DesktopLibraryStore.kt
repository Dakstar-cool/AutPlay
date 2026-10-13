package app.autplay.data.desktop

import androidx.room3.Room
import androidx.room3.RoomDatabase
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopPlaylistEntry
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTrack
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.CREATE
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

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
) : DesktopLibraryStore {
    private val dao: DesktopDao = database.desktopDao()
    private val mutex: Mutex = Mutex()
    private val mutableState: MutableStateFlow<DesktopLibrarySnapshot> = MutableStateFlow(DesktopLibrarySnapshot())
    override val state: StateFlow<DesktopLibrarySnapshot> = mutableState.asStateFlow()
    private var closed: Boolean = false

    suspend fun initialize(): Unit { mutableState.value = dao.snapshot().toDomain() }

    override suspend fun addImportedTrack(track: DesktopImportedTrack): DesktopTrack = mutate {
        validId(track.id)
        validMetadata(track.title, track.artist, track.durationMs)
        requireCode(track.sha256.matches(Regex("[0-9a-f]{64}")) && track.byteSize in 1..MAX_TRACK_BYTES, "desktop_import_invalid")
        requireCode(track.managedFileName == "${track.sha256}.media", "desktop_import_invalid")
        val file = managedFile(track.managedFileName) ?: throw DesktopStorageException("desktop_media_unavailable")
        verifyBytes(file, track.sha256, track.byteSize)
        dao.addTrack(TrackRow(track.id, track.title, track.artist, track.durationMs, track.sha256, track.byteSize),
            LocatorRow(track.id, track.managedFileName)).toDomain()
    }

    override suspend fun updateTrackMetadata(trackId: String, title: String, artist: String, durationMs: Long?): Unit = mutate {
        validId(trackId)
        validMetadata(title, artist, durationMs)
        val row = dao.track(trackId) ?: throw DesktopStorageException("desktop_track_missing")
        if (row.title != title || row.artist != artist || row.durationMs != durationMs) {
            dao.updateMetadata(row.copy(title = title, artist = artist, durationMs = durationMs))
        }
    }

    override suspend fun createPlaylist(name: String): DesktopPlaylist = mutate {
        requireCode(name.isNotBlank() && name.length <= MAX_TEXT_LENGTH && name.none { it == '\u0000' }, "desktop_playlist_name_invalid")
        val row = PlaylistRow(newId(), name)
        dao.addPlaylist(row)
        DesktopPlaylist(row.id, row.name, emptyList())
    }

    override suspend fun appendToPlaylist(playlistId: String, trackId: String): Unit = mutate {
        validId(playlistId)
        validId(trackId)
        val entry = PlaylistEntryRow(newId(), playlistId, trackId, 0)
        dao.appendEntry(entry)
    }

    override suspend fun removePlaylistEntry(playlistId: String, entryId: String): Unit = mutate {
        validId(playlistId)
        validId(entryId)
        dao.removeEntry(playlistId, entryId)
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
            requireCode(copy.queueType.isNotBlank() && copy.queueType.length <= MAX_TEXT_LENGTH &&
                copy.repeatMode.isNotBlank() && copy.repeatMode.length <= MAX_TEXT_LENGTH &&
                copy.queueType.none { it == '\u0000' } && copy.repeatMode.none { it == '\u0000' } &&
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
                sessionId = copy.sessionId, sessionStartedAtMs = startedAt, playedMs = copy.playedMs), rows,
                event?.let { ListeningRow(it.id, it.queueEntryId, it.trackId, it.startedAtMs, it.playedMs, it.positionMs, it.finalized) })
        }
    }

    override suspend fun resolveManagedFile(trackId: String): String? = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureOpen()
            try {
                validId(trackId)
                val locator = dao.locator(trackId) ?: return@withLock null
                managedFile(locator.fileName)?.toString()
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_media_lookup_failed")
            }
        }
    }

    override suspend fun close(): Unit = withContext(Dispatchers.IO + NonCancellable) {
        try {
            mutex.withLock {
                if (!closed) {
                    closed = true
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
            ensureOpen()
            try {
                try { action() } finally {
                    // A cancelled caller may already have committed. Refresh from DB, never guess rollback.
                    withContext(NonCancellable) { mutableState.value = dao.snapshot().toDomain() }
                }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                throw sanitizedStorageFailure(failure, "desktop_mutation_failed")
            }
        }
    }

    private fun ensureOpen(): Unit { requireCode(!closed, "desktop_store_closed") }

    private fun managedFile(fileName: String): Path? {
        if (!fileName.matches(Regex("[0-9a-f]{64}\\.media"))) return null
        if (!Files.isDirectory(root, NOFOLLOW_LINKS) || !Files.isDirectory(media, NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(root) || Files.isSymbolicLink(media) || root.toRealPath() != root || media.toRealPath() != media) return null
        val candidate = media.resolve(fileName).normalize()
        if (candidate.parent != media || Files.isSymbolicLink(candidate) || !Files.isRegularFile(candidate, NOFOLLOW_LINKS) || !Files.isReadable(candidate)) return null
        val real = candidate.toRealPath()
        return real.takeIf { it.parent == media }
    }

    private suspend fun verifyBytes(file: Path, expectedHash: String, expectedSize: Long): Unit = runInterruptible {
        requireCode(Files.size(file) == expectedSize, "desktop_media_integrity_failed")
        val digest = MessageDigest.getInstance("SHA-256")
        var bytes = 0L
        Files.newInputStream(file, READ, NOFOLLOW_LINKS).use { input ->
            val buffer = ByteArray(128 * 1024)
            while (true) {
                if (Thread.currentThread().isInterrupted) throw CancellationException("desktop_import_cancelled")
                val count = input.read(buffer)
                if (count < 0) break
                bytes += count
                requireCode(bytes <= expectedSize, "desktop_media_integrity_failed")
                digest.update(buffer, 0, count)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        requireCode(bytes == expectedSize && actual == expectedHash && managedFile(file.fileName.toString()) == file, "desktop_media_integrity_failed")
    }
}

private const val MAX_TEXT_LENGTH: Int = 4096
private const val MAX_TRACK_BYTES: Long = 2L * 1024 * 1024 * 1024

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
    title.isNotBlank() && title.length <= MAX_TEXT_LENGTH && artist.length <= MAX_TEXT_LENGTH &&
        title.none { it == '\u0000' } && artist.none { it == '\u0000' } && (duration == null || duration >= 0), "desktop_metadata_invalid",
)
private fun sanitizedStorageFailure(failure: Exception, fallback: String): DesktopStorageException {
    if (failure is DesktopStorageException) return failure
    val code = failure.message?.takeIf { it.matches(Regex("desktop_[a-z_]+")) } ?: fallback
    return DesktopStorageException(code)
}
private fun TrackRow.toDomain(): DesktopTrack = DesktopTrack(id, title, artist, durationMs, sha256, byteSize)
private fun DatabaseSnapshot.toDomain(): DesktopLibrarySnapshot = DesktopLibrarySnapshot(
    tracks = tracks.map { it.toDomain() },
    playlists = playlists.map { playlist -> DesktopPlaylist(playlist.id, playlist.name,
        playlistEntries.filter { it.playlistId == playlist.id }.map { DesktopPlaylistEntry(it.id, it.trackId) }) },
    queue = queue?.let { row -> DesktopQueue(row.id, queueEntries.map { DesktopQueueEntry(it.id, it.trackId) },
        row.currentEntryId, row.queueType, row.positionMs, row.shuffleEnabled,
        queueEntries.sortedBy { it.shuffleOrdinal }.map { it.id }, row.repeatMode, row.sessionId, row.sessionStartedAtMs, row.playedMs) },
    history = history.map { DesktopListeningEvent(it.id, it.queueEntryId, it.trackId, it.startedAtMs, it.playedMs, it.positionMs, it.finalized) },
)
