package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.core.content.edit
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.work.CatalogDownloadWorker
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.*

@Composable
internal fun CatalogDownloadAction(kind: String, id: String) {
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val title = stringResource(when (kind) {
        "artist" -> R.string.catalog_download_artist
        "release" -> R.string.catalog_download_release
        else -> R.string.catalog_download_track
    })
    val active = settings ?: return
    val profile = active.activeServerProfileId
    val user = active.activeUserId
    if (profile == null || user == null || active.deviceId == null) {
        if (kind == "artist") {
            OutlinedButton(onClick = {}, enabled = false) { Text(title) }
            Text(stringResource(R.string.music_need_server), style = MaterialTheme.typography.bodySmall)
        }
        return
    }
    val device = active.deviceId
    var open by rememberSaveable(profile.value, user.value, kind, id) { mutableStateOf(false) }
    OutlinedButton(onClick = { open = true }) { Text(title) }
    if (!open) return
    val scope = if (kind == "artist") "studio-discography" else kind
    val key = "${profile.value}:${user.value}:$scope:$id:false"
    val prefs = remember(context) { context.getSharedPreferences("catalog-download-operations", 0) }
    var operation by remember(key) { mutableStateOf(prefs.getString(key, null)) }
    var state by remember(operation) { mutableStateOf<JsonObject?>(null) }
    var error by remember(operation) { mutableStateOf(false) }
    val phase = state?.get("phase")?.jsonPrimitive?.content
    fun run(start: Boolean, fresh: Boolean = false) {
        val op = operation?.takeUnless { fresh } ?: UUID.randomUUID().toString()
        prefs.edit { putString(key, op) }
        operation = op
        error = false
        CatalogDownloadWorker.enqueue(context, profile.value, user.value, op, kind, id, false, start)
    }
    var refresh by remember(operation) { mutableIntStateOf(0) }
    val workManager = remember(context) { WorkManager.getInstance(context) }
    @Composable fun worker(start: Boolean): WorkInfo? = operation?.let { op ->
        key(op, start) {
            remember { workManager.getWorkInfosForUniqueWorkFlow("catalog-download:$op:$start") }
                .collectAsState(initial = emptyList()).value.firstOrNull()
        }
    }
    val startWork = worker(true)
    val previewWork = worker(false)
    LaunchedEffect(operation, refresh) {
        val op = operation ?: return@LaunchedEffect
        val binding = ClientEventBinding(user, device, profile)
        while (true) {
            try {
                state = AutPlayRuntime.serverFeatures(context, binding).catalogDownload(op, "status")
                error = false
                if (state?.get("phase")?.jsonPrimitive?.content == "complete") break
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (_: Exception) { error = true }
            delay(3_000)
        }
    }
    AutPlayDialog(onDismissRequest = { open = false }, title = { Text(title) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (kind == "artist") {
                Text(stringResource(R.string.catalog_download_scope))
            }
            val counts = state?.get("counts")?.jsonObject
            if (counts != null) {
                fun count(key: String) = counts[key]?.jsonPrimitive?.int ?: 0
                Text(stringResource(R.string.catalog_download_counts, count("vault"), count("queued"), count("available"), count("missing") + count("failed")))
                state?.get("issues")?.jsonArray?.forEach { issue ->
                    Text(issue.jsonObject["title"]?.jsonPrimitive?.content.orEmpty() + " · " +
                        when (issue.jsonObject["reason"]?.jsonPrimitive?.content) {
                            "source_not_found" -> stringResource(R.string.catalog_source_missing)
                            "catalogue_match_evidence_missing" -> stringResource(R.string.catalog_match_missing)
                            else -> stringResource(R.string.catalog_track_failed)
                        }, style = MaterialTheme.typography.bodySmall)
                }
            }
            if (kind == "artist" && phase == "complete") Text(stringResource(R.string.catalog_download_vault_complete))
            if (kind != "artist" && startWork?.state == WorkInfo.State.SUCCEEDED) {
                Text(stringResource(R.string.catalog_download_device_result,
                    startWork.outputData.getInt("submitted", 0), startWork.outputData.getInt("unavailable", 0)))
            }
            if (listOfNotNull(startWork, previewWork).any { !it.state.isFinished }) LinearProgressIndicator()
            if (error || startWork?.state == WorkInfo.State.FAILED || previewWork?.state == WorkInfo.State.FAILED) Text(stringResource(R.string.music_catalogue_unavailable))
        }
    }, confirmButton = {
        when {
            startWork?.state == WorkInfo.State.FAILED -> TextButton(onClick = { run(true) }) { Text(stringResource(R.string.music_retry)) }
            startWork?.state?.isFinished == false -> Unit
            previewWork?.state == WorkInfo.State.FAILED -> TextButton(onClick = { run(false); refresh++ }) { Text(stringResource(R.string.music_retry)) }
            phase == "preview" -> TextButton(onClick = { run(true); refresh++ }) { Text(stringResource(R.string.catalog_download_start)) }
            operation == null || phase == "complete" -> TextButton(onClick = { run(false, true) }) { Text(stringResource(R.string.catalog_download_preview)) }
            error -> TextButton(onClick = { run(phase == "downloading"); refresh++ }) { Text(stringResource(R.string.music_retry)) }
        }
    }, dismissButton = { TextButton(onClick = { open = false }) { Text(stringResource(android.R.string.cancel)) } })
}
