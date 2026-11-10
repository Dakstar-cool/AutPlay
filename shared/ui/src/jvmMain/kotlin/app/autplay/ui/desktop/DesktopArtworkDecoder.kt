package app.autplay.ui.desktop

import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import org.jetbrains.skia.Image

/** Called on Dispatchers.Default only, after bounded canonical PNG header validation. */
internal actual fun decodeDesktopArtwork(bytes: ByteArray, width: Int, height: Int): ImageBitmap? {
    return try {
        val image = Image.makeFromEncoded(bytes)
        try {
            if (image.width != width || image.height != height) return null
            // Compose 1.12.1 SkiaImageAsset + Actuals.skikoExcludingWeb allocate and draw into
            // a separate immutable bitmap. The returned ImageBitmap does not own this Image.
            image.toComposeImageBitmap()
        } finally {
            image.close()
        }
    } catch (_: LinkageError) {
        null
    }
}
