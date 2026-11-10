package app.autplay.ui.desktop

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** Header validation runs before any native decoder sees the payload. Full PNG validation is the decoder's job. */
internal fun desktopArtworkHeaderMatches(artwork: DesktopArtwork, bytes: ByteArray): Boolean {
    if (bytes.size !in 33..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES || bytes.size != artwork.byteSize ||
        artwork.width !in 1..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_DIMENSION ||
        artwork.height !in 1..DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_DIMENSION) return false
    val signature = intArrayOf(137, 80, 78, 71, 13, 10, 26, 10)
    if (signature.indices.any { (bytes[it].toInt() and 255) != signature[it] }) return false
    fun integer(offset: Int): Long = (0..3).fold(0L) { value, index ->
        (value shl 8) or (bytes[offset + index].toLong() and 255)
    }
    return integer(8) == 13L && bytes[12] == 73.toByte() && bytes[13] == 72.toByte() &&
        bytes[14] == 68.toByte() && bytes[15] == 82.toByte() &&
        integer(16) == artwork.width.toLong() && integer(20) == artwork.height.toLong()
}

internal expect fun decodeDesktopArtwork(bytes: ByteArray, width: Int, height: Int): ImageBitmap?

internal class DesktopArtworkRenderIdentity(private val trackId: String, private val artwork: DesktopArtwork) {
    fun matches(currentTrackId: String?, currentArtwork: DesktopArtwork?): Boolean =
        trackId == currentTrackId && artwork === currentArtwork
}

private class DesktopDecodedArtwork(val identity: DesktopArtworkRenderIdentity, val bitmap: ImageBitmap?)

/** Only immutable, already inspected artwork enters here. No filesystem or URI access. */
@Composable
internal fun DesktopArtworkView(trackId: String?, artwork: DesktopArtwork?, language: DesktopLanguage,
    size: Dp = 64.dp) {
    DesktopArtworkImage(rememberDesktopArtworkBitmap(trackId, artwork), language, size)
}

/** Decode only the latest supplied cache entry, outside any retained outgoing visual scene. */
@Composable
internal fun rememberDesktopArtworkBitmap(trackId: String?, artwork: DesktopArtwork?): ImageBitmap? {
    val decoded by produceState<DesktopDecodedArtwork?>(null, trackId, artwork, artwork?.sha256) {
        value = null
        if (trackId != null && artwork != null) {
            value = withContext(Dispatchers.Default) {
                currentCoroutineContext().ensureActive()
                val bitmap = try {
                    val bytes = artwork.copyPngBytes()
                    if (desktopArtworkHeaderMatches(artwork, bytes)) {
                        decodeDesktopArtwork(bytes, artwork.width, artwork.height)
                    } else null
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    null
                }
                currentCoroutineContext().ensureActive()
                DesktopDecodedArtwork(DesktopArtworkRenderIdentity(trackId, artwork), bitmap)
            }
        }
    }
    // produceState may still hold the previous value until the new producer starts.
    return decoded?.takeIf { it.identity.matches(trackId, artwork) }?.bitmap
}

/** Passive drawing only; a retained scene never owns a decoder or a command. */
@Composable
internal fun DesktopArtworkImage(bitmap: ImageBitmap?, language: DesktopLanguage, size: Dp) {
    if (bitmap != null) {
        Image(bitmap, DesktopMetadataText.Cover.label(language), Modifier.size(size), contentScale = ContentScale.Fit)
    } else {
        Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.medium) {
            Box(Modifier.size(size).semantics { contentDescription = DesktopMetadataText.NoCover.label(language) },
                contentAlignment = Alignment.Center) { Text("♫", style = MaterialTheme.typography.headlineMedium) }
        }
    }
}
