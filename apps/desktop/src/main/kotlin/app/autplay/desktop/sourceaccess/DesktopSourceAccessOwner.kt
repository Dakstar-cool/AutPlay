package app.autplay.desktop.sourceaccess

import app.autplay.application.sourceaccess.*
import app.autplay.data.sourceaccess.createSourceAccessIndex
import java.nio.file.Path
import java.util.concurrent.ConcurrentHashMap

/** Retain this object before initialize, including failed/cancelled initialization and close. */
public fun createDesktopSourceAccessOwner(identity: MacSourceAccessIdentity, managedDataRoot: Path): DesktopSourceAccessOwner {
    val native = macSourceAccessNative(identity, managedDataRoot)
    return DesktopSourceAccessOwner(native, createSourceAccessIndex(native))
}

/** One source owner, independent of the current account, K, media helpers and Room5. */
public class DesktopSourceAccessOwner internal constructor(
    private val native: SourceAccessNative,
    private val index: SourceAccessIndex,
) : AutoCloseable {
    private val gate: Any = Any()
    @Volatile private var rows: Map<String, SourceLocator> = emptyMap()
    private val rowFences: MutableSet<String> = ConcurrentHashMap.newKeySet<String>()
    private val repair: MutableSet<String> = ConcurrentHashMap.newKeySet<String>()
    private val batches: MutableSet<TrackedBatch> = ConcurrentHashMap.newKeySet<TrackedBatch>()
    private val selections: MutableSet<SourceSelection> = ConcurrentHashMap.newKeySet<SourceSelection>()
    @Volatile private var initialized: Boolean = false
    @Volatile private var nativeStarted: Boolean = false
    @Volatile private var fenced: Boolean = false
    @Volatile private var closing: Boolean = false
    @Volatile private var closed: Boolean = false
    private var attempted: Boolean = false

    public fun initialize(): Unit = synchronized(gate) {
        if(initialized) return@synchronized
        requireSource(!attempted && !fenced && !closing, "source_index_reopen_required"); attempted = true
        nativeStarted = true; native.initialize()
        index.initialize(); reload(); initialized = true
    }

    /** Cached projection only. This does not reopen bookmarks or import on launch/recomposition. */
    public fun snapshot(revision: Long = 0, busy: Boolean = false, errorCode: String? = null): SourceAccessState =
        SourceAccessState(revision, initialized && !closed, busy, fenced || closing || closed,
            rows.values.sortedBy { it.id }.map { row ->
                RememberedSource(row.id, row.revision, row.label, SourceKind.entries.find { it.name == row.kind }, row.enabled,
                    when {
                        row.version != 1L -> SourceAvailability.UNSUPPORTED
                        row.id in rowFences -> SourceAvailability.DRAINING
                        !row.enabled -> SourceAvailability.DISABLED
                        row.id in repair -> SourceAvailability.REPAIR_REQUIRED
                        else -> SourceAvailability.READY
                    })
            }, errorCode)

    public fun rememberChosen(): Unit = synchronized(gate) {
        ready(); val selection = native.choose() ?: return@synchronized
        selections += selection
        try { remember(selection) } finally { discard(selection) }
    }

    /** Host calls only for the current validated explicit drop after a visible Remember opt-in. */
    public fun rememberExplicitDrop(paths: List<String>): Unit = synchronized(gate) {
        ready(); requireSource(paths.size in 1..SourceAccessLimits.SOURCES, "source_selection_invalid")
        val selected = native.admitExplicitDrop(paths); selections.addAll(selected)
        try { for(selection in selected) { ready(); remember(selection) } }
        finally { selected.forEach(::discard) }
    }

    private fun remember(selection: SourceSelection): Unit {
        ready(); val record = native.selectionRecord(selection)
        ready(); index.remember(record.kind, record.label, record.identity, record.bookmark); reload()
    }

    /** Synchronous per-source fence, before any disable/forget/repair drain is dispatched. */
    public fun fenceSource(expected: RememberedSource): Unit {
        val row = exact(expected); requireSource(row.version == 1L, "source_record_unsupported")
        rowFences += row.id
    }

    public fun setEnabled(expected: RememberedSource, enabled: Boolean): Unit = synchronized(gate) {
        ready(); val row = exact(expected)
        if(!enabled) rowFences += row.id
        drainSource(row.id); ready()
        index.setEnabled(row, enabled); reload(); rowFences -= row.id
    }

    public fun forget(expected: RememberedSource): Unit = synchronized(gate) {
        ready(); val row = exact(expected); rowFences += row.id
        drainSource(row.id); ready()
        index.forget(row); reload(); rowFences -= row.id; repair -= row.id
    }

    public fun repairChosen(expected: RememberedSource): Unit = synchronized(gate) {
        ready(); val row = exact(expected); rowFences += row.id; drainSource(row.id)
        val selected = native.choose()
        if(selected == null) { rowFences -= row.id; return@synchronized }
        selections += selected
        try {
            val record = native.selectionRecord(selected); ready()
            // This different resource is allowed only by explicit repair/reselection. Automatic
            // stale replacement below instead verifies the original exact identity and scope.
            index.replace(row, record.kind, record.label, record.identity, record.bookmark)
            reload(); rowFences -= row.id; repair -= row.id
        } finally { discard(selected) }
    }

    public fun rescan(expected: RememberedSource): SourceImportBatch = synchronized(gate) {
        ready(); var row = exact(expected)
        requireSource(row.enabled && row.version == 1L && row.id !in rowFences, "source_fenced")
        requireSource(batches.size < SourceAccessLimits.LEASES, "source_busy")
        try {
            native.refreshedBookmark(row)?.let { fresh ->
                ready(); requireSource(row.id !in rowFences, "source_fenced")
                row = index.replace(row, SourceKind.valueOf(row.kind), row.label, row.identity(), fresh)
                reload()
            }
            val frozen = row
            val batch = native.scan(frozen) {
                val current = rows[frozen.id]
                !fenced && !closing && initialized && frozen.id !in rowFences &&
                    current != null && current.revision == frozen.revision && current.enabled && current.version == 1L
            }
            TrackedBatch(row.id, batch).also { batches += it; repair -= row.id }
        } catch(failure: Exception) {
            if(failure !is SourceAccessFailure || failure.code !in setOf("source_fenced", "source_busy")) repair += row.id
            throw failure
        }
    }

    /** Does not wait for IO. Both host admission and the native walker/read fence are immediate. */
    public fun beginFence(): Unit {
        fenced = true
        if(nativeStarted) native.fence()
    }
    public fun beginClose(): Unit { closing = true; beginFence() }

    /** Caller must first join its admitted chooser/scan/import work. No timeout implies release. */
    public fun drain(): Unit = synchronized(gate) {
        requireSource(fenced || closing, "source_fenced")
        batches.toList().forEach { it.close() }
        selections.toList().forEach(::discard)
        if(nativeStarted) native.drain()
    }
    public fun resumeAfterSleep(): Unit = synchronized(gate) {
        requireSource(initialized && fenced && !closing && !closed, "source_closed")
        drain(); native.resume(); fenced = false
        // A wake never resolves a source or starts a rescan/import.
    }
    override fun close(): Unit = synchronized(gate) {
        if(closed) return@synchronized
        beginClose(); drain()
        index.close() // If this throws, native lifetime/lock remains held.
        if(nativeStarted) native.close()
        closed = true; initialized = false
    }
    private fun drainSource(id: String): Unit { batches.filter { it.sourceId == id }.forEach { it.close() } }
    private fun discard(selection: SourceSelection): Unit { native.discardSelection(selection); selections -= selection }
    private fun reload(): Unit {
        rows = index.list().associateBy { it.id }
    }
    private fun ready(): Unit { requireSource(initialized && !fenced && !closing && !closed, "source_fenced") }
    private fun exact(expected: RememberedSource): SourceLocator {
        val row = rows[expected.id] ?: throw SourceAccessFailure("source_revision_stale")
        requireSource(row.revision == expected.revision && row.version == 1L, "source_revision_stale"); return row
    }
    private inner class TrackedBatch(val sourceId: String, private val delegate: SourceImportBatch) : SourceImportBatch {
        private var closed: Boolean = false
        override val files: List<SourceImportFile> get() = delegate.files
        override val preparationMs: Long get() = delegate.preparationMs
        @Synchronized override fun close(): Unit {
            if(closed) return
            delegate.close(); closed = true; batches -= this
        }
    }
}
private fun requireSource(condition: Boolean, code: String): Unit { if(!condition) throw SourceAccessFailure(code) }
