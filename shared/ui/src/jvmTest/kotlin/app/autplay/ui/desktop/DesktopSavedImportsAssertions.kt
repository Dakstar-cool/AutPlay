package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopImportCandidateMapping
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobCandidateState
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportMappingReview
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopSavedImportsState
import app.autplay.application.desktop.desktopImportMappingValid
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue

/** Helpers for the three existing UI composites only; no new test entries or executed evidence. */
internal fun assertSavedImportsTargets(): Unit {
    val review = savedMappingFixture()
    val job = checkNotNull(review.preview.job)
    val state = DesktopSavedImportsState(open = true, initialized = true, jobs = listOf(job), next = DesktopImportJobCursor("next"))
    val library = DesktopLibrarySnapshot()
    var liveState = state
    var liveMapping: DesktopImportMappingReview? = review
    val owner = DesktopSavedImportsCallbacks(isCurrent = { it === liveState }, isMappingCurrent = { it === liveMapping })
    fun input(s: DesktopSavedImportsState = state, m: DesktopImportMappingReview? = review,
        o: DesktopSavedImportsCallbacks = owner, l: DesktopLibrarySnapshot? = library,
        section: DesktopSection = DesktopSection.Library, nav: Long = 1, selection: Long = 1,
        blocked: Boolean = false, importing: Boolean = false): DesktopSavedImportsInput =
        DesktopSavedImportsInput(s, m, o, l, section, nav, selection, blocked, importing)
    val captured = input()
    var current = captured
    var navigation = true
    val lifetime = DesktopSavedImportsLifetime()
    val revision = lifetime.observe(captured)
    val guard = desktopSavedImportsGuard(lifetime, revision, captured, { current }, { navigation })
    val mappingGuard = desktopSavedImportsGuard(lifetime, revision, captured, { current }, { navigation }, mapping = true)
    assertTrue(guard())
    assertTrue(mappingGuard())
    assertTrue(captured.canResume(job))
    assertFalse(captured.canResume(job.copy()))
    assertFalse(captured.canConfirm()) // A new chooser never prefills a mapping.
    listOf(input(s = state.copy()), input(m = review.copy()), input(o = owner.copy()), input(l = library.copy()),
        input(l = null), input(section = DesktopSection.Home), input(nav = 2), input(selection = 2),
        input(blocked = true), input(importing = true)).forEach { replacement ->
        current = replacement
        assertFalse(guard())
        assertFalse(mappingGuard())
    }
    current = captured
    navigation = false
    assertFalse(guard())
    navigation = true
    // Host updates invalidate callbacks before any recomposition has observed the replacement.
    liveState = state.copy(revision = 1)
    assertFalse(guard())
    liveMapping = review.copy(revision = 1)
    assertFalse(mappingGuard())
    liveState = state
    liveMapping = review
    lifetime.observe(input(nav = 2))
    lifetime.observe(captured)
    assertFalse(guard()) // A-B-A input cannot resurrect the old lifetime revision.
    assertFalse(mappingGuard())
    lifetime.retire()
    assertFalse(lifetime.matches(lifetime.observe(captured)))

    val frozen = review.copy(mappings = listOf(DesktopImportCandidateMapping("old-2", "new-0")))
    assertTrue(input(m = frozen).canConfirm())
    assertFalse(input(m = frozen, blocked = true).canConfirm())
    assertFalse(input(s = state.copy(closed = true), m = frozen).canConfirm())
    assertFalse(input(s = state.copy(loading = true)).canResume(job))
    assertFalse(input(s = state.copy(errorCode = "desktop_import_jobs_load_failed")).canResume(job))
    assertFalse(input(s = state.copy(jobs = listOf(job.copy(phase = DesktopImportJobPhase.UNSUPPORTED)))).canResume(job))

    val importState = DesktopImportReviewState(DesktopImportPhase.REVIEWING, review.preview)
    var importCurrent = true
    val importOwner = DesktopImportReviewCallbacks(isCurrent = { importCurrent })
    val importInput = DesktopImportReviewInput(importState, importOwner, library, DesktopSection.Library, 1, false)
    assertFalse(importInput.canConfirm()) // Resume cannot fall through the initial confirm command.
    importCurrent = false
    assertFalse(DesktopImportReviewAction().cancel(importInput, importInput, true))
}

internal fun assertSavedImportsPreparation(): Unit {
    val review = savedMappingFixture()
    val allOld = desktopMappingCandidateIds(review, "")
    val allNew = desktopMappingFileIds(review, "")
    assertEquals(2_046, allOld.size)
    assertEquals(2_048, allNew.size)
    assertEquals(listOf("old-2", "old-3"), allOld.take(2))
    assertEquals("new-2047", allNew.last())
    listOf(allOld, allNew).forEach { ids ->
        val reached = (0 until desktopBrowsePage(ids.size, 0).pages).flatMap { pageIndex ->
            desktopBrowseSlice(ids, desktopBrowsePage(ids.size, pageIndex)).also { assertTrue(it.size <= 100) }
        }
        assertEquals(ids, reached)
    }
    assertEquals(listOf("old-2047"), desktopMappingCandidateIds(review, "old-2047"))
    assertEquals(listOf("new-2047"), desktopMappingFileIds(review, "new-2047"))
    assertTrue(desktopBrowseSlice(emptyList<String>(), desktopBrowsePage(0, Int.MAX_VALUE)).isEmpty())
    val chosen = DesktopImportMappingDraft(candidateId = "old-2", selectionId = "new-2047")
    assertTrue(desktopMappingPairAvailable(review, chosen))
    assertFalse(desktopMappingPairAvailable(review, chosen.copy(candidateId = "old-0")))
    assertFalse(desktopMappingPairAvailable(review, chosen.copy(candidateQuery = "absent")))
    assertFalse(desktopMappingPairAvailable(review, chosen.copy(fileQuery = "absent")))
    val frozen = review.copy(revision = 1, mappings = listOf(DesktopImportCandidateMapping("old-2", "new-2047")))
    assertTrue(desktopImportMappingValid(frozen))
    assertFalse(desktopMappingPairAvailable(frozen, chosen.copy(candidateId = "old-3")))
    listOf(emptyList(), listOf(DesktopImportCandidateMapping("old-0", "new-0")),
        listOf(DesktopImportCandidateMapping("old-1", "new-0")), listOf(DesktopImportCandidateMapping("old-2", null)),
        listOf(DesktopImportCandidateMapping("unknown", "new-0")), listOf(DesktopImportCandidateMapping("old-2", "unknown")),
        listOf(DesktopImportCandidateMapping("old-2", "new-0"), DesktopImportCandidateMapping("old-2", "new-1")),
        listOf(DesktopImportCandidateMapping("old-2", "new-0"), DesktopImportCandidateMapping("old-3", "new-0")))
        .forEach { assertFalse(desktopImportMappingValid(review.copy(mappings = it))) }
    listOf(DesktopImportJobPhase.COMPLETED, DesktopImportJobPhase.UNSUPPORTED).forEach { phase ->
        assertFalse(desktopImportMappingValid(frozen.copy(preview = frozen.preview.copy(job = checkNotNull(frozen.preview.job).copy(phase = phase)))))
    }
    assertFalse(desktopImportMappingValid(frozen.copy(preview = frozen.preview.copy(errorCode = "import_job_conflict"))))
    assertTrue(desktopImportMappingValid(frozen.copy(preview = frozen.preview.copy(errorCode = "import_source_unreadable", failed = 1))))
    // Equal-looking labels and sizes still start with no assignments, including reversed file order.
    val reversed = review.copy(preview = review.preview.copy(files = review.preview.files.reversed()))
    assertTrue(reversed.mappings.isEmpty())
    assertFalse(desktopImportMappingValid(reversed))
    val replacement = chosen.copy(candidatePage = 1, revision = 1).copy(candidatePage = 0, revision = 2)
    assertNotSame(chosen, replacement)
    assertTrue(replacement.revision > chosen.revision)
    assertEquals(0, replacement.candidatePage)
    var currentDraft = chosen
    var currentScope = true
    var assignments = 0
    val retained = desktopImportMappingDraftGuard(chosen, { currentDraft }, { currentScope })
    val assign = { if (retained() && desktopMappingPairAvailable(review, chosen)) assignments += 1 }
    assign()
    assertEquals(1, assignments)
    listOf(chosen.copy(candidateQuery = "changed", revision = 1), chosen.copy(fileQuery = "changed", revision = 1),
        chosen.copy(candidatePage = 1, revision = 1), chosen.copy(filePage = 1, revision = 1),
        chosen.copy(candidateId = "old-3", revision = 1), chosen.copy(selectionId = "new-0", revision = 1), replacement).forEach {
        currentDraft = it
        assign()
        assertFalse(retained())
    }
    currentDraft = chosen
    currentScope = false
    assign()
    assertEquals(1, assignments)
    assertFalse(DesktopImportReferenceKey(review.preview) == DesktopImportReferenceKey(review.preview.copy()))
}

internal fun assertSavedImportsCopy(): Unit {
    val codes = listOf("import_job_mapping_required", "import_job_mapping_invalid", "import_job_conflict",
        "import_job_reselection_required", "import_job_missing", "import_job_completed", "import_job_history_unsupported",
        "import_job_limit", "import_job_permit_limit", "import_job_invalid", "import_job_reconciliation_required", "import_job_reselection_unavailable")
    DesktopLanguage.entries.forEach { language ->
        DesktopSavedImportText.entries.forEach { assertTrue(it.label(language).isNotBlank()) }
        codes.forEach { code ->
            val copy = desktopImportErrorText(code).label(language)
            assertTrue(copy.isNotBlank())
            assertFalse(copy.contains(code))
            assertFalse(copy.contains("private://"))
            assertTrue(desktopImportResultSummary(DesktopImportResult(0, 0, 0, code), null, language).contains(copy))
        }
        DesktopImportJobPhase.entries.forEach { assertTrue(desktopImportJobPhaseLabel(it).label(language).isNotBlank()) }
        val counts = desktopImportJobCounts(checkNotNull(savedMappingFixture().preview.job), language)
        assertTrue(counts.contains("2045"))
        assertTrue(counts.contains("1"))
        assertFalse(counts.contains("old-"))
    }
    assertTrue(DesktopSavedImportText.More.english.contains("empty"))
    assertTrue(DesktopSavedImportText.Instructions.english.contains("No matches are assigned automatically"))
    assertTrue(DesktopSavedImportText.Reconcile.english.contains("acknowledged"))
    assertTrue(DesktopSavedImportText.Reconcile.english.contains("Reopen"))
    assertEquals("?", desktopImportSafeId("private://path"))
    assertEquals("?", desktopImportSafeId("raw\nvalue"))
    assertEquals("job-a", desktopImportSafeId("job-a"))
}

private fun savedMappingFixture(): DesktopImportMappingReview {
    val candidates = List(2_048) { index -> DesktopImportJobCandidateState(DesktopImportJobCandidate("old-$index", "Same title", 100),
        outcome = when (index) { 0 -> DesktopImportJobOutcome.IMPORTED; 1 -> DesktopImportJobOutcome.DUPLICATE; 2 -> DesktopImportJobOutcome.FAILED; else -> null }) }
    val job = DesktopImportJob("job-a", 7, DesktopImportJobPhase.CANCELLED, candidates)
    val files = List(2_048) { DesktopImportPreviewFile("new-$it", "Same title", 100) }
    return DesktopImportMappingReview(DesktopImportPreview("preview-a", files, 204_800, jobId = job.id, job = job))
}
