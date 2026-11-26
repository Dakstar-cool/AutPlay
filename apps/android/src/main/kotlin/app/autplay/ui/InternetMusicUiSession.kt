package app.autplay.ui

import android.content.Context
import androidx.work.WorkManager
import app.autplay.AutPlayRuntime
import app.autplay.work.InternetMusicWork
import app.autplay.application.server.InternetMetadataDiscoveryPort
import app.autplay.application.sync.ClientEventBinding
import androidx.work.WorkInfo
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map

internal data class InternetMusicWorkStatus(val states: List<WorkInfo.State> = emptyList(), val downloadState: String? = null,
    val localTrackId: String? = null, val transferId: String? = null)

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
internal fun observeInternetMusicWork(context: Context, profileId: String, searchId: String, candidateId: String): Flow<InternetMusicWorkStatus> =
    WorkManager.getInstance(context).getWorkInfosByTagFlow(InternetMusicWork.tag(searchId, candidateId)).flatMapLatest { infos ->
        val states = infos.map { it.state }
        val localTrackId = infos.firstNotNullOfOrNull { info ->
            info.outputData.getString("ref").takeIf { info.state == WorkInfo.State.SUCCEEDED }
        }
        val ids = infos.mapNotNull { it.outputData.getString("download_intent") }.distinct()
        if (ids.isEmpty()) flowOf(InternetMusicWorkStatus(states, localTrackId = localTrackId))
        else AutPlayRuntime.database(context).localAudioDao().observeLatestDownloadIntent(profileId, ids).map { intent ->
            InternetMusicWorkStatus(states, intent?.state, localTrackId, intent?.media3DownloadId ?: intent?.downloadIntentId)
        }
    }

/** Optional presentation dependency; the normal app resolves the active binding through its runtime. */
internal data class InternetMusicUiSession(val binding: ClientEventBinding?, val client: InternetMetadataDiscoveryPort,
    val work: InternetMusicUiWork? = null)

/** Presentation boundary for observing and requesting the existing acquisition worker. */
internal interface InternetMusicUiWork {
    fun states(searchId: String, candidateId: String): Flow<InternetMusicWorkStatus>
    fun enqueue(profileId: String, searchId: String, candidateId: String, download: Boolean)
}
