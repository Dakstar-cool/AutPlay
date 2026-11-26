package app.autplay.ui.player

import android.graphics.Bitmap
import android.graphics.Color as Pixel
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import androidx.core.graphics.scale
import app.autplay.ui.AutPlayTokens
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

internal data class PlayerArtworkBackdrop(val header: Color, val surface: Color)

internal data class ArtworkPalette(val dominant: Color, val accent: Color?)

@Composable
internal fun rememberArtworkPalette(bitmap: Bitmap?): ArtworkPalette? = if (bitmap == null) null else key(bitmap) {
    produceState<ArtworkPalette?>(null) { value = withContext(Dispatchers.Default) { artworkPalette(bitmap) } }.value
}

@Composable
internal fun rememberPlayerArtworkBackdrop(bitmap: Bitmap?): PlayerArtworkBackdrop {
    val tint = rememberArtworkPalette(bitmap)?.dominant
    val colors = MaterialTheme.colorScheme
    val muted = AutPlayTokens.colors.mutedText
    val base = colors.background
    if (tint == null) return PlayerArtworkBackdrop(base, base)
    return remember(tint, colors, muted) {
        val dark = base.luminance() < 0.5f
        PlayerArtworkBackdrop(
            header = readableTint(tint, base, if (dark) .74f else .42f, listOf(colors.onSurface)),
            surface = readableTint(tint, base, if (dark) .21f else .12f, listOf(muted, colors.primary, colors.onSurface)),
        )
    }
}

// Keep small labels legible even for nearly white covers in dark mode or black covers in light mode.
private fun readableTint(tint: Color, base: Color, strength: Float, foregrounds: List<Color>): Color {
    var alpha = strength
    while (alpha > 0.01f) {
        val candidate = tint.copy(alpha = alpha).compositeOver(base)
        val background = candidate.luminance() + .05f
        if (foregrounds.all {
                val foreground = it.luminance() + .05f
                maxOf(background, foreground) / minOf(background, foreground) >= 4.5f
            }) return candidate
        alpha *= .75f
    }
    return base
}

private fun artworkPalette(bitmap: Bitmap): ArtworkPalette? {
    val width = minOf(bitmap.width, 32)
    val height = minOf(bitmap.height, 32)
    val sample = bitmap.scale(width, height, filter = true)
    val pixels = IntArray(width * height)
    sample.getPixels(pixels, 0, width, 0, 0, width, height)
    if (sample !== bitmap) sample.recycle()

    // Quantize similar shades together. Chroma gives saturated regions a modest advantage over grey borders.
    val weights = IntArray(512)
    fun bucket(color: Int): Int = (Pixel.red(color) / 32) * 64 + (Pixel.green(color) / 32) * 8 + Pixel.blue(color) / 32
    for (pixel in pixels) {
        val r = Pixel.red(pixel)
        val g = Pixel.green(pixel)
        val b = Pixel.blue(pixel)
        weights[bucket(pixel)] += Pixel.alpha(pixel) * (96 + maxOf(r, g, b) - minOf(r, g, b))
    }
    val best = weights.indices.maxBy { weights[it] }
    if (weights[best] == 0) return null
    fun average(index: Int): Color {
        var red = 0
        var green = 0
        var blue = 0
        var total = 0
        for (pixel in pixels) {
            if (bucket(pixel) != index) continue
            val alpha = Pixel.alpha(pixel)
            red += Pixel.red(pixel) * alpha
            green += Pixel.green(pixel) * alpha
            blue += Pixel.blue(pixel) * alpha
            total += alpha
        }
        return Color(red / total, green / total, blue / total)
    }
    // A second, distinctly colored region gives the halo the same two tones as the cover.
    val accent = weights.indices.filter { index ->
        val r = index / 64
        val g = index / 8 % 8
        val b = index % 8
        val dr = r - best / 64
        val dg = g - best / 8 % 8
        val db = b - best % 8
        weights[index] > weights[best] / 12 && maxOf(r, g, b) - minOf(r, g, b) >= 2 &&
            dr * dr + dg * dg + db * db >= 16
    }.maxByOrNull { weights[it] }
    return ArtworkPalette(average(best), accent?.let(::average))
}
