package app.autplay.ui

import app.autplay.application.server.InternetMetadataDiscoveryPort
import app.autplay.application.sync.ClientEventBinding
import androidx.work.WorkInfo
import kotlinx.coroutines.flow.Flow

/** Optional presentation dependency; the normal app resolves the active binding through its runtime. */
internal data class InternetMusicUiSession(val binding: ClientEventBinding?, val client: InternetMetadataDiscoveryPort,
    val work: InternetMusicUiWork? = null)

/** Presentation boundary for observing and requesting the existing acquisition worker. */
internal interface InternetMusicUiWork {
    fun states(searchId: String, candidateId: String): Flow<List<WorkInfo.State>>
    fun enqueue(profileId: String, searchId: String, candidateId: String, download: Boolean)
}
