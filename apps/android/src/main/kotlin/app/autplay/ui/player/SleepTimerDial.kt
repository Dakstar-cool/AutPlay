package app.autplay.ui.player

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.math.sin

@Composable
internal fun SleepTimerDial(
    selectedMinutes: Int,
    onMinutesChanged: (Int) -> Unit,
    accessibilityLabel: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val primary = MaterialTheme.colorScheme.primary
    val track = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f)
    val labelColor = MaterialTheme.colorScheme.onSurfaceVariant
    val textColor = MaterialTheme.colorScheme.onSurface
    val labelTextSize = with(androidx.compose.ui.platform.LocalDensity.current) { 14.sp.toPx() }
    val labelPaint = remember(labelColor, labelTextSize) {
        android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            color = labelColor.toArgb()
            textAlign = android.graphics.Paint.Align.CENTER
            textSize = labelTextSize
        }
    }
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        Canvas(
            Modifier
                .size(292.dp)
                .alpha(if (enabled) 1f else 0.34f)
                .testTag("sleep-timer-dial")
                .semantics {
                    contentDescription = accessibilityLabel
                    stateDescription = "$selectedMinutes:00"
                    if (!enabled) disabled()
                    progressBarRangeInfo = ProgressBarRangeInfo(
                        current = selectedMinutes.toFloat(),
                        range = 1f..60f,
                        steps = 58,
                    )
                    setProgress { requested ->
                        if (!enabled) return@setProgress false
                        onMinutesChanged(requested.roundToInt().coerceIn(1, 60))
                        true
                    }
                }
                .pointerInput(onMinutesChanged, enabled) {
                    if (!enabled) return@pointerInput
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        fun update(position: Offset) {
                            dialMinutesFromPosition(position, size.width.toFloat(), size.height.toFloat())
                                ?.let(onMinutesChanged)
                        }
                        update(down.position)
                        do {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull() ?: break
                            update(change.position)
                            change.consume()
                        } while (event.changes.any { it.pressed })
                    }
                },
        ) {
            val center = this.center
            val radius = size.minDimension * 0.335f
            val strokeWidth = 6.dp.toPx()
            drawCircle(color = track, radius = radius, center = center, style = Stroke(strokeWidth, cap = StrokeCap.Round))
            val sweep = if (selectedMinutes == 60) 360f else selectedMinutes * 6f
            drawArc(
                color = primary,
                startAngle = -90f,
                sweepAngle = sweep,
                useCenter = false,
                topLeft = Offset(center.x - radius, center.y - radius),
                size = androidx.compose.ui.geometry.Size(radius * 2f, radius * 2f),
                style = Stroke(strokeWidth, cap = StrokeCap.Round),
            )
            for (minute in 0..55 step 5) {
                val angle = dialAngleRadians(if (minute == 0) 60 else minute)
                val labelRadius = radius + 28.dp.toPx()
                val x = center.x + sin(angle) * labelRadius
                val y = center.y - cos(angle) * labelRadius - (labelPaint.ascent() + labelPaint.descent()) / 2f
                drawContext.canvas.nativeCanvas.drawText(minute.toString(), x, y, labelPaint)
            }
            val selectedAngle = dialAngleRadians(selectedMinutes)
            val knob = Offset(
                center.x + sin(selectedAngle) * radius,
                center.y - cos(selectedAngle) * radius,
            )
            drawCircle(primary, 11.dp.toPx(), knob)
        }
        Text(
            text = "$selectedMinutes:00",
            color = textColor,
            style = MaterialTheme.typography.displaySmall,
            modifier = Modifier.alpha(if (enabled) 1f else 0.34f),
        )
    }
}

internal fun dialMinutesFromPosition(position: Offset, width: Float, height: Float): Int? {
    if (width <= 0f || height <= 0f) return null
    val dx = position.x - width / 2f
    val dy = position.y - height / 2f
    val radius = minOf(width, height) / 2f
    if (hypot(dx, dy) < radius * 0.24f) return null
    var degrees = Math.toDegrees(atan2(dx.toDouble(), -dy.toDouble())).toFloat()
    if (degrees < 0f) degrees += 360f
    val minute = (degrees / 6f).roundToInt()
    return if (minute == 0) 60 else minute.coerceIn(1, 60)
}

private fun dialAngleRadians(minutes: Int): Float =
    ((minutes % 60) * 6f / 180f * PI).toFloat()
