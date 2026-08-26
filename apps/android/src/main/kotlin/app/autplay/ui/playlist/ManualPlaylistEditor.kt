package app.autplay.ui.playlist

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

/** UI identity is the playlist-entry ID, never the repeated track ID. */
public data class ManualPlaylistEntryUi(
    public val entryId: String,
    public val trackRefId: String,
    public val title: String,
    public val subtitle: String? = null,
    public val playable: Boolean = true,
)

public data class ManualPlaylistUi(
    public val playlistId: String,
    public val name: String,
    public val description: String? = null,
    public val entries: List<ManualPlaylistEntryUi> = emptyList(),
)

/** Stable commands for the local-first playlist mutation boundary. */
public data class ManualPlaylistActions(
    public val create: (name: String, description: String?) -> Unit = { _, _ -> },
    public val rename: (playlistId: String, name: String, description: String?) -> Unit = { _, _, _ -> },
    public val delete: (playlistId: String) -> Unit = {},
    public val addTrack: (playlistId: String, trackRefId: String) -> Unit = { _, _ -> },
    public val removeEntry: (entryId: String) -> Unit = {},
    public val moveEntryBefore: (entryId: String, beforeEntryId: String?) -> Unit = { _, _ -> },
    public val playEntry: (entryId: String) -> Unit = {},
)

public data class ManualPlaylistText(
    public val create: String = "Create playlist",
    public val rename: String = "Rename",
    public val delete: String = "Delete",
    public val cancel: String = "Cancel",
    public val save: String = "Save",
    public val add: String = "Add",
    public val remove: String = "Remove",
    public val moveUp: String = "Move up",
    public val moveDown: String = "Move down",
    public val name: String = "Playlist name",
    public val description: String = "Description (optional)",
    public val selectPlaylist: String = "Choose playlist",
    public val confirmDelete: String = "Delete this playlist?",
    public val empty: String = "This playlist is empty.",
    public val play: String = "Play",
)

public fun normalizeManualPlaylistMetadata(name: String, description: String?): ManualPlaylistMetadata? {
    val normalizedName = name.trim()
    val normalizedDescription = description?.trim()?.takeIf(String::isNotEmpty)
    return if (normalizedName.length in 1..120 && (normalizedDescription == null || normalizedDescription.length <= 500)) {
        ManualPlaylistMetadata(normalizedName, normalizedDescription)
    } else null
}

public data class ManualPlaylistMetadata(public val name: String, public val description: String?)

/**
 * Reusable editor for a manual playlist.  It deliberately uses `entryId` for remove/reorder so
 * duplicate track occurrences remain independently editable.
 */
@Composable
public fun ManualPlaylistEditor(
    playlist: ManualPlaylistUi,
    actions: ManualPlaylistActions,
    modifier: Modifier = Modifier,
    text: ManualPlaylistText = ManualPlaylistText(),
) {
    var metadataDialog by remember(playlist.playlistId) { mutableStateOf(false) }
    var deleteDialog by remember(playlist.playlistId) { mutableStateOf(false) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { metadataDialog = true }) { Text(text.rename) }
            OutlinedButton(onClick = { deleteDialog = true }) { Text(text.delete) }
        }
        if (playlist.entries.isEmpty()) Text(text.empty)
        if (playlist.entries.isNotEmpty()) {
            LazyColumn(
                modifier = Modifier.fillMaxWidth().heightIn(max = 600.dp).testTag("playlist-entry-list"),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                itemsIndexed(playlist.entries, key = { _, entry -> entry.entryId }) { index, entry ->
                    Column(
                        modifier = Modifier.fillMaxWidth().testTag("playlist-entry-${entry.entryId}"),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(entry.title)
                        entry.subtitle?.let { Text(it) }
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                enabled = entry.playable,
                                onClick = { actions.playEntry(entry.entryId) },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) { Text(text.play) }
                            OutlinedButton(
                                enabled = index > 0,
                                onClick = { actions.moveEntryBefore(entry.entryId, playlist.entries[index - 1].entryId) },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) { Text(text.moveUp) }
                            OutlinedButton(
                                enabled = index < playlist.entries.lastIndex,
                                onClick = {
                                    // Moving before the item after the next item is the stable "down" operation.
                                    actions.moveEntryBefore(entry.entryId, playlist.entries.getOrNull(index + 2)?.entryId)
                                },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) { Text(text.moveDown) }
                            OutlinedButton(
                                onClick = { actions.removeEntry(entry.entryId) },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) { Text(text.remove) }
                        }
                    }
                }
            }
        }
    }
    if (metadataDialog) {
        PlaylistMetadataDialog(
            initialName = playlist.name,
            initialDescription = playlist.description,
            confirmLabel = text.save,
            cancelLabel = text.cancel,
            nameLabel = text.name,
            descriptionLabel = text.description,
            onDismiss = { metadataDialog = false },
            onConfirm = { metadata -> actions.rename(playlist.playlistId, metadata.name, metadata.description); metadataDialog = false },
        )
    }
    if (deleteDialog) {
        AlertDialog(
            onDismissRequest = { deleteDialog = false },
            text = { Text(text.confirmDelete) },
            confirmButton = { Button(onClick = { actions.delete(playlist.playlistId); deleteDialog = false }) { Text(text.delete) } },
            dismissButton = { OutlinedButton(onClick = { deleteDialog = false }) { Text(text.cancel) } },
        )
    }
}

/** Target chooser used when adding a selected track; callers must provide the exact playlist ID. */
@Composable
public fun AddTrackToPlaylistDialog(
    trackRefId: String,
    playlists: List<ManualPlaylistUi>,
    actions: ManualPlaylistActions,
    onDismiss: () -> Unit,
    text: ManualPlaylistText = ManualPlaylistText(),
) {
    var selectedId by remember(playlists) { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(text.selectPlaylist) },
        text = {
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 360.dp)) {
                items(playlists, key = ManualPlaylistUi::playlistId) { playlist ->
                    Row(
                        modifier = Modifier.fillMaxWidth().testTag("playlist-target-${playlist.playlistId}"),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = selectedId == playlist.playlistId, onClick = { selectedId = playlist.playlistId })
                        Text(playlist.name, modifier = Modifier.padding(start = 8.dp))
                    }
                }
            }
        },
        confirmButton = {
            Button(
                enabled = selectedId != null,
                onClick = { selectedId?.let { actions.addTrack(it, trackRefId) }; onDismiss() },
            ) { Text(text.add) }
        },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text(text.cancel) } },
    )
}

@Composable
public fun CreateManualPlaylistDialog(
    actions: ManualPlaylistActions,
    onDismiss: () -> Unit,
    text: ManualPlaylistText = ManualPlaylistText(),
) {
    PlaylistMetadataDialog(
        initialName = "",
        initialDescription = null,
        confirmLabel = text.create,
        cancelLabel = text.cancel,
        nameLabel = text.name,
        descriptionLabel = text.description,
        onDismiss = onDismiss,
        onConfirm = { metadata -> actions.create(metadata.name, metadata.description); onDismiss() },
    )
}

/** Small library-route surface; detailed entry editing is hosted by [ManualPlaylistEditor]. */
@Composable
public fun ManualPlaylistHub(
    playlists: List<ManualPlaylistUi>,
    selectedTrackRefId: String?,
    actions: ManualPlaylistActions,
    modifier: Modifier = Modifier,
    onOpenPlaylist: (String) -> Unit = {},
    text: ManualPlaylistText = ManualPlaylistText(),
) {
    var createDialog by remember { mutableStateOf(false) }
    var addDialog by remember { mutableStateOf(false) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { createDialog = true }) { Text(text.create) }
        if (selectedTrackRefId != null && playlists.isNotEmpty()) {
            OutlinedButton(onClick = { addDialog = true }) { Text(text.add) }
        }
        if (playlists.isNotEmpty()) {
            LazyColumn(
                modifier = Modifier.fillMaxWidth().heightIn(max = 420.dp).testTag("playlist-hub-list"),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(playlists, key = ManualPlaylistUi::playlistId) { playlist ->
                    OutlinedButton(
                        onClick = { onOpenPlaylist(playlist.playlistId) },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                            .testTag("playlist-open-${playlist.playlistId}"),
                    ) {
                        Text(playlist.name)
                    }
                }
            }
        }
    }
    if (createDialog) CreateManualPlaylistDialog(actions, onDismiss = { createDialog = false }, text = text)
    if (addDialog && selectedTrackRefId != null) {
        AddTrackToPlaylistDialog(selectedTrackRefId, playlists, actions, onDismiss = { addDialog = false }, text = text)
    }
}

@Composable
private fun PlaylistMetadataDialog(
    initialName: String,
    initialDescription: String?,
    confirmLabel: String,
    cancelLabel: String,
    nameLabel: String,
    descriptionLabel: String,
    onDismiss: () -> Unit,
    onConfirm: (ManualPlaylistMetadata) -> Unit,
) {
    var name by remember(initialName) { mutableStateOf(initialName) }
    var description by remember(initialDescription) { mutableStateOf(initialDescription.orEmpty()) }
    val metadata = normalizeManualPlaylistMetadata(name, description)
    AlertDialog(
        onDismissRequest = onDismiss,
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text(nameLabel) }, singleLine = true)
                OutlinedTextField(value = description, onValueChange = { description = it }, label = { Text(descriptionLabel) })
            }
        },
        confirmButton = { Button(enabled = metadata != null, onClick = { metadata?.let(onConfirm) }) { Text(confirmLabel) } },
        dismissButton = { OutlinedButton(onClick = onDismiss, modifier = Modifier.heightIn(min = 48.dp)) { Text(cancelLabel) } },
    )
}
