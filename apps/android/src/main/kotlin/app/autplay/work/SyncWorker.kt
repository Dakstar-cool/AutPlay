package app.autplay.work

import android.content.Context
import android.net.ConnectivityManager
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import app.autplay.AutPlayRuntime
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncBindingInitializer
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.io.IOException
import java.util.concurrent.CancellationException
import kotlinx.coroutines.flow.first

/** Durable P09 coordinator entry point; input holds no payload, token, URL, or cursor. */
class SyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val request = DeferredWorkInput.read(inputData) ?: return Result.failure()
        if (request.kind != DeferredWorkKind.SYNC || request.serverProfileId == null) return Result.failure()
        val settings = applicationNonSecretSettingsStore(applicationContext).settings.first()
        val user = settings.activeUserId ?: return Result.success()
        val device = settings.deviceId ?: return Result.success()
        if (settings.activeServerProfileId != request.serverProfileId) return Result.success()
        val metered = applicationContext.getSystemService(ConnectivityManager::class.java)
            ?.isActiveNetworkMetered ?: true
        if (!syncNetworkAllowed(settings.syncOnMeteredNetwork, metered)) return Result.retry()
        return try {
            val database = AutPlayRuntime.database(applicationContext)
            val initial = ClientEventBinding(user, device, request.serverProfileId)
            val cursor = SyncBindingInitializer(database).ensure(initial)
            val binding = initial.copy(journalEpoch = LocalId(cursor.journalEpoch))
            if (AutPlayRuntime.syncCoordinator(applicationContext, binding).run(binding)) {
                Result.success()
            } else Result.retry()
        } catch (error: Exception) {
            when (syncWorkerErrorDisposition(error)) {
                SyncWorkerErrorDisposition.CANCEL -> throw error
                SyncWorkerErrorDisposition.FAILURE -> Result.failure()
                SyncWorkerErrorDisposition.RETRY -> Result.retry()
            }
        }
    }
}

internal enum class SyncWorkerErrorDisposition { CANCEL, FAILURE, RETRY }

internal fun syncWorkerErrorDisposition(error: Exception): SyncWorkerErrorDisposition = when {
    error is CancellationException -> SyncWorkerErrorDisposition.CANCEL
    error is IOException -> SyncWorkerErrorDisposition.RETRY
    error is IllegalStateException && terminalServerWorkError(serverWorkErrorCode(error)) ->
        SyncWorkerErrorDisposition.FAILURE
    else -> SyncWorkerErrorDisposition.RETRY
}

internal fun syncNetworkAllowed(
    allowMeteredNetwork: Boolean,
    activeNetworkMetered: Boolean,
): Boolean = allowMeteredNetwork || !activeNetworkMetered
