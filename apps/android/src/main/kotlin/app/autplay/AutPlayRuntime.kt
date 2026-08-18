package app.autplay

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.OkHttpSyncTransport
import app.autplay.application.sync.SyncCoordinator
import app.autplay.application.wave.OkHttpWaveTransport
import app.autplay.application.wave.WaveCoordinator
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.playback.AndroidWaveSourceProbe
import app.autplay.playback.Media3WavePrefetchExecutor
import app.autplay.application.recommendation.DecodedOfflinePack
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.OkHttpRecommendationPackTransport
import app.autplay.data.security.AndroidKeystoreCredentialStore
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
        return SyncCoordinator(database(context), OkHttpSyncTransport(settings.serverBaseUrl, AndroidKeystoreCredentialStore(context.applicationContext)))
    }

    /** Wave is available only for the active authenticated profile; local playback remains independent. */
    @OptIn(UnstableApi::class)
    suspend fun waveCoordinator(context: Context, binding: ClientEventBinding): WaveCoordinator {
        val settings = applicationNonSecretSettingsStore(context.applicationContext).settings.first()
        check(settings.activeServerProfileId == binding.serverProfileId && settings.serverBaseUrl != null) { "WAVE_PROFILE_NOT_ACTIVE" }
        val database = database(context)
        return WaveCoordinator(
            database,
            OkHttpWaveTransport(settings.serverBaseUrl, binding.serverProfileId, AndroidKeystoreCredentialStore(context.applicationContext)),
            ServicePlaybackSessionOwner(context.applicationContext),
            AndroidWaveSourceProbe(context.applicationContext, database),
            Media3WavePrefetchExecutor(context.applicationContext, database),
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
            settings.serverBaseUrl,
            AndroidKeystoreCredentialStore(context.applicationContext),
        )
        return repository.refreshPack(binding, transport, nowMs)
    }
}
