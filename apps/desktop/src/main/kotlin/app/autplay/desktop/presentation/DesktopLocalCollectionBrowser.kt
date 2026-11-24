package app.autplay.desktop.presentation

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.*
import app.autplay.data.desktop.DesktopEmbeddedMetadataReader
import app.autplay.ui.AutPlayArtworkPlaceholder
import app.autplay.ui.desktop.DesktopArtworkView
import app.autplay.ui.desktop.DesktopCatalogKind
import app.autplay.ui.desktop.DesktopLanguage
import java.nio.file.Path
import kotlinx.coroutines.*

internal data class LocalRelease(val artist: String, val album: String?)
internal data class CollectionTrack(val track: DesktopTrack, val artist: String, val album: String?)
internal data class LocalMusicIndex(val tracks: List<CollectionTrack>, val artwork: Map<String, DesktopArtwork>, val unreadable: Int)

/** Read explicit managed-copy capabilities only. Keep the same thumbnail memory budget as the player. */
internal suspend fun indexLocalCollection(store: DesktopLibraryStore, tracks: List<DesktopTrack>): LocalMusicIndex =
    withContext(Dispatchers.IO) {
        val rows = ArrayList<CollectionTrack>()
        val art = mutableMapOf<String, DesktopArtwork>()
        val illustrated = mutableSetOf<LocalRelease>()
        var total = 0
        var unreadable = 0
        for (track in tracks) {
            ensureActive()
            if (track.collectionState != "ACTIVE") continue
            val embedded = try {
                store.resolveMetadataFile(track.id)?.let { path -> runInterruptible { DesktopEmbeddedMetadataReader.read(Path.of(path), track.byteSize) } }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { null }
            if (embedded == null) unreadable++
            val artist = track.artist.ifBlank { embedded?.artist.orEmpty() }
            val album = embedded?.album?.trim()?.takeIf(String::isNotEmpty)
            rows += CollectionTrack(track, artist, album)
            val cover = embedded?.artwork
            if (cover != null && total + cover.byteSize <= DesktopEmbeddedMetadataLimits.MAX_CACHE_BYTES &&
                illustrated.add(LocalRelease(artist, album))) {
                art[track.id] = cover
                total += cover.byteSize
            }
        }
        LocalMusicIndex(rows, art, unreadable)
    }

@Composable
internal fun DesktopLocalCollectionBrowser(data: LocalMusicIndex?, kind: DesktopCatalogKind, initialAlbum: LocalRelease?,
    language: DesktopLanguage, play: (List<String>) -> Unit) {
    val ru = language == DesktopLanguage.Russian
    if (data == null) { LinearProgressIndicator(); return }
    var artist by remember(kind) { mutableStateOf<String?>(null) }
    var album by remember(kind, initialAlbum) { mutableStateOf(initialAlbum) }
    var query by remember(kind, artist, album) { mutableStateOf("") }
    var page by remember(kind, artist, album, query) { mutableIntStateOf(0) }
    val unknown = if (ru) "Без альбома" else "Unknown album"
    val unknownArtist = if (ru) "Неизвестный исполнитель" else "Unknown artist"
    val artists = kind == DesktopCatalogKind.Artist && artist == null
    val titleOf: (LocalRelease) -> String = { if (artists) it.artist.ifBlank { unknownArtist } else it.album ?: unknown }
    val groups = remember(data, artist, artists) {
        data.tracks.filter { artist == null || it.artist == artist }.groupBy { LocalRelease(it.artist, if (artists) null else it.album) }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (artist != null || album != null) TextButton(onClick = { if (album != null) album = null else artist = null }) {
            Text(if (ru) "Назад" else "Back")
        }
        (album?.let { it.album ?: unknown } ?: artist)?.let { Text(it, style = MaterialTheme.typography.headlineSmall) }
        OutlinedTextField(query, { query = it.take(200) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            label = { Text(if (ru) "Найти в коллекции" else "Find in collection") })
        val size: Int
        if (album != null) {
            val rows = groups[album].orEmpty()
            val matches = rows.filter { it.track.title.contains(query, ignoreCase = true) }.sortedBy { it.track.title.lowercase() }
            size = matches.size
            if (rows.size in 1..DesktopLocalLimits.MAX_QUEUE_ENTRIES) Button(onClick = { play(rows.map { it.track.id }) }) {
                Text(if (ru) "Слушать альбом" else "Play album")
            }
            matches.drop(page * 24).take(24).forEach { item ->
                ListItem(headlineContent = { Text(item.track.title) }, supportingContent = { Text(item.artist) },
                    modifier = Modifier.clickable { play(listOf(item.track.id)) })
            }
        } else {
            val keys = groups.keys.filter { titleOf(it).contains(query, true) || it.artist.contains(query, true) }
                .sortedWith(compareBy({ titleOf(it).lowercase() }, { it.artist.lowercase() }))
            size = keys.size
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                val columns = ((maxWidth + 10.dp) / 166.dp).toInt().coerceIn(1, 6)
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    keys.drop(page * 24).take(24).chunked(columns).forEach { row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEach { group ->
                                val items = groups.getValue(group)
                                val artId = items.firstOrNull { it.track.id in data.artwork }?.track?.id
                                Surface(onClick = { if (artists) artist = group.artist else album = group }, modifier = Modifier.weight(1f),
                                    shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer) {
                                    Column {
                                        BoxWithConstraints(Modifier.fillMaxWidth().aspectRatio(1f)) {
                                            if (artId != null) DesktopArtworkView(artId, data.artwork[artId], language, maxWidth)
                                            else AutPlayArtworkPlaceholder(titleOf(group), Modifier.fillMaxSize())
                                        }
                                        Text(titleOf(group), Modifier.padding(8.dp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                        if (!artists) Text(group.artist, Modifier.padding(start = 8.dp, bottom = 8.dp), maxLines = 1,
                                            overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                                    }
                                }
                            }
                            repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                }
            }
        }
        if (size == 0) Text(if (ru) "Ничего не найдено" else "No results")
        if (data.unreadable > 0) Text(if (ru) "Не удалось прочитать теги у ${data.unreadable} треков."
            else "Could not read tags for ${data.unreadable} tracks.", style = MaterialTheme.typography.bodySmall)
        Row {
            if (page > 0) TextButton(onClick = { page-- }) { Text(if (ru) "Назад по списку" else "Previous page") }
            if ((page + 1) * 24 < size) TextButton(onClick = { page++ }) { Text(if (ru) "Далее" else "Next page") }
        }
    }
}

@Composable
internal fun DesktopAlbumsRail(index: LocalMusicIndex, language: DesktopLanguage, open: (LocalRelease) -> Unit) {
    val groups = remember(index) {
        index.tracks.filter { it.album != null }.groupBy { LocalRelease(it.artist, it.album) }.entries.take(12)
    }
    if (groups.isEmpty()) return
    Text(if (language == DesktopLanguage.Russian) "Альбомы" else "Albums", style = MaterialTheme.typography.titleLarge)
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        groups.forEach { (release, items) ->
            Surface(onClick = { open(release) }, modifier = Modifier.width(132.dp),
                shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer) {
                Column {
                    val artId = items.firstOrNull { it.track.id in index.artwork }?.track?.id
                    if (artId != null) DesktopArtworkView(artId, index.artwork[artId], language, 132.dp)
                    else AutPlayArtworkPlaceholder(release.album.orEmpty(), Modifier.size(132.dp))
                    Text(release.album.orEmpty(), Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                    Text(release.artist, Modifier.padding(start = 8.dp, bottom = 8.dp), maxLines = 1,
                        overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
