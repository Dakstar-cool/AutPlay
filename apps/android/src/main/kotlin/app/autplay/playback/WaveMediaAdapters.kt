package app.autplay.playback

import android.content.Context
import androidx.media3.common.util.UnstableApi
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.wave.WavePrefetchExecutor
import app.autplay.application.wave.WaveSnapshot
import app.autplay.application.wave.WaveSourceProbe
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.wave.WaveAvailability
import app.autplay.domain.wave.WavePrefetchMode
import app.autplay.domain.wave.WavePrefetchPlanner
import app.autplay.download.DownloadStorageClass

/** Reuses the P08 source policy; it never invents a room-scoped media grant. */
@UnstableApi
class AndroidWaveSourceProbe(context: Context, database: AutPlayDatabase) : WaveSourceProbe {
    private val resolver = AndroidPlaybackSourceResolver(
        context.applicationContext,
        database,
        applicationNonSecretSettingsStore(context.applicationContext),
    )

    override suspend fun resolve(localTrackRefId: String): WaveAvailability =
        when (val result = resolver.resolve(LocalId(localTrackRefId), System.currentTimeMillis())) {
            is AndroidSourceResolution.Unavailable -> WaveAvailability.UNAVAILABLE
            is AndroidSourceResolution.Available -> when (result.value.source) {
                SelectedAudioSource.LOCAL_URI -> WaveAvailability.LOCAL_READABLE
                SelectedAudioSource.MEDIA3_DOWNLOAD -> WaveAvailability.DOWNLOADED
                SelectedAudioSource.VAULT_STREAM -> WaveAvailability.VAULT_STREAMABLE
            }
        }
}

/** Delegates proactive bytes to P08 Media3 DownloadService; its manager owns the two-download cap. */
@UnstableApi
class Media3WavePrefetchExecutor(context: Context, database: AutPlayDatabase) :
    WavePrefetchExecutor {
    private val repository = DownloadIntentRepository(context.applicationContext, database)

    override suspend fun prefetch(
        snapshot: WaveSnapshot,
        mode: WavePrefetchMode,
        unmetered: Boolean,
        nowMs: Long,
    ): Int {
        val count = WavePrefetchPlanner.count(mode, unmetered).coerceAtMost(3)
        if (count == 0) return 0
        val profileId = ServerProfileId(snapshot.profileId)
        return snapshot.entries.sortedBy { it.position }.drop(1).take(count).count { entry ->
            val trackRefId = entry.localTrackRefId ?: return@count false
            runCatching {
                repository.requestPreferredVaultDownload(
                    LocalId(trackRefId),
                    profileId,
                    DownloadStorageClass.PROACTIVE_CACHE,
                    nowMs,
                )
            }.isSuccess
        }
    }
}
