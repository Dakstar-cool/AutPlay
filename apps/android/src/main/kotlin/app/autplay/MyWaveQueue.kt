package app.autplay

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import androidx.media3.common.util.UnstableApi
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.data.settings.NonSecretSettings
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.wave.WavePrefetchMode
import app.autplay.domain.wave.WavePrefetchPlanner
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged
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

internal enum class WaveConnection {
    OFFLINE, METERED, LIMITED, FAST;

    fun prefetch(allowMetered: Boolean): WavePrefetchMode = when (this) {
        OFFLINE -> WavePrefetchMode.OFF
        METERED -> if (allowMetered) WavePrefetchMode.NEXT else WavePrefetchMode.OFF
        LIMITED -> WavePrefetchMode.NEXT
        FAST -> WavePrefetchMode.NEXT_3
    }
}

internal fun ConnectivityManager.waveConnection(): WaveConnection {
    val caps = getNetworkCapabilities(activeNetwork) ?: return WaveConnection.OFFLINE
    return when {
        !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ||
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL) -> WaveConnection.OFFLINE
        !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) -> WaveConnection.METERED
        caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) && caps.linkDownstreamBandwidthKbps >= 3_000 ->
            WaveConnection.FAST
        else -> WaveConnection.LIMITED
    }
}

internal fun waveConnections(manager: ConnectivityManager) = callbackFlow<WaveConnection> {
    val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { trySend(manager.waveConnection()) }
        override fun onLost(network: Network) { trySend(manager.waveConnection()) }
    }
    manager.registerDefaultNetworkCallback(callback)
    trySend(manager.waveConnection())
    awaitClose { manager.unregisterNetworkCallback(callback) }
}.distinctUntilChanged()

@UnstableApi
internal suspend fun warmPersonalWaveTracks(
    context: Context, settings: NonSecretSettings, profile: ServerProfileId, tracks: List<LocalId>,
) {
    val connectivity = context.getSystemService(ConnectivityManager::class.java)
    fun prefixCount(): Int {
        val link = connectivity.waveConnection()
        return WavePrefetchPlanner.count(link.prefetch(settings.downloadOnMeteredNetwork), link != WaveConnection.METERED)
    }
    val resolver = AndroidPlaybackSourceResolver.production(context.applicationContext, AutPlayRuntime.database(context))
    val factory by lazy { PlaybackMediaSourceFactory.cachedVault(context.applicationContext) }
    for ((index, track) in tracks.withIndex()) {
        if (index >= prefixCount()) break
        val resolved = resolver.resolve(track, System.currentTimeMillis()) as? AndroidSourceResolution.Available ?: continue
        if (index >= prefixCount()) break
        if (resolved.value.source == SelectedAudioSource.VAULT_STREAM && resolved.value.runtimeUri.authority == profile.value) {
            try {
                val started = SystemClock.elapsedRealtime()
                warmStreamPrefix(factory, resolved.value.runtimeUri)
                // A slow first range is better evidence than the link bandwidth reported by Android.
                if (SystemClock.elapsedRealtime() - started >= 2_000L) break
            } catch (_: java.io.IOException) {
                // Playback retains its own retry budget when a speculative range cannot finish.
                break
            }
        }
    }
}
