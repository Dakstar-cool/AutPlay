package app.autplay.ui

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate

/** Deterministic graphic sleeve for missing artwork, never a fetched or inferred album cover. */
@Composable
internal fun AutPlayArtworkPlaceholder(seed: String, modifier: Modifier = Modifier) {
    val palette = remember(seed) { playbackVisualPalette(seed) }
    val variant = remember(seed) { seed.hashCode().ushr(1) % 3 }
    Canvas(modifier.testTag("artwork-placeholder")) {
        val unit = size.minDimension
        drawRect(Brush.linearGradient(listOf(palette[0], palette[2])))
        val center = Offset(size.width * 0.56f, size.height * 0.43f)
        when (variant) {
            0 -> {
                drawCircle(Color(0xFF16181C), unit * 0.40f, center)
                repeat(12) { index ->
                    drawCircle(
                        palette[3].copy(alpha = 0.06f + index * 0.008f),
                        unit * (0.13f + index * 0.021f), center,
                        style = Stroke(unit * 0.002f),
                    )
                }
                drawCircle(palette[1], unit * 0.115f, center)
                drawCircle(Color(0xFF16181C), unit * 0.020f, center)
            }
            1 -> rotate(-28f) {
                repeat(8) { index ->
                    drawRoundRect(
                        color = if (index % 2 == 0) palette[1] else palette[3],
                        topLeft = Offset(unit * (index * 0.135f - 0.10f), -unit * 0.15f),
                        size = Size(unit * 0.060f, unit * (0.80f + index * 0.045f)),
                        cornerRadius = androidx.compose.ui.geometry.CornerRadius(unit * 0.030f),
                        alpha = 0.80f,
                    )
                }
            }
            else -> {
                repeat(6) { index ->
                    drawCircle(
                        color = if (index % 2 == 0) palette[1] else palette[2],
                        radius = unit * (0.61f - index * 0.083f),
                        center = Offset(unit * 0.78f, unit * 0.24f),
                    )
                }
            }
        }
        drawRect(
            Brush.verticalGradient(listOf(Color.Transparent, Color(0xC0121418))),
            topLeft = Offset(0f, unit * 0.62f), size = Size(size.width, size.height * 0.38f),
        )
        // Sparse print marks retain detail at large sizes without lettering or rainbow fills.
        repeat(11) { index ->
            drawRect(
                palette[3].copy(alpha = 0.55f),
                Offset(unit * (0.76f + index * 0.010f), unit * 0.875f),
                Size(unit * (if (index % 3 == 0) 0.006f else 0.003f), unit * 0.055f),
            )
        }
        drawCircle(palette[3], unit * 0.023f, Offset(unit * 0.115f, unit * 0.900f))
        drawRect(palette[3], Offset(unit * 0.158f, unit * 0.880f), Size(unit * 0.095f, unit * 0.040f))
    }
}
