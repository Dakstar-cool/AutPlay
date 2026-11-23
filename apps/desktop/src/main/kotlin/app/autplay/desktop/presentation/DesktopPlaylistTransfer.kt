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
import java.nio.file.LinkOption.NOFOLLOW_LINKS
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

/** A saved share is metadata only and can be opened again after more audio has been imported. */
internal data class ReceivedTitle(val token: String, val name: String, val count: Int)

internal class ReceivedPlaylists(private val root: Path) {
    private fun folder(): Path {
        val dir = root.resolve("received-playlists")
        if (!Files.exists(dir, NOFOLLOW_LINKS)) Files.createDirectories(dir)
        check(Files.isDirectory(dir, NOFOLLOW_LINKS) && dir.toRealPath().parent == root)
        return dir
    }

    private fun read(file: Path): SharedPlaylist {
        check(Files.isRegularFile(file, NOFOLLOW_LINKS))
        return Files.newInputStream(file, NOFOLLOW_LINKS).use { SharedPlaylist.decode(it.readNBytes(SharedPlaylist.MAX_FILE_BYTES + 1)) }
    }

    fun save(playlist: SharedPlaylist) {
        val dir = folder()
        val file = dir.resolve(playlist.token + ".autplaylist")
        if (Files.exists(file, NOFOLLOW_LINKS)) {
            check(read(file) == playlist)
            return
        }
        val temp = Files.createTempFile(dir, ".incoming-", ".tmp")
        try {
            Files.write(temp, playlist.encode())
            // Publish a complete closed file, with no overwrite even for simultaneous imports.
            try { Files.createLink(file, temp) }
            catch (_: java.nio.file.FileAlreadyExistsException) { check(read(file) == playlist) }
        } finally { Files.deleteIfExists(temp) }
    }

    fun open(token: String): SharedPlaylist {
        require(UUID.fromString(token).toString() == token)
        return read(folder().resolve(token + ".autplaylist")).also { check(it.token == token) }
    }

    fun page(index: Int): List<ReceivedTitle> {
        require(index >= 0)
        val paths = Files.list(folder()).use { files ->
            files.filter { it.fileName.toString().matches(Regex("[0-9a-f-]{36}\\.autplaylist")) }
                .sorted().skip(index * 12L).limit(13).toList()
        }
        return paths.map { path ->
            val document = open(path.fileName.toString().removeSuffix(".autplaylist"))
            ReceivedTitle(document.token, document.name, document.entries.size)
        }
    }
}

@Composable
internal fun DesktopPlaylistTransfer(owner: Component?, store: DesktopLibraryStore, library: DesktopLibrarySnapshot,
    selected: DesktopPlaylist?, received: ReceivedPlaylists?, music: DesktopOnlineMusic?, lease: AccountLease?, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    val scope = rememberCoroutineScope()
    var review by remember { mutableStateOf<PlaylistReview?>(null) }
    var share by remember { mutableStateOf<DesktopPlaylist?>(null) }
    var url by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var showSaved by remember { mutableStateOf(false) }
    var savedPage by remember { mutableIntStateOf(0) }
    var revision by remember { mutableIntStateOf(0) }
    var saved by remember { mutableStateOf<List<ReceivedTitle>>(emptyList()) }
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
    LaunchedEffect(received, revision, showSaved, savedPage) {
        saved = emptyList()
        if (showSaved && received != null) try { saved = withContext(Dispatchers.IO) { received.page(savedPage) } }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { feedback = if (ru) "Не удалось прочитать полученные плейлисты" else "Could not read received playlists" }
    }
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(enabled = !busy, onClick = { run {
            val file = choosePlaylist(owner, false, ru) ?: return@run
            review = withContext(Dispatchers.IO) {
                val bytes = Files.newInputStream(file).use { it.readNBytes(SharedPlaylist.MAX_FILE_BYTES + 1) }
                reviewPlaylist(SharedPlaylist.decode(bytes), store.state.value)
            }
        } }) { Text(if (ru) "Импорт .autplaylist" else "Import .autplaylist") }
        if (received != null) TextButton(onClick = { showSaved = !showSaved }) {
            Text(if (ru) "Полученные плейлисты" else "Received playlists")
        }
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
    if (showSaved) {
        saved.take(12).forEach { snapshot ->
            TextButton(enabled = !busy, onClick = { run {
                review = withContext(Dispatchers.IO) { reviewPlaylist(checkNotNull(received).open(snapshot.token), store.state.value) }
            } }) { Text("${snapshot.name} · ${snapshot.count}") }
        }
        Row {
            if (savedPage > 0) TextButton(onClick = { savedPage-- }) { Text(if (ru) "Назад" else "Back") }
            if (saved.size > 12) TextButton(onClick = { savedPage++ }) { Text(if (ru) "Далее" else "Next") }
        }
    }
    if (busy) LinearProgressIndicator()
    feedback?.let { Text(it) }
    review?.let { next ->
        val tracks = next.matches.filterNotNull()
        AlertDialog(onDismissRequest = { if (!busy) review = null }, title = { Text(next.file.name) }, text = {
            Column {
            Text(if (ru) "Найдено в вашей музыке: ${tracks.size} из ${next.matches.size}. Недоступно: ${next.missing}."
                else "Matched in your music: ${tracks.size} of ${next.matches.size}. Unavailable: ${next.missing}.")
            androidx.compose.foundation.lazy.LazyColumn(Modifier.heightIn(max = 220.dp)) {
                items(next.file.entries.size) { index ->
                    val item = next.file.tracks[next.file.entries[index]]
                    Text("${index + 1}. " + listOfNotNull(item.artist, item.title).joinToString(" — "),
                        color = if (next.matches[index] == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                }
            }
            if (received != null) TextButton(enabled = !busy, onClick = { run {
                withContext(Dispatchers.IO) { received.save(next.file) }
                review = null
                revision++
                showSaved = true
                feedback = if (ru) "Сохранён полный список треков" else "Saved the complete track list"
            } }) { Text(if (ru) "Сохранить весь список" else "Save complete list") }
            }
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
