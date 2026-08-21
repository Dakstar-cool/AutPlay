package app.autplay

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.OkHttpSyncTransport
import app.autplay.application.sync.SyncCoordinator
import app.autplay.application.wave.OkHttpWaveTransport
import app.autplay.application.wave.WaveCoordinator
import app.autplay.domain.wave.WavePrefetchMode
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.playback.AndroidWaveSourceProbe
import app.autplay.playback.Media3WavePrefetchExecutor
import app.autplay.application.recommendation.DecodedOfflinePack
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.OkHttpRecommendationPackTransport
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.M5RotationContext
import app.autplay.data.security.M5RotationContextResolver
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.data.local.AutPlayDatabase
import kotlinx.coroutines.flow.first

/** Process-scoped graph shared by the Activity and Media3 services. */
object AutPlayRuntime {
    @Volatile private var databaseInstance: AutPlayDatabase? = null

    fun database(context: Context): AutPlayDatabase = databaseInstance ?: synchronized(this) {
        databaseInstance ?: AutPlayDatabase.open(context.applicationContext).also { databaseInstance = it }
    }

    /** Instrumentation-only lifecycle seam; production keeps one database for the process. */
    fun closeDatabaseForTests() = synchronized(this) {
        databaseInstance?.close()
        databaseInstance = null
    }

    /** Resolves profile-scoped non-secret endpoint and Keystore credential only at sync execution. */
    suspend fun syncCoordinator(context: Context, binding: ClientEventBinding): SyncCoordinator {
        val settings = applicationNonSecretSettingsStore(context.applicationContext).settings.first()
        check(settings.activeServerProfileId == binding.serverProfileId && settings.serverBaseUrl != null) { "SYNC_PROFILE_NOT_ACTIVE" }
        return SyncCoordinator(database(context), OkHttpSyncTransport(apiV1BaseUrl(settings.serverBaseUrl), AndroidKeystoreCredentialStore(context.applicationContext), m5Rotation = m5Rotation(context)))
    }

    /** Wave is available only for the active authenticated profile; local playback remains independent. */
    @OptIn(UnstableApi::class)
    suspend fun waveCoordinator(context: Context, binding: ClientEventBinding): WaveCoordinator {
        val settingsStore = applicationNonSecretSettingsStore(context.applicationContext)
        val settings = settingsStore.settings.first()
        check(settings.activeServerProfileId == binding.serverProfileId && settings.serverBaseUrl != null) { "WAVE_PROFILE_NOT_ACTIVE" }
        val database = database(context)
        return WaveCoordinator(
            database,
            OkHttpWaveTransport(
                apiRootBaseUrl(settings.serverBaseUrl),
                binding.serverProfileId,
                AndroidKeystoreCredentialStore(context.applicationContext),
                authBaseUrl = apiV1BaseUrl(settings.serverBaseUrl),
                m5Rotation = m5Rotation(context),
            ),
            ServicePlaybackSessionOwner(context.applicationContext),
            AndroidWaveSourceProbe(context.applicationContext, database),
            Media3WavePrefetchExecutor(context.applicationContext, database),
            prefetchMode = {
                wavePrefetchMode(settingsStore.settings.first().wavePrefetchMode)
            },
        )
    }

    /** Fetches one owner/device-bound pack; callers keep serving the cached local feed on failure. */
    suspend fun refreshRecommendationPack(
        context: Context,
        binding: ClientEventBinding,
        repository: OfflineRecommendationRepository,
        nowMs: Long,
    ): DecodedOfflinePack {
        val settings = applicationNonSecretSettingsStore(context.applicationContext).settings.first()
        check(settings.activeServerProfileId == binding.serverProfileId && settings.serverBaseUrl != null) {
            "RECOMMENDATION_PROFILE_NOT_ACTIVE"
        }
        val transport = OkHttpRecommendationPackTransport(
            apiV1BaseUrl(settings.serverBaseUrl),
            AndroidKeystoreCredentialStore(context.applicationContext),
            m5Rotation = m5Rotation(context),
        )
        return repository.refreshPack(binding, transport, nowMs)
    }

    /** Creates the bounded server-surface adapter for the active profile only. */
    suspend fun serverFeatures(context: Context, binding: ClientEventBinding): ServerFeatureRepository {
        val settings = applicationNonSecretSettingsStore(context.applicationContext).settings.first()
        check(settings.activeServerProfileId == binding.serverProfileId && settings.serverBaseUrl != null) {
            "SERVER_PROFILE_NOT_ACTIVE"
        }
        return ServerFeatureRepository(
            settings.serverBaseUrl,
            settings.streamBaseUrl ?: settings.serverBaseUrl,
            binding.serverProfileId,
            AndroidKeystoreCredentialStore(context.applicationContext),
            m5Rotation = m5Rotation(context),
        )
    }

    private fun apiV1BaseUrl(serverBaseUrl: String): String = serverBaseUrl.trimEnd('/') + "/api/v1"

    private fun m5Rotation(context: Context): M5SessionRotationClient {
        val settings = applicationNonSecretSettingsStore(context.applicationContext)
        return M5SessionRotationClient(object : M5RotationContextResolver {
            override suspend fun resolve(profileId: app.autplay.domain.ServerProfileId): M5RotationContext? {
                val value = settings.settings.first()
                val checkpoint = value.m5Binding ?: return null
                if (value.activeServerProfileId != profileId || value.deviceId == null || value.serverBaseUrl == null) return null
                return M5RotationContext(value.serverBaseUrl, checkpoint.serverInstanceId, checkpoint.identityEpoch, value.deviceId, checkpoint.deviceKeyAlias)
            }

            override suspend fun persistSuccessor(
                profileId: app.autplay.domain.ServerProfileId,
                successor: app.autplay.data.security.SessionCredentialEnvelope,
            ) {
                settings.mutate { current ->
                    val checkpoint = current.m5Binding
                    if (
                        current.activeServerProfileId == profileId &&
                        checkpoint != null &&
                        checkpoint.bindingCommitId == successor.bindingCommitId &&
                        checkpoint.sessionFamilyId == successor.sessionFamilyId &&
                        successor.sessionId != null &&
                        successor.sessionGeneration != null
                    ) {
                        current.copy(
                            m5Binding = checkpoint.copy(
                                sessionId = successor.sessionId,
                                sessionFamilyId = requireNotNull(successor.sessionFamilyId),
                                sessionGeneration = successor.sessionGeneration,
                            ),
                        )
                    } else {
                        current
                    }
                }
            }
        }, AndroidM5DeviceKeyStore())
    }

    // Wave's P13 transport owns the `/v1/wave` suffix and therefore receives the `/api` root.
    private fun apiRootBaseUrl(serverBaseUrl: String): String = serverBaseUrl.trimEnd('/') + "/api"

    internal fun wavePrefetchMode(value: String): WavePrefetchMode =
        runCatching { WavePrefetchMode.valueOf(value) }.getOrDefault(WavePrefetchMode.NEXT)
}
