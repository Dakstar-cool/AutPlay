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
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.time.Duration
import kotlinx.coroutines.flow.first

/** Owner/device-bound offline-pack refresh; cached Home remains available while this work runs. */
class RecommendationPackWorker(
    appContext: Context,
    parameters: WorkerParameters,
) : CoroutineWorker(appContext, parameters) {
    override suspend fun doWork(): Result {
        val expectedProfileId = inputData.getString(KEY_PROFILE_ID) ?: return Result.failure()
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        if (
            settings.activeServerProfileId?.value != expectedProfileId ||
            settings.activeUserId == null || settings.deviceId == null || settings.serverBaseUrl == null
        ) return Result.failure()
        val binding = ClientEventBinding(
            settings.activeUserId,
            settings.deviceId,
            settings.activeServerProfileId,
        )
        return runCatching {
            AutPlayRuntime.refreshRecommendationPack(
                applicationContext,
                binding,
                OfflineRecommendationRepository(AutPlayRuntime.database(applicationContext)),
                System.currentTimeMillis(),
            )
        }.fold(
            onSuccess = { Result.success() },
            onFailure = { if (runAttemptCount >= MAX_RETRIES) Result.failure() else Result.retry() },
        )
    }

    companion object {
        const val KEY_PROFILE_ID = "recommendation_profile_id"
        private const val MAX_RETRIES = 5
    }
}

object RecommendationPackWorkScheduler {
    fun enqueue(context: Context, profileId: String) {
        val request = OneTimeWorkRequestBuilder<RecommendationPackWorker>()
            .setInputData(Data.Builder().putString(RecommendationPackWorker.KEY_PROFILE_ID, profileId).build())
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(30))
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
            "recommendation-pack-$profileId",
            ExistingWorkPolicy.KEEP,
            request,
        )
    }
}
