package app.autplay.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.rememberArtistCatalogPages
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore

@Composable
internal fun CatalogArtistHeader(name: String, artistId: String) {
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val active = settings
    val binding = if (active?.activeUserId != null && active.deviceId != null && active.activeServerProfileId != null)
        ClientEventBinding(active.activeUserId, active.deviceId, active.activeServerProfileId) else null
    val releases = rememberArtistCatalogPages(context, binding, artistId, editions = true, firstPageOnly = true)
    ArtistHeader(name, releaseId = releases.items.firstNotNullOfOrNull { it.releaseId })
}

@Composable
internal fun ArtistHeader(name: String, subtitle: String? = null, artworkTrackId: String? = null,
    releaseId: String? = null, trackCount: Int? = null) {
    val colors = MaterialTheme.colorScheme
    val art = rememberTrackArtwork(artworkTrackId) ?: rememberCatalogCover(releaseId)
    Box(Modifier.fillMaxWidth().clip(MaterialTheme.shapes.large).testTag("artist-header")) {
        Box(Modifier.matchParentSize().background(Brush.linearGradient(listOf(colors.primary, colors.tertiary))))
        if (art != null) Image(art, null, Modifier.matchParentSize().testTag("artist-banner-art"), contentScale = ContentScale.Crop)
        Box(Modifier.matchParentSize().background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = .85f)))))
        Row(Modifier.fillMaxWidth().heightIn(min = 144.dp).padding(16.dp), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Surface(shape = CircleShape, color = colors.secondaryContainer, modifier = Modifier.size(48.dp).testTag("artist-avatar")) {
                val initials = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.take(2)
                    .joinToString("") { it.substring(0, it.offsetByCodePoints(0, 1)).uppercase() }
                Box(contentAlignment = Alignment.Center) { Text(initials, style = MaterialTheme.typography.titleMedium) }
            }
            Column {
                Text(name, style = MaterialTheme.typography.headlineSmall, color = Color.White,
                    modifier = Modifier.semantics { heading() })
                val count = trackCount?.let { pluralStringResource(R.plurals.ui_library_refresh_collection_track_count, it, it) }
                val details = listOfNotNull(subtitle?.takeIf { it.isNotBlank() }, count).joinToString(" · ")
                if (details.isNotEmpty()) Text(details, style = MaterialTheme.typography.bodyMedium, color = Color.White)
            }
        }
    }
}
