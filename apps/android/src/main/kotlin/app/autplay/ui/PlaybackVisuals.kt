package app.autplay.ui

import android.animation.ValueAnimator
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Artwork halo inspired by the organic motion language of modern music players.
 * Playback truth remains in the adjacent text and transport control; this visual is decorative.
 */
@Composable
public fun AutPlayPlaybackHalo(
    seed: String,
    isPlaying: Boolean,
    modifier: Modifier = Modifier,
) {
    val animated = shouldAnimatePlaybackHalo(isPlaying, rememberSystemAnimationsEnabled())
    val phase = if (animated) {
        val transition = rememberInfiniteTransition(label = "playback-halo")
        val animatedPhase by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(animation = tween(3_600, easing = LinearEasing)),
            label = "playback-halo-phase",
        )
        animatedPhase
    } else {
        0f
    }
    val palette = remember(seed) { playbackVisualPalette(seed) }
    val numericSeed = remember(seed) { seed.hashCode().toUInt().toLong() }

    Canvas(
        modifier = modifier
            .testTag("playback-halo"),
    ) {
        val center = Offset(size.width / 2f, size.height / 2f)
        val minDimension = size.minDimension
        val sweep = Brush.sweepGradient(palette + palette.first(), center)

        // Broad translucent layers create the soft fluid body without a platform blur dependency.
        repeat(3) { layer ->
            val path = organicHaloPath(
                center = center,
                minDimension = minDimension,
                phase = phase,
                seed = numericSeed,
                layer = layer,
                animated = animated,
            )
            drawPath(
                path = path,
                brush = sweep,
                alpha = when (layer) {
                    0 -> 0.10f
                    1 -> 0.18f
                    else -> 0.30f
                },
            )
        }

        val edge = organicHaloPath(
            center = center,
            minDimension = minDimension,
            phase = phase,
            seed = numericSeed,
            layer = 3,
            animated = animated,
        )
        drawPath(
            path = edge,
            brush = sweep,
            alpha = if (animated) 0.92f else 0.62f,
            style = Stroke(width = 5.dp.toPx(), cap = StrokeCap.Round),
        )
        drawPath(
            path = edge,
            brush = sweep,
            alpha = if (animated) 0.16f else 0.08f,
            style = Stroke(width = 18.dp.toPx(), cap = StrokeCap.Round),
        )
    }
}

@Composable
private fun rememberSystemAnimationsEnabled(): Boolean {
    val context = LocalContext.current
    var enabled by remember(context) { mutableStateOf(ValueAnimator.areAnimatorsEnabled()) }
    DisposableEffect(context) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                enabled = ValueAnimator.areAnimatorsEnabled()
            }
        }
        val resolver = context.contentResolver
        resolver.registerContentObserver(
            Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE),
            false,
            observer,
        )
        enabled = ValueAnimator.areAnimatorsEnabled()
        onDispose { resolver.unregisterContentObserver(observer) }
    }
    return enabled
}

internal fun shouldAnimatePlaybackHalo(isPlaying: Boolean, systemAnimationsEnabled: Boolean): Boolean =
    isPlaying && systemAnimationsEnabled

private fun organicHaloPath(
    center: Offset,
    minDimension: Float,
    phase: Float,
    seed: Long,
    layer: Int,
    animated: Boolean,
): Path {
    val path = Path()
    val points = 144
    val baseRadius = minDimension * (0.355f + layer * 0.010f)
    val amplitude = minDimension * if (animated) 0.072f else 0.034f
    val phaseRadians = phase * 2f * PI.toFloat()
    val seedA = (seed % 17).toFloat() * 0.19f
    val seedB = (seed % 11).toFloat() * 0.23f
    val layerOffset = layer * 0.72f
    val breathing = if (animated) 1f + sin(phaseRadians * 2f + layerOffset) * 0.025f else 1f

    for (index in 0..points) {
        val angle = index.toFloat() / points.toFloat() * 2f * PI.toFloat()
        val displacement = playbackHaloDisplacement(
            angle = angle,
            phaseRadians = phaseRadians,
            seedA = seedA,
            seedB = seedB,
            layerOffset = layerOffset,
            animated = animated,
        )
        val radius = (baseRadius + amplitude * displacement) * breathing
        val point = Offset(
            x = center.x + cos(angle) * radius,
            y = center.y + sin(angle) * radius,
        )
        if (index == 0) path.moveTo(point.x, point.y) else path.lineTo(point.x, point.y)
    }
    path.close()
    return path
}

internal fun playbackHaloDisplacement(
    angle: Float,
    phaseRadians: Float,
    seedA: Float,
    seedB: Float,
    layerOffset: Float,
    animated: Boolean,
): Float {
    val motion = if (animated) phaseRadians else 0f
    return (
        sin(angle * 2f + motion + seedA + layerOffset) * 0.48f +
            sin(angle * 3f - motion * 0.72f + seedB - layerOffset) * 0.32f +
            sin(angle * 5f + motion * 1.28f + seedA - seedB) * 0.20f
        ).coerceIn(-1f, 1f)
}

internal fun playbackVisualPalette(seed: String): List<Color> = when (seed.hashCode().ushr(1) % 5) {
    0 -> listOf(Color(0xFFFF5B35), Color(0xFFFFB443), Color(0xFF8A5CFF), Color(0xFF46D7C8))
    1 -> listOf(Color(0xFF46D7C8), Color(0xFF2676FF), Color(0xFFA45CFF), Color(0xFFFF5B78))
    2 -> listOf(Color(0xFFFFB443), Color(0xFFFF5B78), Color(0xFF7C5CFF), Color(0xFF33A6FF))
    3 -> listOf(Color(0xFF82E68A), Color(0xFF33A6FF), Color(0xFF7C5CFF), Color(0xFFFF6B45))
    else -> listOf(Color(0xFFFF6B45), Color(0xFFFF5BA6), Color(0xFFA45CFF), Color(0xFF46D7C8))
}
