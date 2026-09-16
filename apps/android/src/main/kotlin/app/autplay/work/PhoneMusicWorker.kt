package app.autplay.work

import android.content.Context
import androidx.work.*
import app.autplay.application.importing.PhoneMusicRepository
import app.autplay.application.importing.PhoneMusicTrack
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first

/** One bounded file per durable work item; no change to the original audio. */
class PhoneMusicWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val profile = inputData.getString("profile").orEmpty()
        if (profile != settings.activeServerProfileId?.value.orEmpty()) return Result.failure()
        val binding = settings.activeServerProfileId?.let { server ->
            val user = settings.activeUserId ?: return Result.failure()
            val device = settings.deviceId ?: return Result.failure()
            ClientEventBinding(user, device, server)
        }
        return try {
            val track = PhoneMusicTrack(inputData.getString("uri") ?: return Result.failure(),
                inputData.getString("title").orEmpty(), inputData.getString("artist").orEmpty(),
                inputData.getLong("size", 0), inputData.getString("mime") ?: "audio/mpeg")
            val id = PhoneMusicRepository(applicationContext).copyIntoLibrary(track, binding)
            if (inputData.getBoolean("upload", false) && binding != null) {
                PhoneVaultUploadWork.enqueue(applicationContext, id, profile)
            }
            Result.success(workDataOf("audio_id" to id))
        } catch (error: CancellationException) { throw error
        } catch (_: SecurityException) { Result.failure(workDataOf("error" to "MEDIA_PERMISSION_REQUIRED"))
        } catch (_: Exception) { Result.failure(workDataOf("error" to "PHONE_IMPORT_FAILED")) }
    }
}

object PhoneMusicWork {
    const val TAG = "phone-music-import"
    fun enqueue(context: Context, tracks: List<PhoneMusicTrack>, profile: String?, upload: Boolean) {
        val manager = WorkManager.getInstance(context)
        tracks.forEach { track ->
            val key = UUID.nameUUIDFromBytes("$profile:${track.uri}:$upload".toByteArray())
            manager.enqueueUniqueWork("phone-music-$key", ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<PhoneMusicWorker>().addTag(TAG).setInputData(workDataOf(
                    "uri" to track.uri, "title" to track.title, "artist" to track.artist,
                    "size" to track.size, "mime" to track.mime, "profile" to profile.orEmpty(), "upload" to upload,
                )).build())
        }
    }
}
