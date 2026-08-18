package app.autplay.application.importing

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecordingProjectionEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LocalImportReviewRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val databaseName = "autplay-p10-import.db"

    @After
    fun clean() {
        context.deleteDatabase(databaseName)
    }

    @Test
    fun duplicateRowsRetryAndManualAcceptSurviveOfflineRestart() = runBlocking {
        var database = AutPlayDatabase.open(context, databaseName)
        var repository = LocalImportReviewRepository(database)
        val command = duplicateImport()

        val job = repository.createOrResume(command)
        val firstEntries = repository.entriesOnce(job.importJobId)
        val retried = repository.createOrResume(command.copy(nowMs = 2))
        assertEquals(job.importJobId, retried.importJobId)
        assertEquals(2, firstEntries.size)
        assertNotEquals(firstEntries[0].importEntryId, firstEntries[1].importEntryId)
        assertEquals("Duplicate", firstEntries[0].rawTitle)
        assertEquals("Duplicate", firstEntries[1].rawTitle)
        assertEquals(2, database.libraryDao().trackRefCount())

        repository.controlJob(job.importJobId, CHANGE_PAUSE, ImportJobControlAction.PAUSE, 2)
        database.close()
        database = AutPlayDatabase.open(context, databaseName)
        repository = LocalImportReviewRepository(database)
        assertEquals("PAUSED", database.importReviewDao().job(job.importJobId)?.state)
        repository.controlJob(job.importJobId, CHANGE_RESUME, ImportJobControlAction.RESUME, 2)
        assertEquals("REVIEW_REQUIRED", database.importReviewDao().job(job.importJobId)?.state)

        database.catalogProjectionDao().upsertRecordings(listOf(recording(RECORDING_A, "Studio"), recording(RECORDING_B, "Live")))
        val evaluation = repository.recordShadowEvaluation(
            evaluation(firstEntries.first().importEntryId, "eval-1", ImportResolverState.REVIEW_REQUIRED),
        )
        assertEquals("SHADOW", evaluation.executionMode)
        assertEquals("REVIEW_REQUIRED", evaluation.resolverState)
        val selected = repository.candidatesOnce(evaluation.decisionId)[1]
        val review = repository.recordReview(
            RecordImportReviewCommand(
                importEntryId = firstEntries.first().importEntryId,
                localChangeId = CHANGE_A,
                idempotencyKey = "review-accept-1",
                action = ImportReviewAction.ACCEPT,
                candidateId = selected.candidateId,
                nowMs = 3,
            ),
        )
        assertEquals("REVIEW_ACTION", review.decisionKind)
        assertEquals("ACCEPT", review.reviewAction)
        assertEquals("APPLIED", review.executionMode)
        assertEquals(RECORDING_B, review.selectedLocalRecordingId)

        database.close()
        database = AutPlayDatabase.open(context, databaseName)
        repository = LocalImportReviewRepository(database)
        val restarted = repository.entriesOnce(job.importJobId).first()
        assertEquals("RESOLVED", restarted.workflowState)
        assertEquals(RECORDING_B, restarted.selectedLocalRecordingId)
        assertEquals(RECORDING_B, database.libraryDao().trackRef(restarted.localUserTrackRefId)?.localRecordingId)
        assertEquals("RESOLVED_MANUAL", database.libraryDao().trackRef(restarted.localUserTrackRefId)?.resolutionStatus)
        assertEquals("IMPORT_REVIEW_ACTION_RECORDED", database.journalDao().outbox(CHANGE_A)?.eventType)
        assertEquals(2, repository.candidatesOnce(evaluation.decisionId).size)
        database.close()
    }

    @Test
    fun hardConflictAllowsOnlyKeepUnresolvedAndRetainsMissingSafIntent() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val job = repository.createOrResume(
            duplicateImport().copy(
                inputSha256 = "b".repeat(64),
                sourceAvailability = ImportSourceAvailability.MISSING,
                persistedUriPermission = false,
                rows = duplicateImport().rows.take(1).map {
                    it.copy(sourceAvailability = ImportSourceAvailability.MISSING, persistedUriPermission = false)
                },
            ),
        )
        val entry = repository.entriesOnce(job.importJobId).single()
        database.catalogProjectionDao().upsertRecordings(listOf(recording(RECORDING_A, "Live")))
        val evaluation = repository.recordShadowEvaluation(
            evaluation(entry.importEntryId, "eval-conflict", ImportResolverState.INTEGRITY_CONFLICT).copy(
                candidates = listOf(candidate(RECORDING_A, 1, 0.99, "[\"VERSION_MARKER_CONFLICT\"]")),
            ),
        )
        val candidate = repository.candidatesOnce(evaluation.decisionId).single()

        val error = try {
            repository.recordReview(
                RecordImportReviewCommand(entry.importEntryId, CHANGE_A, "invalid-accept", ImportReviewAction.ACCEPT, candidateId = candidate.candidateId, nowMs = 3),
            )
            null
        } catch (failure: LocalImportException) {
            failure.code
        }
        assertEquals(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED, error)

        repository.recordReview(
            RecordImportReviewCommand(entry.importEntryId, CHANGE_B, "keep-conflict", ImportReviewAction.KEEP_UNRESOLVED, nowMs = 4),
        )
        val retained = repository.entriesOnce(job.importJobId).single()
        assertEquals("MANUAL_UNRESOLVED", retained.workflowState)
        assertEquals("MISSING", retained.sourceAvailability)
        assertEquals("content://fixture.provider/missing", retained.contentUri)
        assertNull(retained.selectedLocalRecordingId)
        assertNull(database.libraryDao().trackRef(retained.localUserTrackRefId)?.localRecordingId)
        database.close()
    }

    @Test
    fun reviewFailureRollsBackDecisionProjectionAndStandaloneOutbox() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val job = repository.createOrResume(duplicateImport().copy(rows = duplicateImport().rows.take(1)))
        val entry = repository.entriesOnce(job.importJobId).single()
        database.catalogProjectionDao().upsertRecordings(listOf(recording(RECORDING_A, "Studio"), recording(RECORDING_B, "Live")))
        val evaluation = repository.recordShadowEvaluation(
            evaluation(entry.importEntryId, "eval-rollback", ImportResolverState.REVIEW_REQUIRED),
        )
        val candidate = repository.candidatesOnce(evaluation.decisionId).first()
        val failing = LocalImportReviewRepository(database, ImportReviewFailureInjector { error("injected") })

        runCatching {
            failing.recordReview(
                RecordImportReviewCommand(entry.importEntryId, CHANGE_A, "review-rollback", ImportReviewAction.ACCEPT, candidateId = candidate.candidateId, nowMs = 3),
            )
        }

        val unchanged = repository.entriesOnce(job.importJobId).single()
        assertNull(unchanged.latestDecisionId)
        assertEquals("PENDING", unchanged.workflowState)
        assertNull(database.importReviewDao().decisionByIdempotency(entry.importEntryId, "review-rollback"))
        assertNull(database.journalDao().outbox(CHANGE_A))
        database.close()
    }

    @Test
    fun shadowRescoreNeverAdvancesAppliedProjectionAndStartsSeparateLineageAfterReject() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val job = repository.createOrResume(duplicateImport().copy(rows = duplicateImport().rows.take(1)))
        val entry = repository.entriesOnce(job.importJobId).single()
        database.catalogProjectionDao().upsertRecordings(listOf(recording(RECORDING_A, "Studio"), recording(RECORDING_B, "Live")))
        val evaluation = repository.recordShadowEvaluation(evaluation(entry.importEntryId, "shadow-first", ImportResolverState.REVIEW_REQUIRED))
        assertNull(repository.entriesOnce(job.importJobId).single().latestDecisionId)
        assertEquals("PENDING", repository.entriesOnce(job.importJobId).single().workflowState)
        assertEquals(1, database.importReviewDao().job(job.importJobId)?.reviewRequiredCount)

        val candidate = repository.candidatesOnce(evaluation.decisionId).first()
        val rejected = repository.recordReview(
            RecordImportReviewCommand(entry.importEntryId, CHANGE_A, "reject-first", ImportReviewAction.REJECT, candidateId = candidate.candidateId, nowMs = 3),
        )
        val rescored = repository.recordShadowEvaluation(
            evaluation(entry.importEntryId, "shadow-rescore", ImportResolverState.REVIEW_REQUIRED).copy(nowMs = 4),
        )
        val projected = repository.entriesOnce(job.importJobId).single()
        assertEquals(rejected.decisionId, projected.latestDecisionId)
        assertEquals("REVIEW_REQUIRED", projected.workflowState)
        assertNull(rescored.supersedesDecisionId)
        database.close()
    }

    @Test
    fun pausedAndCancelledJobsCannotBeResurrectedByEntryMutations() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val job = repository.createOrResume(duplicateImport().copy(rows = duplicateImport().rows.take(1)))
        val entry = repository.entriesOnce(job.importJobId).single()
        database.catalogProjectionDao().upsertRecordings(listOf(recording(RECORDING_A, "Studio"), recording(RECORDING_B, "Live")))
        repository.controlJob(job.importJobId, CHANGE_PAUSE, ImportJobControlAction.PAUSE, 2)
        assertEquals(
            LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED,
            importFailure { repository.recordShadowEvaluation(evaluation(entry.importEntryId, "paused-eval", ImportResolverState.REVIEW_REQUIRED)) },
        )
        assertEquals("PAUSED", database.importReviewDao().job(job.importJobId)?.state)

        repository.controlJob(job.importJobId, CHANGE_RESUME, ImportJobControlAction.RESUME, 3)
        val evaluation = repository.recordShadowEvaluation(evaluation(entry.importEntryId, "active-eval", ImportResolverState.REVIEW_REQUIRED).copy(nowMs = 4))
        repository.controlJob(job.importJobId, CHANGE_B, ImportJobControlAction.CANCEL, 5)
        val candidate = repository.candidatesOnce(evaluation.decisionId).first()
        assertEquals(
            LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED,
            importFailure {
                repository.recordReview(
                    RecordImportReviewCommand(entry.importEntryId, "77777777-7777-4777-8777-777777777777", "cancelled-review", ImportReviewAction.ACCEPT, candidateId = candidate.candidateId, nowMs = 6),
                )
            },
        )
        assertEquals(
            LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED,
            importFailure { repository.retainSourceAvailability(entry.importEntryId, ImportSourceAvailability.AVAILABLE, true, 6) },
        )
        assertEquals("CANCELLED", database.importReviewDao().job(job.importJobId)?.state)
        database.close()
    }

    @Test
    fun recoverableProviderIoFailurePersistsUnavailableUriIntent() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val uri = "content://app.autplay.test.readable/audio"
        val inspection = ContentUriInspector(context.contentResolver) {
            object : java.io.InputStream() {
                override fun read(): Int = throw java.io.IOException("TEST_TRANSIENT_IO")
            }
        }.inspectWithDigest(uri)
        assertEquals(ContentUriStatus.MISSING, inspection.status)
        val job = repository.createOrResume(singleUriImportCommand(LEGACY_PROFILE_ID, inspection, false, 1))
        val entry = repository.entriesOnce(job.importJobId).single()
        assertEquals(uri, entry.contentUri)
        assertEquals("MISSING", entry.sourceAvailability)
        assertEquals(false, job.inputDigestVerified)
        database.close()
    }

    @Test
    fun identicalVerifiedBytesFromDistinctUrisPreserveBothSourceIntents() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val digest = "d".repeat(64)
        val firstUri = "content://fixture.provider/library/first"
        val secondUri = "content://fixture.provider/library/second"

        val first = repository.createOrResume(
            singleUriImportCommand(
                LEGACY_PROFILE_ID,
                ContentUriInspection(firstUri, ContentUriStatus.AVAILABLE, "copy.mp3", 128, digest),
                true,
                1,
            ),
        )
        val second = repository.createOrResume(
            singleUriImportCommand(
                LEGACY_PROFILE_ID,
                ContentUriInspection(secondUri, ContentUriStatus.AVAILABLE, "copy.mp3", 128, digest),
                true,
                2,
            ),
        )

        assertNotEquals(first.importJobId, second.importJobId)
        assertEquals(digest, first.inputSha256)
        assertEquals(digest, second.inputSha256)
        assertEquals(firstUri, repository.entriesOnce(first.importJobId).single().contentUri)
        assertEquals(secondUri, repository.entriesOnce(second.importJobId).single().contentUri)
        database.close()
    }

    @Test
    fun createRecordingFromNoMatchIsManualLocalProjectionWithoutGlobalMerge() = runBlocking {
        val database = AutPlayDatabase.open(context, databaseName)
        val repository = LocalImportReviewRepository(database)
        val job = repository.createOrResume(duplicateImport().copy(rows = duplicateImport().rows.take(1)))
        val entry = repository.entriesOnce(job.importJobId).single()
        val evaluation = repository.recordShadowEvaluation(
            RecordShadowEvaluationCommand(
                importEntryId = entry.importEntryId,
                idempotencyKey = "no-match-eval",
                resolverState = ImportResolverState.NO_MATCH,
                evidenceMode = "METADATA_ONLY",
                matcherVersion = "fixture-shadow/1",
                explanationJson = "{\"schema_version\":1,\"reason_code\":\"NO_CANDIDATES\"}",
                candidates = emptyList(),
                nowMs = 2,
            ),
        )
        val review = repository.recordReview(
            RecordImportReviewCommand(
                entry.importEntryId,
                CHANGE_A,
                "create-recording-review",
                ImportReviewAction.CREATE_RECORDING,
                createdRecordingId = RECORDING_A,
                predecessorDecisionId = evaluation.decisionId,
                nowMs = 3,
            ),
        )

        assertEquals("CREATE_RECORDING", review.reviewAction)
        assertEquals("RESOLVED", repository.entriesOnce(job.importJobId).single().workflowState)
        assertEquals("Duplicate", database.catalogProjectionDao().recording(RECORDING_A)?.title)
        assertEquals("{\"schema_version\":1,\"playlist\":\"a\"}", repository.entriesOnce(job.importJobId).single().rawProvenanceJson)
        assertTrue(review.explanationJson.contains("\"global_merge\":false"))
        database.close()
    }

    private suspend fun importFailure(block: suspend () -> Unit): LocalImportErrorCode? = try {
        block()
        null
    } catch (failure: LocalImportException) {
        failure.code
    }

    private fun duplicateImport() = CreateLocalImportCommand(
        adapterId = "fixture-json",
        adapterVersion = "1",
        envelopeVersion = 1,
        inputSha256 = "a".repeat(64),
        sourceUri = "content://fixture.provider/missing",
        sourceAvailability = ImportSourceAvailability.MISSING,
        rows = listOf(
            ImportRowInput("playlist-a:0", 0, "Duplicate", "Artist", rawProvenanceJson = "{\"schema_version\":1,\"playlist\":\"a\"}", contentUri = "content://fixture.provider/missing", sourceAvailability = ImportSourceAvailability.MISSING),
            ImportRowInput("playlist-b:0", 1, "Duplicate", "Artist", rawProvenanceJson = "{\"schema_version\":1,\"playlist\":\"b\"}", contentUri = "content://fixture.provider/missing", sourceAvailability = ImportSourceAvailability.MISSING),
        ),
        nowMs = 1,
    )

    private fun evaluation(entryId: String, key: String, state: ImportResolverState) = RecordShadowEvaluationCommand(
        importEntryId = entryId,
        idempotencyKey = key,
        resolverState = state,
        evidenceMode = "METADATA_ONLY",
        matcherVersion = "fixture-shadow/1",
        explanationJson = "{\"schema_version\":1,\"reason_code\":\"TOP_TWO_MARGIN\"}",
        candidates = listOf(
            candidate(RECORDING_A, 1, 0.91),
            candidate(RECORDING_B, 2, 0.90),
        ),
        nowMs = 2,
    )

    private fun candidate(recordingId: String, rank: Int, confidence: Double, conflicts: String = "[]") =
        MatchCandidateInput(
            localRecordingId = recordingId,
            rank = rank,
            rawScore = confidence,
            confidence = confidence,
            evidenceTier = "T1",
            titleSnapshot = if (recordingId == RECORDING_A) "Studio" else "Live",
            artistSnapshot = "Artist",
            featureEvidenceJson = "[{\"feature\":\"title_similarity\",\"present\":true,\"value\":1.0,\"extractor_version\":\"title/1\"}]",
            hardConflictsJson = conflicts,
            candidateOriginsJson = "[{\"generator\":\"metadata\",\"rank\":$rank}]",
            extractorVersionsJson = "{\"schema_version\":1,\"title\":\"title/1\"}",
        )

    private fun recording(id: String, title: String) = RecordingProjectionEntity(
        localRecordingId = id,
        serverRecordingId = null,
        redirectServerRecordingId = null,
        title = title,
        normalizedTitle = title.lowercase(),
        displayArtist = "Artist",
        normalizedArtist = "artist",
        artistCreditJson = "{\"schema_version\":1}",
        durationMs = 180_000,
        recordingKind = "SONG",
        versionText = null,
        explicitState = 0,
        artworkRef = null,
        catalogVersion = 0,
        projectionUpdatedAtMs = 1,
    )

    private companion object {
        const val RECORDING_A = "11111111-1111-4111-8111-111111111111"
        const val RECORDING_B = "22222222-2222-4222-8222-222222222222"
        const val CHANGE_A = "33333333-3333-4333-8333-333333333333"
        const val CHANGE_B = "44444444-4444-4444-8444-444444444444"
        const val CHANGE_PAUSE = "55555555-5555-4555-8555-555555555555"
        const val CHANGE_RESUME = "66666666-6666-4666-8666-666666666666"
    }
}
