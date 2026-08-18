package app.autplay.application.server

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecommendationResponseSnapshotEntity
import app.autplay.data.local.entity.RemoteImportJobProjectionEntity
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.flow.Flow

/** Durable references/status only; server response bodies and source URIs are not duplicated here. */
class ServerFeatureStateRepository(private val database: AutPlayDatabase) {
    private val dao = database.serverFeatureProjectionDao()

    fun observeRemoteImports(profileId: ServerProfileId, limit: Int = 20): Flow<List<RemoteImportJobProjectionEntity>> {
        require(limit in 1..100)
        return dao.observeRemoteImportJobs(profileId.value, limit)
    }

    fun observeVaultUploads(profileId: ServerProfileId, limit: Int = 20): Flow<List<VaultUploadIntentEntity>> {
        require(limit in 1..100)
        return dao.observeVaultUploadIntents(profileId.value, limit)
    }

    suspend fun recordImportStart(
        profileId: ServerProfileId,
        start: RemoteImportStart,
        nowMs: Long,
    ) {
        dao.upsertRemoteImportJob(
            RemoteImportJobProjectionEntity(
                serverProfileId = profileId.value,
                importJobId = start.importJobId,
                deliveryJobId = start.deliveryJobId,
                state = "ACCEPTED",
                progressCurrent = 0,
                progressTotal = 0,
                reviewRequiredCount = 0,
                resolvedCount = 0,
                noMatchCount = 0,
                unresolvedCount = 0,
                failedCount = 0,
                lastErrorCode = null,
                updatedAtMs = nowMs,
            ),
        )
    }

    suspend fun recordImportReport(
        profileId: ServerProfileId,
        report: RemoteImportReport,
        nowMs: Long,
    ) {
        val current = dao.remoteImportJob(profileId.value, report.importJobId)
        val counts = report.counts.mapKeys { (key, _) -> key.uppercase() }
        dao.upsertRemoteImportJob(
            RemoteImportJobProjectionEntity(
                serverProfileId = profileId.value,
                importJobId = report.importJobId,
                deliveryJobId = current?.deliveryJobId,
                state = report.state,
                progressCurrent = report.progressCurrent,
                progressTotal = report.progressTotal,
                reviewRequiredCount = counts["REVIEW_REQUIRED"] ?: 0,
                resolvedCount = (counts["AUTO_MATCH"] ?: 0) + (counts["MANUAL_MATCH"] ?: 0),
                noMatchCount = (counts["NO_MATCH"] ?: 0) + (counts["DEFERRED_EVIDENCE"] ?: 0),
                unresolvedCount = counts["MANUAL_UNRESOLVED"] ?: 0,
                failedCount = (counts["REJECTED"] ?: 0) + (counts["FAILED"] ?: 0),
                lastErrorCode = report.entries.firstNotNullOfOrNull(RemoteImportEntry::errorCode),
                updatedAtMs = nowMs,
            ),
        )
    }

    suspend fun enqueueVaultUpload(
        profileId: ServerProfileId,
        localAudioStateId: String,
        serverRecordingId: String,
        knownSha256: ByteArray?,
        knownSize: Long?,
        nowMs: Long,
    ): VaultUploadIntentEntity {
        val intentId = UUID.nameUUIDFromBytes(
            "vault-upload:${profileId.value}:$localAudioStateId:$serverRecordingId".toByteArray(StandardCharsets.UTF_8),
        ).toString()
        val row = VaultUploadIntentEntity(
            uploadIntentId = intentId,
            serverProfileId = profileId.value,
            localAudioStateId = localAudioStateId,
            serverRecordingId = serverRecordingId,
            declaredSha256 = knownSha256?.toHex() ?: ZERO_SHA256,
            expectedSize = knownSize ?: 0L,
            serverUploadId = null,
            remoteOffset = 0,
            state = "QUEUED",
            attemptCount = 0,
            lastErrorCode = null,
            createdAtMs = nowMs,
            updatedAtMs = nowMs,
        )
        dao.upsertVaultUploadIntent(row)
        return row
    }

    suspend fun cancelVaultUpload(intentId: String, nowMs: Long): VaultUploadIntentEntity? {
        val current = dao.vaultUploadIntent(intentId) ?: return null
        val updated = current.copy(state = "CANCEL_REQUESTED", updatedAtMs = nowMs)
        dao.upsertVaultUploadIntent(updated)
        return updated
    }

    suspend fun recordRecommendation(
        profileId: ServerProfileId,
        result: ServerRecommendationResult,
        nowMs: Long,
    ) {
        val stable = buildString {
            append(result.requestId).append('\n').append(result.replay)
            result.items.forEach { item ->
                append('\n').append(item.recordingId).append(':').append(item.sourceRank)
                    .append(':').append(item.score).append(':').append(item.reasonCode).append(':').append(item.section)
            }
        }.toByteArray(StandardCharsets.UTF_8)
        dao.upsertRecommendationResponseSnapshot(
            RecommendationResponseSnapshotEntity(
                serverProfileId = profileId.value,
                recommendationRequestId = result.requestId,
                replay = result.replay,
                itemCount = result.items.size,
                responseSha256 = MessageDigest.getInstance("SHA-256").digest(stable).toHex(),
                receivedAtMs = nowMs,
            ),
        )
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    private companion object {
        const val ZERO_SHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
    }
}
