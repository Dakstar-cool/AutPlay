package app.autplay.ui.face

/** Bounded visual controls derived only from the process-local PCM projection. */
internal data class ReactivePupilState(
    val dilation: Float = 1f,
    val horizontalBias: Float = 0f,
    val verticalBias: Float = 0f,
    val colorMix: Float = 0f,
    val contourBias: Float = 0f,
)

/**
 * Reduces the smoothed temporal PCM contour to a stable pupil response.
 *
 * The contour bins are consecutive slices of one PCM buffer, not frequency bands. This function
 * therefore expresses local movement/attack only and deliberately makes no pitch, timbre or mood
 * claim. Inactive and reduced-motion surfaces return the neutral optical pupil.
 */
internal fun reactivePupilState(
    audioEnergy: Float,
    audioContour: List<Float>,
    animated: Boolean,
    activeAmount: Float,
): ReactivePupilState {
    val activity = if (animated) activeAmount.coerceIn(0f, 1f) else 0f
    if (activity == 0f || audioContour.isEmpty()) return ReactivePupilState()

    val sampleCount = audioContour.size
    val third = (sampleCount / 3).coerceAtLeast(1)
    val middleStart = ((sampleCount - third) / 2).coerceAtLeast(0)
    val middleEnd = (middleStart + third).coerceAtMost(sampleCount)
    var total = 0f
    var weightedTotal = 0f
    var peak = 0f
    var earlyTotal = 0f
    var earlyCount = 0
    var middleTotal = 0f
    var middleCount = 0
    var lateTotal = 0f
    var lateCount = 0
    audioContour.forEachIndexed { index, raw ->
        val sample = (raw * PCM_VISUAL_GAIN).coerceIn(0f, 1f)
        val position = index.toFloat() / (sampleCount - 1).coerceAtLeast(1)
        total += sample
        weightedTotal += position * sample
        peak = maxOf(peak, sample)
        if (index < third) {
            earlyTotal += sample
            earlyCount++
        }
        if (index in middleStart until middleEnd) {
            middleTotal += sample
            middleCount++
        }
        if (index >= sampleCount - third) {
            lateTotal += sample
            lateCount++
        }
    }
    val centroid = if (total <= 0.0001f || sampleCount == 1) {
        0.5f
    } else {
        weightedTotal / total
    }
    val early = earlyTotal / earlyCount.coerceAtLeast(1)
    val middle = middleTotal / middleCount.coerceAtLeast(1)
    val late = lateTotal / lateCount.coerceAtLeast(1)
    val energy = audioEnergy.coerceIn(0f, 1f)

    return ReactivePupilState(
        dilation = (1f + energy * 0.30f + peak * activity * 0.12f).coerceIn(1f, 1.42f),
        horizontalBias = ((centroid - 0.5f) * 2f * activity).coerceIn(-1f, 1f),
        verticalBias = ((middle - (early + late) / 2f) * 1.6f * activity).coerceIn(-1f, 1f),
        colorMix = (energy * 0.58f + peak * 0.42f * activity).coerceIn(0f, 1f),
        contourBias = ((late - early) * 1.8f * activity).coerceIn(-1f, 1f),
    )
}

private const val PCM_VISUAL_GAIN = 4.2f
