package app.autplay

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import app.autplay.application.playback.ActiveQueueContext

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
