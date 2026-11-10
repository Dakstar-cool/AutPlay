package app.autplay.desktop.sourceaccess

import app.autplay.application.sourceaccess.*
import java.nio.ByteBuffer
import java.nio.channels.ReadableByteChannel
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.FileTime
import java.util.concurrent.TimeUnit
import java.util.concurrent.ConcurrentHashMap
import kotlinx.serialization.json.*

/** Public Developer Team or community certificate SHA-256; native code pins the build mode. */
public class MacSourceAccessIdentity(public val developerTeam: String) {
    init {
        if (!developerTeam.matches(Regex("[A-Z0-9]{10}|[0-9a-f]{64}"))) throw SourceAccessFailure("source_identity_unavailable")
    }
    override fun toString(): String = "MacSourceAccessIdentity(<redacted>)"
}

/** Independent from Keychain/audio. Native preflight verifies the build's signer and non-sandbox mode. */
internal fun macSourceAccessNative(identity: MacSourceAccessIdentity, managedDataRoot: Path): SourceAccessNative =
    MacSourceAccessApi(identity, managedDataRoot.toAbsolutePath().normalize())

private class MacSourceAccessApi(private val identity: MacSourceAccessIdentity, private val managedRoot: Path) : SourceAccessNative {
    @Volatile private var handle: Long = 0
    private var nativeAttempted: Boolean = false
    private val batches: MutableSet<Batch> = mutableSetOf()
    @Volatile private var fenced: Boolean = false

    @Synchronized override fun initialize(): Unit = safe {
        if(handle != 0L) return@safe
        checkSource(System.getProperty("os.name") == "Mac OS X" && System.getProperty("os.arch") in setOf("aarch64", "arm64"), "source_identity_unavailable")
        val executable = Path.of(ProcessHandle.current().info().command().orElseThrow()).toRealPath()
        val contents = executable.parent.parent; val bundle = contents.parent
        checkSource(executable.parent.fileName.toString() == "MacOS" && contents.fileName.toString() == "Contents" &&
            bundle.fileName.toString().endsWith(".app") && bundle == bundle.toRealPath(), "source_identity_unavailable")
        val library = contents.resolve("app/resources/native/libAutPlaySourceAccess.dylib")
        checkSource(Files.isRegularFile(library, NOFOLLOW_LINKS) && library == library.toRealPath(), "source_identity_unavailable")
        SourceLibrary.load(library)
        checkSource(SourceAccessJni.abiVersion() == 1, "source_identity_unavailable")
        nativeAttempted = true
        handle = SourceAccessJni.open(1, identity.developerTeam, managedRoot.toString())
        checkSource(handle > 0, "source_identity_unavailable")
    }
    @Synchronized override fun verifyPrivateIndex(): Unit = safe { SourceAccessJni.verify(owner()) }
    @Synchronized override fun canonicalIndexPath(): String = safe { SourceAccessJni.indexPath(owner()) }
    override fun choose(): SourceSelection? = safe {
        val token = SourceAccessJni.choose(owner())
        if(token == 0L) null else selection(token)
    }
    override fun admitExplicitDrop(paths: List<String>): List<SourceSelection> = safe {
        checkSource(paths.size in 1..SourceAccessLimits.SOURCES, "source_selection_invalid")
        val admitted = mutableListOf<SourceSelection>()
        try { for(path in paths) admitted += selection(SourceAccessJni.admitDrop(owner(), path)); admitted }
        catch(failure: Exception) { admitted.forEach { discardSelection(it) }; throw failure }
    }
    private fun selection(token: Long): SourceSelection {
        try {
            val info = document(SourceAccessJni.selectionInfo(owner(), token), 2048)
            return SourceSelection(token, SourceKind.valueOf(info.getValue("kind").jsonPrimitive.content), info.getValue("label").jsonPrimitive.content)
        } catch(failure: Exception) { SourceAccessJni.discardSelection(owner(), token); throw failure }
    }
    override fun selectionRecord(selection: SourceSelection): SourceSelectionRecord = safe {
        SourceSelectionRecord(selection.kind, selection.label, SourceAccessJni.selectionIdentity(owner(), selection.token),
            SourceAccessJni.selectionBookmark(owner(), selection.token)).also {
            checkSource(it.identity.size in 1..SourceAccessLimits.IDENTITY_BYTES && it.bookmark.size in 1..SourceAccessLimits.BOOKMARK_BYTES, "source_selection_invalid")
        }
    }
    override fun discardSelection(selection: SourceSelection): Unit = safe { SourceAccessJni.discardSelection(owner(), selection.token) }
    override fun refreshedBookmark(locator: SourceLocator): ByteArray? = safe {
        SourceAccessJni.refreshedBookmark(owner(), locator.kind, locator.identity(), locator.bookmark())
    }
    override fun scan(locator: SourceLocator, current: () -> Boolean): SourceImportBatch = safe {
        checkSource(!fenced && current(), "source_fenced")
        val token = SourceAccessJni.scan(owner(), locator.kind, locator.identity(), locator.bookmark())
        val batch = Batch(token, current)
        synchronized(this) { batches += batch }
        try { batch.load(); checkSource(!fenced && current(), "source_fenced"); batch }
        catch(failure: Exception) { batch.close(); throw failure }
    }
    override fun fence(): Unit { fenced = true; val live = handle; if(live > 0) safe { SourceAccessJni.fence(live) } }
    override fun drain(): Unit {
        val retained = synchronized(this) { batches.toList() }
        retained.forEach { it.close() }
    }
    override fun resume(): Unit = safe { SourceAccessJni.resume(owner()); fenced = false }
    @Synchronized override fun close(): Unit = safe {
        fenced = true
        if(handle == 0L) {
            if(nativeAttempted) SourceAccessJni.settleInitialization()
            nativeAttempted = false
            return@safe
        }
        SourceAccessJni.fence(handle)
        // Native refuses release while any descriptor/selection/batch remains. Retain on failure.
        checkSource(batches.isEmpty(), "source_drain_pending")
        SourceAccessJni.close(handle); handle = 0
    }
    private fun owner(): Long = handle.also { checkSource(it > 0, "source_closed") }

    private inner class Batch(private val token: Long, private val current: () -> Boolean) : SourceImportBatch {
        @Volatile private var closed: Boolean = false
        private val channels: MutableSet<Channel> = ConcurrentHashMap.newKeySet<Channel>()
        override var files: List<SourceImportFile> = emptyList(); private set
        override var preparationMs: Long = 0; private set
        fun load(): Unit {
            val info = document(SourceAccessJni.batchInfo(owner(), token), 2_097_152)
            preparationMs = info.getValue("preparation_ms").jsonPrimitive.long
            files = info.getValue("files").jsonArray.mapIndexed { index, value ->
                val item = value.jsonObject
                val fileName = item.getValue("name").jsonPrimitive.content
                checkSource(fileName.isNotBlank() && fileName.toByteArray().size <= 1024 && fileName.none { it == '/' || it == '\\' || it.isISOControl() }, "source_unavailable")
                object : SourceImportFile {
                    override val name: String = fileName
                    override fun attributes(): BasicFileAttributes = safe {
                        admit(); attributesOf(document(SourceAccessJni.attributes(owner(), token, index), 4096))
                    }
                    override fun open(): ReadableByteChannel = safe {
                        synchronized(this@Batch) {
                            admit(); val native = SourceAccessJni.openFile(owner(), token, index)
                            Channel(native).also { channels += it }
                        }
                    }
                }
            }
            checkSource(files.size <= SourceAccessLimits.FILES && preparationMs in 0..SourceAccessLimits.ACTIVE_MS, "source_limit")
        }
        private fun admit(): Unit { checkSource(!closed && !fenced && current(), "source_fenced") }
        @Synchronized override fun close(): Unit = safe {
            if(closed) return@safe
            // Do not forget a failed descriptor close or report release on coroutine cancellation.
            channels.toList().forEach { it.close() }
            SourceAccessJni.closeBatch(owner(), token)
            closed = true
            synchronized(this@MacSourceAccessApi) { batches -= this }
        }
        private inner class Channel(private val token: Long) : ReadableByteChannel {
            private var open: Boolean = true
            override fun isOpen(): Boolean = open
            @Synchronized override fun read(dst: ByteBuffer): Int = safe {
                admit(); checkSource(open, "source_closed")
                if(!dst.hasRemaining()) 0 else {
                    val bytes = SourceAccessJni.read(owner(), token, minOf(dst.remaining(), 65_536))
                    if(bytes == null) -1 else { checkSource(bytes.isNotEmpty(), "source_unavailable"); dst.put(bytes); bytes.size }
                }
            }
            @Synchronized override fun close(): Unit = safe {
                if(open) { SourceAccessJni.closeFile(owner(), token); open = false; channels -= this }
            }
        }
    }
}

private object SourceLibrary {
    private var loaded: Path? = null
    @Synchronized fun load(path: Path): Unit {
        checkSource(loaded == null || loaded == path, "source_identity_unavailable")
        if(loaded == null) { System.load(path.toString()); loaded = path }
    }
}
private fun document(bytes: ByteArray, max: Int): JsonObject {
    checkSource(bytes.size in 2..max, "source_unavailable")
    return Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
}
private fun attributesOf(value: JsonObject): BasicFileAttributes = object : BasicFileAttributes {
    private fun time(key: String): FileTime = FileTime.from(value.getValue(key).jsonPrimitive.long, TimeUnit.NANOSECONDS)
    override fun lastModifiedTime(): FileTime = time("modified_ns")
    override fun creationTime(): FileTime = time("created_ns")
    override fun lastAccessTime(): FileTime = lastModifiedTime()
    override fun isRegularFile(): Boolean = true
    override fun isDirectory(): Boolean = false
    override fun isSymbolicLink(): Boolean = false
    override fun isOther(): Boolean = false
    override fun size(): Long = value.getValue("size").jsonPrimitive.long
    override fun fileKey(): Any = value.getValue("key").jsonPrimitive.content
}
private fun checkSource(value: Boolean, code: String): Unit { if(!value) throw SourceAccessFailure(code) }
private inline fun <T> safe(block: () -> T): T = try { block() }
catch(failure: SourceAccessFailure) { throw failure }
catch(failure: Exception) {
    val code = failure.message?.takeIf { it in SOURCE_ERRORS } ?: "source_unavailable"
    throw SourceAccessFailure(code)
} catch (_: LinkageError) { throw SourceAccessFailure("source_identity_unavailable") }
private val SOURCE_ERRORS: Set<String> = setOf("source_identity_unavailable", "source_busy", "source_closed", "source_fenced",
    "source_drain_pending", "source_selection_invalid", "source_unavailable", "source_repair_required", "source_limit", "source_internal_rejected")

/** Separate JNI ABI1 and library; no fd/path is ever passed to a media child. */
internal object SourceAccessJni {
    external fun abiVersion(): Int
    external fun settleInitialization()
    external fun open(abi: Int, team: String, managedRoot: String): Long
    external fun verify(handle: Long)
    external fun indexPath(handle: Long): String
    external fun choose(handle: Long): Long
    external fun admitDrop(handle: Long, path: String): Long
    external fun selectionInfo(handle: Long, token: Long): ByteArray
    external fun selectionIdentity(handle: Long, token: Long): ByteArray
    external fun selectionBookmark(handle: Long, token: Long): ByteArray
    external fun discardSelection(handle: Long, token: Long)
    external fun refreshedBookmark(handle: Long, kind: String, identity: ByteArray, bookmark: ByteArray): ByteArray?
    external fun scan(handle: Long, kind: String, identity: ByteArray, bookmark: ByteArray): Long
    external fun batchInfo(handle: Long, batch: Long): ByteArray
    external fun attributes(handle: Long, batch: Long, index: Int): ByteArray
    external fun openFile(handle: Long, batch: Long, index: Int): Long
    external fun read(handle: Long, file: Long, maximum: Int): ByteArray?
    external fun closeFile(handle: Long, file: Long)
    external fun closeBatch(handle: Long, batch: Long)
    external fun fence(handle: Long)
    external fun resume(handle: Long)
    external fun close(handle: Long)
}
