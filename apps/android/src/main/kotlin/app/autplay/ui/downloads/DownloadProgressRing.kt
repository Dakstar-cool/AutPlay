package app.autplay.ui.downloads

import android.text.format.Formatter
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.progressSemantics
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.autplay.R
import app.autplay.download.MediaDownloadComponents
import app.autplay.download.TransferProgress
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

internal val LocalTrackDownloads = compositionLocalOf<Map<String, DownloadIntentPresentation>> { emptyMap() }
internal val LocalPreparingDownloads = compositionLocalOf<Set<String>> { emptySet() }

@Composable
internal fun InlineTrackDownload(trackId: String, savedLocally: Boolean, onClick: () -> Unit) {
    val download = LocalTrackDownloads.current[trackId]
    val saved = savedLocally || download?.state == "COMPLETED"
    val preparing = trackId in LocalPreparingDownloads.current
    val active = !saved && (preparing || download?.state in setOf("REQUESTED", "QUEUED", "DOWNLOADING", "PAUSED"))
    val transfer = if (active) liveTransferProgress(download?.transferId) else null
    val label = when {
        saved -> stringResource(R.string.music_saved_offline)
        active -> listOfNotNull(stringResource(R.string.music_downloading), transfer?.let { transferredSize(it) }).joinToString(" · ")
        else -> stringResource(R.string.action_download)
    }
    IconButton(onClick = onClick, enabled = !saved && !active, modifier = Modifier.size(48.dp).semantics { contentDescription = label }) {
        if (active) DownloadProgressRing(transfer?.fraction, Modifier.size(40.dp), running = preparing || download?.state == "DOWNLOADING")
        else AutPlayPlatformIcon(if (saved) AutPlayIcon.Downloaded else AutPlayIcon.Download, null, Modifier.size(24.dp),
            tint = if (saved) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
    }
}

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@Composable
internal fun liveTransferProgress(id: String?): TransferProgress? {
    if (id == null) return null
    val context = LocalContext.current
    return key(id) {
        remember(context, id) {
            MediaDownloadComponents.get(context).transferProgress.map { it[id] }.distinctUntilChanged()
        }.collectAsStateWithLifecycle(initialValue = null).value
    }
}

@Composable
internal fun transferredSize(progress: TransferProgress): String {
    val context = LocalContext.current
    val received = Formatter.formatShortFileSize(context, progress.bytes.coerceAtLeast(0))
    return if (progress.total > 0) stringResource(R.string.download_bytes_of, received,
        Formatter.formatShortFileSize(context, progress.total)) else received
}

@Composable
internal fun progressPercent(fraction: Double): String {
    val percent = if (fraction >= 1.0) 100 else (fraction * 100 + 1e-9).toInt().coerceIn(0, 99)
    return stringResource(R.string.download_percent, percent)
}

@Composable
internal fun DownloadProgressRing(
    fraction: Double?,
    modifier: Modifier = Modifier.size(52.dp),
    showPercent: Boolean = true,
    running: Boolean = true,
) {
    if (fraction == null && running) {
        CircularProgressIndicator(modifier, strokeWidth = 3.dp)
        return
    }
    val fill = MaterialTheme.colorScheme.primary
    val track = MaterialTheme.colorScheme.outlineVariant
    val target = (fraction ?: 0.0).toFloat().coerceIn(0f, 1f)
    val animated = animateFloatAsState(target, tween(if (running) 400 else 0, easing = LinearEasing), label = "Download progress")
    val semantics = if (fraction != null) Modifier.progressSemantics(target) else Modifier
    Box(modifier.then(semantics), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round)
            val radius = (size.minDimension - stroke.width) / 2
            drawCircle(track, radius, style = stroke)
            val progress = animated.value
            if (progress >= 1f) drawCircle(fill, radius, style = stroke)
            else if (progress > 0f) {
                drawArc(fill, -90f, 360f * progress, false, Offset(center.x - radius, center.y - radius),
                    Size(radius * 2, radius * 2), style = stroke)
            }
        }
        if (showPercent && fraction != null) {
            Text(progressPercent(fraction), modifier = Modifier.clearAndSetSemantics {},
                style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
    }
}
