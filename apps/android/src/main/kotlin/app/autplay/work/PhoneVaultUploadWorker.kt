package app.autplay.work

import android.content.Context
import androidx.work.*
import app.autplay.AutPlayRuntime
import app.autplay.application.server.ServerFeatureStateRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncBindingInitializer
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.time.Duration
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first

class PhoneVaultUploadWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val profile = settings.activeServerProfileId ?: return Result.failure()
        if (inputData.getString("profile") != profile.value) return Result.failure()
        val user = settings.activeUserId ?: return Result.failure()
        val device = settings.deviceId ?: return Result.failure()
        return try {
            val database = AutPlayRuntime.database(applicationContext)
            val initial = ClientEventBinding(user, device, profile)
            val cursor = SyncBindingInitializer(database).ensure(initial)
            val binding = initial.copy(journalEpoch = LocalId(cursor.journalEpoch))
            val local = database.localAudioDao().state(inputData.getString("audio_id") ?: return Result.failure()) ?: return Result.failure()
            var track = database.libraryDao().trackRef(local.localUserTrackRefId) ?: return Result.failure()
            if (track.serverUserTrackRefId == null) {
                AutPlayRuntime.syncCoordinator(applicationContext, binding).run(binding)
                track = database.libraryDao().trackRef(local.localUserTrackRefId) ?: return Result.failure()
            }
            val ref = track.serverUserTrackRefId ?: return retryOrFail()
            val server = AutPlayRuntime.serverFeatures(applicationContext, binding)
            val recording = server.prepareMusicUpload(ref)
            val intent = ServerFeatureStateRepository(database).enqueueVaultUpload(profile, local.localAudioStateId,
                recording, local.localSha256, local.byteSize, System.currentTimeMillis(), operationId = id.toString())
            when (intent.state) {
                "COMMITTED", "REUSED" -> {
                    server.publishMusicUpload(ref, checkNotNull(intent.serverUploadId))
                    AutPlayRuntime.syncCoordinator(applicationContext, binding).run(binding)
                    Result.success()
                }
                "FAILED", "QUARANTINED", "CANCELLED", "EXPIRED", "INGEST_POLLING_PAUSED" -> Result.failure()
                else -> { VaultUploadWorkScheduler.enqueue(applicationContext, intent.uploadIntentId); retryOrFail() }
            }
        } catch (error: CancellationException) { throw error
        } catch (_: Exception) { retryOrFail() }
    }
    private fun retryOrFail(): Result = if (runAttemptCount < 60) Result.retry() else Result.failure()
}

object PhoneVaultUploadWork {
    const val TAG = "phone-vault-upload"
    fun enqueue(context: Context, audioId: String, profile: String) {
        WorkManager.getInstance(context).enqueueUniqueWork("phone-vault-$profile-$audioId", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<PhoneVaultUploadWorker>().addTag(TAG).setInputData(workDataOf("audio_id" to audioId, "profile" to profile))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, Duration.ofSeconds(15)).build())
    }
}
