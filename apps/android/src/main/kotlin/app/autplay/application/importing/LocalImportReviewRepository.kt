package app.autplay.application.importing

import android.content.ContentResolver
import androidx.core.net.toUri
import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LocalImportEntryEntity
import app.autplay.data.local.entity.LocalImportJobEntity
import app.autplay.data.local.entity.LocalMatchCandidateEntity
import app.autplay.data.local.entity.LocalMatchDecisionEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.text.Normalizer
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

enum class ImportSourceAvailability { AVAILABLE, MISSING, PERMISSION_REVOKED, METADATA_ONLY }

enum class ImportResolverState { REVIEW_REQUIRED, NO_MATCH, INTEGRITY_CONFLICT, DEFERRED_EVIDENCE }

enum class ImportReviewAction { ACCEPT, REJECT, KEEP_UNRESOLVED, CREATE_RECORDING }

enum class ImportJobControlAction { PAUSE, RESUME, CANCEL }

data class ImportRowInput(
    val sourceRowKey: String,
    val sourcePosition: Int,
    val rawTitle: String,
    val rawArtist: String,
    val rawAlbum: String? = null,
    val rawDurationMs: Long? = null,
    val rawProvenanceJson: String,
    val contentUri: String? = null,
    val persistedUriPermission: Boolean = false,
    val sourceAvailability: ImportSourceAvailability = ImportSourceAvailability.METADATA_ONLY,
    val fingerprintAlgorithm: String? = null,
    val fingerprintVersion: String? = null,
)

data class CreateLocalImportCommand(
    val serverProfileId: String = LEGACY_PROFILE_ID,
    val adapterId: String,
    val adapterVersion: String,
    val envelopeVersion: Int,
    val inputSha256: String,
    val inputDigestVerified: Boolean = true,
    val sourceUri: String? = null,
    val persistedUriPermission: Boolean = false,
    val sourceAvailability: ImportSourceAvailability = ImportSourceAvailability.METADATA_ONLY,
    val rows: List<ImportRowInput>,
    val nowMs: Long,
)

data class MatchCandidateInput(
    val localRecordingId: String,
    val rank: Int,
    val rawScore: Double?,
    val confidence: Double?,
    val evidenceTier: String,
    val titleSnapshot: String,
    val artistSnapshot: String,
    val versionSnapshot: String? = null,
    val durationMs: Long? = null,
    val featureEvidenceJson: String,
    val hardConflictsJson: String,
    val candidateOriginsJson: String,
    val extractorVersionsJson: String,
    val fingerprintAlgorithm: String? = null,
    val fingerprintVersion: String? = null,
)

data class RecordShadowEvaluationCommand(
    val importEntryId: String,
    val idempotencyKey: String,
    val resolverState: ImportResolverState,
    val evidenceMode: String,
    val matcherVersion: String,
    val explanationJson: String,
    val candidates: List<MatchCandidateInput>,
    val fingerprintAlgorithm: String? = null,
    val fingerprintVersion: String? = null,
    val nowMs: Long,
)

data class RecordImportReviewCommand(
    val importEntryId: String,
    val localChangeId: String,
    val idempotencyKey: String,
    val action: ImportReviewAction,
    val candidateId: String? = null,
    val createdRecordingId: String? = null,
    val predecessorDecisionId: String? = null,
    val nowMs: Long,
)

data class ImportReviewItem(
    val entry: LocalImportEntryEntity,
    val latestEvaluation: LocalMatchDecisionEntity?,
) {
    val effectiveState: String
        get() = if (entry.workflowState == "PENDING") latestEvaluation?.resolverState ?: "PENDING" else entry.workflowState
}

enum class LocalImportErrorCode {
    IMPORT_IDEMPOTENCY_CONFLICT,
    DECISION_IDEMPOTENCY_CONFLICT,
    IMPORT_ENTRY_NOT_FOUND,
    REVIEW_PREDECESSOR_NOT_FOUND,
    REVIEW_PREDECESSOR_CHANGED,
    REVIEW_ACTION_NOT_ALLOWED,
    REVIEW_CANDIDATE_NOT_FOUND,
    REVIEW_CANDIDATE_NOT_IMMEDIATE,
    REVIEW_RECORDING_ALREADY_EXISTS,
    IMPORT_JOB_NOT_FOUND,
    IMPORT_JOB_TRANSITION_NOT_ALLOWED,
    IMPORT_JOB_CONTROL_IDEMPOTENCY_CONFLICT,
}

class LocalImportException(val code: LocalImportErrorCode) : IllegalStateException(code.name)

fun interface ImportReviewFailureInjector {
    suspend fun afterDecisionInsert()

    companion object {
        val NONE = ImportReviewFailureInjector {}
    }
}

/**
 * Local-first P10 import and review projection.
 *
 * Evaluations are always SHADOW and can never produce AUTO_MATCH. A user review appends a
 * REVIEW_ACTION, updates the entry and UserTrackRef, and appends a local-only mutation outbox row
 * in one transaction. P10 does not change the frozen P04 wire vocabulary; later contract work may
 * explicitly materialize this outbox action.
 */
class LocalImportReviewRepository(
    private val database: AutPlayDatabase,
    private val reviewFailureInjector: ImportReviewFailureInjector = ImportReviewFailureInjector.NONE,
) {
    fun observeLatestJob(serverProfileId: String = LEGACY_PROFILE_ID): Flow<LocalImportJobEntity?> =
        database.importReviewDao().observeLatestJob(serverProfileId)

    fun observeEntries(jobId: String, limit: Int = MAX_IMPORT_ENTRIES): Flow<List<LocalImportEntryEntity>> {
        require(limit in 1..MAX_IMPORT_ENTRIES)
        return database.importReviewDao().observeEntries(jobId, limit)
    }

    fun observeReviewItems(jobId: String, limit: Int = MAX_IMPORT_ENTRIES): Flow<List<ImportReviewItem>> {
        require(limit in 1..MAX_IMPORT_ENTRIES)
        return combine(
            database.importReviewDao().observeEntries(jobId, limit),
            database.importReviewDao().observeDecisionsForJob(jobId),
        ) { entries, decisions ->
            entries.map { entry ->
                ImportReviewItem(
                    entry,
                    decisions.lastOrNull { it.importEntryId == entry.importEntryId && it.decisionKind == "EVALUATION" },
                )
            }
        }
    }

    suspend fun entriesOnce(jobId: String, limit: Int = MAX_IMPORT_ENTRIES): List<LocalImportEntryEntity> {
        require(limit in 1..MAX_IMPORT_ENTRIES)
        return database.importReviewDao().entries(jobId, limit)
    }

    fun observeCandidates(decisionId: String, limit: Int = MAX_CANDIDATES): Flow<List<LocalMatchCandidateEntity>> {
        require(limit in 1..MAX_CANDIDATES)
        return database.importReviewDao().observeCandidates(decisionId, limit)
    }

    suspend fun candidatesOnce(decisionId: String, limit: Int = MAX_CANDIDATES): List<LocalMatchCandidateEntity> {
        require(limit in 1..MAX_CANDIDATES)
        return database.importReviewDao().candidates(decisionId, limit)
    }

    suspend fun createOrResume(command: CreateLocalImportCommand): LocalImportJobEntity {
        validateImport(command)
        val jobId = stableUuid(
            "import-job",
            command.serverProfileId,
            command.adapterId,
            command.inputSha256,
            command.sourceUri ?: "no-source-uri",
        )
        val prepared = command.rows.map { row -> prepareRow(jobId, command.serverProfileId, row, command.nowMs) }
        return database.withWriteTransaction {
            val existing = database.importReviewDao().jobByIdentity(
                command.serverProfileId,
                command.adapterId,
                command.inputSha256,
                command.sourceUri,
            )
            if (existing != null) {
                val existingEntries = database.importReviewDao().entries(existing.importJobId, MAX_IMPORT_ENTRIES)
                val sameEnvelope = existing.importJobId == jobId &&
                    existing.adapterVersion == command.adapterVersion &&
                    existing.envelopeVersion == command.envelopeVersion &&
                    existing.inputDigestVerified == command.inputDigestVerified &&
                    existing.totalEntries == prepared.size &&
                    existingEntries.map { it.sourceRowKey to it.rowSha256 } ==
                    prepared.map { it.entry.sourceRowKey to it.entry.rowSha256 }
                if (!sameEnvelope) fail(LocalImportErrorCode.IMPORT_IDEMPOTENCY_CONFLICT)
                return@withWriteTransaction existing
            }

            val initialReport = report(command.rows.size, 0, 0, 0, command.rows.size, 0)
            val job = LocalImportJobEntity(
                importJobId = jobId,
                serverProfileId = command.serverProfileId,
                adapterId = command.adapterId,
                adapterVersion = command.adapterVersion,
                envelopeVersion = command.envelopeVersion,
                inputSha256 = command.inputSha256,
                inputDigestVerified = command.inputDigestVerified,
                sourceUri = command.sourceUri,
                persistedUriPermission = command.persistedUriPermission,
                sourceAvailability = command.sourceAvailability.name,
                state = "PENDING",
                checkpointPosition = 0,
                totalEntries = command.rows.size,
                reviewRequiredCount = 0,
                resolvedCount = 0,
                noMatchCount = 0,
                unresolvedCount = command.rows.size,
                failedCount = 0,
                reportJson = initialReport,
                createdAtMs = command.nowMs,
                updatedAtMs = command.nowMs,
                completedAtMs = null,
            )
            database.importReviewDao().insertJob(job)
            database.libraryDao().upsertTrackRefs(prepared.map { it.trackRef })
            database.importReviewDao().insertEntries(prepared.map { it.entry })
            job
        }
    }

    suspend fun recordShadowEvaluation(command: RecordShadowEvaluationCommand): LocalMatchDecisionEntity {
        validateEvaluation(command)
        val requestSha256 = evaluationHash(command)
        val decisionId = stableUuid("match-decision", command.importEntryId, command.idempotencyKey)
        return database.withWriteTransaction {
            database.importReviewDao().decisionByIdempotency(command.importEntryId, command.idempotencyKey)?.let { existing ->
                if (existing.requestSha256 != requestSha256) fail(LocalImportErrorCode.DECISION_IDEMPOTENCY_CONFLICT)
                return@withWriteTransaction existing
            }
            val entry = database.importReviewDao().entry(command.importEntryId)
                ?: fail(LocalImportErrorCode.IMPORT_ENTRY_NOT_FOUND)
            ensureMutableJob(entry.importJobId)
            if (entry.workflowState == "RESOLVED" || entry.workflowState == "MANUAL_UNRESOLVED") {
                fail(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED)
            }
            val candidates = command.candidates.map { candidate ->
                LocalMatchCandidateEntity(
                    candidateId = stableUuid("match-candidate", decisionId, candidate.rank.toString()),
                    decisionId = decisionId,
                    localRecordingId = candidate.localRecordingId,
                    rank = candidate.rank,
                    rawScore = candidate.rawScore,
                    confidence = candidate.confidence,
                    evidenceTier = candidate.evidenceTier,
                    titleSnapshot = candidate.titleSnapshot,
                    artistSnapshot = candidate.artistSnapshot,
                    versionSnapshot = candidate.versionSnapshot,
                    durationMs = candidate.durationMs,
                    featureEvidenceJson = candidate.featureEvidenceJson,
                    hardConflictsJson = candidate.hardConflictsJson,
                    candidateOriginsJson = candidate.candidateOriginsJson,
                    extractorVersionsJson = candidate.extractorVersionsJson,
                    fingerprintAlgorithm = candidate.fingerprintAlgorithm,
                    fingerprintVersion = candidate.fingerprintVersion,
                    createdAtMs = command.nowMs,
                )
            }
            val latestEvaluation = database.importReviewDao().latestEvaluation(entry.importEntryId)
            val shadowPredecessor = latestEvaluation?.takeIf { database.importReviewDao().successor(it.decisionId) == null }
            val decision = LocalMatchDecisionEntity(
                decisionId = decisionId,
                importEntryId = entry.importEntryId,
                decisionKind = "EVALUATION",
                executionMode = "SHADOW",
                resolverState = command.resolverState.name,
                reviewAction = null,
                selectedLocalRecordingId = null,
                reviewedCandidateId = null,
                supersedesDecisionId = shadowPredecessor?.decisionId,
                evidenceDecisionId = decisionId,
                candidateCount = candidates.size,
                topConfidence = candidates.firstOrNull()?.confidence,
                topTwoMargin = topTwoMargin(candidates),
                evidenceMode = command.evidenceMode,
                matcherVersion = command.matcherVersion,
                fingerprintAlgorithm = command.fingerprintAlgorithm,
                fingerprintVersion = command.fingerprintVersion,
                explanationJson = command.explanationJson,
                idempotencyKey = command.idempotencyKey,
                requestSha256 = requestSha256,
                createdAtMs = command.nowMs,
            )
            database.importReviewDao().insertDecision(decision)
            database.importReviewDao().insertCandidates(candidates)
            refreshJob(entry.importJobId, command.nowMs)
            decision
        }
    }

    suspend fun recordReview(command: RecordImportReviewCommand): LocalMatchDecisionEntity {
        validateReview(command)
        val requestSha256 = reviewHash(command)
        val decisionId = stableUuid("review-decision", command.importEntryId, command.idempotencyKey)
        return database.withWriteTransaction {
            database.importReviewDao().decisionByIdempotency(command.importEntryId, command.idempotencyKey)?.let { existing ->
                if (existing.requestSha256 != requestSha256) fail(LocalImportErrorCode.DECISION_IDEMPOTENCY_CONFLICT)
                return@withWriteTransaction existing
            }
            val entry = database.importReviewDao().entry(command.importEntryId)
                ?: fail(LocalImportErrorCode.IMPORT_ENTRY_NOT_FOUND)
            ensureMutableJob(entry.importJobId)
            val candidate = command.candidateId?.let { database.importReviewDao().candidate(it) }
            val latestEvaluation = database.importReviewDao().latestEvaluation(entry.importEntryId)
                ?: fail(LocalImportErrorCode.REVIEW_PREDECESSOR_NOT_FOUND)
            val predecessorId = command.predecessorDecisionId ?: candidate?.decisionId ?: latestEvaluation.decisionId
            val predecessor = database.importReviewDao().decision(predecessorId)
                ?: fail(LocalImportErrorCode.REVIEW_PREDECESSOR_NOT_FOUND)
            if (predecessor.decisionId != latestEvaluation.decisionId ||
                database.importReviewDao().successor(predecessor.decisionId) != null
            ) fail(LocalImportErrorCode.REVIEW_PREDECESSOR_CHANGED)
            validateReviewTransition(predecessor, command, candidate)

            val selectedRecordingId = when (command.action) {
                ImportReviewAction.ACCEPT -> candidate!!.localRecordingId
                ImportReviewAction.CREATE_RECORDING -> command.createdRecordingId!!
                ImportReviewAction.REJECT, ImportReviewAction.KEEP_UNRESOLVED -> null
            }
            if (command.action == ImportReviewAction.CREATE_RECORDING) {
                if (database.catalogProjectionDao().recording(selectedRecordingId!!) != null) {
                    fail(LocalImportErrorCode.REVIEW_RECORDING_ALREADY_EXISTS)
                }
                database.catalogProjectionDao().upsertRecordings(listOf(manualRecording(entry, selectedRecordingId, command.nowMs)))
            }
            val review = LocalMatchDecisionEntity(
                decisionId = decisionId,
                importEntryId = entry.importEntryId,
                decisionKind = "REVIEW_ACTION",
                executionMode = "APPLIED",
                resolverState = predecessor.resolverState,
                reviewAction = command.action.name,
                selectedLocalRecordingId = selectedRecordingId,
                reviewedCandidateId = candidate?.candidateId,
                supersedesDecisionId = predecessor.decisionId,
                evidenceDecisionId = predecessor.evidenceDecisionId,
                candidateCount = predecessor.candidateCount,
                topConfidence = predecessor.topConfidence,
                topTwoMargin = predecessor.topTwoMargin,
                evidenceMode = predecessor.evidenceMode,
                matcherVersion = predecessor.matcherVersion,
                fingerprintAlgorithm = predecessor.fingerprintAlgorithm,
                fingerprintVersion = predecessor.fingerprintVersion,
                explanationJson = reviewExplanation(command.action),
                idempotencyKey = command.idempotencyKey,
                requestSha256 = requestSha256,
                createdAtMs = command.nowMs,
            )
            database.importReviewDao().insertDecision(review)
            reviewFailureInjector.afterDecisionInsert()

            val workflowState = when (command.action) {
                ImportReviewAction.ACCEPT, ImportReviewAction.CREATE_RECORDING -> "RESOLVED"
                ImportReviewAction.REJECT -> "REVIEW_REQUIRED"
                ImportReviewAction.KEEP_UNRESOLVED -> "MANUAL_UNRESOLVED"
            }
            val advanced = database.importReviewDao().advanceEntry(
                entry.importEntryId,
                entry.latestDecisionId,
                workflowState,
                selectedRecordingId,
                review.decisionId,
                null,
                command.nowMs,
            )
            if (advanced != 1) fail(LocalImportErrorCode.REVIEW_PREDECESSOR_CHANGED)
            projectTrackRef(entry, workflowState, selectedRecordingId, candidate?.confidence, command.nowMs)
            database.journalDao().insertOutbox(
                LocalMutationOutboxEntity(
                    localChangeId = command.localChangeId,
                    eventType = REVIEW_EVENT_TYPE,
                    schemaVersion = 1,
                    aggregateType = "IMPORT_ENTRY",
                    aggregateLocalId = entry.importEntryId,
                    payloadJson = reviewOutboxPayload(command.action, review.decisionId, selectedRecordingId),
                    occurredAtMs = command.nowMs,
                    materializationState = "UNMATERIALIZED",
                ),
            )
            refreshJob(entry.importJobId, command.nowMs)
            review
        }
    }

    suspend fun retainSourceAvailability(
        importEntryId: String,
        availability: ImportSourceAvailability,
        persistedPermission: Boolean,
        nowMs: Long,
    ) {
        require(nowMs >= 0)
        database.withWriteTransaction {
            val entry = database.importReviewDao().entry(importEntryId)
                ?: fail(LocalImportErrorCode.IMPORT_ENTRY_NOT_FOUND)
            val job = database.importReviewDao().job(entry.importJobId)
                ?: fail(LocalImportErrorCode.IMPORT_JOB_NOT_FOUND)
            if (job.state == "CANCELLED") fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
            check(database.importReviewDao().updateSourceAvailability(
                importEntryId,
                availability.name,
                persistedPermission,
                nowMs,
            ) == 1)
        }
    }

    suspend fun controlJob(
        importJobId: String,
        localChangeId: String,
        action: ImportJobControlAction,
        nowMs: Long,
    ): LocalImportJobEntity = database.withWriteTransaction {
        require(nowMs >= 0)
        val current = database.importReviewDao().job(importJobId)
            ?: fail(LocalImportErrorCode.IMPORT_JOB_NOT_FOUND)
        val payload = jobControlPayload(action)
        database.journalDao().outbox(localChangeId)?.let { existing ->
            if (existing.aggregateLocalId != importJobId || existing.payloadJson != payload) {
                fail(LocalImportErrorCode.IMPORT_JOB_CONTROL_IDEMPOTENCY_CONFLICT)
            }
            return@withWriteTransaction current
        }
        val targetState = when (action) {
            ImportJobControlAction.PAUSE -> {
                if (current.state !in setOf("PENDING", "REVIEW_REQUIRED")) {
                    fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
                }
                "PAUSED"
            }
            ImportJobControlAction.RESUME -> {
                if (current.state != "PAUSED") fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
                "PENDING"
            }
            ImportJobControlAction.CANCEL -> {
                if (current.state in setOf("COMPLETED", "CANCELLED")) {
                    fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
                }
                "CANCELLED"
            }
        }
        if (database.importReviewDao().transitionJobState(
                importJobId,
                current.state,
                targetState,
                nowMs,
                if (targetState == "CANCELLED") nowMs else null,
            ) != 1
        ) fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
        database.journalDao().insertOutbox(
            LocalMutationOutboxEntity(
                localChangeId = localChangeId,
                eventType = JOB_CONTROL_EVENT_TYPE,
                schemaVersion = 1,
                aggregateType = "IMPORT_JOB",
                aggregateLocalId = importJobId,
                payloadJson = payload,
                occurredAtMs = nowMs,
                materializationState = "UNMATERIALIZED",
            ),
        )
        if (action == ImportJobControlAction.RESUME) refreshJob(importJobId, nowMs)
        database.importReviewDao().job(importJobId) ?: fail(LocalImportErrorCode.IMPORT_JOB_NOT_FOUND)
    }

    private suspend fun projectTrackRef(
        entry: LocalImportEntryEntity,
        workflowState: String,
        recordingId: String?,
        confidence: Double?,
        nowMs: Long,
    ) {
        val current = database.libraryDao().trackRef(entry.localUserTrackRefId)
            ?: fail(LocalImportErrorCode.IMPORT_ENTRY_NOT_FOUND)
        database.libraryDao().upsertTrackRef(
            current.copy(
                localRecordingId = recordingId,
                resolutionStatus = when (workflowState) {
                    "RESOLVED" -> "RESOLVED_MANUAL"
                    "MANUAL_UNRESOLVED" -> "MANUAL_UNRESOLVED"
                    else -> "REVIEW_REQUIRED"
                },
                resolutionConfidence = if (workflowState == "RESOLVED") confidence else null,
                syncState = "LOCAL_ONLY",
                updatedAtMs = nowMs,
            ),
        )
    }

    private suspend fun refreshJob(jobId: String, nowMs: Long) {
        val job = database.importReviewDao().job(jobId) ?: fail(LocalImportErrorCode.IMPORT_JOB_NOT_FOUND)
        if (job.state in setOf("PAUSED", "CANCELLED")) return
        val rows = database.importReviewDao().entries(jobId, MAX_IMPORT_ENTRIES)
        val effectiveStates = rows.map { row ->
            if (row.workflowState == "PENDING") {
                database.importReviewDao().latestEvaluation(row.importEntryId)?.resolverState ?: "PENDING"
            } else {
                row.workflowState
            }
        }
        val review = effectiveStates.count { it in BLOCKING_STATES }
        val resolved = effectiveStates.count { it == "RESOLVED" }
        val noMatch = effectiveStates.count { it == "NO_MATCH" }
        val unresolved = effectiveStates.count { it in UNRESOLVED_STATES }
        val failed = effectiveStates.count { it == "REJECTED" }
        val state = if (review > 0 || effectiveStates.any { it == "PENDING" }) "REVIEW_REQUIRED" else "COMPLETED"
        check(database.importReviewDao().updateJobSummary(
            jobId,
            state,
            effectiveStates.count { it != "PENDING" },
            review,
            resolved,
            noMatch,
            unresolved,
            failed,
            report(rows.size, review, resolved, noMatch, unresolved, failed),
            nowMs,
            if (state == "COMPLETED") nowMs else null,
        ) == 1)
    }

    private fun prepareRow(jobId: String, profileId: String, row: ImportRowInput, nowMs: Long): PreparedRow {
        val entryId = stableUuid("import-entry", jobId, row.sourceRowKey)
        val trackRefId = stableUuid("import-track-ref", jobId, row.sourceRowKey)
        val rowHash = sha256(
            framed(
                row.sourceRowKey,
                row.sourcePosition.toString(),
                row.rawTitle,
                row.rawArtist,
                row.rawAlbum.orEmpty(),
                row.rawDurationMs?.toString().orEmpty(),
                row.rawProvenanceJson,
                row.contentUri.orEmpty(),
                row.persistedUriPermission.toString(),
                row.sourceAvailability.name,
                row.fingerprintAlgorithm.orEmpty(),
                row.fingerprintVersion.orEmpty(),
            ),
        )
        return PreparedRow(
            UserTrackRefEntity(
                trackRefId,
                null,
                null,
                null,
                "UNRESOLVED",
                row.rawTitle,
                row.rawArtist,
                row.rawAlbum,
                row.rawDurationMs,
                null,
                "LOCAL_ONLY",
                null,
                0,
                nowMs,
                nowMs,
                null,
                profileId,
            ),
            LocalImportEntryEntity(
                entryId,
                jobId,
                row.sourceRowKey,
                row.sourcePosition,
                rowHash,
                row.rawTitle,
                row.rawArtist,
                row.rawAlbum,
                row.rawDurationMs,
                row.rawProvenanceJson,
                row.contentUri,
                row.persistedUriPermission,
                row.sourceAvailability.name,
                row.fingerprintAlgorithm,
                row.fingerprintVersion,
                trackRefId,
                "PENDING",
                null,
                null,
                null,
                nowMs,
                nowMs,
            ),
        )
    }

    private fun manualRecording(entry: LocalImportEntryEntity, recordingId: String, nowMs: Long) =
        RecordingProjectionEntity(
            localRecordingId = recordingId,
            serverRecordingId = null,
            redirectServerRecordingId = null,
            title = entry.rawTitle,
            normalizedTitle = normalized(entry.rawTitle),
            displayArtist = entry.rawArtist,
            normalizedArtist = normalized(entry.rawArtist),
            artistCreditJson = "{\"schema_version\":1,\"display_artist\":${jsonString(entry.rawArtist)}}",
            durationMs = entry.rawDurationMs,
            recordingKind = "LOCAL_MANUAL",
            versionText = null,
            explicitState = 0,
            artworkRef = null,
            catalogVersion = 0,
            projectionUpdatedAtMs = nowMs,
            isDeleted = false,
        )

    private fun validateImport(command: CreateLocalImportCommand) {
        require(command.adapterId.isValidToken() && command.adapterVersion.isValidToken())
        require(command.envelopeVersion > 0)
        require(command.inputSha256.matches(SHA256_REGEX))
        require(command.rows.size in 1..MAX_IMPORT_ENTRIES)
        require(command.nowMs >= 0)
        validateContentUri(command.sourceUri)
        require(command.rows.map { it.sourceRowKey }.toSet().size == command.rows.size)
        require(command.rows.map { it.sourcePosition }.toSet().size == command.rows.size)
        command.rows.forEach(::validateRow)
    }

    private suspend fun ensureMutableJob(jobId: String) {
        val state = database.importReviewDao().job(jobId)?.state
            ?: fail(LocalImportErrorCode.IMPORT_JOB_NOT_FOUND)
        if (state in setOf("PAUSED", "CANCELLED")) {
            fail(LocalImportErrorCode.IMPORT_JOB_TRANSITION_NOT_ALLOWED)
        }
    }

    private fun validateRow(row: ImportRowInput) {
        require(row.sourceRowKey.isNotBlank() && row.sourceRowKey.utf8Size() <= MAX_KEY_BYTES)
        require(row.sourcePosition in 0 until MAX_IMPORT_ENTRIES)
        require(row.rawTitle.isNotBlank() && row.rawTitle.utf8Size() <= MAX_TEXT_BYTES)
        require(row.rawArtist.isNotBlank() && row.rawArtist.utf8Size() <= MAX_TEXT_BYTES)
        require(row.rawAlbum == null || row.rawAlbum.utf8Size() <= MAX_TEXT_BYTES)
        require(row.rawDurationMs == null || row.rawDurationMs >= 0)
        validateJson(row.rawProvenanceJson, MAX_PROVENANCE_BYTES)
        validateContentUri(row.contentUri)
        requireVersionPair(row.fingerprintAlgorithm, row.fingerprintVersion)
    }

    private fun validateEvaluation(command: RecordShadowEvaluationCommand) {
        require(command.idempotencyKey.isValidToken())
        require(command.evidenceMode.isValidToken() && command.matcherVersion.isValidToken())
        require(command.candidates.size <= MAX_CANDIDATES)
        require(command.nowMs >= 0)
        validateJson(command.explanationJson, MAX_EXPLANATION_BYTES)
        requireVersionPair(command.fingerprintAlgorithm, command.fingerprintVersion)
        require(command.candidates.map { it.rank } == (1..command.candidates.size).toList())
        require(command.candidates.map { it.localRecordingId }.toSet().size == command.candidates.size)
        if (command.resolverState == ImportResolverState.REVIEW_REQUIRED) require(command.candidates.isNotEmpty())
        command.candidates.forEach { candidate ->
            require(candidate.rawScore == null || candidate.rawScore in 0.0..1.0)
            require(candidate.confidence == null || candidate.confidence in 0.0..1.0)
            require(candidate.titleSnapshot.utf8Size() <= MAX_TEXT_BYTES)
            require(candidate.artistSnapshot.utf8Size() <= MAX_TEXT_BYTES)
            listOf(
                candidate.featureEvidenceJson,
                candidate.hardConflictsJson,
                candidate.candidateOriginsJson,
                candidate.extractorVersionsJson,
            ).forEach { validateJson(it, MAX_EVIDENCE_BYTES) }
            requireVersionPair(candidate.fingerprintAlgorithm, candidate.fingerprintVersion)
        }
        if (command.resolverState == ImportResolverState.INTEGRITY_CONFLICT) {
            require(command.candidates.any { Json.parseToJsonElement(it.hardConflictsJson) is JsonArray && Json.parseToJsonElement(it.hardConflictsJson).let { value -> (value as JsonArray).isNotEmpty() } })
        }
    }

    private fun validateReview(command: RecordImportReviewCommand) {
        require(command.idempotencyKey.isValidToken())
        require(command.nowMs >= 0)
        when (command.action) {
            ImportReviewAction.ACCEPT, ImportReviewAction.REJECT -> {
                require(command.candidateId != null && command.createdRecordingId == null)
            }
            ImportReviewAction.KEEP_UNRESOLVED -> require(command.candidateId == null && command.createdRecordingId == null)
            ImportReviewAction.CREATE_RECORDING -> require(command.candidateId == null && command.createdRecordingId != null)
        }
    }

    private fun validateReviewTransition(
        predecessor: LocalMatchDecisionEntity,
        command: RecordImportReviewCommand,
        candidate: LocalMatchCandidateEntity?,
    ) {
        if (predecessor.resolverState == "INTEGRITY_CONFLICT" && command.action != ImportReviewAction.KEEP_UNRESOLVED) {
            fail(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED)
        }
        when (command.action) {
            ImportReviewAction.ACCEPT, ImportReviewAction.REJECT -> {
                if (predecessor.decisionKind != "EVALUATION" || predecessor.resolverState != "REVIEW_REQUIRED") {
                    fail(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED)
                }
                if (candidate == null) fail(LocalImportErrorCode.REVIEW_CANDIDATE_NOT_FOUND)
                if (candidate.decisionId != predecessor.decisionId) {
                    fail(LocalImportErrorCode.REVIEW_CANDIDATE_NOT_IMMEDIATE)
                }
            }
            ImportReviewAction.CREATE_RECORDING -> if (predecessor.resolverState !in CREATE_RECORDING_STATES) {
                fail(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED)
            }
            ImportReviewAction.KEEP_UNRESOLVED -> if (predecessor.resolverState == "AUTO_MATCH") {
                fail(LocalImportErrorCode.REVIEW_ACTION_NOT_ALLOWED)
            }
        }
    }

    private fun evaluationHash(command: RecordShadowEvaluationCommand): String = sha256(
        framed(
            command.importEntryId,
            command.resolverState.name,
            command.evidenceMode,
            command.matcherVersion,
            command.explanationJson,
            command.fingerprintAlgorithm.orEmpty(),
            command.fingerprintVersion.orEmpty(),
            *command.candidates.flatMap { candidate ->
                listOf(
                    candidate.localRecordingId,
                    candidate.rank.toString(),
                    candidate.rawScore?.toString().orEmpty(),
                    candidate.confidence?.toString().orEmpty(),
                    candidate.evidenceTier,
                    candidate.titleSnapshot,
                    candidate.artistSnapshot,
                    candidate.versionSnapshot.orEmpty(),
                    candidate.durationMs?.toString().orEmpty(),
                    candidate.featureEvidenceJson,
                    candidate.hardConflictsJson,
                    candidate.candidateOriginsJson,
                    candidate.extractorVersionsJson,
                    candidate.fingerprintAlgorithm.orEmpty(),
                    candidate.fingerprintVersion.orEmpty(),
                )
            }.toTypedArray(),
        ),
    )

    private fun reviewHash(command: RecordImportReviewCommand): String = sha256(
        framed(
            command.importEntryId,
            command.action.name,
            command.candidateId.orEmpty(),
            command.createdRecordingId.orEmpty(),
            command.predecessorDecisionId.orEmpty(),
        ),
    )

    private fun topTwoMargin(candidates: List<LocalMatchCandidateEntity>): Double? {
        val top = candidates.getOrNull(0)?.confidence ?: return null
        val second = candidates.getOrNull(1)?.confidence ?: return null
        return top - second
    }

    private fun report(total: Int, review: Int, resolved: Int, noMatch: Int, unresolved: Int, failed: Int): String =
        "{\"schema_version\":1,\"total\":$total,\"review_required\":$review,\"resolved\":$resolved," +
            "\"no_match\":$noMatch,\"unresolved\":$unresolved,\"failed\":$failed,\"redacted\":true}"

    private fun reviewExplanation(action: ImportReviewAction): String =
        "{\"schema_version\":1,\"manual_action\":\"${action.name}\",\"global_merge\":false}"

    private fun reviewOutboxPayload(action: ImportReviewAction, decisionId: String, recordingId: String?): String =
        "{\"decision_id\":${jsonString(decisionId)},\"manual_action\":${jsonString(action.name)}," +
            "\"recording_local_id\":${recordingId?.let(::jsonString) ?: "null"},\"schema_version\":1}"

    private fun jobControlPayload(action: ImportJobControlAction): String =
        "{\"action\":${jsonString(action.name)},\"schema_version\":1}"

    private fun validateContentUri(value: String?) {
        value ?: return
        val uri = value.toUri()
        require(uri.scheme == ContentResolver.SCHEME_CONTENT && !uri.authority.isNullOrBlank())
        require(value.utf8Size() <= MAX_URI_BYTES)
    }

    private fun requireVersionPair(algorithm: String?, version: String?) {
        require((algorithm == null) == (version == null))
        require(algorithm == null || algorithm.isValidToken())
        require(version == null || version.isValidToken())
    }

    private fun validateJson(value: String, maxBytes: Int) {
        require(value.utf8Size() <= maxBytes)
        Json.parseToJsonElement(value)
    }

    private fun String.isValidToken(): Boolean = isNotBlank() && utf8Size() <= MAX_KEY_BYTES

    private fun String.utf8Size(): Int = toByteArray(StandardCharsets.UTF_8).size

    private fun normalized(value: String): String = Normalizer.normalize(value, Normalizer.Form.NFKC)
        .lowercase(Locale.ROOT)
        .trim()

    private fun stableUuid(vararg parts: String): String = UUID.nameUUIDFromBytes(
        framed(*parts).toByteArray(StandardCharsets.UTF_8),
    ).toString()

    private fun framed(vararg parts: String): String = buildString {
        parts.forEach { part -> append(part.utf8Size()).append(':').append(part) }
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(StandardCharsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private fun jsonString(value: String): String = buildString {
        append('"')
        value.forEach { character ->
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (character.code < 0x20) append("\\u").append(character.code.toString(16).padStart(4, '0')) else append(character)
            }
        }
        append('"')
    }

    private fun fail(code: LocalImportErrorCode): Nothing = throw LocalImportException(code)

    private data class PreparedRow(val trackRef: UserTrackRefEntity, val entry: LocalImportEntryEntity)

    private companion object {
        const val MAX_IMPORT_ENTRIES = 10_000
        const val MAX_CANDIDATES = 100
        const val MAX_KEY_BYTES = 200
        const val MAX_TEXT_BYTES = 4_096
        const val MAX_URI_BYTES = 8_192
        const val MAX_PROVENANCE_BYTES = 128 * 1_024
        const val MAX_EVIDENCE_BYTES = 128 * 1_024
        const val MAX_EXPLANATION_BYTES = 128 * 1_024
        const val REVIEW_EVENT_TYPE = "IMPORT_REVIEW_ACTION_RECORDED"
        const val JOB_CONTROL_EVENT_TYPE = "IMPORT_JOB_CONTROL_RECORDED"
        val SHA256_REGEX = Regex("[0-9a-f]{64}")
        val BLOCKING_STATES = setOf("REVIEW_REQUIRED", "INTEGRITY_CONFLICT", "DEFERRED_EVIDENCE")
        val UNRESOLVED_STATES = setOf("PENDING", "MANUAL_UNRESOLVED", "INTEGRITY_CONFLICT", "DEFERRED_EVIDENCE")
        val CREATE_RECORDING_STATES = setOf("REVIEW_REQUIRED", "NO_MATCH", "DEFERRED_EVIDENCE")
    }
}

const val LEGACY_PROFILE_ID = "legacy-unscoped"

/** Creates one deterministic local-file envelope without placing the private URI in its report. */
fun singleUriImportCommand(
    serverProfileId: String,
    inspection: ContentUriInspection,
    persistedPermission: Boolean,
    nowMs: Long,
): CreateLocalImportCommand {
    val availability = when (inspection.status) {
        ContentUriStatus.AVAILABLE -> ImportSourceAvailability.AVAILABLE
        ContentUriStatus.MISSING -> ImportSourceAvailability.MISSING
        ContentUriStatus.PERMISSION_REVOKED -> ImportSourceAvailability.PERMISSION_REVOKED
        ContentUriStatus.INVALID -> ImportSourceAvailability.MISSING
    }
    val verifiedDigest = inspection.contentSha256
    val identity = "opaque-source\u0000${inspection.uri}"
    val digest = verifiedDigest ?: MessageDigest.getInstance("SHA-256")
        .digest(identity.toByteArray(StandardCharsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
    val provenance = buildJsonObject {
        put("schema_version", 1)
        put("source_kind", "ANDROID_CONTENT_URI")
        put("display_name", inspection.displayName?.let(::JsonPrimitive) ?: JsonNull)
        put("byte_size", inspection.byteSize?.let(::JsonPrimitive) ?: JsonNull)
        put("availability", availability.name)
        put("input_digest_verified", verifiedDigest != null)
    }.toString()
    return CreateLocalImportCommand(
        serverProfileId = serverProfileId,
        adapterId = "android-content-uri",
        adapterVersion = "1",
        envelopeVersion = 1,
        inputSha256 = digest,
        inputDigestVerified = verifiedDigest != null,
        sourceUri = inspection.uri,
        persistedUriPermission = persistedPermission,
        sourceAvailability = availability,
        rows = listOf(
            ImportRowInput(
                sourceRowKey = "row:0",
                sourcePosition = 0,
                rawTitle = inspection.displayName ?: "Imported track",
                rawArtist = "Unknown artist",
                rawProvenanceJson = provenance,
                contentUri = inspection.uri,
                persistedUriPermission = persistedPermission,
                sourceAvailability = availability,
            ),
        ),
        nowMs = nowMs,
    )
}
