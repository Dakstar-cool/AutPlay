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
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.work.InternetMusicWork
import java.util.UUID
import kotlinx.coroutines.CancellationException

@Composable
internal fun InternetMusicSearchSection(query: String, request: Int) {
    val context = LocalContext.current
    val settings by remember { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    var result by remember { mutableStateOf<InternetMusicSearch?>(null) }
    var loading by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    var retry by remember { mutableIntStateOf(0) }
    val profile = settings?.activeServerProfileId
    LaunchedEffect(request, profile, settings?.activeUserId, retry) {
        result = null
        failed = false
        val user = settings?.activeUserId
        val device = settings?.deviceId
        if (profile == null || user == null || device == null || request == 0 || query.isBlank()) return@LaunchedEffect
        loading = true
        try {
            result = AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
                .searchInternetMusic(query.trim().take(200), UUID.randomUUID().toString())
        } catch (error: CancellationException) { throw error
        } catch (_: Exception) { failed = true
        } finally { loading = false }
    }
    Column(Modifier.fillMaxWidth().testTag("internet-music-results"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.music_internet_title), style = MaterialTheme.typography.titleLarge)
        when {
            profile == null -> Text(stringResource(R.string.music_need_server))
            loading -> LinearProgressIndicator(Modifier.fillMaxWidth())
            failed -> {
                Text(stringResource(R.string.music_internet_error), color = MaterialTheme.colorScheme.error)
                TextButton(onClick = { retry++ }) { Text(stringResource(R.string.music_retry)) }
            }
            result?.candidates?.isEmpty() == true -> Text(stringResource(R.string.music_internet_empty))
        }
        result?.let { search ->
            search.candidates.forEach { candidate ->
                val work by remember(search.id, candidate.id) {
                    WorkManager.getInstance(context).getWorkInfosByTagFlow(InternetMusicWork.tag(search.id, candidate.id))
                }.collectAsState(initial = emptyList())
                val active = work.any { !it.state.isFinished }
                val completed = work.any { it.state == WorkInfo.State.SUCCEEDED }
                Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                        Text(candidate.title, style = MaterialTheme.typography.titleMedium)
                        Text("${candidate.artist} · ${candidate.provider} · ${candidate.durationMs / 60000}:${(candidate.durationMs / 1000 % 60).toString().padStart(2, '0')}", style = MaterialTheme.typography.bodySmall)
                        when {
                            active -> Text(stringResource(R.string.music_internet_adding))
                            completed -> Text(stringResource(R.string.music_internet_added))
                            work.any { it.state == WorkInfo.State.FAILED } -> {
                                Text(stringResource(R.string.music_internet_error), color = MaterialTheme.colorScheme.error)
                                TextButton(onClick = { retry++ }) { Text(stringResource(R.string.music_retry)) }
                            }
                        }
                        Button(enabled = !active && !completed && profile != null, modifier = Modifier.fillMaxWidth(), onClick = {
                            profile?.let { InternetMusicWork.enqueue(context, it.value, search.id, candidate.id, false) }
                        }) { Text(stringResource(R.string.music_add_vault)) }
                        OutlinedButton(enabled = !active && profile != null, modifier = Modifier.fillMaxWidth(), onClick = {
                            profile?.let { InternetMusicWork.enqueue(context, it.value, search.id, candidate.id, true) }
                        }) { Text(stringResource(R.string.music_add_download)) }
                    }
                }
            }
        }
    }
}
