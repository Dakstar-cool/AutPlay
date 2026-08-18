package app.autplay.work

import android.content.Context
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
import app.autplay.application.server.ServerFeatureStateRepository
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.time.Duration
import kotlinx.coroutines.flow.first

/** Bounded status polling for a durable remote import reference. */
class RemoteImportStatusWorker(
    appContext: Context,
    parameters: WorkerParameters,
) : CoroutineWorker(appContext, parameters) {
    override suspend fun doWork(): Result {
        val importJobId = inputData.getString(KEY_IMPORT_JOB_ID) ?: return Result.failure()
        val database = AutPlayRuntime.database(applicationContext)
        val dao = database.serverFeatureProjectionDao()
        val local = dao.remoteImportJobById(importJobId) ?: return Result.success()
        if (local.state in TERMINAL_STATES) return Result.success()
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        if (settings.activeServerProfileId?.value != local.serverProfileId || settings.serverBaseUrl == null) {
            dao.upsertRemoteImportJob(local.copy(lastErrorCode = "SERVER_PROFILE_NOT_ACTIVE", updatedAtMs = now()))
            return Result.failure()
        }
        return try {
            val server = ServerFeatureRepository(
                settings.serverBaseUrl,
                settings.streamBaseUrl ?: settings.serverBaseUrl,
                ServerProfileId(local.serverProfileId),
                AndroidKeystoreCredentialStore(applicationContext),
            )
            val report = server.importReport(importJobId)
            ServerFeatureStateRepository(database).recordImportReport(
                ServerProfileId(local.serverProfileId), report, now(),
            )
            if (report.state in TERMINAL_STATES) Result.success()
            else if (runAttemptCount >= MAX_POLLS) {
                val updated = dao.remoteImportJob(local.serverProfileId, importJobId)
                updated?.let { dao.upsertRemoteImportJob(it.copy(lastErrorCode = "IMPORT_POLLING_PAUSED", updatedAtMs = now())) }
                Result.success()
            } else Result.retry()
        } catch (_: Exception) {
            if (runAttemptCount >= MAX_POLLS) {
                dao.upsertRemoteImportJob(local.copy(lastErrorCode = "IMPORT_STATUS_UNAVAILABLE", updatedAtMs = now()))
                Result.failure()
            } else Result.retry()
        }
    }

    private fun now(): Long = System.currentTimeMillis()

    companion object {
        const val KEY_IMPORT_JOB_ID = "remote_import_job_id"
        private const val MAX_POLLS = 12
        private val TERMINAL_STATES = setOf("COMPLETED", "FAILED", "CANCELLED")
    }
}

object RemoteImportWorkScheduler {
    fun enqueue(context: Context, importJobId: String) {
        val request = OneTimeWorkRequestBuilder<RemoteImportStatusWorker>()
            .setInputData(Data.Builder().putString(RemoteImportStatusWorker.KEY_IMPORT_JOB_ID, importJobId).build())
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(30))
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            "remote-import-$importJobId",
            ExistingWorkPolicy.KEEP,
            request,
        )
    }
}

internal fun shouldScheduleRemoteImport(state: String, lastErrorCode: String?): Boolean =
    state !in setOf("COMPLETED", "FAILED", "CANCELLED") &&
        lastErrorCode !in setOf("IMPORT_POLLING_PAUSED", "IMPORT_STATUS_UNAVAILABLE")
