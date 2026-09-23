package app.autplay.work

import android.content.Context
import androidx.work.*
import app.autplay.AutPlayRuntime
import app.autplay.application.library.ArtworkBatchResult
import app.autplay.application.library.TrackMetadataRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.*
import java.util.UUID
import java.util.concurrent.TimeUnit

class TrackMetadataWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val profile = inputData.getString("profile") ?: return Result.failure()
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        if (settings.activeServerProfileId?.value != profile) return Result.failure()
        if (runAttemptCount >= 12) return Result.failure(workDataOf("error" to "METADATA_RELOAD_REQUIRED"))
        val metered = applicationContext.getSystemService(android.net.ConnectivityManager::class.java)?.isActiveNetworkMetered ?: true
        if (!syncNetworkAllowed(settings.syncOnMeteredNetwork, metered)) return Result.retry()
        val binding = ClientEventBinding(settings.activeUserId ?: return Result.failure(), settings.deviceId ?: return Result.failure(), settings.activeServerProfileId)
        return try {
            val api = AutPlayRuntime.serverFeatures(applicationContext, binding)
            val repository = TrackMetadataRepository(applicationContext)
            val trackId = inputData.getString("track")
            val body = inputData.getString("command")
            if (inputData.getBoolean("backfill", false)) api.metadataBackfill()
            if (trackId != null && body != null) {
                val track = AutPlayRuntime.database(applicationContext).libraryDao().trackRef(trackId) ?: return Result.failure()
                if (track.serverProfileId != profile) return Result.failure()
                val refId = track.serverUserTrackRefId ?: return Result.retry()
                val command = Json.parseToJsonElement(body).jsonObject
                val accepted = try { api.metadataCommand(refId, command) }
                catch (error: Exception) {
                    if (error.message == "SERVER_HTTP_409") repository.project(profile, trackId, api.trackMetadata(refId))
                    throw error
                }
                repository.project(profile, trackId, accepted)
                AutPlayRuntime.syncScheduler(applicationContext).enqueue(DeferredWorkRequest(
                    DeferredWorkKind.SYNC, DeferredWorkSubject.Device(binding.deviceId), binding.serverProfileId))
                if (command["action"]?.jsonPrimitive?.content == "EDIT") {
                    TrackMetadataWork.artwork(applicationContext, profile)
                    return Result.success()
                }
                var pending = true
                for (attempt in 0 until 12) {
                    val current = api.trackMetadata(refId)
                    repository.project(profile, trackId, current)
                    pending = current["state"]?.jsonPrimitive?.content in setOf("QUEUED", "RETRY")
                    if (!pending) break
                    delay(5_000)
                }
                if (pending) return Result.retry()
                TrackMetadataWork.artwork(applicationContext, profile)
                return Result.success()
            }
            val batch = repository.fetchArtwork(profile, api, inputData.getInt("artworkOffset", 0))
            when (artworkNextStep(batch)) {
                ArtworkNextStep.COMPLETE -> Result.success()
                ArtworkNextStep.UNAVAILABLE -> Result.failure(workDataOf("error" to "METADATA_ARTWORK_UNAVAILABLE"))
                ArtworkNextStep.CONTINUE -> {
                    TrackMetadataWork.continueArtwork(applicationContext, profile, batch.nextOffset)
                    Result.success()
                }
            }
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (runAttemptCount >= 12 || error.message in setOf("SERVER_HTTP_404", "SERVER_HTTP_409", "SERVER_HTTP_422"))
                Result.failure(workDataOf("error" to "METADATA_RELOAD_REQUIRED")) else Result.retry()
        }
    }
}

internal enum class ArtworkNextStep { COMPLETE, CONTINUE, UNAVAILABLE }

internal fun artworkNextStep(batch: ArtworkBatchResult): ArtworkNextStep = when {
    batch.remaining == 0 -> ArtworkNextStep.COMPLETE
    batch.nextOffset >= batch.remaining -> ArtworkNextStep.UNAVAILABLE
    else -> ArtworkNextStep.CONTINUE
}

object TrackMetadataWork {
    fun tag(track: String) = "metadata-track-$track"
    fun artwork(context: Context, profile: String) {
        WorkManager.getInstance(context).enqueueUniqueWork("metadata-art-$profile", ExistingWorkPolicy.KEEP,
            request(profile).build())
    }
    suspend fun continueArtwork(context: Context, profile: String, offset: Int) {
        // Each successful page gets a fresh retry budget. A page with only stale 404s
        // advances to the next offset and eventually stops after one full pass.
        WorkManager.getInstance(context).enqueueUniqueWork("metadata-art-$profile", ExistingWorkPolicy.APPEND_OR_REPLACE,
            request(profile).setInputData(workDataOf("profile" to profile, "artworkOffset" to offset))
                .setInitialDelay(5, TimeUnit.SECONDS).build()).await()
    }
    fun command(context: Context, profile: String, track: String, revision: Long, action: String, fields: JsonObject? = null, candidate: String? = null): UUID {
        val operation = UUID.randomUUID().toString()
        val body = buildJsonObject {
            put("operation_id", operation); put("expected_revision", revision); put("action", action)
            fields?.let { put("fields", it) }; candidate?.let { put("candidate_id", it) }
        }
        val work = request(profile).addTag(tag(track)).setInputData(workDataOf("profile" to profile, "track" to track, "command" to body.toString())).build()
        WorkManager.getInstance(context).enqueueUniqueWork("metadata-command-$profile-$track", ExistingWorkPolicy.KEEP, work)
        return work.id
    }
    fun backfill(context: Context, profile: String) {
        WorkManager.getInstance(context).enqueueUniqueWork("metadata-backfill-$profile", ExistingWorkPolicy.KEEP,
            request(profile).addTag("metadata-backfill").setInputData(workDataOf("profile" to profile, "backfill" to true)).build())
    }
    private fun request(profile: String) = OneTimeWorkRequestBuilder<TrackMetadataWorker>()
        .setInputData(workDataOf("profile" to profile))
        .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
        .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
}
