package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.AccountFailure
import app.autplay.application.desktop.account.AccountDeliveryLease
import app.autplay.application.desktop.account.AccountDeliveryOperation
import app.autplay.application.desktop.account.AccountDeliveryOutcome
import app.autplay.application.desktop.account.AccountDeliverySettlement
import app.autplay.application.desktop.account.AccountPageOperation
import app.autplay.application.desktop.account.AccountPageOutcome
import app.autplay.application.desktop.account.AccountStoreException
import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.desktop.account.DesktopAccountLimits
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.PosixFileAttributes
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/** Read-only positive absence evidence; call only while the real K canonical lock is held. */
fun requireDesktopAccountVirginNamespace(): Unit =
    AccountPrivateFiles(Path.of(System.getProperty("user.home"))).requireVirginNamespace()

/** One real thread, one connection owner. No IO dispatcher, future cancellation or shutdownNow. */
internal class AccountSerialOwner {
    private val gate: Any = Any()
    private val executor: ThreadPoolExecutor = ThreadPoolExecutor(
        1, 1, 0, TimeUnit.MILLISECONDS, LinkedBlockingQueue<Runnable>(DesktopAccountLimits.CALLERS + 1),
        { runnable -> Thread(runnable, "autplay-account-sql").apply { isDaemon = false } },
    )
    private var callers: Int = 0
    private var retired: Boolean = false
    private var closed: Boolean = false
    private var closeAttempt: CompletableFuture<Unit>? = null

    fun retire(): Unit = synchronized(gate) { retired = true }

    suspend fun <T> submit(action: () -> T): T = enqueue(settlement = false, action = action)

    suspend fun <T> submitSettlement(action: () -> T): T = enqueue(settlement = true, action = action)

    /** No suspension between admission and returning the stable, caller-retained result ticket. */
    fun <T> startDelivery(action: (AccountUuid, (AccountDeliveryLease) -> Unit) -> T): AccountDeliveryOperation<T> {
        val operationId = AccountUuid.random()
        val future = enqueueFuture<AccountDeliveryOutcome<T>>(settlement = false) {
            // This slot belongs to this runnable/ticket, never to the currently selected account.
            // Publish only AFTER deliveryTransaction proves settlement and the exact live owner is set.
            var committed: AccountDeliverySettlement? = null
            try {
                AccountDeliveryOutcome.Completed(action(operationId) { lease ->
                    committed = AccountDeliverySettlement(operationId, lease)
                })
            } catch (failure: Throwable) {
                val code = sanitized(failure).failure
                committed?.let { AccountDeliveryOutcome.CommittedForSettlement(it, code) }
                    ?: AccountDeliveryOutcome.Failed(code)
            }
        }
        return AccountDeliveryOperation(operationId) { await(future) }
    }

    /** The retained result is published only after the real SQL outcome has been proven. */
    fun <T> startPage(action: (AccountUuid, (T) -> Unit) -> T): AccountPageOperation<T> {
        val operationId = AccountUuid.random()
        val future = enqueueFuture<AccountPageOutcome<T>>(settlement = false) {
            var retained: (() -> T)? = null
            try {
                AccountPageOutcome.Completed(action(operationId) { value -> retained = { value } })
            } catch (failure: Throwable) {
                val code = sanitized(failure).failure
                retained?.let { AccountPageOutcome.Retained(it(), code) } ?: AccountPageOutcome.Failed(code)
            }
        }
        return AccountPageOperation(operationId) { await(future) }
    }

    private suspend fun <T> enqueue(settlement: Boolean, action: () -> T): T =
        await(enqueueFuture(settlement, action))

    private fun <T> enqueueFuture(settlement: Boolean, action: () -> T): CompletableFuture<T> {
        val future = CompletableFuture<T>()
        synchronized(gate) {
            if (closed || (retired && !settlement)) fail(AccountFailure.ACCOUNT_CLOSED)
            if (callers >= DesktopAccountLimits.CALLERS) fail(AccountFailure.ACCOUNT_BUSY)
            callers++
            // Count belongs to the actual runnable, never to a caller's coroutine/future lifetime.
            executor.execute {
                try { future.complete(action()) }
                catch (failure: Throwable) { future.completeExceptionally(sanitized(failure)) }
                finally { synchronized(gate) { callers-- } }
            }
        }
        return future
    }

    suspend fun close(action: () -> Unit): Unit {
        val future = synchronized(gate) {
            retired = true
            if (closed) CompletableFuture.completedFuture(Unit)
            else closeAttempt ?: CompletableFuture<Unit>().also { attempt ->
                closeAttempt = attempt
                // A reserved queue slot remains even when all eight caller descriptors are admitted.
                executor.execute {
                    try {
                        action() // Connection.close MUST actually return before locks are released.
                        synchronized(gate) { closed = true }
                        executor.shutdown()
                        attempt.complete(Unit)
                    } catch (failure: Throwable) {
                        // Keep the owner/thread/locks for an explicit subsequent close attempt.
                        synchronized(gate) { closeAttempt = null }
                        attempt.completeExceptionally(sanitized(failure))
                    }
                }
            }
        }
        await(future)
    }

    private suspend fun <T> await(future: CompletableFuture<T>): T = suspendCoroutine { continuation ->
        // No cancellation handler: coroutine cancellation does not cancel/interrupt native SQLite.
        future.whenComplete { value, failure ->
            if (failure == null) continuation.resume(value)
            else continuation.resumeWithException(sanitized(failure))
        }
    }
}

/**
 * Canonical production namespace only; no AUTPLAY_DATA_ROOT/cwd/private-origin derivation.
 * Advisory/type/permission checks are not same-user tamper/rollback protection or secure erasure.
 * An isolated fixture may construct this internal owner directly; public factory has no path input.
 */
internal class AccountPrivateFiles(private val home: Path) {
    val root: Path = home.toAbsolutePath().normalize().resolve("Library/Application Support/AutPlay")
    private val directory: Path = root.resolve("accounts")
    val database: Path = directory.resolve("desktop-accounts-v1.db")
    private val lockPath: Path = root.resolve("desktop-accounts.lock")
    private val sidecars: List<Path> = listOf(
        directory.resolve("desktop-accounts-v1.db-wal"), directory.resolve("desktop-accounts-v1.db-shm"),
    )
    private var channel: FileChannel? = null
    private var lock: FileLock? = null
    private var lockIdentity: Any? = null
    private var databaseIdentity: Any? = null
    private var processReserved: Boolean = false

    fun requireVirginNamespace(): Unit {
        ancestors(home.toAbsolutePath().normalize())
        ancestors(directory)
        if (Files.notExists(directory, NOFOLLOW_LINKS)) return
        if (!Files.isDirectory(directory, NOFOLLOW_LINKS)) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        // Access denial or an unrecognized entry is not absence. Retain every such namespace.
        Files.newDirectoryStream(directory).use { entries ->
            if (entries.iterator().hasNext()) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }

    /** Called only AFTER protected lifetime coordination was acquired nonblockingly. */
    fun acquire(): Boolean {
        ancestors(home.toAbsolutePath().normalize())
        if (!home.fileSystem.supportedFileAttributeViews().contains("posix")) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        val owner = Files.getOwner(home, NOFOLLOW_LINKS)
        var current = home.toAbsolutePath().normalize()
        for (name in listOf("Library", "Application Support", "AutPlay", "accounts")) {
            current = current.resolve(name)
            if (!Files.exists(current, NOFOLLOW_LINKS)) {
                Files.createDirectory(current, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
            }
            ancestors(current)
            val attributes = Files.readAttributes(current, PosixFileAttributes::class.java, NOFOLLOW_LINKS)
            if (!attributes.isDirectory || attributes.owner() != owner) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            if (name == "AutPlay" || name == "accounts") {
                if (attributes.permissions() != PosixFilePermissions.fromString("rwx------")) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            } else if (attributes.permissions().any { it.name == "GROUP_WRITE" || it.name == "OTHERS_WRITE" }) {
                fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            }
        }
        // Avoid even opening a second channel for this JVM's live lock. Some POSIX systems release
        // process locks when another channel for the same file closes; K normally rejects earlier.
        if (!processLocks.add(lockPath)) fail(AccountFailure.ACCOUNT_BUSY)
        processReserved = true
        if (!Files.exists(lockPath, NOFOLLOW_LINKS)) privateFile(lockPath)
        val before = ownedFile(lockPath)
        lockIdentity = before.fileKey() ?: fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        val opened = FileChannel.open(lockPath, WRITE, NOFOLLOW_LINKS)
        channel = opened
        lock = try { opened.tryLock() ?: fail(AccountFailure.ACCOUNT_BUSY) }
        catch (_: OverlappingFileLockException) { fail(AccountFailure.ACCOUNT_BUSY) }
        if (ownedFile(lockPath).fileKey() != lockIdentity) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        for (path in sidecars) if (Files.exists(path, NOFOLLOW_LINKS)) ownedFile(path)
        val existed = Files.exists(database, NOFOLLOW_LINKS)
        if (existed) databaseIdentity = ownedFile(database).fileKey() ?: fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        return existed
    }

    /** Never initialize an empty preexisting file or a missing file beside existing K publication. */
    fun createNewDatabase(): Unit {
        // RESERVED restart may recreate only an absent database in an empty exact namespace.
        // Orphan WAL/SHM and every unknown entry are retained, never attached to a new database.
        requireVirginNamespace()
        privateFile(database)
        databaseIdentity = ownedFile(database).fileKey() ?: fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
    }

    fun verify(): Unit {
        ancestors(directory)
        val owner = Files.getOwner(home, NOFOLLOW_LINKS)
        for (path in listOf(root, directory)) {
            val attributes = Files.readAttributes(path, PosixFileAttributes::class.java, NOFOLLOW_LINKS)
            if (!attributes.isDirectory || attributes.owner() != owner ||
                attributes.permissions() != PosixFilePermissions.fromString("rwx------")) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
        if (lock?.isValid != true || ownedFile(lockPath).fileKey() != lockIdentity ||
            ownedFile(database).fileKey() != databaseIdentity) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        for (path in sidecars) if (Files.exists(path, NOFOLLOW_LINKS)) ownedFile(path)
    }

    fun requireSpace(mainAllocatedBytes: Long, extraBytes: Long, progress: Boolean): Unit {
        verify()
        val maximum = if (progress) DesktopAccountLimits.PROGRESS_BYTES else DesktopAccountLimits.PAGE_GROWTH_BYTES
        require(extraBytes in 0..maximum)
        val mainLimit = DesktopAccountLimits.MAIN_BYTES - if (progress) 0 else DesktopAccountLimits.PROGRESS_BYTES
        val wal = sidecars[0].let { if (Files.exists(it, NOFOLLOW_LINKS)) Files.size(it) else 0 }
        val combined = (listOf(database) + sidecars).sumOf { if (Files.exists(it, NOFOLLOW_LINKS)) Files.size(it) else 0L }
        // Pessimistic allocation, including journal amplification; no assumption that disk errors cannot occur.
        if (mainAllocatedBytes > mainLimit - extraBytes ||
            combined > DesktopAccountLimits.COMBINED_BYTES - extraBytes * 2 ||
            Files.getFileStore(directory).usableSpace < DesktopAccountLimits.FREE_RESERVE_BYTES + extraBytes * 2 ||
            (!progress && wal >= DesktopAccountLimits.WAL_SOFT_BYTES)) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
    }

    fun walSoftLimitReached(): Boolean = Files.exists(sidecars[0], NOFOLLOW_LINKS) &&
        Files.size(sidecars[0]) >= DesktopAccountLimits.WAL_SOFT_BYTES

    /** Reverse order. Never unlink/replace a live or released coordination lock file. */
    fun close(): Unit {
        lock?.release()
        lock = null
        channel?.close()
        channel = null
        if (processReserved) {
            processLocks.remove(lockPath)
            processReserved = false
        }
    }

    private fun privateFile(path: Path): Unit {
        Files.createFile(path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
    }

    private fun ownedFile(path: Path): PosixFileAttributes {
        val attributes = Files.readAttributes(path, PosixFileAttributes::class.java, NOFOLLOW_LINKS)
        if (!attributes.isRegularFile || attributes.isSymbolicLink ||
            attributes.owner() != Files.getOwner(home, NOFOLLOW_LINKS) ||
            attributes.permissions() != PosixFilePermissions.fromString("rw-------")) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        return attributes
    }

    private fun ancestors(path: Path): Unit {
        var current = path.root
        for (part in path) {
            current = current.resolve(part)
            if (Files.isSymbolicLink(current)) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
    }

    private companion object {
        val processLocks: MutableSet<Path> = ConcurrentHashMap.newKeySet<Path>()
    }
}

internal fun fail(code: AccountFailure): Nothing = throw AccountStoreException(code)
internal fun sanitized(failure: Throwable): AccountStoreException =
    if (failure is AccountStoreException) failure else AccountStoreException(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
