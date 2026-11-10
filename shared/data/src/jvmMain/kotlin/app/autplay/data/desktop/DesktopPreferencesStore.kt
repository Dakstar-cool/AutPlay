package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.NoSuchFileException
import java.nio.file.Path
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardCopyOption.REPLACE_EXISTING
import java.nio.file.StandardOpenOption.CREATE_NEW
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.PosixFilePermissions
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject

/** Independent presentation storage: opening failures are informational, never a library latch. */
suspend fun openDesktopPreferencesStore(
    dataRoot: Path,
    defaults: DesktopPreferences = DesktopPreferences(),
): DesktopPreferencesStore {
    val store = PersistentDesktopPreferencesStore(dataRoot.toAbsolutePath().normalize(), defaults)
    try {
        return withContext(Dispatchers.IO) {
            store.initialize()
            store
        }
    } catch (failure: CancellationException) {
        try { store.close() } catch (closeFailure: DesktopSettingsException) { failure.addSuppressed(closeFailure) }
        throw failure
    }
}

/** Reject same-process reopens before opening another channel to the lifetime lock file. */
private object DesktopSettingsOwners {
    val mutex: Mutex = Mutex()
    val roots: MutableSet<Path> = mutableSetOf()
}

private class PersistentDesktopPreferencesStore(
    private val root: Path,
    private val defaults: DesktopPreferences,
) : DesktopPreferencesStore {
    private val mutex: Mutex = Mutex()
    private val mutableState: MutableStateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState(defaults))
    override val state: StateFlow<DesktopPreferencesState> = mutableState.asStateFlow()
    private val file: Path = root.resolve("desktop-settings-v1.json")
    private val lockPath: Path = root.resolve("desktop-settings-v1.lock")
    private var channel: FileChannel? = null
    private var lock: FileLock? = null
    private var rootIdentity: BasicFileAttributes? = null
    private var lockIdentity: BasicFileAttributes? = null
    private var document: JsonObject = JsonObject(emptyMap())
    private var confirmedBytes: ByteArray? = null
    private var closed: Boolean = false
    private var ownerKey: Path? = null

    suspend fun initialize(): Unit {
        try {
            rejectAncestors()
            if (attributes(root) == null) {
                // Create only the requested application root, without scanning or deleting siblings.
                if (root.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    Files.createDirectories(root, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
                } else Files.createDirectories(root)
            }
            rejectAncestors()
            val rootAttributes = checkNotNull(attributes(root))
            requireSettings(rootAttributes.isDirectory && !rootAttributes.isSymbolicLink, "desktop_settings_file_unsafe")
            rootIdentity = rootAttributes
            val canonical = root.toRealPath()
            DesktopSettingsOwners.mutex.withLock {
                requireSettings(DesktopSettingsOwners.roots.add(canonical), "desktop_settings_in_use")
                ownerKey = canonical
            }
            if (attributes(lockPath) == null) privateFile(lockPath).close()
            val before = regular(lockPath)
            val opened = FileChannel.open(lockPath, WRITE, NOFOLLOW_LINKS)
            channel = opened
            requireSettings(sameIdentity(before, regular(lockPath)), "desktop_settings_file_changed")
            lockIdentity = before
            lock = try { opened.tryLock() } catch (_: OverlappingFileLockException) { null }
            requireSettings(lock != null, "desktop_settings_in_use")
            validateRoot()
            val bytes = readFile()
            validateRoot()
            if (bytes != null) {
                val parsed = DesktopSettingsJson.parse(bytes)
                requireSettings(DesktopSettingsJson.version(parsed) == 1, "desktop_settings_schema_unsupported")
                val value = DesktopSettingsJson.preferences(parsed, defaults, 1, portable = false)
                document = parsed
                confirmedBytes = bytes
                mutableState.value = DesktopPreferencesState(value)
            }
            currentCoroutineContext().ensureActive()
        } catch (failure: CancellationException) {
            throw failure
        } catch (failure: Exception) {
            failReadOnly(settingsCode(failure, "desktop_settings_unavailable"))
        }
    }

    override suspend fun setAppLanguage(value: String): Unit = edit {
        requireSettings(value in setOf("EN", "RU"), "desktop_settings_language_invalid")
        it.copy(appLanguage = value)
    }

    override suspend fun setAppearanceMode(value: String): Unit = edit {
        requireSettings(value in setOf("SYSTEM", "LIGHT", "DARK"), "desktop_settings_appearance_invalid")
        it.copy(appearanceMode = value)
    }

    override suspend fun setAccentPalette(value: String): Unit = edit {
        requireSettings(value in setOf("CORAL", "VIOLET", "GREEN", "BLUE"), "desktop_settings_palette_invalid")
        it.copy(accentPalette = value)
    }

    override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = edit {
        it.copy(smoothTrackTransitions = value)
    }

    override suspend fun setDownloadOnMeteredNetwork(value: Boolean): Unit = edit {
        it.copy(downloadOnMeteredNetwork = value)
    }

    override suspend fun applyPortable(value: DesktopPreferences): Unit = edit {
        DesktopSettingsJson.validatePortable(value)
        value
    }

    private suspend fun edit(change: (DesktopPreferences) -> DesktopPreferences): Unit = withContext(Dispatchers.IO) {
        mutex.withLock {
            requireSettings(!closed, "desktop_settings_closed")
            requireSettings(mutableState.value.writable, mutableState.value.errorCode ?: "desktop_settings_read_only")
            val next = change(mutableState.value.value)
            if (next == mutableState.value.value) return@withLock
            // Replacing allowlisted fields in the LOCAL document preserves opaque local extensions.
            // No imported JSON document is ever copied here.
            val merged = JsonObject(document + DesktopSettingsJson.document(next, 1))
            val bytes = merged.toString().encodeToByteArray()
            requireSettings(bytes.size <= DesktopSettingsTransferCodec.MAX_BYTES, "desktop_settings_too_large")
            // Merged extensions must still satisfy the parser's depth/node limits on restart.
            DesktopSettingsJson.parse(bytes)
            var temp: Path? = null
            var tempIdentity: BasicFileAttributes? = null
            var published = false
            var primaryFailure: Throwable? = null
            try {
                validateRoot()
                verifyCurrentFile()
                currentCoroutineContext().ensureActive()
                val candidate = root.resolve(".desktop-settings-${UUID.randomUUID()}.tmp")
                privateFile(candidate).use { output ->
                    temp = candidate
                    tempIdentity = regular(candidate)
                    val buffer = ByteBuffer.wrap(bytes)
                    while (buffer.hasRemaining()) {
                        currentCoroutineContext().ensureActive()
                        requireSettings(output.write(buffer) > 0, "desktop_settings_write_failed")
                    }
                    output.force(true)
                    requireSettings(output.size() == bytes.size.toLong(), "desktop_settings_file_changed")
                }
                validateRoot()
                requireSettings(sameIdentity(checkNotNull(tempIdentity), regular(candidate)), "desktop_settings_file_changed")
                verifyCurrentFile()
                currentCoroutineContext().ensureActive()
                // Publication and its observation are inseparable. Cancellation after the move cannot
                // mean rollback, delete the published file, or leave an optimistic preference value.
                withContext(NonCancellable) {
                    Files.move(candidate, file, ATOMIC_MOVE, REPLACE_EXISTING)
                    published = true
                    forceDirectory()
                    validateRoot()
                    val observed = readFile()
                    requireSettings(observed != null && observed.contentEquals(bytes), "desktop_settings_refresh_failed")
                    val refreshed = DesktopSettingsJson.parse(checkNotNull(observed))
                    requireSettings(DesktopSettingsJson.version(refreshed) == 1, "desktop_settings_refresh_failed")
                    val confirmed = DesktopSettingsJson.preferences(refreshed, defaults, 1, portable = false)
                    requireSettings(confirmed == next, "desktop_settings_refresh_failed")
                    document = refreshed
                    confirmedBytes = observed
                    mutableState.value = DesktopPreferencesState(confirmed)
                }
            } catch (failure: CancellationException) {
                primaryFailure = failure
                throw failure
            } catch (failure: Exception) {
                val code = if (published) "desktop_settings_reopen_required" else when (failure) {
                    is AtomicMoveNotSupportedException -> "desktop_settings_atomic_unavailable"
                    else -> settingsCode(failure, "desktop_settings_write_failed")
                }
                failReadOnly(code)
                val sanitized = DesktopSettingsException(code)
                primaryFailure = sanitized
                throw sanitized
            } catch (failure: Throwable) {
                primaryFailure = failure
                throw failure
            } finally {
                // Only this invocation's unpublished private temp is eligible for cleanup.
                // A crash orphan or foreign file is never swept.
                if (!published) {
                    val ownedTemp = temp
                    val ownedIdentity = tempIdentity
                    if (ownedTemp != null) {
                        try {
                            validateRoot()
                            val now = attributes(ownedTemp)
                            if (now != null) {
                                requireSettings(ownedIdentity != null && now.isRegularFile && !now.isSymbolicLink &&
                                    sameIdentity(ownedIdentity, now), "desktop_settings_temp_cleanup_failed")
                                Files.delete(ownedTemp)
                            }
                            validateRoot()
                            requireSettings(attributes(ownedTemp) == null, "desktop_settings_temp_cleanup_failed")
                        } catch (_: Exception) {
                            // Do not expose filesystem messages or delete a replaced/unidentified entry.
                            val cleanup = DesktopSettingsException("desktop_settings_temp_cleanup_failed")
                            if (mutableState.value.writable) failReadOnly(cleanup.code)
                            val primary = primaryFailure
                            if (primary != null) primary.addSuppressed(cleanup) else throw cleanup
                        }
                    }
                }
            }
        }
    }

    override suspend fun close(): Unit = withContext(Dispatchers.IO + NonCancellable) {
        mutex.withLock {
            if (closed) return@withLock
            closed = true
            failReadOnly("desktop_settings_closed")
            // Closing the channel releases the OS lock even when explicit release fails.
            try {
                try { lock?.release() } finally { channel?.close() }
            } catch (_: Exception) {
                throw DesktopSettingsException("desktop_settings_close_failed")
            }
            DesktopSettingsOwners.mutex.withLock {
                ownerKey?.let { DesktopSettingsOwners.roots.remove(it) }
                ownerKey = null
            }
        }
    }

    private fun failReadOnly(code: String): Unit {
        mutableState.value = mutableState.value.copy(writable = false, errorCode = code)
    }

    private fun validateRoot(): Unit {
        rejectAncestors()
        val now = checkNotNull(attributes(root))
        requireSettings(now.isDirectory && !now.isSymbolicLink && sameIdentity(checkNotNull(rootIdentity), now),
            "desktop_settings_file_unsafe")
        requireSettings(sameIdentity(checkNotNull(lockIdentity), regular(lockPath)), "desktop_settings_file_changed")
        requireSettings(lock?.isValid == true, "desktop_settings_in_use")
    }

    private fun rejectAncestors(): Unit {
        var path: Path? = root
        while (path != null) {
            val attr = attributes(path)
            requireSettings(attr == null || (attr.isDirectory && !attr.isSymbolicLink), "desktop_settings_file_unsafe")
            path = path.parent
        }
    }

    private fun verifyCurrentFile(): Unit {
        val observed = readFile()
        val previous = confirmedBytes
        requireSettings(if (previous == null) observed == null else observed != null && previous.contentEquals(observed),
            "desktop_settings_file_changed")
    }

    private fun readFile(): ByteArray? {
        val before = attributes(file) ?: return null
        requireSettings(before.isRegularFile && !before.isSymbolicLink, "desktop_settings_file_unsafe")
        rejectHardLinks(file)
        requireSettings(before.size() <= DesktopSettingsTransferCodec.MAX_BYTES, "desktop_settings_too_large")
        val bytes = FileChannel.open(file, READ, NOFOLLOW_LINKS).use { input ->
            requireSettings(input.size() == before.size(), "desktop_settings_file_changed")
            // One extra byte detects growth without unbounded readAllBytes allocation.
            val buffer = ByteBuffer.allocate(DesktopSettingsTransferCodec.MAX_BYTES + 1)
            while (buffer.hasRemaining()) {
                val count = input.read(buffer)
                if (count < 0) break
                requireSettings(count != 0, "desktop_settings_read_failed")
            }
            requireSettings(buffer.position() <= DesktopSettingsTransferCodec.MAX_BYTES, "desktop_settings_too_large")
            requireSettings(input.size() == before.size(), "desktop_settings_file_changed")
            buffer.array().copyOf(buffer.position())
        }
        val after = regular(file)
        requireSettings(sameIdentity(before, after) && before.size() == after.size() &&
            before.lastModifiedTime() == after.lastModifiedTime() && bytes.size.toLong() == before.size(),
            "desktop_settings_file_changed")
        return bytes
    }

    private fun forceDirectory(): Unit {
        // Windows NIO cannot open directory channels. File force + atomic replace still apply;
        // this explicitly does not qualify power-loss durability on that platform.
        if (!Files.getFileStore(root).supportsFileAttributeView("posix")) return
        FileChannel.open(root, READ, NOFOLLOW_LINKS).use { it.force(true) }
    }

    private fun privateFile(path: Path): FileChannel {
        requireSettings(attributes(path) == null, "desktop_settings_file_unsafe")
        val options: Set<java.nio.file.OpenOption> = setOf(CREATE_NEW, WRITE, NOFOLLOW_LINKS)
        return if (Files.getFileStore(root).supportsFileAttributeView("posix")) {
            FileChannel.open(path, options, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
        } else FileChannel.open(path, options)
    }

    private fun regular(path: Path): BasicFileAttributes {
        val attr = attributes(path) ?: throw DesktopSettingsException("desktop_settings_file_changed")
        requireSettings(attr.isRegularFile && !attr.isSymbolicLink, "desktop_settings_file_unsafe")
        rejectHardLinks(path)
        return attr
    }

    private fun rejectHardLinks(path: Path): Unit {
        if (Files.getFileStore(root).supportsFileAttributeView("unix")) {
            val links = Files.getAttribute(path, "unix:nlink", NOFOLLOW_LINKS) as Number
            requireSettings(links.toLong() == 1L, "desktop_settings_file_unsafe")
        }
    }

    private fun attributes(path: Path): BasicFileAttributes? = try {
        Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
    } catch (_: NoSuchFileException) { null }

    private fun sameIdentity(first: BasicFileAttributes, second: BasicFileAttributes): Boolean =
        first.fileKey() == second.fileKey() && first.creationTime() == second.creationTime()

    private fun requireSettings(valid: Boolean, code: String): Unit = DesktopSettingsJson.requireCode(valid, code)

    private fun settingsCode(failure: Exception, fallback: String): String =
        (failure as? DesktopSettingsException)?.code ?: fallback
}
