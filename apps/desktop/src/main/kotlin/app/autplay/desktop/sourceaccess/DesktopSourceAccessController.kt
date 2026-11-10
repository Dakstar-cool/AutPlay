package app.autplay.desktop.sourceaccess

import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.sourceaccess.*
import app.autplay.desktop.library.DesktopFileImporter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Host owns this controller before initialize and through actual sleep/Quit settlement.
 * onReviewPrepared must atomically adopt the exact preview into the existing import review
 * host, returning false when its admission/navigation/lifetime is no longer current.
 */
public class DesktopSourceAccessController(
    private val source: DesktopSourceAccessOwner,
    private val importer: DesktopFileImporter,
    private val scope: CoroutineScope,
    private val canPrepareReview: () -> Boolean,
    private val onReviewPrepared: suspend (DesktopImportPreview) -> Boolean,
) {
    private val gate: Any = Any()
    private val mutableState: MutableStateFlow<SourceAccessState> = MutableStateFlow(SourceAccessState())
    public val state: StateFlow<SourceAccessState> = mutableState.asStateFlow()
    private var active: Job? = null
    private var initializing: Boolean = false
    private var fenced: Boolean = false
    private var closing: Boolean = false
    private var closed: Boolean = false

    public val actions: SourceAccessActions = SourceAccessActions(
        remember = { expected -> command(expected) { source.rememberChosen() } },
        enable = { expected, row -> command(expected, row) { source.setEnabled(row, true) } },
        disable = { expected, row -> command(expected, row, fenceRow = true) { source.setEnabled(row, false) } },
        forget = { expected, row -> command(expected, row, fenceRow = true) { source.forget(row) } },
        rescan = { expected, row -> command(expected, row) { prepare(row) } },
        repair = { expected, row -> command(expected, row, fenceRow = true) { source.repairChosen(row) } },
    )

    public fun initialize(): Unit = synchronized(gate) {
        if(initializing || mutableState.value.available || closed || closing || fenced || active != null) return@synchronized
        initializing = true
        start { source.initialize() }
    }

    /** Must be called by the current explicit drop event, with Remember visibly selected. */
    public fun rememberExplicitDrop(expected: SourceAccessState, paths: List<String>): Unit {
        if(paths.size !in 1..SourceAccessLimits.SOURCES) return
        val frozen = paths.toList()
        command(expected) { source.rememberExplicitDrop(frozen) }
    }

    private fun command(expected: SourceAccessState, row: RememberedSource? = null, fenceRow: Boolean = false,
        action: suspend () -> Unit): Unit = synchronized(gate) {
        if(!accepts(expected) || (row != null && expected.sources.none { it === row })) return@synchronized
        if(row?.availability == SourceAvailability.UNSUPPORTED) return@synchronized
        try {
            if(fenceRow && row != null) source.fenceSource(row)
            start(action)
        } catch(failure: Exception) { publish(error(failure)) }
    }

    private suspend fun prepare(row: RememberedSource): Unit {
        if(!canPrepareReview()) throw SourceAccessFailure("import_busy")
        val batch = source.rescan(row)
        var preview: DesktopImportPreview? = null
        var adopted = false
        try {
            currentCoroutineContext().ensureActive()
            val frozenPreview = importer.prepareRemembered(batch)
            preview = frozenPreview
            currentCoroutineContext().ensureActive()
            frozenPreview.errorCode?.let { throw SourceAccessFailure(it) }
            val allowed = synchronized(gate) { !fenced && !closing && !closed }
            if(allowed && canPrepareReview()) {
                // Preserve the adoption result even if sleep/Quit cancellation arrives while
                // the host atomically publishes its review owner. The host rechecks its fence.
                adopted = withContext(NonCancellable) { onReviewPrepared(frozenPreview) }
            }
        } finally {
            if(!adopted) {
                preview?.let { importer.discardPrepared(it.id) }
                batch.close()
            }
        }
    }

    private fun accepts(expected: SourceAccessState): Boolean = !fenced && !closing && !closed && active == null &&
        mutableState.value === expected && expected.available && !expected.busy && !expected.fenced

    /** One admitted host job, no unbounded commands or automatic reopens/rescans. */
    private fun start(action: suspend () -> Unit): Unit {
        publish(busy = true)
        var failureCode: String? = null
        val job = scope.launch(Dispatchers.IO, start = CoroutineStart.LAZY) {
            try { action() }
            catch(failure: Exception) {
                failureCode = error(failure)
                if(failure is CancellationException) throw failure
            }
        }
        active = job
        job.invokeOnCompletion { failure -> synchronized(gate) {
            if(active === job) {
                active = null; initializing = false
                publish(failureCode ?: failure?.let(::error))
            }
        } }
        job.start()
    }

    /** Call synchronously on sleep/Quit admission, before cancelling/joining other hosts. */
    public fun beginFence(): Unit = synchronized(gate) {
        fenced = true
        source.beginFence()
        active?.cancel()
        publish(busy = active != null)
    }
    public fun beginClose(): Unit = synchronized(gate) { closing = true; beginFence() }

    /** Call after the existing import host has cancelled/discarded and joined its real work. */
    public suspend fun settleSleep(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        beginFence(); val admitted = synchronized(gate) { active }; admitted?.join(); source.drain()
        synchronized(gate) { publish() }
    }
    public suspend fun resumeAfterSleep(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        synchronized(gate) { if(closing || closed) throw SourceAccessFailure("source_closed") }
        source.resumeAfterSleep()
        synchronized(gate) { fenced = false; publish() }
    }
    public suspend fun close(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        beginClose(); val admitted = synchronized(gate) { active }; admitted?.join()
        source.close()
        synchronized(gate) { closed = true; publish() }
    }
    private fun publish(errorCode: String? = null, busy: Boolean = false): Unit {
        val old = mutableState.value
        mutableState.value = source.snapshot(old.revision + 1, busy, errorCode).let {
            if(fenced || closing || closed) it.copy(fenced = true) else it
        }
    }
    private fun error(failure: Throwable): String = when(failure) {
        is SourceAccessFailure -> failure.code.takeIf { it in ERRORS } ?: "source_unavailable"
        is CancellationException -> "source_fenced"
        else -> "source_unavailable"
    }
    private companion object {
        val ERRORS: Set<String> = setOf("source_identity_unavailable", "source_busy", "source_closed", "source_fenced",
            "source_drain_pending", "source_selection_invalid", "source_unavailable", "source_repair_required", "source_limit",
            "source_internal_rejected", "source_revision_stale", "source_record_unsupported", "source_index_full",
            "source_index_unsupported", "source_index_reopen_required", "source_index_unavailable", "import_busy")
    }
}
