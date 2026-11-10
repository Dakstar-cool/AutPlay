package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopImportCandidateMapping
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobCandidateState
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPage
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportMappingReview
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportReviewPort
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopTextSearchMatches
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.application.desktop.DesktopTrack
import app.autplay.desktop.DesktopRuntime
import app.autplay.desktop.DesktopSourceSelection
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.settings.DesktopSettingsController
import java.io.File
import java.lang.reflect.Proxy
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.Continuation
import kotlin.coroutines.intrinsics.startCoroutineUninterceptedOrReturn
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue

/** Parent adds ONE hook to the existing queue composite. No test entry or real DB/media/native IO. */
internal suspend fun assertDesktopImportJobsHostPolicy(): Unit {
    assertSavedImportPages()
    assertSavedImportReadOnlyJobs()
    assertSavedReadReplacement()
    assertSavedReadShutdown(snapshotRefusal = false)
    assertSavedReadShutdown(snapshotRefusal = true)
    assertImportMappingAndDrain()
    assertImportReconciliation()
    assertPreparationReconciliation()
    assertPreparationMarkerOnCancellation()
}

private suspend fun assertSavedImportPages(): Unit {
    val fixture = SavedImportHostFixture()
    val cursorA = DesktopImportJobCursor("page-a")
    val cursorB = DesktopImportJobCursor("page-b")
    var fail = true
    fixture.readPage = { cursor -> when (cursor) {
        null -> DesktopImportJobPage(emptyList(), cursorA)
        cursorA -> DesktopImportJobPage(List(16) { savedHostJob("job-$it") }, cursorB)
        cursorB -> if (fail) error("private_read_detail") else DesktopImportJobPage(listOf(savedHostJob("last-job")), null)
        else -> error("unexpected_cursor")
    } }
    try {
        val host = fixture.host
        val reader = host.savedImports
        assertEquals(0, fixture.reads.get()) // Constructing/opening the screen never scans automatically.
        val initial = reader.state.value
        host.refreshSavedImports(initial)
        eventually("empty saved scan page") { reader.state.value.initialized && !reader.state.value.loading }
        val empty = reader.state.value
        assertTrue(empty.jobs.isEmpty())
        assertEquals(cursorA, empty.next)
        host.nextSavedImports(empty)
        eventually("sixteen saved jobs") { reader.state.value.jobs.size == 16 && !reader.state.value.loading }
        val middle = reader.state.value
        val readCount = fixture.reads.get()
        host.nextSavedImports(empty)
        host.retrySavedImports(empty)
        assertEquals(readCount, fixture.reads.get())
        host.nextSavedImports(middle)
        eventually("safe saved page failure") { reader.state.value.errorCode == "desktop_import_jobs_load_failed" }
        val failed = reader.state.value
        assertEquals(middle.jobs, failed.jobs)
        assertEquals(cursorB, failed.next)
        host.retrySavedImports(middle) // Stale retries cannot replay a later failed cursor.
        fail = false
        host.retrySavedImports(failed)
        eventually("exact saved cursor retry") { reader.state.value.jobs.singleOrNull()?.id == "last-job" && !reader.state.value.loading }
        assertNull(reader.state.value.next)
        assertEquals(listOf(null, cursorA, cursorB, cursorB), fixture.cursors.toList())
        assertEquals(1, reader.state.value.jobs.size) // Old visible pages are released, never accumulated.
        val finished = reader.state.value
        reader.dismiss(finished)
        assertFalse(reader.state.value.open)
        host.refreshSavedImports(finished)
        assertFalse(reader.state.value.loading)
        host.refreshSavedImports(reader.state.value)
        eventually("explicit refresh returns to start") { reader.state.value.initialized && !reader.state.value.loading && reader.state.value.jobs.isEmpty() }
        assertEquals(cursorA, reader.state.value.next)
    } finally { fixture.close() }
}

private suspend fun assertSavedImportReadOnlyJobs(): Unit {
    val fixture = SavedImportHostFixture()
    try {
        val original = fixture.importer.job
        for (job in listOf(original.copy(phase = DesktopImportJobPhase.UNSUPPORTED, revision = null,
            errorCode = "import_job_history_unsupported"), original.copy(phase = DesktopImportJobPhase.COMPLETED))) {
            fixture.readPage = { DesktopImportJobPage(listOf(job), null) }
            val saved = fixture.saved()
            assertSame(job, saved.jobs.single())
            assertFalse(fixture.host.resumeImportUsingChooser(saved, job) { error("read_only_job_chooser") })
            assertEquals(0, fixture.importer.preparations.get())
        }
    } finally { fixture.close() }
}

private suspend fun assertSavedReadReplacement(): Unit {
    val fixture = SavedImportHostFixture()
    val entered = CompletableDeferred<Unit>()
    val release = CompletableDeferred<Unit>()
    fixture.readPage = {
        if (fixture.reads.get() == 1) {
            entered.complete(Unit)
            withContext(NonCancellable) { release.await() }
            DesktopImportJobPage(listOf(savedHostJob("obsolete")), null)
        } else DesktopImportJobPage(listOf(savedHostJob("replacement")), null)
    }
    try {
        fixture.host.refreshSavedImports(fixture.host.savedImports.state.value)
        withTimeout(2_000) { entered.await() }
        val firstLoading = fixture.host.savedImports.state.value
        fixture.host.refreshSavedImports(firstLoading)
        val replacementLoading = fixture.host.savedImports.state.value
        assertTrue(replacementLoading.revision > firstLoading.revision)
        fixture.host.refreshSavedImports(firstLoading)
        assertSame(replacementLoading, fixture.host.savedImports.state.value)
        assertEquals(1, fixture.reads.get())
        release.complete(Unit)
        eventually("retired read drains before replacement") { fixture.host.savedImports.state.value.jobs.singleOrNull()?.id == "replacement" }
        assertEquals(2, fixture.reads.get())
    } finally { release.complete(Unit); fixture.close() }
}

private suspend fun assertSavedReadShutdown(snapshotRefusal: Boolean): Unit = coroutineScope {
    val fixture = SavedImportHostFixture()
    val entered = CompletableDeferred<Unit>()
    val release = CompletableDeferred<Unit>()
    fixture.readPage = {
        entered.complete(Unit)
        withContext(NonCancellable) { release.await() }
        DesktopImportJobPage(listOf(savedHostJob("retired-job")), null)
    }
    try {
        val host = fixture.host
        host.refreshSavedImports(host.savedImports.state.value)
        withTimeout(2_000) { entered.await() }
        if (snapshotRefusal) {
            host.controller.reportSnapshotUnavailable()
            eventually("sticky saved reader refusal") { host.savedImports.state.value.errorCode == "desktop_snapshot_unavailable" }
            assertFalse(host.resumeImportUsingChooser(host.savedImports.state.value, fixture.importer.job) { error("refused_chooser") })
        }
        host.beginClose()
        val retired = host.savedImports.state.value
        assertTrue(retired.closed)
        host.refreshSavedImports(retired)
        host.nextSavedImports(retired)
        host.retrySavedImports(retired)
        assertFalse(host.resumeImportUsingChooser(retired, fixture.importer.job) { error("closing_chooser") })
        val close = async(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) { host.close() }
        withTimeout(2_000) { fixture.searchClosed.await() }
        assertFalse(close.isCompleted)
        assertEquals(0, fixture.closes.get())
        release.complete(Unit)
        withTimeout(5_000) { close.await() }
        assertSame(retired, host.savedImports.state.value)
        assertEquals(1, fixture.closes.get())
        assertEquals(1, fixture.reads.get())
    } finally { release.complete(Unit); fixture.close() }
}

private suspend fun assertImportMappingAndDrain(): Unit = coroutineScope {
    val fixture = SavedImportHostFixture()
    val selected = CompletableDeferred<DesktopSourceSelection>()
    val chooserEntered = CompletableDeferred<Unit>()
    val committed = CompletableDeferred<Unit>()
    val drain = CompletableDeferred<Unit>()
    val chooserCalls = AtomicInteger()
    fixture.importer.execute = { preview, mappings, publish ->
        val old = checkNotNull(preview.job)
        val candidateId = mappings.single().candidateId
        val settled = old.copy(revision = checkNotNull(old.revision) + 1, phase = DesktopImportJobPhase.RESELECTION_REQUIRED, candidates = old.candidates.map {
            if (it.candidate.id == candidateId) it.copy(outcome = DesktopImportJobOutcome.IMPORTED) else it
        })
        fixture.importer.job = settled
        publish(DesktopImportProgress(preview.id, 1, 1, 1, jobId = old.id, jobRevision = settled.revision, jobPhase = settled.phase))
        committed.complete(Unit)
        try { awaitCancellation() }
        finally {
            withContext(NonCancellable) { drain.await() }
            fixture.importer.job = settled.copy(revision = checkNotNull(settled.revision) + 1, phase = DesktopImportJobPhase.CANCELLED)
            publish(DesktopImportProgress(preview.id, 1, 1, 1, jobId = old.id,
                jobRevision = fixture.importer.job.revision, jobPhase = DesktopImportJobPhase.CANCELLED))
        }
    }
    var firstSelectionId: String? = null
    val retainedLibrary = fixture.library.value
    try {
        val host = fixture.host
        val saved = fixture.saved()
        assertTrue(host.resumeImportUsingChooser(saved, saved.jobs.single()) {
            chooserCalls.incrementAndGet(); chooserEntered.complete(Unit); selected.await()
        })
        withTimeout(2_000) { chooserEntered.await() }
        assertEquals(DesktopImportPhase.SELECTING, host.importReview.value.phase)
        assertEquals(0, fixture.importer.preparations.get())
        assertFalse(host.resumeImportUsingChooser(saved, saved.jobs.single()) { error("second_chooser") })
        selected.complete(DesktopSourceSelection(listOf("synthetic-new-selection")))
        eventually("fresh mapping preview") { host.importMapping.value != null }
        val empty = checkNotNull(host.importMapping.value)
        val file = empty.preview.files.single()
        firstSelectionId = file.id
        assertTrue(empty.mappings.isEmpty())
        assertEquals(listOf("synthetic-new-selection"), fixture.importer.selectedPaths)
        assertNotEquals(empty.preview.job?.candidates?.last()?.candidate?.title, file.title)
        assertFalse(host.confirmImport(empty.preview.id))
        assertFalse(host.confirmImportMapping(empty))
        assertFalse(host.mapImportCandidate(empty.copy(), "failed", file.id))
        assertFalse(host.mapImportCandidate(empty, "imported", file.id))
        assertFalse(host.mapImportCandidate(empty, "duplicate", file.id))
        assertFalse(host.mapImportCandidate(empty, "missing", file.id))
        assertTrue(host.mapImportCandidate(empty, "failed", file.id))
        val mapped = checkNotNull(host.importMapping.value)
        assertFalse(host.mapImportCandidate(empty, "unsettled", file.id))
        assertFalse(host.mapImportCandidate(mapped, "unsettled", file.id))
        assertTrue(host.mapImportCandidate(mapped, "failed", null))
        val unmapped = checkNotNull(host.importMapping.value)
        assertTrue(unmapped.mappings.isEmpty())
        assertTrue(host.mapImportCandidate(unmapped, "failed", file.id))
        val remapped = checkNotNull(host.importMapping.value)
        assertEquals(mapped.mappings, remapped.mappings)
        assertTrue(remapped.revision > mapped.revision)
        assertFalse(host.confirmImportMapping(mapped)) // A-B-A mapping cannot revive the old confirmation.
        assertTrue(host.confirmImportMapping(remapped))
        assertFalse(host.confirmImportMapping(remapped))
        withTimeout(2_000) { committed.await() }
        assertEquals(1, host.importReview.value.progress?.imported)
        host.cancelImport()
        assertTrue(host.importing.value)
        assertFalse(host.importDroppedFiles(listOf(File("synthetic-unread-source"))))
        val close = async(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) { host.close() }
        withTimeout(2_000) { fixture.searchClosed.await() }
        assertFalse(close.isCompleted)
        assertEquals(0, fixture.closes.get())
        drain.complete(Unit)
        withTimeout(5_000) { close.await() }
        assertEquals(1, host.importResult.value?.imported)
        assertTrue(host.importResult.value?.cancelled == true)
        assertEquals(DesktopImportJobPhase.CANCELLED, host.lastImportProgress.value?.jobPhase)
        assertEquals(listOf(DesktopImportCandidateMapping("failed", file.id)), fixture.importer.confirmedMappings)
        assertEquals(1, chooserCalls.get())
        assertEquals(DesktopImportJobOutcome.IMPORTED, fixture.importer.job.candidates.first { it.candidate.id == "imported" }.outcome)
        assertEquals(DesktopImportJobOutcome.DUPLICATE, fixture.importer.job.candidates.first { it.candidate.id == "duplicate" }.outcome)
        assertNull(fixture.importer.job.candidates.first { it.candidate.id == "unsettled" }.outcome)
        assertSame(retainedLibrary, fixture.library.value)
        assertEquals("LIKED", fixture.library.value.tracks.single().preference)
        assertEquals("REMOVED", fixture.library.value.tracks.single().collectionState)
    } finally { selected.complete(DesktopSourceSelection(cancelled = true)); drain.complete(Unit); fixture.close() }
    val reopened = SavedImportHostFixture(fixture.importer.job)
    try {
        assertEquals(0, reopened.reads.get())
        val saved = reopened.saved()
        assertEquals(DesktopImportJobPhase.CANCELLED, saved.jobs.single().phase)
        assertTrue(reopened.host.resumeImportUsingChooser(saved, saved.jobs.single()) { DesktopSourceSelection(listOf("new-after-reopen")) })
        eventually("restart requires fresh mapping") { reopened.host.importMapping.value != null }
        val preview = checkNotNull(reopened.host.importMapping.value)
        assertTrue(preview.mappings.isEmpty())
        assertNotEquals(firstSelectionId, preview.preview.files.single().id)
        assertFalse(reopened.host.confirmImportMapping(preview))
    } finally { reopened.close() }
}

private suspend fun assertImportReconciliation(): Unit {
    val fixture = SavedImportHostFixture()
    fixture.importer.execute = { preview, _, publish ->
        val old = checkNotNull(preview.job)
        fixture.importer.job = old.copy(revision = checkNotNull(old.revision) + 1, phase = DesktopImportJobPhase.COMPLETED,
            candidates = old.candidates.map { it.copy(outcome = DesktopImportJobOutcome.IMPORTED) })
        publish(DesktopImportProgress(preview.id, 1, jobId = old.id, jobRevision = fixture.importer.job.revision,
            jobPhase = DesktopImportJobPhase.COMPLETED, errorCode = "import_job_reconciliation_required"))
        DesktopImportResult(0, 0, 0, "import_job_reconciliation_required")
    }
    try {
        val saved = fixture.saved()
        assertTrue(fixture.host.resumeImportUsingChooser(saved, saved.jobs.single()) { DesktopSourceSelection(listOf("explicit")) })
        eventually("reconciliation preview") { fixture.host.importMapping.value != null }
        val empty = checkNotNull(fixture.host.importMapping.value)
        assertTrue(fixture.host.mapImportCandidate(empty, "failed", empty.preview.files.single().id))
        assertTrue(fixture.host.confirmImportMapping(checkNotNull(fixture.host.importMapping.value)))
        eventually("uncertain batch stops") { !fixture.host.importing.value }
        assertEquals(0, fixture.host.importResult.value?.imported)
        assertEquals("import_job_reconciliation_required", fixture.host.lastImportProgress.value?.errorCode)
        assertFalse(fixture.host.importDroppedFiles(listOf(File("refused-before-reopen"))))
        val reconciled = fixture.saved()
        assertTrue(reconciled.jobs.isEmpty())
        assertEquals(DesktopImportJobPhase.COMPLETED, reconciled.lastJob?.phase)
        assertTrue(checkNotNull(reconciled.lastJob).candidates.all { it.outcome == DesktopImportJobOutcome.IMPORTED })
        assertEquals(0, fixture.host.importResult.value?.imported) // Explicit history read never invents an acknowledgement.
    } finally { fixture.close() }
}

private suspend fun assertPreparationReconciliation(): Unit {
    val fixture = SavedImportHostFixture()
    fixture.importer.preparationError = "import_job_reconciliation_required"
    val chooserCalls = AtomicInteger()
    try {
        val saved = fixture.saved()
        assertTrue(fixture.host.resumeImportUsingChooser(saved, saved.jobs.single()) {
            chooserCalls.incrementAndGet()
            DesktopSourceSelection(listOf("fresh-explicit-selection"))
        })
        eventually("refused preparation preview") {
            fixture.host.importReview.value.preview?.errorCode == "import_job_reconciliation_required" &&
                fixture.host.lastImportProgress.value?.jobId == fixture.importer.job.id &&
                fixture.host.lastImportProgress.value?.errorCode == "import_job_reconciliation_required"
        }
        assertEquals(fixture.importer.job.id, fixture.host.lastImportProgress.value?.jobId)
        assertEquals("import_job_reconciliation_required", fixture.host.lastImportProgress.value?.errorCode)
        fixture.host.cancelImport()
        eventually("refused preview dismissed") { !fixture.host.importing.value }
        assertTrue(fixture.host.importResult.value?.cancelled == true)
        assertEquals("import_job_reconciliation_required", fixture.host.importResult.value?.errorCode)
        val reads = fixture.reads.get()
        val refreshed = fixture.saved()
        assertTrue(fixture.reads.get() > reads)
        assertEquals(fixture.importer.job.id, refreshed.lastJob?.id)
        assertFalse(fixture.host.importDroppedFiles(listOf(File("must-not-reopen-import"))))
        assertFalse(fixture.host.resumeImportUsingChooser(refreshed, refreshed.jobs.single()) {
            chooserCalls.incrementAndGet()
            DesktopSourceSelection(listOf("must-not-open-chooser"))
        })
        assertEquals(1, chooserCalls.get())
        assertEquals(1, fixture.importer.preparations.get())
    } finally { fixture.close() }
    val reopened = SavedImportHostFixture()
    try {
        val saved = reopened.saved()
        assertTrue(reopened.host.resumeImportUsingChooser(saved, saved.jobs.single()) { DesktopSourceSelection(listOf("new-runtime-selection")) })
        eventually("new runtime accepts fresh review") { reopened.host.importMapping.value != null }
        reopened.host.cancelImport()
        eventually("new runtime review dismissed") { !reopened.host.importing.value }
    } finally { reopened.close() }
}

private suspend fun assertPreparationMarkerOnCancellation(): Unit {
    val fixture = SavedImportHostFixture()
    fixture.importer.preparationError = "import_library_reopen_required"
    // Cancel when the adapter has constructed its preview, before host publication.
    fixture.importer.onPrepared = { fixture.host.cancelImport() }
    try {
        val saved = fixture.saved()
        assertTrue(fixture.host.resumeImportUsingChooser(saved, saved.jobs.single()) { DesktopSourceSelection(listOf("explicit-before-cancel")) })
        eventually("delivered preparation marker survives cancellation") {
            !fixture.host.importing.value && fixture.host.error.value == "desktop_snapshot_unavailable"
        }
        assertEquals("import_library_reopen_required", fixture.host.importResult.value?.errorCode)
        assertTrue(fixture.host.importResult.value?.cancelled == true)
        assertFalse(fixture.host.importDroppedFiles(listOf(File("globally-refused-import"))))
    } finally { fixture.close() }
}

private class SavedImportHostFixture(job: DesktopImportJob = savedHostJob("saved-job")) {
    private val scopeJob = SupervisorJob()
    private val scope = CoroutineScope(scopeJob + Dispatchers.Default)
    val importer = SavedImportAdapterFixture(job)
    val reads = AtomicInteger()
    val closes = AtomicInteger()
    private val activeReads = AtomicInteger()
    val cursors = CopyOnWriteArrayList<DesktopImportJobCursor?>()
    val searchClosed = CompletableDeferred<Unit>()
    @Volatile var readPage: suspend (DesktopImportJobCursor?) -> DesktopImportJobPage = {
        DesktopImportJobPage(if (importer.job.phase == DesktopImportJobPhase.COMPLETED) emptyList() else listOf(importer.job), null)
    }
    val library = MutableStateFlow(DesktopLibrarySnapshot(tracks = listOf(
        DesktopTrack("retained-track", "Retained title", "Retained artist", null, "0".repeat(64), 100, "LIKED", "REMOVED"))))
    private val store = Proxy.newProxyInstance(DesktopLibraryStore::class.java.classLoader, arrayOf(DesktopLibraryStore::class.java)) { _, method, args ->
        when (method.name) {
            "getState" -> library
            "loadUnfinishedImportJobs" -> importJobsSuspend(args) {
                assertEquals(16, args[1])
                val cursor = args[0] as DesktopImportJobCursor?
                cursors.add(cursor); reads.incrementAndGet(); assertEquals(1, activeReads.incrementAndGet())
                try { readPage(cursor) } finally { activeReads.decrementAndGet() }
            }
            "loadImportJob" -> importer.job.takeIf { it.id == args[0] }
            "close" -> { assertEquals(0, activeReads.get()); assertFalse(importer.executing); closes.incrementAndGet(); Unit }
            else -> error("unexpected_saved_import_store_call")
        }
    } as DesktopLibraryStore
    private val preferences = object : DesktopPreferencesStore {
        override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState())
        override suspend fun setAppLanguage(value: String): Unit = error("unexpected_import_settings_write")
        override suspend fun setAppearanceMode(value: String): Unit = error("unexpected_import_settings_write")
        override suspend fun setAccentPalette(value: String): Unit = error("unexpected_import_settings_write")
        override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("unexpected_import_settings_write")
        override suspend fun applyPortable(value: DesktopPreferences): Unit = error("unexpected_import_settings_write")
        override suspend fun close(): Unit = Unit
    }
    private val search = object : DesktopTextSearchPort {
        override suspend fun search(snapshot: DesktopLibrarySnapshot, query: String): DesktopTextSearchMatches = DesktopTextSearchMatches(query, true)
        override fun retire(): Unit = Unit
        override suspend fun close(): Unit { searchClosed.complete(Unit) }
    }
    private val controller = DesktopPlaybackController(store, PolicyEngine(), scope, periodicObservations = emptyFlow())
    private val settings = DesktopSettingsController(preferences, scope)
    val host: DesktopRuntime = DesktopRuntime::class.java.declaredConstructors.single { !it.isSynthetic && it.parameterCount == 9 }
        .apply { isAccessible = true }.newInstance(store, importer, controller, scope, settings, search, null, null, null) as DesktopRuntime

    suspend fun saved(): app.autplay.application.desktop.DesktopSavedImportsState {
        host.refreshSavedImports(host.savedImports.state.value)
        eventually("explicit saved jobs refresh") { host.savedImports.state.value.initialized && !host.savedImports.state.value.loading }
        return host.savedImports.state.value
    }
    suspend fun close(): Unit { host.close(); scopeJob.cancelAndJoin() }
}

private class SavedImportAdapterFixture(@Volatile var job: DesktopImportJob) : DesktopImportReviewPort {
    val preparations = AtomicInteger()
    @Volatile var executing: Boolean = false
    var preparationError: String? = null
    var onPrepared: (DesktopImportPreview) -> Unit = {}
    var selectedPaths: List<String> = emptyList()
    var confirmedMappings: List<DesktopImportCandidateMapping> = emptyList()
    var execute: suspend (DesktopImportPreview, List<DesktopImportCandidateMapping>, (DesktopImportProgress) -> Unit) -> DesktopImportResult =
        { _, _, _ -> error("unexpected_import_execution") }
    private var preview: DesktopImportPreview? = null
    override suspend fun prepareSelected(paths: List<String>): DesktopImportPreview = error("resume_must_use_new_chooser")
    override suspend fun importSelected(paths: List<String>): DesktopImportResult = error("resume_must_use_review")
    override suspend fun importPrepared(previewId: String, onProgress: (DesktopImportProgress) -> Unit): DesktopImportResult = error("resume_requires_mapping")
    override suspend fun prepareReselected(jobId: String, paths: List<String>): DesktopImportPreview {
        preparations.incrementAndGet()
        selectedPaths = paths.toList()
        assertEquals(job.id, jobId)
        val result = if (preparationError != null) DesktopImportPreview(UUID.randomUUID().toString(), emptyList(), 0,
            failed = 1, errorCode = preparationError, jobId = job.id)
        else DesktopImportPreview(UUID.randomUUID().toString(), listOf(DesktopImportPreviewFile(UUID.randomUUID().toString(), "New observation", 200)),
            200, jobId = job.id, job = job)
        return result.also { preview = it; onPrepared(it) }
    }
    override suspend fun importReselected(previewId: String, mappings: List<DesktopImportCandidateMapping>,
        onProgress: (DesktopImportProgress) -> Unit): DesktopImportResult {
        val plan = checkNotNull(preview)
        assertEquals(plan.id, previewId)
        assertEquals(plan.job?.revision, job.revision)
        confirmedMappings = mappings.toList()
        executing = true
        try { return execute(plan, mappings, onProgress) } finally { executing = false }
    }
    override fun discardPrepared(previewId: String): Unit { if (preview?.id == previewId) preview = null }
}

@Suppress("UNCHECKED_CAST")
private fun <T> importJobsSuspend(args: Array<out Any?>, block: suspend () -> T): Any? =
    block.startCoroutineUninterceptedOrReturn(args.last() as Continuation<T>)

private fun savedHostJob(id: String): DesktopImportJob = DesktopImportJob(id, 7, DesktopImportJobPhase.PAUSED,
    listOf("imported", "duplicate", "failed", "unsettled").mapIndexed { index, candidate ->
        DesktopImportJobCandidateState(DesktopImportJobCandidate(candidate, "Old observation", 100), outcome = when (index) {
            0 -> DesktopImportJobOutcome.IMPORTED; 1 -> DesktopImportJobOutcome.DUPLICATE; 2 -> DesktopImportJobOutcome.FAILED; else -> null
        })
    })
