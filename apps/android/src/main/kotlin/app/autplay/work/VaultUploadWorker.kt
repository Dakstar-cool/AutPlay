package app.autplay.work

import android.content.Context
import android.net.Uri
import androidx.core.net.toUri
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.autplay.AutPlayRuntime
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.io.InputStream
import java.security.MessageDigest
import java.time.Duration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

/** Durable, resumable Vault upload. Work input contains only the local intent identifier. */
class VaultUploadWorker(
    appContext: Context,
    parameters: WorkerParameters,
) : CoroutineWorker(appContext, parameters) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val intentId = inputData.getString(KEY_INTENT_ID) ?: return@withContext Result.failure()
        val database = AutPlayRuntime.database(applicationContext)
        val dao = database.serverFeatureProjectionDao()
        var intent = dao.vaultUploadIntent(intentId) ?: return@withContext Result.success()
        if (intent.state in TERMINAL_STATES) return@withContext Result.success()

        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        if (settings.activeServerProfileId?.value != intent.serverProfileId || settings.serverBaseUrl == null) {
            dao.upsertVaultUploadIntent(intent.copy(state = "BLOCKED_PROFILE", lastErrorCode = "SERVER_PROFILE_NOT_ACTIVE", updatedAtMs = now()))
            return@withContext Result.failure()
        }
        val server = ServerFeatureRepository(
            settings.serverBaseUrl,
            settings.streamBaseUrl ?: settings.serverBaseUrl,
            ServerProfileId(intent.serverProfileId),
            AndroidKeystoreCredentialStore(applicationContext),
        )

        try {
            if (intent.state == "CANCEL_REQUESTED") {
                intent.serverUploadId?.let { server.cancelVaultUpload(it) }
                dao.upsertVaultUploadIntent(intent.copy(state = "CANCELLED", lastErrorCode = null, updatedAtMs = now()))
                return@withContext Result.success()
            }
            intent.serverUploadId?.let { uploadId ->
                val remote = server.vaultUploadStatus(uploadId)
                validateRemote(intent, remote)
                settleOrPoll(intent, remote)?.let { return@withContext it }
            }
            val local = database.localAudioDao().state(intent.localAudioStateId)
                ?: return@withContext terminalFailure(intent, "LOCAL_AUDIO_MISSING")
            val sourceUri = local.contentUri.toUri()
            val digest = digestSource { open(sourceUri) }
            intent = intent.copy(
                declaredSha256 = digest.sha256,
                expectedSize = digest.size,
                state = "UPLOADING",
                attemptCount = intent.attemptCount + 1,
                lastErrorCode = null,
                updatedAtMs = now(),
            )
            dao.upsertVaultUploadIntent(intent)

            val remote = if (intent.serverUploadId == null) {
                server.createVaultUpload(
                    intent.serverRecordingId,
                    digest.size,
                    digest.sha256,
                    "android:${intent.uploadIntentId}:${digest.sha256}",
                )
            } else {
                server.vaultUploadStatus(intent.serverUploadId)
            }
            check(remote.expectedSize == digest.size && remote.offset in 0..digest.size) {
                "VAULT_UPLOAD_STATE_INVALID"
            }
            if (intent.serverUploadId != null) check(intent.serverUploadId == remote.uploadId) {
                "VAULT_UPLOAD_ID_CONFLICT"
            }
            intent = intent.copy(serverUploadId = remote.uploadId, remoteOffset = remote.offset, updatedAtMs = now())
            dao.upsertVaultUploadIntent(intent)

            open(sourceUri).use { input ->
                skipFully(input, remote.offset)
                var offset = remote.offset
                var chunkIndex = offset / CHUNK_BYTES
                val buffer = ByteArray(CHUNK_BYTES)
                while (offset < digest.size) {
                    val current = dao.vaultUploadIntent(intentId) ?: return@withContext Result.success()
                    if (current.state == "CANCEL_REQUESTED") {
                        server.cancelVaultUpload(remote.uploadId)
                        dao.upsertVaultUploadIntent(current.copy(state = "CANCELLED", updatedAtMs = now()))
                        return@withContext Result.success()
                    }
                    val wanted = minOf(buffer.size.toLong(), digest.size - offset).toInt()
                    val count = readFullyOrEof(input, buffer, wanted)
                    check(count == wanted) { "VAULT_UPLOAD_SOURCE_CHANGED" }
                    val chunk = buffer.copyOf(count)
                    val next = server.appendVaultUpload(remote.uploadId, offset, chunkIndex, chunk)
                    check(next == offset + count) { "VAULT_UPLOAD_OFFSET_INVALID" }
                    offset = next
                    chunkIndex += 1
                    intent = intent.copy(remoteOffset = offset, state = "UPLOADING", updatedAtMs = now())
                    dao.upsertVaultUploadIntent(intent)
                }
                check(input.read() == -1) { "VAULT_UPLOAD_SOURCE_CHANGED" }
            }
            val completed = server.completeVaultUpload(remote.uploadId)
            check(completed.offset == digest.size) { "VAULT_UPLOAD_INCOMPLETE" }
            settleOrPoll(intent, completed) ?: error("VAULT_UPLOAD_STATE_INVALID")
        } catch (error: Exception) {
            val code = stableErrorCode(error)
            val current = dao.vaultUploadIntent(intentId) ?: intent
            if (runAttemptCount >= MAX_RETRIES || code in NON_RETRYABLE_ERRORS) {
                terminalFailure(current, code)
            } else {
                dao.upsertVaultUploadIntent(
                    current.copy(state = "RETRY", attemptCount = current.attemptCount + 1, lastErrorCode = code, updatedAtMs = now()),
                )
                Result.retry()
            }
        }
    }

    private suspend fun terminalFailure(intent: VaultUploadIntentEntity, code: String): Result {
        AutPlayRuntime.database(applicationContext).serverFeatureProjectionDao().upsertVaultUploadIntent(
            intent.copy(state = "FAILED", lastErrorCode = code, updatedAtMs = now()),
        )
        return Result.failure()
    }

    private fun validateRemote(
        intent: VaultUploadIntentEntity,
        remote: app.autplay.application.server.VaultUploadResult,
    ) {
        check(remote.uploadId == intent.serverUploadId) { "VAULT_UPLOAD_ID_CONFLICT" }
        check(intent.expectedSize > 0 && remote.expectedSize == intent.expectedSize) { "VAULT_UPLOAD_STATE_INVALID" }
        check(remote.offset in 0..remote.expectedSize) { "VAULT_UPLOAD_STATE_INVALID" }
    }

    private suspend fun settleOrPoll(
        intent: VaultUploadIntentEntity,
        remote: app.autplay.application.server.VaultUploadResult,
    ): Result? {
        val state = remote.state.uppercase()
        val outcome = vaultUploadOutcome(state)
        if (outcome == VaultUploadOutcome.OPEN) return null
        val paused = outcome == VaultUploadOutcome.POLL && runAttemptCount >= MAX_INGEST_POLLS
        val localState = if (paused) "INGEST_POLLING_PAUSED" else state
        val errorCode = when {
            paused -> "VAULT_INGEST_POLLING_PAUSED"
            outcome == VaultUploadOutcome.FAILURE -> "VAULT_INGEST_$state"
            else -> null
        }
        AutPlayRuntime.database(applicationContext).serverFeatureProjectionDao().upsertVaultUploadIntent(
            intent.copy(
                serverUploadId = remote.uploadId,
                remoteOffset = remote.offset,
                expectedSize = remote.expectedSize,
                state = localState,
                lastErrorCode = errorCode,
                updatedAtMs = now(),
            ),
        )
        return when {
            paused || outcome == VaultUploadOutcome.SUCCESS -> Result.success()
            outcome == VaultUploadOutcome.FAILURE -> Result.failure()
            else -> Result.retry()
        }
    }

    private fun open(uri: Uri): InputStream = applicationContext.contentResolver.openInputStream(uri)
        ?: error("LOCAL_AUDIO_UNAVAILABLE")

    private fun digestSource(openSource: () -> InputStream): SourceDigest {
        val digest = MessageDigest.getInstance("SHA-256")
        var size = 0L
        openSource().use { input ->
            val buffer = ByteArray(64 * 1_024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                size += count
                require(size <= MAX_UPLOAD_BYTES) { "VAULT_UPLOAD_TOO_LARGE" }
                digest.update(buffer, 0, count)
            }
        }
        require(size > 0) { "VAULT_UPLOAD_EMPTY" }
        return SourceDigest(size, digest.digest().joinToString("") { "%02x".format(it) })
    }

    private fun skipFully(input: InputStream, count: Long) {
        var remaining = count
        while (remaining > 0) {
            val skipped = input.skip(remaining)
            if (skipped > 0) remaining -= skipped else {
                check(input.read() >= 0) { "VAULT_UPLOAD_SOURCE_CHANGED" }
                remaining -= 1
            }
        }
    }

    private fun readFullyOrEof(input: InputStream, buffer: ByteArray, length: Int): Int {
        var total = 0
        while (total < length) {
            val count = input.read(buffer, total, length - total)
            if (count < 0) break
            total += count
        }
        return total
    }

    private fun stableErrorCode(error: Exception): String {
        val value = error.message
        return if (value != null && STABLE_CODE.matches(value)) value.take(100) else error::class.java.simpleName.uppercase()
    }

    private fun now(): Long = System.currentTimeMillis()
    private data class SourceDigest(val size: Long, val sha256: String)

    companion object {
        const val KEY_INTENT_ID = "vault_upload_intent_id"
        private const val CHUNK_BYTES = 1 * 1_024 * 1_024
        private const val MAX_RETRIES = 5
        private const val MAX_INGEST_POLLS = 12
        private const val MAX_UPLOAD_BYTES = 2L * 1_024 * 1_024 * 1_024
        private val TERMINAL_STATES = setOf(
            "COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED", "EXPIRED", "INGEST_POLLING_PAUSED",
        )
        private val NON_RETRYABLE_ERRORS = setOf(
            "LOCAL_AUDIO_MISSING", "LOCAL_AUDIO_UNAVAILABLE", "VAULT_UPLOAD_EMPTY", "VAULT_UPLOAD_TOO_LARGE",
            "VAULT_UPLOAD_SOURCE_CHANGED", "VAULT_UPLOAD_STATE_INVALID", "VAULT_UPLOAD_ID_CONFLICT",
        )
        private val STABLE_CODE = Regex("[A-Z][A-Z0-9_]{2,99}")
    }
}

internal enum class VaultUploadOutcome { OPEN, POLL, SUCCESS, FAILURE }

internal fun vaultUploadOutcome(serverState: String): VaultUploadOutcome = when (serverState.uppercase()) {
    "OPEN" -> VaultUploadOutcome.OPEN
    "SEALED", "PROCESSING", "COMMIT_PREPARED" -> VaultUploadOutcome.POLL
    "COMMITTED", "REUSED" -> VaultUploadOutcome.SUCCESS
    "QUARANTINED", "FAILED", "CANCELLED", "EXPIRED" -> VaultUploadOutcome.FAILURE
    else -> throw IllegalStateException("VAULT_UPLOAD_STATE_INVALID")
}

object VaultUploadWorkScheduler {
    fun enqueue(context: Context, intentId: String) {
        val request = OneTimeWorkRequestBuilder<VaultUploadWorker>()
            .setInputData(Data.Builder().putString(VaultUploadWorker.KEY_INTENT_ID, intentId).build())
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(30))
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            "vault-upload-$intentId",
            ExistingWorkPolicy.KEEP,
            request,
        )
    }
}
