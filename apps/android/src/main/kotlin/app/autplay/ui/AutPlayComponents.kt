package app.autplay.ui

import androidx.annotation.StringRes
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.R

@Composable
public fun AutPlayPlatformIcon(
    icon: AutPlayIcon,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    tint: Color = androidx.compose.material3.LocalContentColor.current,
) {
    Icon(
        painter = painterResource(platformIconResource(icon)),
        contentDescription = contentDescription,
        modifier = modifier,
        tint = tint,
    )
}

@Composable
public fun AutPlayIconButton(
    icon: AutPlayIcon,
    @StringRes labelRes: Int,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val label = stringResource(labelRes)
    IconButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .size(AutPlayTokens.dimensions.minimumTouchTarget)
            .semantics { contentDescription = label },
    ) {
        AutPlayPlatformIcon(icon, null, Modifier.size(24.dp))
    }
}

@Composable
public fun AutPlayArtwork(
    title: String,
    modifier: Modifier = Modifier,
    size: Dp = 56.dp,
    painter: Painter? = null,
) {
    val shape = MaterialTheme.shapes.medium
    val placeholder = Brush.linearGradient(
        listOf(MaterialTheme.colorScheme.primaryContainer, AutPlayTokens.colors.raisedSurface),
    )
    Box(
        modifier = modifier
            .size(size)
            .clip(shape)
            .background(placeholder)
            .semantics { contentDescription = title },
        contentAlignment = Alignment.Center,
    ) {
        if (painter == null) {
            Text(
                text = title.trim().firstOrNull()?.uppercase() ?: "A",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        } else {
            Icon(painter = painter, contentDescription = null, tint = Color.Unspecified)
        }
    }
}

@Composable
public fun AutPlayCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    val interaction = if (onClick == null) modifier else modifier.clickable(role = Role.Button, onClick = onClick)
    Surface(
        modifier = interaction
            .fillMaxWidth()
            .border(1.dp, AutPlayTokens.colors.border, MaterialTheme.shapes.large),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 1.dp,
    ) {
        Box(Modifier.padding(18.dp)) { content() }
    }
}

@Composable
public fun AutPlaySectionHeader(
    title: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth().semantics { heading() },
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        if (actionLabel != null && onAction != null) {
            androidx.compose.material3.TextButton(
                onClick = onAction,
                modifier = Modifier.heightIn(min = AutPlayTokens.dimensions.minimumTouchTarget),
            ) { Text(actionLabel) }
        }
    }
}

@Composable
public fun AutPlayChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Surface(
        modifier = modifier
            .heightIn(min = AutPlayTokens.dimensions.minimumTouchTarget)
            .clip(CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .semantics {
                role = Role.Button
                if (!enabled) disabled()
            },
        shape = CircleShape,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else AutPlayTokens.colors.raisedSurface,
        border = androidx.compose.foundation.BorderStroke(1.dp, AutPlayTokens.colors.border),
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
        )
    }
}

public enum class AutPlayStateKind { Loading, Empty, Offline, Error, PermissionRevoked, PlaybackUnavailable }

@Composable
public fun AutPlayStateSurface(
    kind: AutPlayStateKind,
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    val title = stringResource(
        when (kind) {
            AutPlayStateKind.Loading -> R.string.state_loading_title
            AutPlayStateKind.Empty -> R.string.state_empty_title
            AutPlayStateKind.Offline -> R.string.state_offline_title
            AutPlayStateKind.Error -> R.string.state_error_title
            AutPlayStateKind.PermissionRevoked -> R.string.state_permission_title
            AutPlayStateKind.PlaybackUnavailable -> R.string.state_playback_unavailable_title
        },
    )
    AutPlayCard(modifier) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(message, style = MaterialTheme.typography.bodyMedium, color = AutPlayTokens.colors.mutedText)
            if (actionLabel != null && onAction != null) {
                Button(onClick = onAction, modifier = Modifier.heightIn(min = 48.dp)) { Text(actionLabel) }
            }
        }
    }
}

private fun platformIconResource(icon: AutPlayIcon): Int = when (icon) {
    AutPlayIcon.Home -> R.drawable.ic_autplay_home
    AutPlayIcon.Search -> R.drawable.ic_autplay_search
    AutPlayIcon.Library -> R.drawable.ic_autplay_library
    AutPlayIcon.Playlist, AutPlayIcon.History -> R.drawable.ic_autplay_list
    AutPlayIcon.Download -> R.drawable.ic_autplay_download
    AutPlayIcon.Import -> R.drawable.ic_autplay_upload
    AutPlayIcon.Play -> R.drawable.ic_autplay_play
    AutPlayIcon.Wave -> R.drawable.ic_autplay_wave
    AutPlayIcon.Sync -> R.drawable.ic_autplay_sync
    AutPlayIcon.Server, AutPlayIcon.Settings -> R.drawable.ic_autplay_settings
    AutPlayIcon.Profile -> R.drawable.ic_autplay_profile
    AutPlayIcon.Privacy -> R.drawable.ic_autplay_lock
    AutPlayIcon.Back -> R.drawable.ic_autplay_back
    AutPlayIcon.Pause -> R.drawable.ic_autplay_pause
    AutPlayIcon.Previous -> R.drawable.ic_autplay_previous
    AutPlayIcon.Next -> R.drawable.ic_autplay_next
    AutPlayIcon.Shuffle -> R.drawable.ic_autplay_shuffle
    AutPlayIcon.Repeat -> R.drawable.ic_autplay_repeat
    AutPlayIcon.Check -> R.drawable.ic_autplay_check
    AutPlayIcon.Favorite -> R.drawable.ic_autplay_favorite
}
