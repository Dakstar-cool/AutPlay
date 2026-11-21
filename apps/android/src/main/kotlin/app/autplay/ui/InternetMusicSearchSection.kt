package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.work.WorkInfo
import androidx.work.WorkManager
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.server.InternetMusicSearch
import app.autplay.application.server.InternetSourceSearchSession
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.work.InternetMusicWork
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.map

private data class InternetSourceRequestKey(val query: String, val request: Int, val kind: LibrarySearchKind,
    val profile: String?, val user: String?, val device: String?, val contextId: String?)

@Composable
internal fun InternetMusicSearchSection(query: String, request: Int, kind: LibrarySearchKind = LibrarySearchKind.All,
    catalogueContextId: String? = null, session: InternetMusicUiSession? = null,
    onCatalogueContextExpired: (() -> Unit)? = null) {
    if (kind == LibrarySearchKind.Artist || kind == LibrarySearchKind.Album) {
        InternetMetadataDiscoverySection(query, kind, request, session)
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
                val work by remember(search.id, candidate.id, session?.work) {
                    session?.work?.states(search.id, candidate.id)
                        ?: WorkManager.getInstance(context).getWorkInfosByTagFlow(InternetMusicWork.tag(search.id, candidate.id))
                            .map { infos -> infos.map { it.state } }
                }.collectAsState(initial = emptyList())
                val active = work.any { !it.isFinished }
                val completed = work.any { it == WorkInfo.State.SUCCEEDED }
                val failed = work.any { it == WorkInfo.State.FAILED }
                val status = when {
                    active -> stringResource(R.string.music_internet_adding)
                    completed -> stringResource(R.string.music_internet_added)
                    failed -> stringResource(R.string.music_internet_error)
                    else -> null
                }
                val duration = "${candidate.durationMs / 60000}:${(candidate.durationMs / 1000 % 60).toString().padStart(2, '0')}"
                AutPlayTrackRow(
                    title = candidate.title,
                    artist = candidate.artist,
                    trackId = null,
                    onPlay = null,
                    detail = listOfNotNull(candidate.provider, duration, status).joinToString(" · "),
                ) {
                    TrackActionsMenu(Modifier.testTag("source-actions-${candidate.id}")) { dismiss ->
                        fun acquire(download: Boolean) {
                            dismiss()
                            val owner = profile ?: return
                            if (session?.work != null) session.work.enqueue(owner.value, search.id, candidate.id, download)
                            else InternetMusicWork.enqueue(context, owner.value, search.id, candidate.id, download)
                        }
                        DropdownMenuItem(text = { Text(stringResource(R.string.music_add_vault)) },
                            enabled = !active && !completed && !failed && profile != null, onClick = { acquire(false) })
                        DropdownMenuItem(text = { Text(stringResource(R.string.music_add_download)) },
                            enabled = !active && !failed && profile != null, onClick = { acquire(true) })
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
