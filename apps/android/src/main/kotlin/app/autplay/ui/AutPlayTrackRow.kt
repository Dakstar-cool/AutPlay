package app.autplay.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R

@Composable
internal fun AutPlayTrackRow(
    title: String,
    artist: String?,
    trackId: String?,
    onPlay: (() -> Unit)?,
    modifier: Modifier = Modifier,
    playEnabled: Boolean = true,
    selected: Boolean = false,
    detail: String? = null,
    emphasizeDetail: Boolean = false,
    actions: (@Composable () -> Unit)? = null,
) {
    Surface(
        shape = MaterialTheme.shapes.medium,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else AutPlayTokens.colors.raisedSurface,
    ) {
        val playAction = if (onPlay == null) Modifier else Modifier.clickable(
            enabled = playEnabled,
            role = Role.Button,
            onClickLabel = stringResource(R.string.library_play_track, title),
            onClick = onPlay,
        )
        Row(
            modifier.fillMaxWidth().then(playAction).semantics { if (selected) this.selected = true }.padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            AutPlayArtwork(title, Modifier.clearAndSetSemantics {}, size = 48.dp, trackId = trackId)
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    fontWeight = if (selected) FontWeight.Bold else null,
                    color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                Text(artist ?: stringResource(R.string.library_unknown_artist), style = MaterialTheme.typography.bodyMedium,
                    color = AutPlayTokens.colors.mutedText, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (!detail.isNullOrBlank()) {
                    Text(
                        detail,
                        style = if (emphasizeDetail) MaterialTheme.typography.labelMedium else MaterialTheme.typography.labelSmall,
                        color = if (emphasizeDetail) AutPlayTokens.colors.success else AutPlayTokens.colors.mutedText,
                    )
                }
            }
            if (actions != null) {
                Row(verticalAlignment = Alignment.CenterVertically) { actions() }
            } else if (onPlay != null) {
                AutPlayPlatformIcon(AutPlayIcon.Play, null, Modifier.padding(horizontal = 12.dp).size(20.dp),
                    tint = AutPlayTokens.colors.mutedText)
            }
        }
    }
}

@Composable
internal fun TrackActionsMenu(
    modifier: Modifier = Modifier,
    content: @Composable (dismiss: () -> Unit) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { expanded = true }, modifier = modifier.size(48.dp)) {
            Icon(painterResource(R.drawable.ic_autplay_more_vertical), stringResource(R.string.ui_library_refresh_track_actions))
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            content { expanded = false }
        }
    }
}

@Composable
internal fun AddToPlaylistMenu(onAdd: () -> Unit) {
    TrackActionsMenu { dismiss ->
        androidx.compose.material3.DropdownMenuItem(
            text = { Text(stringResource(R.string.playlist_add_selected)) },
            onClick = { dismiss(); onAdd() },
        )
    }
}
