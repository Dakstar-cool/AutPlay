package app.autplay.desktop.account

import app.autplay.application.securestate.SecretBytes
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Explicit, one-use picker result. No bookmark, retained source grant or automatic destination. */
public class AccountRecoveryDestination private constructor(internal val path: Path) {
    override fun toString(): String = "AccountRecoveryDestination(<redacted>)"
    public companion object {
        public fun fromExplicitSaveSelection(path: Path): AccountRecoveryDestination {
            controlRequire(path.isAbsolute && path.fileName != null)
            return AccountRecoveryDestination(path.normalize())
        }
    }
}
public class AccountRecoverySource private constructor(internal val path: Path) {
    override fun toString(): String = "AccountRecoverySource(<redacted>)"
    public companion object {
        public fun fromExplicitOpenSelection(path: Path): AccountRecoverySource {
            controlRequire(path.isAbsolute && path.fileName != null)
            return AccountRecoverySource(path.normalize())
        }
    }
}

/** Picker I/O never holds K/J locks. CREATE_NEW, close and exact bounded readback precede acknowledgment. */
public class DesktopRecoveryFiles(private val runtime: DesktopAccountRuntime) : AutoCloseable {
    private val owner = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(2),
        { task -> Thread(task, "autplay-recovery-file").apply { isDaemon = true } }, ThreadPoolExecutor.AbortPolicy())
    private val closed = AtomicBoolean(false)
    private val gate = Any()
    private val operations = java.util.concurrent.ConcurrentHashMap.newKeySet<CompletableFuture<Unit>>()

    public fun exportPortable(destination: AccountRecoveryDestination): CompletionStage<Unit> {
        val result = admit()
        runtime.recoveryForExport().whenComplete { secret, failure ->
            if (failure != null) result.completeExceptionally(AccountControlException(safeFailure(failure)))
            else try {
                owner.execute {
                    try {
                        controlRequire(!closed.get(), AccountControlFailure.CLOSED)
                        val bytes = secret.copy()
                        try {
                            writeNew(destination.path, bytes)
                            val readback = readBounded(destination.path, 65_536)
                            try {
                                controlRequire(bytes.contentEquals(readback), AccountControlFailure.CONFLICT)
                                // K compares against the CURRENT exact archive; stale picker results cannot acknowledge it.
                                runtime.acknowledgeRecoveryExport(readback).whenComplete { _, error ->
                                    if (error == null) result.complete(Unit)
                                    else result.completeExceptionally(AccountControlException(safeFailure(error)))
                                }
                            } finally { readback.fill(0) }
                        } finally { bytes.fill(0) }
                    } catch (_: Exception) { result.completeExceptionally(AccountControlException(AccountControlFailure.CONFLICT)) }
                    finally { secret.close() }
                }
            } catch (_: Exception) { secret.close(); result.completeExceptionally(AccountControlException(AccountControlFailure.BUSY)) }
        }
        return result.minimalCompletionStage()
    }

    public fun exportServerRecovery(controller: DesktopServerRecoveryController, source: Boolean,
        destination: AccountRecoveryDestination, deletionCancellation: Boolean = false): CompletionStage<Unit> {
        val result = admit()
        controller.documentForExplicitExport(source, deletionCancellation).whenComplete { secret, failure ->
            if (failure != null) result.completeExceptionally(AccountControlException(safeFailure(failure)))
            else try { owner.execute {
                try {
                    controlRequire(!closed.get(), AccountControlFailure.CLOSED)
                    val bytes = secret.copy()
                    try {
                        writeNew(destination.path, bytes)
                        val readback = readBounded(destination.path, 4096)
                        try {
                            controlRequire(bytes.contentEquals(readback), AccountControlFailure.CONFLICT)
                            controller.acknowledgeExport(source, readback, deletionCancellation).whenComplete { _, error ->
                                if (error == null) result.complete(Unit)
                                else result.completeExceptionally(AccountControlException(safeFailure(error)))
                            }
                        } finally { readback.fill(0) }
                    } finally { bytes.fill(0) }
                } catch (_: Exception) { result.completeExceptionally(AccountControlException(AccountControlFailure.CONFLICT)) }
                finally { secret.close() }
            } } catch (_: Exception) { secret.close(); result.completeExceptionally(AccountControlException(AccountControlFailure.BUSY)) }
        }
        return result.minimalCompletionStage()
    }

    public fun importPortable(source: AccountRecoverySource): CompletionStage<Unit> {
        val result = admit()
        try {
            owner.execute {
                try {
                    controlRequire(!closed.get(), AccountControlFailure.CLOSED)
                    val bytes = readBounded(source.path, 65_536)
                    try { runtime.importPortable(bytes).whenComplete { _, failure ->
                        if (failure == null) result.complete(Unit)
                        else result.completeExceptionally(AccountControlException(safeFailure(failure)))
                    } } finally { bytes.fill(0) }
                } catch (_: Exception) { result.completeExceptionally(AccountControlException(AccountControlFailure.INVALID_RESPONSE)) }
            }
        } catch (_: Exception) { result.completeExceptionally(AccountControlException(AccountControlFailure.BUSY)) }
        return result.minimalCompletionStage()
    }

    internal fun writeNew(path: Path, bytes: ByteArray) {
        controlRequire(bytes.size in 2..65_536 && !Files.exists(path, LinkOption.NOFOLLOW_LINKS))
        // A Mac POSIX create applies private permissions atomically. No fallback with broader permissions.
        FileChannel.open(path, setOf(StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS),
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------"))).use { output ->
            val buffer = ByteBuffer.wrap(bytes)
            while (buffer.hasRemaining()) output.write(buffer)
            output.force(true)
        }
        // An uncertain partial file is retained for the user. It is never overwritten/deleted automatically.
    }
    internal fun readBounded(path: Path, maximum: Int): ByteArray {
        controlRequire(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(path))
        FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS).use { input ->
            controlRequire(input.size() in 2..maximum.toLong())
            val buffer = ByteBuffer.allocate(maximum + 1)
            try {
                while (buffer.hasRemaining() && input.read(buffer) >= 0) { }
                controlRequire(buffer.position() in 2..maximum && input.size() == buffer.position().toLong())
                return buffer.array().copyOf(buffer.position())
            } finally { buffer.array().fill(0) }
        }
    }
    private fun admit(): CompletableFuture<Unit> = synchronized(gate) {
        controlRequire(!closed.get(), AccountControlFailure.CLOSED)
        CompletableFuture<Unit>().also { result -> operations.add(result); result.whenComplete { _, _ -> operations.remove(result) } }
    }
    public fun drain(): CompletionStage<Unit> = synchronized(gate) {
        controlRequire(closed.get(), AccountControlFailure.CONTEXT_CHANGED)
        CompletableFuture.allOf(*operations.toTypedArray()).handle { _, _ -> Unit }
    }
    override fun close(): Unit = synchronized(gate) { if (closed.compareAndSet(false, true)) owner.shutdown() }
}
