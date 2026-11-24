package app.autplay.ui

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.library.SharedPlaylist
import app.autplay.application.library.playlistPublication
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private fun NonSecretSettings.playlistBinding(): ClientEventBinding? {
    val profile = activeServerProfileId ?: return null
    return ClientEventBinding(checkNotNull(activeUserId), checkNotNull(deviceId), profile)
}

@Composable
internal fun SharePlaylistAction(playlistId: String) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var confirm by rememberSaveable(playlistId) { mutableStateOf(false) }
    var busy by remember(playlistId) { mutableStateOf(false) }
    var error by remember(playlistId) { mutableIntStateOf(0) }
    OutlinedButton(onClick = { error = 0; confirm = true }) { Text(stringResource(R.string.playlist_share)) }
    if (confirm) AutPlayDialog(
        onDismissRequest = { if (!busy) confirm = false },
        title = { Text(stringResource(R.string.playlist_share)) },
        text = { Column {
            Text(stringResource(R.string.playlist_share_permanent))
            if (error != 0) Text(stringResource(error))
        } },
        confirmButton = { TextButton(enabled = !busy, onClick = {
            busy = true
            scope.launch {
                try {
                    val binding = applicationNonSecretSettingsStore(context).settings.first().playlistBinding()
                    if (binding == null) error = R.string.playlist_share_needs_server
                    else {
                        val body = playlistPublication(AutPlayRuntime.database(context), playlistId, binding.serverProfileId.value)
                        val url = AutPlayRuntime.serverFeatures(context, binding).publishPlaylist(body)
                        val intent = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, url)
                        context.startActivity(Intent.createChooser(intent, null))
                        confirm = false
                    }
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { error = R.string.playlist_share_error }
                finally { busy = false }
            }
        }) { Text(stringResource(R.string.playlist_share_link)) } },
        dismissButton = { TextButton(enabled = !busy, onClick = { confirm = false }) { Text(stringResource(android.R.string.cancel)) } },
    )
}

@Composable
internal fun ImportPlaylistAction() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = settings?.activeServerProfileId
    var uri by rememberSaveable(profile) { mutableStateOf<String?>(null) }
    var operation by rememberSaveable(profile) { mutableStateOf<String?>(null) }
    var snapshot by remember(uri) { mutableStateOf<SharedPlaylist?>(null) }
    var error by remember(uri) { mutableIntStateOf(0) }
    var busy by remember(uri) { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { selected ->
        if (selected != null) {
            operation = LocalId.random().value
            uri = selected.toString()
        }
    }
    OutlinedButton(enabled = settings != null, onClick = { picker.launch(arrayOf("*/*")) }) {
        Text(stringResource(R.string.playlist_import_file))
    }
    LaunchedEffect(uri) {
        val selected = uri?.let(Uri::parse) ?: return@LaunchedEffect
        try {
            snapshot = withContext(Dispatchers.IO) {
                require(selected.scheme == "content")
                checkNotNull(context.contentResolver.openInputStream(selected)).use { input ->
                    val buffer = ByteArray(8192)
                    val output = ByteArrayOutputStream()
                    while (true) {
                        val size = input.read(buffer)
                        if (size < 0) break
                        require(output.size() + size <= SharedPlaylist.MAX_FILE_BYTES)
                        output.write(buffer, 0, size)
                    }
                    SharedPlaylist.decode(output.toByteArray())
                }
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { error = R.string.playlist_invalid_file }
    }
    if (uri == null) return
    val document = snapshot
    AutPlayDialog(
        onDismissRequest = { if (!busy) uri = null },
        title = { Text(document?.name ?: stringResource(R.string.playlist_import_file)) },
        text = { Column {
            if (document != null) Text(stringResource(R.string.playlist_save_count, document.entries.size))
            if (error != 0) Text(stringResource(error))
            if (busy || (document == null && error == 0)) LinearProgressIndicator()
        } },
        confirmButton = { TextButton(enabled = document != null && !busy, onClick = {
            val copy = document ?: return@TextButton
            val copyId = LocalId(checkNotNull(operation))
            busy = true
            scope.launch {
                try {
                    val current = applicationNonSecretSettingsStore(context).settings.first()
                    check(current.activeServerProfileId == profile)
                    val repository = LibraryVerticalSliceRepository(AutPlayRuntime.database(context),
                        syncScheduler = AutPlayRuntime.syncScheduler(context))
                    repository.saveSharedPlaylist(current.playlistBinding(), copyId, copy, System.currentTimeMillis())
                    Toast.makeText(context, R.string.playlist_copy_saved, Toast.LENGTH_SHORT).show()
                    uri = null
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { error = R.string.playlist_save_error }
                finally { busy = false }
            }
        }) { Text(stringResource(R.string.playlist_save_copy)) } },
        dismissButton = { TextButton(enabled = !busy, onClick = { uri = null }) { Text(stringResource(android.R.string.cancel)) } },
    )
}
