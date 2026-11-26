package app.autplay.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
internal fun CollectionCoverTile(
    title: String,
    artworkTrackId: String?,
    subtitle: String?,
    modifier: Modifier,
    caption: String? = null,
    enabled: Boolean = true,
    catalogCoverId: String? = null,
    onClick: () -> Unit,
) {
    Surface(onClick = onClick, enabled = enabled, modifier = modifier, shape = MaterialTheme.shapes.large,
        color = AutPlayTokens.colors.raisedSurface, border = BorderStroke(1.dp, AutPlayTokens.colors.border.copy(alpha = .45f))) {
        Column {
            AutPlayArtwork(title, Modifier.fillMaxWidth().aspectRatio(1f).clearAndSetSemantics {}, trackId = artworkTrackId,
                painter = rememberCatalogCover(catalogCoverId))
            Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (!subtitle.isNullOrBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall,
                    color = AutPlayTokens.colors.mutedText, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (caption != null) Text(caption, style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
