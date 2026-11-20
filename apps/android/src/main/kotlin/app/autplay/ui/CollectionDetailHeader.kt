package app.autplay.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R

@Composable
internal fun CollectionDetailHeader(
    title: String, subtitle: String?, date: String?, count: Int, artworkTrackId: String?,
    onListen: (() -> Unit)?, onOpenArtist: (() -> Unit)? = null, secondaryAction: (@Composable () -> Unit)? = null,
) {
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            AutPlayArtwork(title, Modifier.testTag("collection-artwork"), size = 124.dp, trackId = artworkTrackId)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
                if (!subtitle.isNullOrBlank()) {
                    val artistLink = if (onOpenArtist == null) Modifier else Modifier
                        .testTag("collection-open-artist").clickable(onClickLabel = stringResource(R.string.detail_open_artist), onClick = onOpenArtist)
                    Text(subtitle, modifier = artistLink, style = MaterialTheme.typography.bodyLarge,
                        color = if (onOpenArtist != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                }
                val tracks = pluralStringResource(R.plurals.ui_library_refresh_collection_track_count, count, count)
                Text(listOfNotNull(date?.takeIf { it.isNotBlank() }, tracks).joinToString(" · "),
                    color = AutPlayTokens.colors.mutedText, style = MaterialTheme.typography.bodyMedium)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onListen?.invoke() }, enabled = onListen != null, modifier = Modifier.testTag("collection-listen")) {
                AutPlayPlatformIcon(AutPlayIcon.Play, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.detail_collection_listen))
            }
            secondaryAction?.invoke()
        }
    }
}

@Composable
internal fun CollectionTrackRow(number: String, title: String, artist: String?, durationMs: Long?, onPlay: (() -> Unit)?, tag: String) {
    Surface(onClick = { onPlay?.invoke() }, enabled = onPlay != null, modifier = Modifier.fillMaxWidth().testTag(tag),
        color = AutPlayTokens.colors.raisedSurface, shape = MaterialTheme.shapes.medium) {
        Row(Modifier.heightIn(min = 56.dp).padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(number, style = MaterialTheme.typography.labelLarge, color = AutPlayTokens.colors.mutedText)
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (!artist.isNullOrBlank()) Text(artist, style = MaterialTheme.typography.bodyMedium,
                    color = AutPlayTokens.colors.mutedText, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (onPlay == null) Text(stringResource(R.string.search_vault_playback_unavailable), style = MaterialTheme.typography.labelSmall)
            }
            if (durationMs != null) Text(formatDuration(durationMs), style = MaterialTheme.typography.labelMedium,
                color = AutPlayTokens.colors.mutedText)
        }
    }
}
