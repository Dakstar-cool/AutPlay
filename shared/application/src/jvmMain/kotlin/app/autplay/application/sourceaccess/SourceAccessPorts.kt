package app.autplay.application.sourceaccess

import java.nio.channels.ReadableByteChannel
import java.nio.file.attribute.BasicFileAttributes

/** Adapter-only row. Never serialize this object into settings, exports, events or logs. */
public class SourceLocator(
    public val id: String,
    public val version: Long,
    public val revision: Long,
    public val kind: String,
    public val enabled: Boolean,
    public val label: String,
    identity: ByteArray,
    bookmark: ByteArray,
) {
    private val identityBytes: ByteArray = identity.copyOf()
    private val bookmarkBytes: ByteArray = bookmark.copyOf()
    public fun identity(): ByteArray = identityBytes.copyOf()
    public fun bookmark(): ByteArray = bookmarkBytes.copyOf()
    override fun toString(): String = "SourceLocator(<redacted>)"
}

public class SourceAccessFailure(public val code: String) : RuntimeException(code)

/** One lifetime, owned by the host that holds the canonical native installation lock. */
public interface SourceAccessIndex : AutoCloseable {
    public fun initialize(): Unit
    public fun list(): List<SourceLocator>
    public fun load(id: String): SourceLocator?
    public fun remember(kind: SourceKind, label: String, identity: ByteArray, bookmark: ByteArray): SourceLocator
    public fun replace(expected: SourceLocator, kind: SourceKind, label: String, identity: ByteArray, bookmark: ByteArray): SourceLocator
    public fun setEnabled(expected: SourceLocator, enabled: Boolean): SourceLocator
    public fun forget(expected: SourceLocator): Unit
}

/** Native-owned selected token; callers cannot manufacture/import a bookmark. */
public class SourceSelection(public val token: Long, public val kind: SourceKind, public val label: String) {
    override fun toString(): String = "SourceSelection(<redacted>)"
}

public class SourceSelectionRecord(public val kind: SourceKind, public val label: String,
    public val identity: ByteArray, public val bookmark: ByteArray) {
    override fun toString(): String = "SourceSelectionRecord(<redacted>)"
}

/** Frozen original file capability; neither paths nor locators enter the import review. */
public interface SourceImportFile {
    public val name: String
    public fun attributes(): BasicFileAttributes
    public fun open(): ReadableByteChannel
}

/** Actual native descriptor close must complete before this owner is released. */
public interface SourceImportBatch : AutoCloseable {
    public val files: List<SourceImportFile>
    public val preparationMs: Long
}

/** No audio helper, network, credential or arbitrary bookmark-import operation. */
public interface SourceAccessNative : AutoCloseable {
    public fun initialize(): Unit
    public fun verifyPrivateIndex(): Unit
    public fun canonicalIndexPath(): String
    public fun choose(): SourceSelection?
    /** Only the event's current, explicit drop list may call this host boundary. */
    public fun admitExplicitDrop(paths: List<String>): List<SourceSelection>
    public fun selectionRecord(selection: SourceSelection): SourceSelectionRecord
    public fun discardSelection(selection: SourceSelection): Unit
    public fun scan(locator: SourceLocator, current: () -> Boolean): SourceImportBatch
    /** Return a replacement only after exact selected volume/resource/scope equality. */
    public fun refreshedBookmark(locator: SourceLocator): ByteArray?
    public fun fence(): Unit
    /** Retry retained actual descriptor closes, including a failed scan/preview cleanup. */
    public fun drain(): Unit
    public fun resume(): Unit
}
