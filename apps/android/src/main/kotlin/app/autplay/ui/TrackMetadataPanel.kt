package app.autplay.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.work.WorkInfo
import androidx.work.WorkManager
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.library.CoreTrackDetail
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.work.TrackMetadataWork
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

@Composable
internal fun rememberTrackArtwork(trackId: String?): Painter? {
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = settings?.activeServerProfileId?.value ?: "legacy-unscoped"
    val database = remember(context) { AutPlayRuntime.database(context) }
    val art by remember(profile, trackId) { database.trackMetadataDao().observeArtwork(profile, trackId.orEmpty()) }.collectAsState(initial = null)
    val eligibleArt = art?.takeIf { it.serverProfileId == profile }
    val imageKey = "$profile/$trackId/${eligibleArt?.filePath}"
    val loaded by produceState<Pair<String, android.graphics.Bitmap>?>(null, imageKey) {
        value = null
        value = withContext(Dispatchers.IO) {
            eligibleArt?.filePath?.let { path ->
                val file = File(path)
                val root = File(context.filesDir, "metadata-art").canonicalPath + File.separator
                if (file.canonicalPath.startsWith(root) && file.isFile && file.length() <= 2_097_152) BitmapFactory.decodeFile(path)?.let { imageKey to it } else null
            }
        }
    }
    val bitmap = loaded?.takeIf { it.first == imageKey }?.second
    return remember(bitmap) { bitmap?.let { BitmapPainter(it.asImageBitmap()) } }
}

@Composable
internal fun TrackMetadataPanel(detail: CoreTrackDetail) {
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = settings?.activeServerProfileId?.value
    val metadata = detail.metadata
    val work by remember(detail.localUserTrackRefId) { WorkManager.getInstance(context).getWorkInfosByTagFlow(TrackMetadataWork.tag(detail.localUserTrackRefId)) }.collectAsState(initial = emptyList())
    val busy = work.any { !it.state.isFinished }
    var editing by remember(detail.localUserTrackRefId) { mutableStateOf(false) }
    var editRevision by remember(detail.localUserTrackRefId) { mutableLongStateOf(0) }
    var saveWorkId by remember(detail.localUserTrackRefId) { mutableStateOf<java.util.UUID?>(null) }
    val saveState = work.firstOrNull { it.id == saveWorkId }?.state
    LaunchedEffect(saveState) { if (saveState == WorkInfo.State.SUCCEEDED) { editing = false; saveWorkId = null } }
    val labels = linkedMapOf("title" to R.string.metadata_title_field, "artist" to R.string.metadata_artist,
        "album" to R.string.detail_album, "album_artist" to R.string.metadata_album_artist,
        "release_date" to R.string.metadata_release_date, "original_release_date" to R.string.metadata_first_release,
        "recording_date" to R.string.metadata_recording_date, "label" to R.string.metadata_label,
        "track_number" to R.string.metadata_track_number, "disc_number" to R.string.metadata_disc_number)
    Column(Modifier.fillMaxWidth().testTag("track-metadata"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.metadata_heading), style = MaterialTheme.typography.titleMedium)
        Text(stringResource(when (metadata?.state) {
            "LOCAL" -> R.string.metadata_local; "READY" -> R.string.metadata_ready
            "REVIEW" -> R.string.metadata_review; "NOT_FOUND" -> R.string.metadata_not_found
            "QUEUED", "RETRY" -> R.string.metadata_pending; "FAILED" -> R.string.metadata_failed
            else -> R.string.metadata_missing
        }))
        labels.filterKeys { it !in setOf("title", "artist", "album") }.forEach { (key, label) ->
            metadata?.text(key)?.let { value ->
                val source = (metadata.provenance[key] as? JsonObject)?.get("source")?.jsonPrimitive?.content
                val sourceLabel = when (source) { "USER" -> stringResource(R.string.metadata_manual); "EMBEDDED" -> stringResource(R.string.metadata_from_file); else -> "MusicBrainz" }
                Text("${stringResource(label)}: $value · $sourceLabel", style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (!busy && work.any { it.state == WorkInfo.State.FAILED }) Text(stringResource(R.string.metadata_command_failed), color = MaterialTheme.colorScheme.error)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(enabled = profile != null && !busy, onClick = {
                profile?.let { TrackMetadataWork.command(context, it, detail.localUserTrackRefId, metadata?.revision ?: 0, "REFRESH") }
            }, modifier = Modifier.testTag("metadata-refresh")) { Text(stringResource(R.string.metadata_refresh)) }
            OutlinedButton(enabled = profile != null && !busy, onClick = { editRevision = metadata?.revision ?: 0; saveWorkId = null; editing = true }, modifier = Modifier.testTag("metadata-edit")) { Text(stringResource(R.string.metadata_edit)) }
        }
        if (metadata?.state == "REVIEW") metadata.candidates.forEach { candidate ->
            val fields = candidate["fields"]?.jsonObject ?: JsonObject(emptyMap())
            val candidateId = candidate["candidate_id"]?.jsonPrimitive?.content.orEmpty()
            OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(fields["title"]?.jsonPrimitive?.content.orEmpty(), style = MaterialTheme.typography.titleSmall)
                    Text(listOf("artist", "album", "release_date", "country").mapNotNull { fields[it]?.jsonPrimitive?.contentOrNull }.joinToString(" · "))
                    TextButton(enabled = profile != null && !busy, onClick = {
                        profile?.let { TrackMetadataWork.command(context, it, detail.localUserTrackRefId, metadata.revision, "SELECT", candidate = candidateId) }
                    }) { Text(stringResource(R.string.metadata_choose)) }
                }
            }
        }
    }
    if (editing) {
        val values = remember(detail.localUserTrackRefId) {
            mutableStateMapOf<String, String>().apply { labels.keys.forEach { key -> put(key, metadata?.text(key, when (key) {
                "title" -> detail.title; "artist" -> detail.artistName; "album" -> detail.albumName; else -> null
            }).orEmpty()) } }
        }
        val initial = remember(detail.localUserTrackRefId) { values.toMap() }
        val valid = values.all { (key, value) -> value.length <= 500 && (value.isBlank() || when (key) {
            "track_number", "disc_number" -> value.toIntOrNull()?.let { it in 1..9999 } == true
            "release_date", "original_release_date", "recording_date" -> app.autplay.application.library.validMetadataDate(value)
            else -> true
        }) }
        AlertDialog(onDismissRequest = { editing = false }, title = { Text(stringResource(R.string.metadata_edit)) },
            text = { Column(Modifier.heightIn(max = 450.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.metadata_edit_hint))
                if (saveState == WorkInfo.State.FAILED) Text(stringResource(R.string.metadata_command_failed), color = MaterialTheme.colorScheme.error)
                labels.forEach { (key, label) -> OutlinedTextField(value = values[key].orEmpty(), onValueChange = { values[key] = it.take(500) }, enabled = !busy, label = { Text(stringResource(label)) }, singleLine = true) }
            } }, confirmButton = { TextButton(enabled = valid && !busy, onClick = {
                val fields = buildJsonObject { values.filter { (key, value) -> initial[key] != value }.forEach { (key, value) ->
                    if (value.isBlank()) put(key, JsonNull) else if (key in setOf("track_number", "disc_number")) put(key, value.toInt()) else put(key, value.trim())
                } }
                if (saveState == WorkInfo.State.FAILED) editRevision = metadata?.revision ?: editRevision
                profile?.let { saveWorkId = TrackMetadataWork.command(context, it, detail.localUserTrackRefId, editRevision, "EDIT", fields) }
            }) { Text(stringResource(R.string.action_save)) } }, dismissButton = { TextButton(onClick = { editing = false }) { Text(stringResource(R.string.action_cancel)) } })
    }
}
