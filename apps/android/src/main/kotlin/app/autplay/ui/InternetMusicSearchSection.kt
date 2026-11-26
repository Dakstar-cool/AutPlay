package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.work.WorkInfo
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.server.InternetMusicCandidate
import app.autplay.application.server.InternetMusicSearch
import app.autplay.application.server.InternetSourceSearchSession
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.work.InternetMusicWork
import app.autplay.ui.downloads.DownloadProgressRing
import app.autplay.ui.downloads.liveTransferProgress
import app.autplay.ui.downloads.progressPercent
import app.autplay.ui.downloads.transferredSize
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.flowOf

private data class InternetSourceRequestKey(val query: String, val request: Int, val kind: LibrarySearchKind,
    val profile: String?, val user: String?, val device: String?, val contextId: String?)

@Composable
internal fun InternetMusicSearchSection(query: String, request: Int, kind: LibrarySearchKind = LibrarySearchKind.All,
    catalogueContextId: String? = null, session: InternetMusicUiSession? = null,
    onCatalogueContextExpired: (() -> Unit)? = null,
    vaultTracks: List<VaultSearchUiItem> = emptyList(),
    onPlayVault: ((String) -> Unit)? = null,
    onPlayAcquired: ((String) -> Unit)? = null,
    onPlaylistTrack: ((String) -> Unit)? = null,
    onOpenCatalog: ((app.autplay.ui.core.DetailTarget) -> Unit)? = null) {
    if (kind == LibrarySearchKind.Artist || kind == LibrarySearchKind.Album) {
        InternetMetadataDiscoverySection(query, kind, request, session, onPlaylistTrack, onOpenCatalog = onOpenCatalog)
        return
    }
    val context = LocalContext.current
    val settings by remember { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = if (session != null) session.binding?.serverProfileId else settings?.activeServerProfileId
    val user = if (session != null) session.binding?.userId else settings?.activeUserId
    val device = if (session != null) session.binding?.deviceId else settings?.deviceId
    val key = InternetSourceRequestKey(query.trim().take(200), request, kind, profile?.value, user?.value, device?.value, catalogueContextId)
    val currentKey by rememberUpdatedState(key)
    val searchSession = remember(key) { InternetSourceSearchSession(key.query, catalogueContextId) }
    var attempt by remember(key) { mutableStateOf(searchSession.current) }
    var result by remember(key) { mutableStateOf<InternetMusicSearch?>(null) }
    var loading by remember(key) { mutableStateOf(false) }
    var failure by remember(key) { mutableStateOf<String?>(null) }
    fun retrySearch(terminal: Boolean) {
        result = null; failure = null; loading = true
        attempt = if (terminal) searchSession.retryTerminalAcquisition() else searchSession.retryTransport()
    }
    LaunchedEffect(key, attempt) {
        val expectedAttempt = attempt
        result = null
        failure = null
        loading = false
        if (profile == null || user == null || device == null || request == 0 || key.query.isBlank()) return@LaunchedEffect
        loading = true
        try {
            val client = session?.client ?: AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
            val response = client.searchInternetMusic(expectedAttempt.query, expectedAttempt.operationId, expectedAttempt.catalogueContextId)
            if (currentKey == key && searchSession.accepts(expectedAttempt)) result = response
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (currentKey == key && searchSession.accepts(expectedAttempt)) failure = catalogueErrorCode(error)
        } finally { if (currentKey == key && searchSession.accepts(expectedAttempt)) loading = false }
    }
    Column(Modifier.fillMaxWidth().testTag("internet-music-results"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.music_internet_title), style = MaterialTheme.typography.titleLarge)
        when {
            profile == null -> Text(stringResource(R.string.music_need_server))
            loading -> LinearProgressIndicator(Modifier.fillMaxWidth())
            failure != null -> {
                CatalogueErrorText(failure)
                if (failure == "music_catalogue_context_expired") {
                    TextButton(enabled = onCatalogueContextExpired != null, onClick = { onCatalogueContextExpired?.invoke() },
                        modifier = Modifier.testTag("catalogue-context-rehydrate")) {
                        Text(stringResource(R.string.music_catalogue_retry_selection))
                    }
                } else TextButton(onClick = { retrySearch(false) }, modifier = Modifier.testTag("source-http-retry")) {
                    Text(stringResource(R.string.music_retry))
                }
            }
            result?.candidates?.isEmpty() == true -> Text(stringResource(R.string.music_internet_empty))
        }
        result?.let { search ->
            search.candidates.forEach { candidate ->
                val work by remember(profile, search.id, candidate.id, session?.work) {
                    session?.work?.states(search.id, candidate.id)
                        ?: profile?.let { observeInternetMusicWork(context, it.value, search.id, candidate.id) }
                        ?: flowOf(InternetMusicWorkStatus())
                }.collectAsState(initial = InternetMusicWorkStatus())
                val active = work.states.any { !it.isFinished }
                val completed = WorkInfo.State.SUCCEEDED in work.states
                val failed = WorkInfo.State.FAILED in work.states && !completed
                val downloading = work.downloadState in setOf("REQUESTED", "QUEUED", "DOWNLOADING")
                val transfer = if (downloading || work.downloadState == "PAUSED") liveTransferProgress(work.transferId) else null
                val status = when {
                    active -> stringResource(R.string.music_internet_adding)
                    downloading -> stringResource(R.string.music_device_downloading)
                    work.downloadState == "COMPLETED" -> stringResource(R.string.music_device_ready)
                    work.downloadState == "PAUSED" -> stringResource(R.string.music_device_paused)
                    work.downloadState == "FAILED" -> stringResource(R.string.music_device_failed)
                    completed -> stringResource(R.string.music_internet_added)
                    failed -> stringResource(R.string.music_internet_add_failed)
                    else -> null
                }
                val duration = "${candidate.durationMs / 60000}:${(candidate.durationMs / 1000 % 60).toString().padStart(2, '0')}"
                val vaultTrack = remember(candidate, vaultTracks) { matchingVaultTrack(candidate, vaultTracks) }
                val acquiredId = work.localTrackId
                fun acquire(download: Boolean) {
                    val owner = profile ?: return
                    if (session?.work != null) session.work.enqueue(owner.value, search.id, candidate.id, download)
                    else InternetMusicWork.enqueue(context, owner.value, search.id, candidate.id, download)
                }
                val canDownload = !active && !failed && !downloading && work.downloadState !in setOf("COMPLETED", "PAUSED") && profile != null
                val play: (() -> Unit)? = when {
                    vaultTrack != null && onPlayVault != null -> ({ onPlayVault(vaultTrack.id) })
                    acquiredId != null && onPlayAcquired != null -> ({ onPlayAcquired(acquiredId) })
                    else -> null
                }
                AutPlayTrackRow(
                    title = candidate.title,
                    artist = candidate.artist,
                    trackId = vaultTrack?.localTrackId,
                    onPlay = play,
                    detail = listOfNotNull(candidate.provider, duration, status,
                        transfer?.let { it.fraction?.let { fraction -> progressPercent(fraction) } ?: transferredSize(it) }
                    ).joinToString(" · "),
                    emphasizeDetail = completed && !active && !downloading && work.downloadState !in setOf("FAILED", "PAUSED"),
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                ) {
                    if (completed && !active && work.downloadState == null) {
                        AutPlayPlatformIcon(AutPlayIcon.Check, stringResource(R.string.music_internet_added),
                            Modifier.size(20.dp).testTag("source-ready-${candidate.id}"), tint = AutPlayTokens.colors.success)
                    }
                    Box(Modifier.size(48.dp), contentAlignment = androidx.compose.ui.Alignment.Center) {
                        when {
                            active || downloading || work.downloadState == "PAUSED" ->
                                DownloadProgressRing(transfer?.fraction, Modifier.size(38.dp).testTag("source-progress-${candidate.id}"),
                                    running = active || work.downloadState == "DOWNLOADING")
                            work.downloadState == "COMPLETED" -> AutPlayPlatformIcon(AutPlayIcon.Downloaded, stringResource(R.string.music_saved_offline),
                                Modifier.size(25.dp).testTag("source-ready-${candidate.id}"), tint = MaterialTheme.colorScheme.primary)
                            else -> IconButton(enabled = canDownload, onClick = { acquire(true) }) {
                                AutPlayPlatformIcon(AutPlayIcon.Download, stringResource(R.string.music_add_download), Modifier.size(24.dp))
                            }
                        }
                    }
                    if (play != null) {
                        IconButton(onClick = play) {
                            AutPlayPlatformIcon(AutPlayIcon.Play, stringResource(R.string.library_play_track, candidate.title))
                        }
                    }
                    TrackActionsMenu(Modifier.testTag("source-actions-${candidate.id}")) { dismiss ->
                        DropdownMenuItem(text = { Text(stringResource(R.string.music_add_vault)) },
                            enabled = !active && !completed && !failed && profile != null, onClick = { dismiss(); acquire(false) })
                        DropdownMenuItem(text = { Text(stringResource(R.string.music_add_download)) },
                            enabled = canDownload, onClick = { dismiss(); acquire(true) })
                        if (failed) {
                            DropdownMenuItem(text = { Text(stringResource(R.string.music_retry)) },
                                modifier = Modifier.testTag("source-acquisition-retry"), onClick = {
                                    dismiss()
                                    retrySearch(true)
                                })
                        }
                    }
                }
            }
        }
    }
}

internal fun matchingVaultTrack(candidate: InternetMusicCandidate, tracks: List<VaultSearchUiItem>): VaultSearchUiItem? {
    fun key(text: String?) = text?.trim()?.replace(Regex("\\s+"), " ")?.lowercase(java.util.Locale.ROOT)
    if (candidate.durationMs <= 0 || candidate.title.isBlank() || candidate.artist.isBlank()) return null
    return tracks.singleOrNull { row ->
        val duration = row.durationMs ?: return@singleOrNull false
        row.playable && duration > 0 && kotlin.math.abs(duration - candidate.durationMs) <= 2_000 &&
            key(row.title) == key(candidate.title) && key(row.artist) == key(candidate.artist)
    }
}
