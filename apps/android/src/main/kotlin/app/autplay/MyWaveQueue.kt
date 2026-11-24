package app.autplay

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.media3.common.util.UnstableApi
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.data.settings.NonSecretSettings
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.wave.WavePrefetchPlanner
import app.autplay.playback.AndroidPlaybackSourceResolver
import app.autplay.playback.AndroidSourceResolution
import app.autplay.playback.PlaybackMediaSourceFactory
import app.autplay.playback.SelectedAudioSource
import app.autplay.playback.warmStreamPrefix

internal fun canStartMyWave(context: ActiveQueueContext, roomActive: Boolean): Boolean =
    !roomActive && when (context) {
        ActiveQueueContext.Absent -> true
        is ActiveQueueContext.Loaded -> context.queueType in setOf("USER", "LIBRARY", "SEARCH", "PLAYLIST")
        ActiveQueueContext.Loading, ActiveQueueContext.Unavailable -> false
    }

/** A local connectivity hint only; Vault access is still authorized lazily by the playback owner. */
internal fun myWaveNetworkAvailable(context: Context): Boolean = runCatching {
    val connectivity = context.getSystemService(ConnectivityManager::class.java)
    val network = connectivity.activeNetwork ?: return@runCatching false
    connectivity.getNetworkCapabilities(network)
        ?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
}.getOrDefault(false)

@UnstableApi
internal suspend fun warmPersonalWaveTracks(
    context: Context, settings: NonSecretSettings, profile: ServerProfileId, tracks: List<LocalId>,
) {
    val connectivity = context.getSystemService(ConnectivityManager::class.java)
    val mode = AutPlayRuntime.wavePrefetchMode(settings.wavePrefetchMode)
    val resolver = AndroidPlaybackSourceResolver.production(context.applicationContext, AutPlayRuntime.database(context))
    val factory by lazy { PlaybackMediaSourceFactory.cachedVault(context.applicationContext) }
    for ((index, track) in tracks.withIndex()) {
        val unmetered = !connectivity.isActiveNetworkMetered
        if (index >= WavePrefetchPlanner.count(mode, unmetered) || !myWaveNetworkAvailable(context)) break
        val resolved = resolver.resolve(track, System.currentTimeMillis()) as? AndroidSourceResolution.Available ?: continue
        if (connectivity.isActiveNetworkMetered && !settings.downloadOnMeteredNetwork) continue
        if (resolved.value.source == SelectedAudioSource.VAULT_STREAM && resolved.value.runtimeUri.authority == profile.value) {
            try {
                warmStreamPrefix(factory, resolved.value.runtimeUri)
            } catch (_: java.io.IOException) {
                // Playback retains its own retry budget when a speculative range cannot finish.
                break
            }
        }
    }
}
