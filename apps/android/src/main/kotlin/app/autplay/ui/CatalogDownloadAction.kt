package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.work.WorkInfo
import androidx.work.WorkManager
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.download.CollectionDownloadStore
import app.autplay.download.MediaDownloadComponents
import app.autplay.ui.downloads.DownloadProgressRing
import app.autplay.work.CatalogDownloadProgress
import app.autplay.work.CatalogDownloadWorker
import java.util.UUID
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map

private data class DeviceBatch(val ready: Int = 0, val active: Set<String> = emptySet())

private fun deviceBatch(db: AutPlayDatabase, profile: String, refs: Set<String>) =
    if (refs.isEmpty()) flowOf(DeviceBatch()) else combine(refs.chunked(500).map { page ->
        combine(db.searchDao().offlineTrackIds(profile, page, permanentOnly = true), db.localAudioDao().collectionDownloads(profile, page)) { offline, intents ->
            val ready = offline.toSet()
            val active = intents.distinctBy { it.localUserTrackRefId }.filter {
                it.localUserTrackRefId !in ready && it.state in setOf("REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED")
            }.mapTo(mutableSetOf()) { it.media3DownloadId ?: it.downloadIntentId }
            DeviceBatch(ready.size, active)
        }
    }) { pages -> DeviceBatch(pages.sumOf { it.ready }, pages.flatMapTo(mutableSetOf()) { it.active }) }

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@Composable
internal fun CatalogDownloadAction(kind: String, id: String, itemTitle: String? = null, tracks: List<String?>? = null) {
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsStateWithLifecycle(initialValue = null)
    val profile = settings?.activeServerProfileId?.value
    val user = settings?.activeUserId?.value
    val title = stringResource(when (kind) {
        "artist" -> R.string.catalog_download_artist
        "release", "library_release" -> R.string.catalog_download_release
        "playlist" -> R.string.download_playlist
        else -> R.string.catalog_download_track
    })
    if (profile == null || user == null || settings?.deviceId == null || settings?.accountSignedOut == true) {
        if (settings == null) return
        val refs = remember(tracks) { tracks?.filterNotNull()?.toSet().orEmpty() }
        val offline = key(refs, profile) {
            remember { deviceBatch(AutPlayRuntime.database(context), profile ?: "legacy-unscoped", refs) }
                .collectAsStateWithLifecycle(initialValue = DeviceBatch()).value.ready
        }
        val saved = refs.isNotEmpty() && tracks?.none { it == null } == true && offline == refs.size
        Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
            AutPlayPlatformIcon(if (saved) AutPlayIcon.Downloaded else AutPlayIcon.Download,
                if (saved) stringResource(R.string.music_saved_offline) else "$title · ${stringResource(R.string.music_need_server)}",
                Modifier.size(26.dp), tint = if (saved) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline)
        }
        if (kind == "artist") Text(stringResource(R.string.music_need_server), style = MaterialTheme.typography.bodySmall)
        return
    }
    val scope = if (kind == "artist") "studio-discography" else kind
    key(profile, user, scope, id) {
        val target = "$profile:$user:$scope:$id:false"
        val store = remember { CollectionDownloadStore(context) }
        val snapshot by remember { store.changes(target).map {
            val operation = store.operation(target)
            operation to operation?.let(store::result)
        } }.collectAsStateWithLifecycle(initialValue = store.operation(target).let { it to it?.let(store::result) })
        val (operation, result) = snapshot
        val manager = remember { WorkManager.getInstance(context) }
        val workList = key(operation) {
            if (operation == null) null else remember {
                manager.getWorkInfosForUniqueWorkFlow("catalog-download:$operation:true")
            }.collectAsStateWithLifecycle(initialValue = null).value
        }
        val work = workList?.firstOrNull { !it.state.isFinished } ?: workList?.firstOrNull()
        val requested = remember(tracks) { tracks?.filterNotNull()?.toSet() }
        val missing = tracks?.count { it == null } ?: 0
        val refs = requested ?: result?.refs.orEmpty()
        val device = key(refs) {
            remember { deviceBatch(AutPlayRuntime.database(context), profile, refs) }
                .collectAsStateWithLifecycle(initialValue = null).value
        }
        val total = if (kind == "artist") (result?.saved ?: 0) + (result?.unavailable ?: 0)
            else refs.size + (if (requested != null) missing else result?.unavailable ?: 0)
        val ready = if (kind == "artist") result?.saved ?: 0 else device?.ready ?: 0
        val activeIds = device?.active.orEmpty()
        val transferShare = if (activeIds.isEmpty()) 0.0 else key(activeIds, total) {
            remember { MediaDownloadComponents.get(context).transferProgress.map { live ->
                val shares = activeIds.map { live[it]?.fraction }
                if (total == 1) shares.singleOrNull() else shares.sumOf { it ?: 0.0 }
            }.distinctUntilChanged() }.collectAsStateWithLifecycle(initialValue = null).value
        }
        var starting by remember { mutableStateOf<String?>(null) }
        LaunchedEffect(work, operation) { if (work != null && starting == operation) starting = null }
        val workerBusy = work != null && !work.state.isFinished || starting != null || (operation != null && workList == null)
        val busy = workerBusy || activeIds.isNotEmpty()
        val complete = !busy && total > 0 && ready == total
        val progress = work?.progress?.let(CatalogDownloadProgress::from)
        val fraction = when {
            workerBusy && requested == null && result == null -> if (kind == "artist") progress?.savedFraction else null
            total > 0 && transferShare != null -> (ready + transferShare) / total
            else -> null
        }
        val failed = !busy && !complete && work?.state == WorkInfo.State.FAILED
        val partial = !busy && !complete && result != null && device != null
        val caption = when {
            failed -> stringResource(R.string.download_retry_inline)
            partial && total == 0 -> stringResource(R.string.music_internet_empty)
            partial -> stringResource(R.string.download_partial, ready, total)
            busy && result == null && requested == null -> when (progress?.phase) {
                "discovery" -> stringResource(R.string.download_catalog_found, progress.total)
                "inspect", "preview" -> stringResource(R.string.download_catalog_checked, progress.processed, progress.total)
                "downloading", "complete" -> stringResource(R.string.download_catalog_processed, progress.processed, progress.total)
                else -> null
            }
            busy && total > 1 -> stringResource(R.string.download_saved_count, ready, total)
            else -> null
        }
        val label = when {
            complete && kind == "artist" -> stringResource(R.string.catalog_download_vault_complete)
            complete -> stringResource(R.string.music_saved_offline)
            busy -> stringResource(R.string.music_downloading)
            else -> title
        }
        val enabled = !busy && !complete && tracks?.isEmpty() != true && (refs.isEmpty() || device != null)
        val description = listOfNotNull(label, itemTitle, caption).joinToString(" · ")
        Row(modifier = Modifier.testTag("download-$kind-$id").semantics { contentDescription = description }
            .clickable(enabled = enabled, role = Role.Button) {
                // A preview created by an older client can already hold a complete catalogue scan.
                val op = operation?.takeIf { tracks == null && result == null && work == null } ?: UUID.randomUUID().toString()
                starting = op
                store.begin(target, op, requested.orEmpty(), missing)
                CatalogDownloadWorker.enqueue(context, profile, user, op, kind, if (tracks == null) id else op, false, true)
            }, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                when {
                    busy -> DownloadProgressRing(fraction, Modifier.size(40.dp))
                    complete -> AutPlayPlatformIcon(AutPlayIcon.Downloaded, null,
                        Modifier.size(28.dp).testTag("download-saved-$id"), tint = MaterialTheme.colorScheme.primary)
                    else -> AutPlayPlatformIcon(AutPlayIcon.Download, null, Modifier.size(26.dp),
                        tint = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.outline)
                }
            }
            if (caption != null) Text(caption, style = MaterialTheme.typography.labelSmall)
        }
        if (failed) work.outputData.getString("error")?.let { CatalogueErrorText(it) }
    }
}
