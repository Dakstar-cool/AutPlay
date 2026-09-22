package app.autplay.work

import android.content.Context
import androidx.media3.common.util.UnstableApi
import androidx.work.*
import app.autplay.AutPlayRuntime
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncBindingInitializer
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import java.time.Duration
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first

@androidx.annotation.OptIn(UnstableApi::class)
class InternetMusicWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val profile = settings.activeServerProfileId ?: return Result.failure()
        if (inputData.getString("profile") != profile.value) return Result.failure()
        val user = settings.activeUserId ?: return Result.failure()
        val device = settings.deviceId ?: return Result.failure()
        val database = AutPlayRuntime.database(applicationContext)
        val cursor = database.syncDao().cursor(profile.value) ?: return Result.retry()
        val binding = ClientEventBinding(user, device, profile, LocalId(cursor.journalEpoch))
        return try {
            val server = AutPlayRuntime.serverFeatures(applicationContext, binding)
            val selected = server.selectInternetMusic(inputData.getString("search") ?: return Result.failure(),
                inputData.getString("candidate") ?: return Result.failure())
            val status = server.internetMusicStatus(selected.id)
            setProgress(workDataOf("state" to status.state))
            when (status.state) {
                "FAILED" -> Result.failure()
                "READY" -> {
                    val coordinator = AutPlayRuntime.syncCoordinator(applicationContext, binding)
                    coordinator.run(binding)
                    val serverRef = checkNotNull(status.refId)
                    var track = database.libraryDao().trackRefByServerId(profile.value, serverRef)
                    if (
                        track == null &&
                        database.syncDao().cursor(profile.value)?.bootstrapState == "READY"
                    ) {
                        SyncBindingInitializer(database).requestFullSnapshot(binding)
                        coordinator.run(binding)
                        track = database.libraryDao().trackRefByServerId(profile.value, serverRef)
                    }
                    track ?: return retryOrFail()
                    if (inputData.getBoolean("download", false)) DownloadIntentRepository(applicationContext, database).requestVaultDownload(
                        LocalId(track.localUserTrackRefId), profile, checkNotNull(status.variantId), DownloadStorageClass.USER_DOWNLOAD, System.currentTimeMillis(),
                    )
                    Result.success(workDataOf("ref" to track.localUserTrackRefId))
                }
                else -> retryOrFail()
            }
        } catch (error: CancellationException) { throw error
        } catch (_: Exception) { retryOrFail() }
    }
    private fun retryOrFail(): Result = if (runAttemptCount < 60) Result.retry() else Result.failure()
}

object InternetMusicWork {
    const val TAG = "internet-music-acquire"
    fun tag(search: String, candidate: String) = "$TAG:$search:$candidate"
    fun enqueue(context: Context, profile: String, search: String, candidate: String, download: Boolean) {
        WorkManager.getInstance(context).enqueueUniqueWork("internet-music-$profile-$search-$candidate-$download", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<InternetMusicWorker>().addTag(TAG).addTag(tag(search, candidate))
                .setInputData(workDataOf("profile" to profile, "search" to search, "candidate" to candidate, "download" to download))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, Duration.ofSeconds(15)).build())
    }
}
