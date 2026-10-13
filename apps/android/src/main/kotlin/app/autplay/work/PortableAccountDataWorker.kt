package app.autplay.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import app.autplay.AutPlayRuntime
import app.autplay.application.portableaccount.*
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SettingsM5RotationContextResolver
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first

/** Credentials are read from Keystore at execution; a retired binding stops before further I/O. */
class PortableAccountDataWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val accountId = inputData.getString(PortableAccountWork.ACCOUNT_ID) ?: return Result.failure()
        val profileId = inputData.getString(PortableAccountWork.PROFILE_ID)
        if (runCatching { PortableAccountDataCodec.uuid(accountId); profileId?.let(PortableAccountDataCodec::uuid) }.isFailure) return Result.failure()
        val store = applicationNonSecretSettingsStore(applicationContext)
        val captured = store.settings.first()
        val credentials = AndroidKeystoreCredentialStore(applicationContext)
        val authority = PortableAccountStore(credentials, store)
        val owner = authority.ownership() ?: return Result.success()
        if (owner.accountId != accountId) return Result.success()
        if (profileId == null) {
            return try {
                val outcome = PortableAccountDataRepository(AutPlayRuntime.database(applicationContext)).captureLocal(accountId, deadlineMs = 60_000)
                if (outcome == PortableAccountDataSyncOutcome.CONTINUE) PortableAccountWork.continuePass(applicationContext, accountId, null)
                Result.success()
            } catch (error: CancellationException) { throw error }
            catch (_: Exception) { Result.failure() }
        }
        val binding = captured.m5Binding ?: return Result.success()
        if (captured.activeUserId?.value != accountId || captured.activeServerProfileId?.value != profileId || captured.serverBaseUrl == null) return Result.success()
        return try {
            val repository = PortableAccountDataRepository(AutPlayRuntime.database(applicationContext))
            check(repository.scopes(accountId).any { it.profileId == profileId }) { "ACCOUNT_SCOPE_REQUIRED" }
            val transport = OkHttpPortableAccountDataTransport(
                captured.serverBaseUrl.trimEnd('/') + "/api/v1", ServerProfileId(profileId),
                credentials,
                M5SessionRotationClient(SettingsM5RotationContextResolver(store), AndroidM5DeviceKeyStore()),
                beforeRequest = {
                    val current = store.settings.first()
                    check(authority.ownership()?.accountId == accountId && current.activeUserId?.value == accountId &&
                        current.activeServerProfileId?.value == profileId && current.serverBaseUrl == captured.serverBaseUrl &&
                        current.deviceId == captured.deviceId && current.m5Binding?.bindingCommitId == binding.bindingCommitId) { "ACCOUNT_DATA_BINDING_CHANGED" }
                },
            )
            when (PortableAccountDataSync(repository, transport).run(accountId, profileId)) {
                PortableAccountDataSyncOutcome.COMPLETE -> Result.success()
                PortableAccountDataSyncOutcome.CONTINUE -> {
                    PortableAccountWork.continuePass(applicationContext, accountId, profileId)
                    Result.success()
                }
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            when {
                error is IllegalStateException && error.message == "ACCOUNT_DATA_BINDING_CHANGED" -> Result.success()
                error is PortableAccountDataException -> if (error.transient && runAttemptCount < 3) Result.retry() else Result.failure()
                error is IOException && runAttemptCount < 3 -> Result.retry()
                else -> Result.failure()
            }
        }
    }
}
