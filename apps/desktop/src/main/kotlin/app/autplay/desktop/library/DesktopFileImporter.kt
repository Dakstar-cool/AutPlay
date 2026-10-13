package app.autplay.desktop.library

import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopLibraryStore
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.FileAlreadyExistsException
import java.nio.file.FileVisitResult
import java.nio.file.Files
import java.nio.file.InvalidPathException
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.SimpleFileVisitor
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Imports only explicit file/folder selections. The original is never written or removed.
 * Limits apply to one call, including folder enumeration and verification of managed copies.
 * The store factory owns the process lock; publication is serialized across importer instances
 * inside that process. The owned data directory must not be writable by untrusted processes.
 */
class DesktopFileImporter(
    private val store: DesktopLibraryStore,
    dataRoot: Path,
) : DesktopImportPort {
    private val root: Path = dataRoot.toAbsolutePath().normalize()

    override suspend fun importSelected(paths: List<String>): DesktopImportResult = withContext(Dispatchers.IO) {
        val report = Report()
        if (paths.size > MAX_SELECTIONS) {
            return@withContext DesktopImportResult(0, 0, 1, "import_selection_limit")
        }
        if (paths.isEmpty()) return@withContext report.result()
        val completed = withTimeoutOrNull(BATCH_TIMEOUT_MS) {
            try {
                val media = prepareMediaDirectory()
                val candidates = collectSelections(paths, report)
                val budget = ByteBudget()
                for (source in candidates) {
                    currentCoroutineContext().ensureActive()
                    try {
                        importOne(source, media, budget, report)
                    } catch (failure: ImportFailure) {
                        report.failure(failure.code)
                        if (failure.stopBatch) break
                    } catch (failure: IOException) {
                        report.failure("import_io_failed")
                    } catch (failure: SecurityException) {
                        report.failure("import_access_denied")
                    }
                }
            } catch (failure: ImportFailure) {
                report.failure(failure.code)
            } catch (failure: IOException) {
                report.failure("import_io_failed")
            } catch (failure: SecurityException) {
                report.failure("import_access_denied")
            }
            true
        }
        if (completed == null) report.failure("import_time_limit")
        report.result()
    }

    private suspend fun prepareMediaDirectory(): Path = runInterruptible {
        rejectSymlinkComponents(root)
        Files.createDirectories(root)
        val media = root.resolve("media")
        if (!Files.exists(media, NOFOLLOW_LINKS)) {
            try {
                if (root.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    Files.createDirectory(media, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
                } else {
                    Files.createDirectory(media)
                }
            } catch (failure: FileAlreadyExistsException) {
                // Another importer may have created it; validate it before use below.
            }
        }
        validateMediaDirectory(media)
        media
    }

    private fun validateMediaDirectory(media: Path): Unit {
        rejectSymlinkComponents(media)
        if (!Files.isDirectory(media, NOFOLLOW_LINKS) || !Files.isReadable(media) || !Files.isWritable(media)) {
            throw ImportFailure("import_managed_directory_unavailable", true)
        }
        val realRoot = root.toRealPath()
        val realMedia = media.toRealPath()
        if (realMedia.parent != realRoot || realMedia.fileName.toString() != "media") {
            throw ImportFailure("import_managed_directory_unsafe", true)
        }
    }

    private suspend fun collectSelections(paths: List<String>, report: Report): List<Path> = runInterruptible {
        val candidates = linkedSetOf<Path>()
        var visited = 0
        var stop = false
        for (raw in paths) {
            checkInterrupted()
            if (stop) break
            try {
                if (raw.isBlank() || raw.length > MAX_SELECTION_CHARS) throw ImportFailure("import_invalid_selection")
                val selected = Path.of(raw).toAbsolutePath().normalize()
                rejectSymlinkComponents(selected)
                val selectedAttributes = Files.readAttributes(selected, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (selectedAttributes.isRegularFile) {
                    if (!isSupportedFile(selected)) throw ImportFailure("import_unsupported_format")
                    candidates.add(selected)
                    if (candidates.size > MAX_FILES) throw ImportFailure("import_file_limit", true)
                } else if (selectedAttributes.isDirectory) {
                    Files.walkFileTree(selected, emptySet(), MAX_DEPTH + 1, object : SimpleFileVisitor<Path>() {
                        override fun preVisitDirectory(dir: Path, attrs: BasicFileAttributes): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            rejectSymlinkComponents(dir)
                            return FileVisitResult.CONTINUE
                        }

                        override fun visitFile(file: Path, attrs: BasicFileAttributes): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            when {
                                attrs.isSymbolicLink -> report.failure("import_symlink_rejected")
                                attrs.isDirectory -> report.failure("import_depth_limit")
                                attrs.isRegularFile && isSupportedFile(file) -> {
                                    candidates.add(file.toAbsolutePath().normalize())
                                    if (candidates.size > MAX_FILES) throw ImportFailure("import_file_limit", true)
                                }
                            }
                            return FileVisitResult.CONTINUE
                        }

                        override fun visitFileFailed(file: Path, exc: IOException): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            report.failure("import_source_unreadable")
                            return FileVisitResult.CONTINUE
                        }
                    })
                } else {
                    throw ImportFailure("import_source_not_regular")
                }
            } catch (failure: InvalidPathException) {
                report.failure("import_invalid_selection")
            } catch (failure: ImportFailure) {
                report.failure(failure.code)
                stop = failure.stopBatch
            } catch (failure: IOException) {
                report.failure("import_source_unreadable")
            } catch (failure: SecurityException) {
                report.failure("import_access_denied")
            }
        }
        candidates.take(MAX_FILES)
    }

    private suspend fun importOne(source: Path, media: Path, budget: ByteBudget, report: Report): Unit {
        var temporary: Path? = null
        try {
            val copy = runInterruptible {
                validateMediaDirectory(media)
                rejectSymlinkComponents(source)
                val before = Files.readAttributes(source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (!before.isRegularFile || !Files.isReadable(source)) throw ImportFailure("import_source_unreadable")
                if (before.size() <= 0) throw ImportFailure("import_empty_file")
                if (before.size() > MAX_FILE_BYTES) throw ImportFailure("import_file_size_limit")
                budget.reserve(before.size())
                requireFreeSpace(media, before.size())
                val attributes = if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    arrayOf(PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
                } else {
                    emptyArray()
                }
                val part = Files.createTempFile(media, ".import-", ".part", *attributes)
                temporary = part
                val digest = MessageDigest.getInstance("SHA-256")
                var copied = 0L
                FileChannel.open(source, READ, NOFOLLOW_LINKS).use { input ->
                    FileChannel.open(part, WRITE, NOFOLLOW_LINKS).use { output ->
                        val buffer = ByteBuffer.allocate(BUFFER_BYTES)
                        while (true) {
                            checkInterrupted()
                            buffer.clear()
                            val read = input.read(buffer)
                            if (read < 0) break
                            if (read == 0) continue
                            copied += read
                            if (copied > before.size() || copied > MAX_FILE_BYTES) throw ImportFailure("import_source_changed")
                            requireFreeSpace(media, 0)
                            buffer.flip()
                            digest.update(buffer.asReadOnlyBuffer())
                            while (buffer.hasRemaining()) {
                                checkInterrupted()
                                output.write(buffer)
                            }
                        }
                        output.force(true)
                    }
                }
                rejectSymlinkComponents(source)
                val after = Files.readAttributes(source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (!after.isRegularFile || copied != before.size() || after.size() != before.size() ||
                    after.lastModifiedTime() != before.lastModifiedTime() || after.fileKey() != before.fileKey()
                ) {
                    throw ImportFailure("import_source_changed")
                }
                val hash = hex(digest.digest())
                verifyFile(part, copied, hash)
                VerifiedCopy(part, copied, hash)
            }
            publicationMutex.withLock {
                currentCoroutineContext().ensureActive()
                val destination = media.resolve("${copy.hash}.media")
                runInterruptible {
                    validateMediaDirectory(media)
                    if (Files.exists(destination, NOFOLLOW_LINKS)) {
                        // Never overwrite a corrupt or unsafe managed file during deduplication.
                        verifyFile(destination, copy.size, copy.hash)
                    } else {
                        try {
                            Files.move(copy.path, destination, ATOMIC_MOVE)
                        } catch (failure: AtomicMoveNotSupportedException) {
                            throw ImportFailure("import_atomic_publish_unavailable")
                        }
                        verifyFile(destination, copy.size, copy.hash)
                    }
                    // Persist the rename before committing a row on POSIX/macOS. Windows NIO
                    // does not support opening directories as channels; file force still applies.
                    if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                        try {
                            FileChannel.open(media, READ, NOFOLLOW_LINKS).use { it.force(true) }
                        } catch (failure: IOException) {
                            throw ImportFailure("import_publish_sync_failed")
                        }
                    }
                }
                val candidateId = UUID.randomUUID().toString()
                val imported = try {
                    store.addImportedTrack(
                        DesktopImportedTrack(
                            id = candidateId,
                            title = fallbackTitle(source),
                            artist = "",
                            durationMs = null,
                            sha256 = copy.hash,
                            byteSize = copy.size,
                            managedFileName = destination.fileName.toString(),
                        ),
                    )
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    // A commit might have completed before an adapter reported failure. Retain
                    // verified published bytes; a subsequent exact-byte import safely reuses them.
                    throw ImportFailure("import_store_failed")
                }
                if (imported.id == candidateId) report.imported++ else report.duplicates++
            }
        } finally {
            temporary?.let { part ->
                withContext(NonCancellable + Dispatchers.IO) {
                    try {
                        validateMediaDirectory(media)
                        Files.deleteIfExists(part)
                    } catch (failure: IOException) {
                        // Never mask the original failure or delete published managed media.
                    } catch (failure: SecurityException) {
                        // A later explicit import does not trust or enumerate these temp files.
                    } catch (failure: ImportFailure) {
                        // Refuse cleanup if the owned directory has become unsafe.
                    }
                }
            }
        }
    }

    private fun verifyFile(path: Path, expectedSize: Long, expectedHash: String): Unit {
        checkInterrupted()
        rejectSymlinkComponents(path)
        val attrs = Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        if (!attrs.isRegularFile || !Files.isReadable(path) || attrs.size() != expectedSize) {
            throw ImportFailure("import_managed_integrity_failed")
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var readBytes = 0L
        FileChannel.open(path, READ, NOFOLLOW_LINKS).use { channel ->
            val buffer = ByteBuffer.allocate(BUFFER_BYTES)
            while (true) {
                checkInterrupted()
                buffer.clear()
                val read = channel.read(buffer)
                if (read < 0) break
                if (read == 0) continue
                readBytes += read
                if (readBytes > expectedSize) throw ImportFailure("import_managed_integrity_failed")
                buffer.flip()
                digest.update(buffer)
            }
        }
        if (readBytes != expectedSize || hex(digest.digest()) != expectedHash) {
            throw ImportFailure("import_managed_integrity_failed")
        }
    }

    private fun rejectSymlinkComponents(path: Path): Unit {
        val absolute = path.toAbsolutePath().normalize()
        var current = absolute.root
        for (component in absolute) {
            checkInterrupted()
            current = current.resolve(component)
            if (Files.isSymbolicLink(current)) throw ImportFailure("import_symlink_rejected")
            if (Files.exists(current, NOFOLLOW_LINKS)) {
                val attrs = Files.readAttributes(current, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (attrs.isOther) throw ImportFailure("import_source_not_regular")
            }
        }
    }

    private fun requireFreeSpace(media: Path, requestedBytes: Long): Unit {
        if (Files.getFileStore(media).usableSpace < requestedBytes + SPACE_RESERVE_BYTES) {
            throw ImportFailure("import_insufficient_space", true)
        }
    }

    private fun fallbackTitle(path: Path): String = path.fileName.toString()
        .substringBeforeLast('.')
        .filterNot { it.isISOControl() }
        .take(MAX_TITLE_CHARS)
        .ifBlank { "Imported audio" }

    private fun isSupportedFile(path: Path): Boolean =
        path.fileName.toString().substringAfterLast('.', "").lowercase(Locale.ROOT) in SUPPORTED_EXTENSIONS

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun checkInterrupted(): Unit {
        if (Thread.currentThread().isInterrupted) throw CancellationException("import_cancelled")
    }

    private data class VerifiedCopy(val path: Path, val size: Long, val hash: String)

    private class ImportFailure(val code: String, val stopBatch: Boolean = false) : RuntimeException(code)

    private class ByteBudget {
        private var bytes: Long = 0
        fun reserve(size: Long): Unit {
            if (size > MAX_BATCH_BYTES - bytes) throw ImportFailure("import_batch_size_limit", true)
            bytes += size
        }
    }

    private class Report {
        var imported: Int = 0
        var duplicates: Int = 0
        private var failed: Int = 0
        private var errorCode: String? = null
        fun failure(code: String): Unit {
            failed++
            if (errorCode == null) errorCode = code
        }
        fun result(): DesktopImportResult = DesktopImportResult(imported, duplicates, failed, errorCode)
    }

    private companion object {
        val publicationMutex: Mutex = Mutex()
        const val MAX_SELECTIONS: Int = 256
        const val MAX_SELECTION_CHARS: Int = 4_096
        const val MAX_FILES: Int = 2_048
        const val MAX_ENTRIES: Int = 10_000
        const val MAX_DEPTH: Int = 16
        const val MAX_FILE_BYTES: Long = 2L * 1024 * 1024 * 1024
        const val MAX_BATCH_BYTES: Long = 8L * 1024 * 1024 * 1024
        const val SPACE_RESERVE_BYTES: Long = 128L * 1024 * 1024
        const val BATCH_TIMEOUT_MS: Long = 15L * 60 * 1000
        const val BUFFER_BYTES: Int = 256 * 1024
        const val MAX_TITLE_CHARS: Int = 512
        val SUPPORTED_EXTENSIONS: Set<String> = setOf("mp3", "flac", "m4a", "m4b", "aac", "wav", "aif", "aiff", "ogg", "opus", "alac")
    }
}
