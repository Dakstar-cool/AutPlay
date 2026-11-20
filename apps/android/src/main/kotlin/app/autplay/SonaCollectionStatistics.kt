package app.autplay

import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import app.autplay.application.statistics.SonaVariationSkipReport
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.statistics.SonaVariationSkipSection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.isActive

@Composable
internal fun SonaCollectionStatistics(settings: NonSecretSettings, consent: TrainingConsentUi) {
    val profile = settings.activeServerProfileId ?: return
    val account = settings.activeUserId?.value ?: return
    val device = settings.deviceId ?: return
    val commit = settings.m5Binding?.bindingCommitId ?: return
    val origin = settings.serverBaseUrl ?: return
    val policy = consent.state.confirmed ?: return
    if (!consent.available || !policy.granted || policy.accountId != account ||
        consent.state.pending || consent.state.error || consent.state.busy) return

    val context = LocalContext.current.applicationContext
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    key(profile, account, device, commit, origin, policy.revision, lifecycle) {
        val port = remember { AutPlayRuntime.accountStatisticsPort(context, origin) }
        val online = remember { networkAvailability(context.getSystemService(ConnectivityManager::class.java)) }
        LiveSonaStatistics(online) { port.loadSonaVariationSkips(profile, account) }
    }
}

@Composable
internal fun LiveSonaStatistics(online: Flow<Boolean>, load: suspend () -> SonaVariationSkipReport) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var report by remember { mutableStateOf<SonaVariationSkipReport?>(null) }
    LaunchedEffect(online, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            try {
                online.collectLatest { available ->
                    report = null
                    if (!available) return@collectLatest
                    while (isActive) {
                        val started = SystemClock.elapsedRealtime()
                        val fresh = try {
                            load()
                        } catch (cancelled: CancellationException) {
                            throw cancelled
                        } catch (_: Exception) {
                            null
                        }
                        val remaining = (fresh?.remainingMs ?: 0) - (SystemClock.elapsedRealtime() - started)
                        if (fresh != null && remaining > 0) {
                            report = fresh
                            delay(minOf(remaining, 15_000L))
                            report = null
                        } else {
                            delay(30_000L)
                        }
                    }
                }
            } finally {
                report = null
            }
        }
    }
    report?.let { SonaVariationSkipSection(it.rows) }
}

private fun networkAvailability(manager: ConnectivityManager) = callbackFlow<Boolean> {
    val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { trySend(true) }
        override fun onLost(network: Network) { trySend(false) }
    }
    manager.registerDefaultNetworkCallback(callback)
    trySend(manager.activeNetwork != null)
    awaitClose { manager.unregisterNetworkCallback(callback) }
}.distinctUntilChanged()
