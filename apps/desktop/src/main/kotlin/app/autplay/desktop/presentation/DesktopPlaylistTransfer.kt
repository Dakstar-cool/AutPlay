package app.autplay.desktop.presentation

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.*
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.library.SharedPlaylist
import app.autplay.ui.desktop.DesktopLanguage
import java.awt.Component
import java.awt.EventQueue
import java.awt.Toolkit
import java.awt.datatransfer.StringSelection
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.UUID
import javax.swing.JFileChooser
import javax.swing.JOptionPane
import javax.swing.filechooser.FileNameExtensionFilter
import kotlinx.coroutines.*
import kotlinx.serialization.json.*

internal data class PlaylistReview(val file: SharedPlaylist, val matches: List<String?>, val copyId: String = UUID.randomUUID().toString()) {
    val missing: Int get() = matches.count { it == null }
}

internal fun reviewPlaylist(file: SharedPlaylist, library: DesktopLibrarySnapshot): PlaylistReview {
    val labels = library.tracks.filter { it.collectionState == "ACTIVE" }.groupBy {
        it.title.trim().lowercase() to it.artist.trim().lowercase()
    }
    val resolved = file.tracks.map { entry ->
        val candidates = labels[entry.title?.trim()?.lowercase() to entry.artist.orEmpty().trim().lowercase()].orEmpty()
        candidates.singleOrNull { entry.durationMs == null || entry.durationMs == it.durationMs }?.id
    }
    return PlaylistReview(file, file.entries.map(resolved::get))
}

internal fun portablePlaylist(playlist: DesktopPlaylist, library: DesktopLibrarySnapshot): ByteArray {
    val body = playlistShareBody(playlist, library)
    return buildJsonObject {
        put("format", "autplay-playlist-v1")
        put("token", UUID.randomUUID().toString())
        put("name", body.getValue("name"))
        put("description", body.getValue("description"))
        put("entries", body.getValue("entries"))
        putJsonArray("tracks") {
            body.getValue("tracks").jsonArray.forEach { item ->
                add(buildJsonObject {
                    for (field in listOf("title", "artist", "duration_ms")) put(field, item.jsonObject.getValue(field))
                    put("recording_id", JsonNull)
                })
            }
        }
    }.toString().encodeToByteArray().also { SharedPlaylist.decode(it) }
}

private fun choosePlaylist(owner: Component?, save: Boolean, ru: Boolean): Path? {
    check(EventQueue.isDispatchThread())
    val dialog = JFileChooser().apply { fileFilter = FileNameExtensionFilter("AutPlay (*.autplaylist)", "autplaylist") }
    val result = if (save) dialog.showSaveDialog(owner) else dialog.showOpenDialog(owner)
    if (result != JFileChooser.APPROVE_OPTION) return null
    val chosen = dialog.selectedFile.toPath().toAbsolutePath()
    val file = if (save && !chosen.fileName.toString().endsWith(".autplaylist", true))
        chosen.resolveSibling("${chosen.fileName}.autplaylist") else chosen
    if (save && Files.exists(file) && JOptionPane.showConfirmDialog(owner,
        if (ru) "Заменить выбранный файл?" else "Replace the selected file?", "AutPlay",
        JOptionPane.YES_NO_OPTION) != JOptionPane.YES_OPTION) return null
    return file
}

private fun exportPlaylist(file: Path, bytes: ByteArray) {
    val temp = Files.createTempFile(file.parent, ".autplay-", ".tmp")
    try {
        Files.write(temp, bytes)
        try { Files.move(temp, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING) }
        catch (_: java.nio.file.AtomicMoveNotSupportedException) { Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING) }
    } finally { Files.deleteIfExists(temp) }
}

@Composable
internal fun DesktopPlaylistTransfer(owner: Component?, store: DesktopLibraryStore, library: DesktopLibrarySnapshot,
    selected: DesktopPlaylist?, music: DesktopOnlineMusic?, lease: AccountLease?, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    val scope = rememberCoroutineScope()
    var review by remember { mutableStateOf<PlaylistReview?>(null) }
    var share by remember { mutableStateOf<DesktopPlaylist?>(null) }
    var url by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var feedback by remember { mutableStateOf<String?>(null) }
    fun run(action: suspend () -> Unit) {
        if (busy) return
        busy = true
        feedback = null
        scope.launch {
            try { action() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { feedback = if (ru) "Не удалось выполнить действие с плейлистом." else "Playlist action failed." }
            finally { busy = false }
        }
    }
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(enabled = !busy, onClick = { run {
            val file = choosePlaylist(owner, false, ru) ?: return@run
            review = withContext(Dispatchers.IO) {
                val bytes = Files.newInputStream(file).use { it.readNBytes(SharedPlaylist.MAX_FILE_BYTES + 1) }
                reviewPlaylist(SharedPlaylist.decode(bytes), store.state.value)
            }
        } }) { Text(if (ru) "Импорт .autplaylist" else "Import .autplaylist") }
        if (selected != null) {
            TextButton(enabled = !busy, onClick = { run {
                val file = choosePlaylist(owner, true, ru) ?: return@run
                withContext(Dispatchers.IO) { exportPlaylist(file, portablePlaylist(selected, library)) }
                feedback = if (ru) "Файл сохранён" else "File saved"
            } }) { Text(if (ru) "Экспорт" else "Export") }
            if (music != null && lease != null) TextButton(enabled = !busy, onClick = { share = selected }) {
                Text(if (ru) "Поделиться ссылкой" else "Share link")
            }
        }
    }
    if (busy) LinearProgressIndicator()
    feedback?.let { Text(it) }
    review?.let { next ->
        val tracks = next.matches.filterNotNull()
        AlertDialog(onDismissRequest = { if (!busy) review = null }, title = { Text(next.file.name) }, text = {
            Text(if (ru) "Найдено в вашей музыке: ${tracks.size} из ${next.matches.size}. Пропущено: ${next.missing}."
                else "Matched in your music: ${tracks.size} of ${next.matches.size}. Skipped: ${next.missing}.")
        }, confirmButton = { TextButton(enabled = !busy && (tracks.isNotEmpty() || next.matches.isEmpty()), onClick = { run {
            store.importPlaylist(next.copyId, next.file.name, next.file.description, tracks)
            review = null
            feedback = if (ru) "Плейлист добавлен" else "Playlist added"
        } }) { Text(if (ru) "Импортировать (${tracks.size})" else "Import (${tracks.size})") } },
            dismissButton = { TextButton(enabled = !busy, onClick = { review = null }) { Text(if (ru) "Отмена" else "Cancel") } })
    }
    share?.let { target ->
        AlertDialog(onDismissRequest = { share = null }, title = { Text(target.name) }, text = {
            Text(if (ru) "Список треков будет доступен всем, у кого есть постоянная ссылка."
                else "Anyone with the permanent link can access this track list.")
        }, confirmButton = { TextButton(enabled = !busy, onClick = {
            share = null
            if (music != null && lease != null) run { url = music.publish(lease, target, library) }
        }) { Text(if (ru) "Создать ссылку" else "Create link") } },
            dismissButton = { TextButton(onClick = { share = null }) { Text(if (ru) "Отмена" else "Cancel") } })
    }
    url?.let { value ->
        AlertDialog(onDismissRequest = { url = null }, title = { Text(if (ru) "Ссылка на плейлист" else "Playlist link") },
            text = { androidx.compose.foundation.text.selection.SelectionContainer { Text(value) } },
            confirmButton = { TextButton(onClick = { run {
                Toolkit.getDefaultToolkit().systemClipboard.setContents(StringSelection(value), null)
                url = null
            } }) { Text(if (ru) "Копировать" else "Copy") } },
            dismissButton = { TextButton(onClick = { url = null }) { Text(if (ru) "Закрыть" else "Close") } })
    }
}
