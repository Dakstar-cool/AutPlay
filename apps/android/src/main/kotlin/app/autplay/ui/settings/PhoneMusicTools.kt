package app.autplay.ui.settings

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.work.WorkManager
import app.autplay.R
import app.autplay.application.importing.PhoneMusicRepository
import app.autplay.application.importing.PhoneMusicTrack
import app.autplay.data.settings.NonSecretSettings
import app.autplay.work.PhoneMusicWork
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@Composable
internal fun PhoneMusicTools(settings: NonSecretSettings) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var open by remember { mutableStateOf(false) }
    var upload by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var permissionDenied by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    var tracks by remember { mutableStateOf(emptyList<PhoneMusicTrack>()) }
    var selected by remember { mutableStateOf(emptySet<String>()) }
    val work by remember(context) { WorkManager.getInstance(context).getWorkInfosByTagFlow(PhoneMusicWork.TAG) }
        .collectAsState(initial = emptyList())
    val uploads by remember(context) { WorkManager.getInstance(context).getWorkInfosByTagFlow(app.autplay.work.PhoneVaultUploadWork.TAG) }
        .collectAsState(initial = emptyList())
    fun scan() {
        open = true
        loading = true
        failed = false
        selected = emptySet()
        scope.launch {
            try { tracks = PhoneMusicRepository(context).scan()
            } catch (error: CancellationException) { throw error
            } catch (_: Exception) { failed = true
            } finally { loading = false }
        }
    }
    val permissions = remember {
        when {
            Build.VERSION.SDK_INT >= 33 -> arrayOf(Manifest.permission.READ_MEDIA_AUDIO)
            Build.VERSION.SDK_INT >= 29 -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
            else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
        permissionDenied = permissions.any { grants[it] != true }
        if (!permissionDenied) scan()
    }
    fun start(send: Boolean) {
        upload = send
        permissionDenied = false
        if (permissions.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED }) scan()
        else permission.launch(permissions)
    }
    Text(stringResource(R.string.music_scan_body))
    OutlinedButton(enabled = settings.activeServerProfileId != null, modifier = Modifier.fillMaxWidth().testTag("metadata-backfill"), onClick = {
        settings.activeServerProfileId?.let { app.autplay.work.TrackMetadataWork.backfill(context, it.value) }
    }) { Text(stringResource(R.string.metadata_backfill)) }
    Button(onClick = { start(false) }, modifier = Modifier.fillMaxWidth().testTag("phone-music-scan")) {
        Text(stringResource(R.string.music_scan_phone))
    }
    Text(stringResource(R.string.music_upload_body))
    OutlinedButton(onClick = { start(true) }, enabled = settings.activeServerProfileId != null,
        modifier = Modifier.fillMaxWidth().testTag("phone-music-upload")) {
        Text(stringResource(R.string.music_upload_title))
    }
    if (permissionDenied) Text(stringResource(R.string.music_permission_required), color = MaterialTheme.colorScheme.error)
    if (work.isNotEmpty()) Text(stringResource(R.string.music_transfer_progress,
        work.count { it.state == androidx.work.WorkInfo.State.SUCCEEDED },
        work.count { !it.state.isFinished }, work.count { it.state == androidx.work.WorkInfo.State.FAILED }))
    if (uploads.isNotEmpty()) {
        Text(stringResource(R.string.music_upload_title))
        Text(stringResource(R.string.music_transfer_progress,
            uploads.count { it.state == androidx.work.WorkInfo.State.SUCCEEDED },
            uploads.count { !it.state.isFinished }, uploads.count { it.state == androidx.work.WorkInfo.State.FAILED }))
    }
    if (open) AlertDialog(
        onDismissRequest = { open = false },
        title = { Text(stringResource(if (upload) R.string.music_upload_title else R.string.music_phone_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when {
                    loading -> Text(stringResource(R.string.music_scan_loading))
                    failed -> Text(stringResource(R.string.music_scan_failed), color = MaterialTheme.colorScheme.error)
                    tracks.isEmpty() -> Text(stringResource(R.string.music_scan_empty))
                    else -> {
                        TextButton(onClick = { selected = if (selected.size == tracks.size) emptySet() else tracks.map { it.uri }.toSet() }) {
                            Text(stringResource(if (selected.size == tracks.size) R.string.music_clear_selection else R.string.music_select_all))
                        }
                        LazyColumn(Modifier.heightIn(max = 390.dp)) {
                            items(tracks, key = { it.uri }) { track ->
                                fun toggle() { selected = if (track.uri in selected) selected - track.uri else selected + track.uri }
                                Row(Modifier.fillMaxWidth().clickable { toggle() }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Checkbox(checked = track.uri in selected, onCheckedChange = { toggle() })
                                    Column {
                                        Text(track.title, style = MaterialTheme.typography.titleSmall, maxLines = 2)
                                        Text(track.artist, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = selected.isNotEmpty() && !loading, onClick = {
                PhoneMusicWork.enqueue(context, tracks.filter { it.uri in selected }, settings.activeServerProfileId?.value, upload)
                open = false
            }) { Text(stringResource(if (upload) R.string.music_upload_selected else R.string.music_import_selected, selected.size)) }
        },
        dismissButton = { TextButton(onClick = { open = false }) { Text(stringResource(R.string.music_close)) } },
    )
}
