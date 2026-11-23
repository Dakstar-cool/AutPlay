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
    val profile = settings?.activeServerProfileId ?: return
    val user = settings?.activeUserId ?: return
    val device = settings?.deviceId ?: return
    val title = stringResource(when (kind) {
        "artist" -> R.string.catalog_download_artist
        "release" -> R.string.catalog_download_release
        else -> R.string.catalog_download_track
    })
    var open by rememberSaveable(profile.value, user.value, id) { mutableStateOf(false) }
    var alternatives by rememberSaveable(id) { mutableStateOf(false) }
    OutlinedButton(onClick = { open = true }) { Text(title) }
    if (!open) return
    val key = "${profile.value}:${user.value}:$kind:$id:$alternatives"
    val prefs = remember(context) { context.getSharedPreferences("catalog-download-operations", 0) }
    var operation by remember(key) { mutableStateOf(prefs.getString(key, null)) }
    var state by remember(operation) { mutableStateOf<JsonObject?>(null) }
    var error by remember(operation) { mutableStateOf(false) }
    val phase = state?.get("phase")?.jsonPrimitive?.content
    fun run(start: Boolean, fresh: Boolean = false) {
        val op = operation?.takeUnless { fresh } ?: UUID.randomUUID().toString()
        prefs.edit().putString(key, op).apply()
        operation = op
        error = false
        CatalogDownloadWorker.enqueue(context, profile.value, user.value, op, kind, id, alternatives, start)
    }
    var refresh by remember(operation) { mutableIntStateOf(0) }
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
                Row { Checkbox(checked = alternatives, onCheckedChange = { alternatives = it })
                    Text(stringResource(R.string.catalog_download_alternatives)) }
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
            if (operation != null && phase !in setOf("preview", "complete")) LinearProgressIndicator()
            if (error) Text(stringResource(R.string.music_catalogue_unavailable))
        }
    }, confirmButton = {
        when {
            phase == "preview" -> TextButton(onClick = { run(true); refresh++ }) { Text(stringResource(R.string.catalog_download_start)) }
            operation == null || phase == "complete" -> TextButton(onClick = { run(false, true) }) { Text(stringResource(R.string.catalog_download_preview)) }
            error -> TextButton(onClick = { run(phase == "downloading"); refresh++ }) { Text(stringResource(R.string.music_retry)) }
        }
    }, dismissButton = { TextButton(onClick = { open = false }) { Text(stringResource(android.R.string.cancel)) } })
}
