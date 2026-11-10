package app.autplay.desktop.settings

import app.autplay.data.desktop.DesktopSettingsTransferCodec
import java.io.IOException
import java.io.InterruptedIOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.FileAlreadyExistsException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.CREATE_NEW
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.BasicFileAttributes

/** Sanitized failure only: neither the selected capability nor an OS exception reaches UI/logs. */
public class DesktopSettingsFileException(public val code: String) : IOException(code)

/** Reads one explicitly selected regular file. Call from interruptible IO, never from the UI. */
public fun readSettingsImport(path: Path): ByteArray = settingsFileOperation("desktop_settings_import_failed") {
    checkSettingsInterrupted()
    val selected = path.toAbsolutePath()
    rejectSettingsSymlinks(selected)
    val before = settingsAttributes(selected)
    if (!before.isRegularFile || before.size() > DesktopSettingsTransferCodec.MAX_BYTES) {
        throw DesktopSettingsFileException("desktop_settings_import_failed")
    }
    // One extra byte catches growth rather than silently accepting a truncated prefix.
    val buffer = ByteBuffer.allocate(DesktopSettingsTransferCodec.MAX_BYTES + 1)
    FileChannel.open(selected, READ, NOFOLLOW_LINKS).use { channel ->
        if (channel.size() != before.size()) throw DesktopSettingsFileException("desktop_settings_import_failed")
        var stalls = 0
        while (buffer.hasRemaining()) {
            checkSettingsInterrupted()
            val count = channel.read(buffer)
            if (count < 0) break
            stalls = if (count == 0) stalls + 1 else 0
            if (stalls > 16) throw DesktopSettingsFileException("desktop_settings_import_failed")
        }
        checkSettingsInterrupted()
        if (buffer.position() > DesktopSettingsTransferCodec.MAX_BYTES || channel.size() != before.size()) {
            throw DesktopSettingsFileException("desktop_settings_import_failed")
        }
    }
    rejectSettingsSymlinks(selected)
    val after = settingsAttributes(selected)
    if (!after.isRegularFile || after.size() != before.size() || buffer.position().toLong() != before.size() ||
        after.fileKey() != before.fileKey() || after.creationTime() != before.creationTime() ||
        after.lastModifiedTime() != before.lastModifiedTime()
    ) throw DesktopSettingsFileException("desktop_settings_import_failed")
    checkSettingsInterrupted()
    buffer.array().copyOf(buffer.position())
}

/**
 * Creates a new explicitly selected export file, writes bounded bytes and forces its channel.
 * Existing files/links are never overwritten. An interrupted owned new file is retained;
 * deleting by path after failure could delete somebody else's replacement.
 */
public fun writeSettingsExportNew(path: Path, bytes: ByteArray): Unit =
    settingsFileOperation("desktop_settings_export_failed") {
        if (bytes.size > DesktopSettingsTransferCodec.MAX_BYTES) {
            throw DesktopSettingsFileException("desktop_settings_export_failed")
        }
        val frozen = bytes.copyOf()
        checkSettingsInterrupted()
        val selected = path.toAbsolutePath()
        if (Files.exists(selected, NOFOLLOW_LINKS)) {
            throw DesktopSettingsFileException("desktop_settings_export_target_exists")
        }
        rejectSettingsSymlinks(selected)
        FileChannel.open(selected, WRITE, CREATE_NEW, NOFOLLOW_LINKS).use { channel ->
            val created = settingsAttributes(selected)
            if (!created.isRegularFile) throw DesktopSettingsFileException("desktop_settings_export_failed")
            val buffer = ByteBuffer.wrap(frozen)
            var stalls = 0
            while (buffer.hasRemaining()) {
                checkSettingsInterrupted()
                val count = channel.write(buffer)
                stalls = if (count == 0) stalls + 1 else 0
                if (stalls > 16) throw DesktopSettingsFileException("desktop_settings_export_failed")
            }
            checkSettingsInterrupted()
            channel.force(true)
            rejectSettingsSymlinks(selected)
            val after = settingsAttributes(selected)
            if (!after.isRegularFile || after.fileKey() != created.fileKey() || after.creationTime() != created.creationTime() ||
                after.size() != frozen.size.toLong() || channel.size() != frozen.size.toLong()
            ) throw DesktopSettingsFileException("desktop_settings_export_failed")
            checkSettingsInterrupted()
        }
    }

private inline fun <T> settingsFileOperation(code: String, action: () -> T): T = try {
    action()
} catch (failure: DesktopSettingsFileException) {
    throw failure
} catch (failure: InterruptedIOException) {
    throw failure
} catch (failure: FileAlreadyExistsException) {
    throw DesktopSettingsFileException("desktop_settings_export_target_exists")
} catch (failure: Exception) {
    checkSettingsInterrupted()
    throw DesktopSettingsFileException(code)
}

private fun settingsAttributes(path: Path): BasicFileAttributes =
    Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS)

private fun rejectSettingsSymlinks(path: Path): Unit {
    var current = checkNotNull(path.root)
    path.forEachIndexed { index, component ->
        checkSettingsInterrupted()
        current = current.resolve(component)
        if (Files.exists(current, NOFOLLOW_LINKS)) {
            val attributes = settingsAttributes(current)
            if (attributes.isSymbolicLink || attributes.isOther ||
                (index < path.nameCount - 1 && !attributes.isDirectory)
            ) throw DesktopSettingsFileException("desktop_settings_path_unsafe")
        }
    }
}

private fun checkSettingsInterrupted(): Unit {
    if (Thread.currentThread().isInterrupted) throw InterruptedIOException("desktop_settings_cancelled")
}
