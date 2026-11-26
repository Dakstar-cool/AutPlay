package app.autplay.work

import android.content.Context
import androidx.work.*
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.application.server.localTarget
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.download.CollectionDownloadStore
import app.autplay.AutPlayRuntime
import app.autplay.application.server.ServerFeatureHttpException
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.time.Duration
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.*

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class CatalogDownloadWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    private val batches = CollectionDownloadStore(context)
    override suspend fun doWork(): Result {
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val profile = settings.activeServerProfileId ?: return Result.failure()
        val user = settings.activeUserId ?: return Result.failure()
        val device = settings.deviceId ?: return Result.failure()
        if (profile.value != inputData.getString("profile") || user.value != inputData.getString("user")) return Result.failure()
        val operation = inputData.getString("operation") ?: return Result.failure()
        val kind = inputData.getString("kind") ?: return Result.failure()
        val id = inputData.getString("id") ?: return Result.failure()
        val start = inputData.getBoolean("start", false)
        return try {
            val binding = ClientEventBinding(user, device, profile)
            if (kind == "playlist" || kind == "library_release") return downloadLibrary(binding, operation)
            val server = AutPlayRuntime.serverFeatures(applicationContext, binding)
            if (kind == "library_artist") return Result.failure()
            var state = server.catalogDownloadPreview(operation, kind, id, inputData.getBoolean("alternatives", false))
            var lastProgress: CatalogDownloadProgress? = null
            repeat(45) {
                val progress = CatalogDownloadProgress.from(state)
                if (progress != null && progress != lastProgress) {
                    setProgress(progress.asData())
                    lastProgress = progress
                }
                when (state["phase"]?.jsonPrimitive?.content) {
                    "preview" -> {
                        if (!start) return Result.success()
                        checkBinding(binding)
                        state = server.catalogDownload(operation, "start")
                        return@repeat
                    }
                    "complete" -> {
                        if (start) {
                            val summary = requireNotNull(progress)
                            val result = if (kind == "artist") {
                                batches.finish(operation, emptySet(), summary.saved, summary.unavailable)
                                Result.success()
                            } else downloadFiles(binding, server, operation, summary.unavailable)
                            AutPlayRuntime.database(applicationContext).catalogProjectionDao()
                                .expireVaultPages(profile.value, "${user.value}:RELEASE:")
                            AutPlayRuntime.syncScheduler(applicationContext).enqueue(DeferredWorkRequest(
                                DeferredWorkKind.SYNC, DeferredWorkSubject.Device(device), profile))
                            return result
                        }
                        return Result.success()
                    }
                    "downloading" -> if (state["counts"]?.jsonObject?.get("available")?.jsonPrimitive?.int == 0) delay(4_000)
                }
                if (applicationNonSecretSettingsStore(applicationContext).settings.first().let {
                    it.activeServerProfileId != profile || it.activeUserId != user
                }) return Result.failure()
                state = server.catalogDownload(operation, "advance")
            }
            Result.retry()
        } catch (cancelled: CancellationException) { throw cancelled
        } catch (error: ServerFeatureHttpException) {
            if (error.status == 429 || error.status >= 500 || error.retryable == true) Result.retry()
            else Result.failure(workDataOf("error" to error.errorCode))
        } catch (_: java.io.IOException) { Result.retry() }
    }

    private suspend fun checkBinding(binding: ClientEventBinding) {
        val active = applicationNonSecretSettingsStore(applicationContext).settings.first()
        check(active.activeServerProfileId == binding.serverProfileId && active.activeUserId == binding.userId &&
            active.deviceId == binding.deviceId) { "DOWNLOAD_PROFILE_MISMATCH" }
    }

    private suspend fun downloadLibrary(binding: ClientEventBinding, operation: String): Result {
        val db = AutPlayRuntime.database(applicationContext)
        val repository = DownloadIntentRepository(applicationContext, db)
        val requested = batches.requested(operation)
        val admitted = mutableSetOf<String>()
        val profile = binding.serverProfileId.value
        for (page in requested.chunked(500)) {
            val offline = db.searchDao().offlineTrackIds(profile, page, permanentOnly = true).first().toSet()
            val owned = db.libraryDao().trackRefs(page, page.size).filter { it.serverProfileId == profile && it.deletedAtMs == null }
            for (track in owned) {
                checkBinding(binding)
                val ref = track.localUserTrackRefId
                if (ref !in offline) {
                    try {
                        repository.requestPreferredVaultDownload(LocalId(ref), binding.serverProfileId,
                            DownloadStorageClass.USER_DOWNLOAD, System.currentTimeMillis())
                    } catch (e: ServerFeatureHttpException) {
                        if (e.status != 404 && e.status != 410) throw e
                        continue
                    } catch (e: IllegalStateException) {
                        if (e.message != "VAULT_VARIANT_UNKNOWN") throw e
                        continue
                    }
                }
                admitted += ref
            }
        }
        batches.finish(operation, admitted, 0, requested.size - admitted.size + batches.missing(operation))
        return Result.success()
    }

    private suspend fun downloadFiles(binding: ClientEventBinding, server: ServerFeatureRepository, operation: String, serverUnavailable: Int): Result {
        val db = AutPlayRuntime.database(applicationContext)
        val downloads = DownloadIntentRepository(applicationContext, db)
        var offset = 0
        var submitted = 0
        val refs = mutableSetOf<String>()
        var unavailable = 0
        do {
            val page = server.catalogDownloadFiles(operation, offset)
            unavailable += page.unavailable
            for ((reference, variant) in page.files) {
                checkBinding(binding)
                val local = reference.localTarget(db, binding.serverProfileId.value)
                if (db.searchDao().offlineTrackIds(binding.serverProfileId.value, listOf(local), permanentOnly = true).first().isEmpty()) {
                    downloads.requestVaultDownload(LocalId(local), binding.serverProfileId, variant,
                        DownloadStorageClass.USER_DOWNLOAD, System.currentTimeMillis())
                }
                submitted++
                refs += local
            }
            offset = page.nextOffset ?: break
        } while (true)
        batches.finish(operation, refs, 0, unavailable + serverUnavailable)
        return Result.success(workDataOf("submitted" to submitted, "unavailable" to unavailable))
    }

    companion object {
        fun enqueue(context: Context, profile: String, user: String, operation: String, kind: String, id: String,
                    alternatives: Boolean, start: Boolean) {
            val work = OneTimeWorkRequestBuilder<CatalogDownloadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInputData(workDataOf("profile" to profile, "user" to user, "operation" to operation,
                    "kind" to kind, "id" to id, "alternatives" to alternatives, "start" to start))
                .setBackoffCriteria(BackoffPolicy.LINEAR, Duration.ofSeconds(30)).build()
            WorkManager.getInstance(context).enqueueUniqueWork("catalog-download:$operation:$start", ExistingWorkPolicy.KEEP, work)
        }
    }
}
