package app.autplay.ui.desktop

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopTrack

/** Commands carry stable IDs, never filesystem capabilities. Selection and search are view state. */
public data class DesktopLibraryCallbacks(
    val onImport: () -> Unit = {},
    val onPlayTracks: (List<String>, String) -> Unit = { _, _ -> },
    val onEnqueue: (String) -> Unit = {},
    val onSelectEntry: (String) -> Unit = {},
    val onRemoveEntry: (String) -> Unit = {},
    val onCreatePlaylist: (String) -> Unit = {},
    val onAddToPlaylist: (String, String) -> Unit = { _, _ -> },
    val onRemovePlaylistEntry: (String, String) -> Unit = { _, _ -> },
    val onShuffle: (Boolean) -> Unit = {},
    val onRepeat: (String) -> Unit = {},
)

@Composable
internal fun DesktopLibraryContent(
    section: DesktopSection,
    library: DesktopLibrarySnapshot,
    language: DesktopLanguage,
    importing: Boolean,
    importResult: DesktopImportResult?,
    errorCode: String?,
    callbacks: DesktopLibraryCallbacks,
    modifier: Modifier = Modifier,
) {
    var search by remember { mutableStateOf("") }
    var playlistName by remember { mutableStateOf("") }
    var selectedPlaylistId by remember { mutableStateOf<String?>(null) }
    val selectedPlaylist = library.playlists.firstOrNull { it.id == selectedPlaylistId } ?: library.playlists.firstOrNull()
    val tracksById = remember(library.tracks) { library.tracks.associateBy { it.id } }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        errorCode?.let {
            Text(when (it) {
                "SOURCE_UNAVAILABLE" -> DesktopText.SourceMissing
                "AUDIO_ERROR" -> DesktopText.AudioError
                "AUDIO_UNAVAILABLE" -> DesktopText.PlaybackUnavailable
                "UNSUPPORTED_QUEUE_MODE", "QUEUE_LIMIT_OR_MODE" -> DesktopText.UnsupportedMode
                else -> DesktopText.OperationFailed
            }.inLanguage(language), color = MaterialTheme.colorScheme.error)
        }
        if (section == DesktopSection.Library) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = callbacks.onImport, enabled = !importing) {
                    Text((if (importing) DesktopText.Importing else DesktopText.Import).inLanguage(language))
                }
                Text(DesktopText.LibrarySubtitle.inLanguage(language), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            importResult?.let {
                Text("${DesktopText.Imported.inLanguage(language)}: ${it.imported} · ${DesktopText.Duplicates.inLanguage(language)}: ${it.duplicates} · ${DesktopText.Failed.inLanguage(language)}: ${it.failed}")
                if (it.errorCode != null) Text(DesktopText.OperationFailed.inLanguage(language), color = MaterialTheme.colorScheme.error)
            }
            if (library.tracks.isEmpty()) Text(DesktopText.EmptyLibraryBody.inLanguage(language))
            OutlinedTextField(value = search, onValueChange = { search = it.take(256) }, singleLine = true,
                label = { Text(DesktopText.Search.inLanguage(language)) }, modifier = Modifier.fillMaxWidth())
            val matches = remember(library.tracks, search) {
                library.tracks.filter { search.isBlank() || it.title.contains(search, true) || it.artist.contains(search, true) }
            }
            val visible = matches.take(LIST_LIMIT)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(onClick = { callbacks.onPlayTracks(visible.map { it.id }, if (search.isBlank()) "LIBRARY" else "SEARCH") }, enabled = visible.isNotEmpty()) {
                    Text(DesktopText.PlayAll.inLanguage(language))
                }
                selectedPlaylist?.let { Text("${DesktopText.AddToPlaylist.inLanguage(language)}: ${it.name}", maxLines = 1, overflow = TextOverflow.Ellipsis) }
            }
            PlaylistSelector(library, selectedPlaylist?.id, language) { selectedPlaylistId = it }
            if (matches.size > LIST_LIMIT) Text(DesktopText.ListBound.inLanguage(language))
            if (visible.isEmpty() && library.tracks.isNotEmpty()) Text(DesktopText.NoResults.inLanguage(language))
            LazyColumn(Modifier.weight(1f)) {
                items(visible, key = { it.id }) { track ->
                    TrackRow(track, onPlay = {
                        callbacks.onPlayTracks(visible.dropWhile { it.id != track.id }.map { it.id }, if (search.isBlank()) "LIBRARY" else "SEARCH")
                    }) {
                        OutlinedButton(onClick = { callbacks.onEnqueue(track.id) }) { Text(DesktopText.Enqueue.inLanguage(language)) }
                        selectedPlaylist?.let { playlist ->
                            OutlinedButton(onClick = { callbacks.onAddToPlaylist(playlist.id, track.id) }) { Text(DesktopText.AddToPlaylist.inLanguage(language)) }
                        }
                    }
                }
            }
        }
        if (section == DesktopSection.Playlists) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(playlistName, { playlistName = it.take(200) }, singleLine = true,
                    label = { Text(DesktopText.PlaylistName.inLanguage(language)) }, modifier = Modifier.weight(1f))
                Button(onClick = { callbacks.onCreatePlaylist(playlistName.trim()); playlistName = "" }, enabled = playlistName.isNotBlank()) {
                    Text(DesktopText.CreatePlaylist.inLanguage(language))
                }
            }
            PlaylistSelector(library, selectedPlaylist?.id, language) { selectedPlaylistId = it }
            if (selectedPlaylist == null) Text(DesktopText.EmptyPlaylistsBody.inLanguage(language))
            selectedPlaylist?.let { playlist ->
                val entries = playlist.entries.take(LIST_LIMIT)
                OutlinedButton(onClick = { callbacks.onPlayTracks(entries.map { it.trackId }, "PLAYLIST") }, enabled = entries.isNotEmpty()) {
                    Text(DesktopText.PlayAll.inLanguage(language))
                }
                LazyColumn(Modifier.weight(1f)) {
                    items(entries, key = { it.id }) { entry ->
                        tracksById[entry.trackId]?.let { track ->
                            TrackRow(track, onPlay = { callbacks.onPlayTracks(entries.dropWhile { it.id != entry.id }.map { it.trackId }, "PLAYLIST") }) {
                                OutlinedButton(onClick = { callbacks.onRemovePlaylistEntry(playlist.id, entry.id) }) { Text(DesktopText.Remove.inLanguage(language)) }
                            }
                        }
                    }
                }
            }
        }
        if (section == DesktopSection.Queue) {
            val queue = library.queue
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FilterChip(selected = queue?.shuffleEnabled == true, onClick = { callbacks.onShuffle(queue?.shuffleEnabled != true) },
                    enabled = queue != null, label = { Text(DesktopText.Shuffle.inLanguage(language)) })
                listOf("OFF" to DesktopText.RepeatOff, "ONE" to DesktopText.RepeatOne, "ALL" to DesktopText.RepeatAll).forEach { (mode, label) ->
                    FilterChip(selected = queue?.repeatMode == mode, onClick = { callbacks.onRepeat(mode) }, enabled = queue != null,
                        label = { Text(label.inLanguage(language)) })
                }
            }
            val entries = if (queue?.shuffleEnabled == true) {
                val byId = queue.entries.associateBy { it.id }
                queue.shuffleOrder.mapNotNull { byId[it] }
            } else queue?.entries.orEmpty()
            if (entries.isEmpty()) Text(DesktopText.EmptyQueue.inLanguage(language))
            LazyColumn(Modifier.weight(1f)) {
                items(entries.take(LIST_LIMIT), key = { it.id }) { entry ->
                    val track = tracksById[entry.trackId]
                    Row(Modifier.fillMaxWidth().clickable { callbacks.onSelectEntry(entry.id) }.padding(vertical = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (entry.id == queue?.currentEntryId) "▶" else "♫", color = MaterialTheme.colorScheme.primary)
                        Column(Modifier.weight(1f)) {
                            Text(track?.title ?: DesktopText.SourceMissing.inLanguage(language), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(track?.artist.orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        OutlinedButton(onClick = { callbacks.onRemoveEntry(entry.id) }) { Text(DesktopText.Remove.inLanguage(language)) }
                    }
                    HorizontalDivider()
                }
            }
        }
        if (section == DesktopSection.History) {
            if (library.history.isEmpty()) Text(DesktopText.EmptyHistory.inLanguage(language))
            LazyColumn(Modifier.weight(1f)) {
                items(library.history.sortedByDescending { it.startedAtMs }.take(LIST_LIMIT), key = { it.id }) { event ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                        Text(tracksById[event.trackId]?.title ?: DesktopText.SourceMissing.inLanguage(language), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("${DesktopText.Played.inLanguage(language)}: ${formatPlaybackTime(event.playedMs)}" +
                            if (!event.finalized) " · ${DesktopText.ActiveSession.inLanguage(language)}" else "", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun PlaylistSelector(library: DesktopLibrarySnapshot, selectedId: String?, language: DesktopLanguage, onSelect: (String) -> Unit) {
    if (library.playlists.isEmpty()) return
    Text(DesktopText.SelectPlaylist.inLanguage(language), style = MaterialTheme.typography.labelMedium)
    LazyRow(Modifier.fillMaxWidth().heightIn(max = 64.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(library.playlists.take(1_000), key = { it.id }) { playlist ->
            FilterChip(selected = playlist.id == selectedId, onClick = { onSelect(playlist.id) },
                label = { Text(playlist.name, maxLines = 1, overflow = TextOverflow.Ellipsis) })
        }
    }
}

@Composable
private fun TrackRow(track: DesktopTrack, onPlay: () -> Unit, actions: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onPlay).padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
            Text(track.artist, maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(track.durationMs?.let(::formatPlaybackTime) ?: "--:--", style = MaterialTheme.typography.labelMedium)
        actions()
    }
    HorizontalDivider()
}

private const val LIST_LIMIT: Int = 10_000
