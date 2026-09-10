package app.autplay.ui.face

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import app.autplay.playback.PlaybackAudioContourRuntime
import app.autplay.ui.rememberSystemAnimationsEnabled
import kotlin.math.PI
import kotlin.math.absoluteValue
import kotlin.math.cos
import kotlin.math.sin

/** Stable playback facts that may influence the local Face without implying musical analysis. */
internal enum class FacePlaybackMode { Idle, Playing, Paused, Buffering }

/** Confirmed preference state. Changes create a brief secondary reaction, never a mood claim. */
internal enum class FacePreferenceMode { Neutral, Liked, Disliked }

internal enum class FaceAppReaction { None, Play, Pause, Like, Dislike }

/**
 * Theme-specific geometry. Values are continuous so a future semantic adapter can interpolate the
 * rig without cross-fading bitmaps or exposing model-specific embeddings to the renderer.
 */
internal data class ResonanceLensPose(
    val innerY: Float,
    val outerY: Float,
    val arch: Float,
    val centerPull: Float,
    val ribbonTension: Float,
    val upperOpen: Float,
    val lowerLift: Float,
    val lidTightness: Float,
) {
    fun bounded(): ResonanceLensPose = copy(
        innerY = innerY.coerceIn(-1f, 1f),
        outerY = outerY.coerceIn(-1f, 1f),
        arch = arch.coerceIn(-1f, 1f),
        centerPull = centerPull.coerceIn(0f, 1f),
        ribbonTension = ribbonTension.coerceIn(0f, 1f),
        upperOpen = upperOpen.coerceIn(0f, 1f),
        lowerLift = lowerLift.coerceIn(0f, 1f),
        lidTightness = lidTightness.coerceIn(0f, 1f),
    )
}

internal data class FaceSpectralPalette(
    val primary: Color,
    val secondary: Color,
    val tertiary: Color,
    val filament: Color,
)

/** Perceptual fixtures used for geometry tests and debug evidence; production does not select one. */
internal enum class FaceReferenceAnchor(
    internal val pose: ResonanceLensPose,
    internal val palette: FaceSpectralPalette,
) {
    Neutral(
        ResonanceLensPose(0.00f, 0.00f, 0.05f, 0.00f, 0.30f, 0.55f, 0.05f, 0.10f),
        FaceSpectralPalette(Color(0xFF9BB7E8), Color(0xFF7198D8), Color(0xFF4C628C), Color(0xFFDDF7FF)),
    ),
    CalmSoft(
        ResonanceLensPose(0.10f, 0.05f, 0.12f, 0.00f, 0.12f, 0.48f, 0.10f, 0.05f),
        FaceSpectralPalette(Color(0xFF76E5F3), Color(0xFF35BFD5), Color(0xFF357E98), Color(0xFFCFFBFF)),
    ),
    PositiveLight(
        ResonanceLensPose(0.08f, 0.24f, 0.16f, 0.00f, 0.30f, 0.68f, 0.34f, 0.10f),
        FaceSpectralPalette(Color(0xFFFFDA83), Color(0xFFF3A33D), Color(0xFF80562A), Color(0xFFFFF0BB)),
    ),
    MelancholicDark(
        ResonanceLensPose(0.34f, -0.10f, 0.08f, 0.24f, 0.38f, 0.40f, 0.08f, 0.24f),
        FaceSpectralPalette(Color(0xFFB69AE8), Color(0xFF7955B8), Color(0xFF46365D), Color(0xFFD8C4FF)),
    ),
    DreamyAtmospheric(
        ResonanceLensPose(0.10f, 0.18f, 0.28f, 0.00f, 0.08f, 0.40f, 0.08f, 0.08f),
        FaceSpectralPalette(Color(0xFFB2C8FF), Color(0xFF738BD0), Color(0xFF49517A), Color(0xFFD8E7FF)),
    ),
    EnergeticBright(
        ResonanceLensPose(0.28f, 0.36f, 0.20f, 0.02f, 0.76f, 0.88f, 0.16f, 0.28f),
        FaceSpectralPalette(Color(0xFF7EFBFF), Color(0xFF1AD7EB), Color(0xFF207D94), Color(0xFFE8FFFF)),
    ),
    AggressiveTense(
        ResonanceLensPose(-0.32f, -0.10f, -0.04f, 0.44f, 0.94f, 0.42f, 0.42f, 0.82f),
        FaceSpectralPalette(Color(0xFFFF8A62), Color(0xFFE05236), Color(0xFF6D3029), Color(0xFFFFD0B6)),
    ),
    Euphoric(
        ResonanceLensPose(0.22f, 0.34f, 0.24f, 0.00f, 0.38f, 0.78f, 0.58f, 0.12f),
        FaceSpectralPalette(Color(0xFFFFD86C), Color(0xFFF4A91E), Color(0xFF7B561D), Color(0xFFFFF3B2)),
    ),
    Ominous(
        ResonanceLensPose(-0.18f, -0.16f, 0.00f, 0.16f, 0.82f, 0.24f, 0.08f, 0.56f),
        FaceSpectralPalette(Color(0xFF8B70B5), Color(0xFF57406F), Color(0xFF342D42), Color(0xFFB7A4CE)),
    ),
}

@Composable
internal fun AutPlayResonanceLens(
    trackSeed: String,
    playbackMode: FacePlaybackMode,
    preference: FacePreferenceMode,
    accessibilitySummary: String,
    modifier: Modifier = Modifier,
    referenceAnchor: FaceReferenceAnchor? = null,
) {
    val animationsEnabled = rememberSystemAnimationsEnabled()
    val animated = shouldAnimateResonanceLens(playbackMode, animationsEnabled)
    val surfaceId = remember(trackSeed) { "resonance-lens-${trackSeed.hashCode()}" }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(surfaceId, animated, lifecycleOwner) {
        fun updateObservation() {
            val lifecycleStarted = lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
            PlaybackAudioContourRuntime.setSurfaceObserving(
                surfaceId,
                shouldObserveAudioContour(animated, lifecycleStarted),
            )
        }
        val observer = LifecycleEventObserver { _, _ -> updateObservation() }
        lifecycleOwner.lifecycle.addObserver(observer)
        updateObservation()
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            PlaybackAudioContourRuntime.setSurfaceObserving(surfaceId, false)
        }
    }

    val audioFrame by PlaybackAudioContourRuntime.state.collectAsState()
    val audioEnergy by animateFloatAsState(
        targetValue = if (animated) (audioFrame.energy * 4.2f).coerceIn(0f, 1f) else 0f,
        animationSpec = spring(
            dampingRatio = Spring.DampingRatioNoBouncy,
            stiffness = Spring.StiffnessMediumLow,
        ),
        label = "face-audio-energy",
    )
    val phase = if (animated) {
        val transition = rememberInfiniteTransition(label = "resonance-lens")
        val value by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(7_200, easing = LinearEasing)),
            label = "resonance-lens-phase",
        )
        value
    } else {
        0f
    }

    var previousMode by remember(trackSeed) { mutableStateOf(playbackMode) }
    var previousPreference by remember(trackSeed) { mutableStateOf(preference) }
    var reactionKind by remember(trackSeed) { mutableStateOf(FaceAppReaction.None) }
    val reaction = remember(trackSeed) { Animatable(0f) }
    LaunchedEffect(playbackMode, preference, trackSeed, animationsEnabled) {
        val nextReaction = when {
            preference != previousPreference && preference == FacePreferenceMode.Liked -> FaceAppReaction.Like
            preference != previousPreference && preference == FacePreferenceMode.Disliked -> FaceAppReaction.Dislike
            playbackMode != previousMode && playbackMode == FacePlaybackMode.Playing -> FaceAppReaction.Play
            playbackMode != previousMode && playbackMode == FacePlaybackMode.Paused -> FaceAppReaction.Pause
            else -> FaceAppReaction.None
        }
        previousMode = playbackMode
        previousPreference = preference
        // Every key change starts from a known baseline. This also clears a reaction whose
        // previous animation coroutine was cancelled by a rapid playback/preference update.
        reactionKind = FaceAppReaction.None
        reaction.snapTo(0f)
        if (nextReaction != FaceAppReaction.None && animationsEnabled) {
            reactionKind = nextReaction
            reaction.animateTo(1f, tween(150, easing = FastOutSlowInEasing))
            reaction.animateTo(0f, tween(620, easing = FastOutSlowInEasing))
            reactionKind = FaceAppReaction.None
        }
    }

    val basePose = referenceAnchor?.pose ?: FaceReferenceAnchor.Neutral.pose
    val basePalette = referenceAnchor?.palette ?: FaceReferenceAnchor.Neutral.palette
    val restEnvelope = if (animated) faceRestEnvelope(phase) else 0f
    val effectiveAudioEnergy = audioEnergy * restEnvelope
    val localPose = localFallbackPose(basePose, playbackMode, effectiveAudioEnergy)
    val pose = applyFaceReaction(localPose, reactionKind, reaction.value)
    val colors = resonanceLensColors(basePalette, reactionKind, reaction.value)
    val numericSeed = remember(trackSeed) { trackSeed.hashCode() }
    val asymmetry = remember(numericSeed, referenceAnchor) {
        when (referenceAnchor) {
            FaceReferenceAnchor.DreamyAtmospheric -> 0.04f
            null -> stableMicroAsymmetry(numericSeed)
            else -> 0f
        }
    }

    Canvas(
        modifier = modifier
            .aspectRatio(FACE_ASPECT_RATIO)
            .testTag("autplay-face")
            .semantics { contentDescription = accessibilitySummary },
    ) {
        drawResonanceLens(
            pose = pose,
            colors = colors,
            phase = phase,
            audioEnergy = effectiveAudioEnergy,
            audioContour = audioFrame.contour,
            animated = animated,
            asymmetry = asymmetry,
        )
    }
}

internal fun shouldAnimateResonanceLens(
    playbackMode: FacePlaybackMode,
    systemAnimationsEnabled: Boolean,
): Boolean = playbackMode == FacePlaybackMode.Playing && systemAnimationsEnabled

internal fun shouldObserveAudioContour(animated: Boolean, lifecycleStarted: Boolean): Boolean =
    animated && lifecycleStarted

internal fun localFallbackPose(
    base: ResonanceLensPose = FaceReferenceAnchor.Neutral.pose,
    playbackMode: FacePlaybackMode,
    audioEnergy: Float,
): ResonanceLensPose {
    val boundedEnergy = if (playbackMode == FacePlaybackMode.Playing) {
        audioEnergy.coerceIn(0f, 1f)
    } else {
        0f
    }
    val pausedOffset = if (playbackMode == FacePlaybackMode.Paused) -0.10f else 0f
    val bufferingOffset = if (playbackMode == FacePlaybackMode.Buffering) -0.05f else 0f
    return base.copy(
        upperOpen = base.upperOpen + boundedEnergy * 0.12f + pausedOffset + bufferingOffset,
        lidTightness = base.lidTightness + boundedEnergy * 0.08f,
    ).bounded()
}

internal fun applyFaceReaction(
    pose: ResonanceLensPose,
    reaction: FaceAppReaction,
    amount: Float,
): ResonanceLensPose {
    val progress = amount.coerceIn(0f, 1f)
    return when (reaction) {
        FaceAppReaction.None -> pose
        FaceAppReaction.Play -> pose.copy(upperOpen = pose.upperOpen + 0.14f * progress)
        FaceAppReaction.Pause -> pose.copy(upperOpen = pose.upperOpen - 0.14f * progress)
        FaceAppReaction.Like -> pose.copy(
            outerY = pose.outerY + 0.12f * progress,
            upperOpen = pose.upperOpen + 0.06f * progress,
            lowerLift = pose.lowerLift + 0.22f * progress,
        )
        FaceAppReaction.Dislike -> pose.copy(
            innerY = pose.innerY - 0.16f * progress,
            centerPull = pose.centerPull + 0.18f * progress,
            lidTightness = pose.lidTightness + 0.22f * progress,
        )
    }.bounded()
}

internal fun blendResonanceLensPose(
    start: ResonanceLensPose,
    end: ResonanceLensPose,
    fraction: Float,
): ResonanceLensPose {
    val t = fraction.coerceIn(0f, 1f)
    if (t == 0f) return start
    if (t == 1f) return end
    fun blend(a: Float, b: Float): Float = a + (b - a) * t
    return ResonanceLensPose(
        innerY = blend(start.innerY, end.innerY),
        outerY = blend(start.outerY, end.outerY),
        arch = blend(start.arch, end.arch),
        centerPull = blend(start.centerPull, end.centerPull),
        ribbonTension = blend(start.ribbonTension, end.ribbonTension),
        upperOpen = blend(start.upperOpen, end.upperOpen),
        lowerLift = blend(start.lowerLift, end.lowerLift),
        lidTightness = blend(start.lidTightness, end.lidTightness),
    ).bounded()
}

internal data class UpperRibbonProfile(
    val outerHeight: Float,
    val innerHeight: Float,
    val midpointHeight: Float,
    val thickness: Float,
    val controlInset: Float,
)

/** Normalized upward distances used by the closed four-point optical ribbon. */
internal fun upperRibbonProfile(pose: ResonanceLensPose): UpperRibbonProfile {
    // Zero is the aperture baseline. State controls move the two tips around that
    // baseline while arch owns the lifted optical surface between them.
    val endpointRise = 0f
    val outerHeight = endpointRise + pose.outerY * 0.24f
    val innerHeight = endpointRise + pose.innerY * 0.24f
    val midpointHeight = (outerHeight + innerHeight) / 2f +
        0.14f + pose.arch * 0.30f
    return UpperRibbonProfile(
        outerHeight = outerHeight,
        innerHeight = innerHeight,
        midpointHeight = midpointHeight,
        thickness = 0.065f + (1f - pose.ribbonTension) * 0.035f,
        controlInset = 0.21f + (1f - pose.ribbonTension) * 0.10f,
    )
}

/** A cycle deliberately contains a long still interval instead of constant BPM-like pulsing. */
internal fun faceRestEnvelope(phase: Float): Float {
    val normalized = ((phase % 1f) + 1f) % 1f
    return when {
        normalized < 0.24f -> 0f
        normalized < 0.38f -> smoothStep((normalized - 0.24f) / 0.14f)
        normalized < 0.72f -> 1f
        normalized < 0.88f -> 1f - smoothStep((normalized - 0.72f) / 0.16f)
        else -> 0f
    }
}

private data class ResonanceLensColors(
    val visorTop: Color,
    val visorBottom: Color,
    val primary: Color,
    val secondary: Color,
    val tertiary: Color,
    val filament: Color,
)

private fun resonanceLensColors(
    base: FaceSpectralPalette,
    reaction: FaceAppReaction,
    reactionAmount: Float,
): ResonanceLensColors {
    val amount = reactionAmount.coerceIn(0f, 1f)
    val accent = when (reaction) {
        FaceAppReaction.Like -> Color(0xFFF0C767)
        FaceAppReaction.Dislike -> Color(0xFFF08068)
        FaceAppReaction.Play -> Color(0xFF8EEBC7)
        FaceAppReaction.Pause, FaceAppReaction.None -> base.primary
    }
    return ResonanceLensColors(
        visorTop = Color(0xFF111827),
        visorBottom = Color(0xFF05070D),
        primary = blendColor(base.primary, accent, amount * 0.56f),
        secondary = blendColor(base.secondary, accent, amount * 0.30f),
        tertiary = blendColor(base.tertiary, accent, amount * 0.20f),
        filament = blendColor(base.filament, accent, amount * 0.72f),
    )
}

private fun DrawScope.drawResonanceLens(
    pose: ResonanceLensPose,
    colors: ResonanceLensColors,
    phase: Float,
    audioEnergy: Float,
    audioContour: List<Float>,
    animated: Boolean,
    asymmetry: Float,
) {
    val corner = CornerRadius(size.height * 0.16f)
    drawRoundRect(
        brush = Brush.linearGradient(
            listOf(colors.visorTop, colors.visorBottom),
            start = Offset.Zero,
            end = Offset(size.width, size.height),
        ),
        cornerRadius = corner,
    )
    drawRoundRect(
        color = Color.White.copy(alpha = 0.09f),
        cornerRadius = corner,
        style = Stroke(width = 1.dp.toPx()),
    )
    drawRoundRect(
        brush = Brush.verticalGradient(
            listOf(Color.White.copy(alpha = 0.07f), Color.Transparent),
            endY = size.height * 0.48f,
        ),
        size = Size(size.width, size.height * 0.52f),
        cornerRadius = corner,
    )
    drawRoundRect(
        color = colors.tertiary.copy(alpha = 0.12f),
        topLeft = Offset(4.dp.toPx(), 4.dp.toPx()),
        size = Size(size.width - 8.dp.toPx(), size.height - 8.dp.toPx()),
        cornerRadius = CornerRadius(
            x = (corner.x - 4.dp.toPx()).coerceAtLeast(0f),
            y = (corner.y - 4.dp.toPx()).coerceAtLeast(0f),
        ),
        style = Stroke(width = 0.65.dp.toPx()),
    )

    // Broad optical wells give the two apertures a shared instrument housing instead of leaving
    // the luminous geometry floating on a flat black card. Layered radial gradients are used
    // instead of a platform blur so the result stays deterministic across supported Android APIs.
    listOf(0.29f, 0.71f).forEach { centerX ->
        drawOval(
            brush = Brush.radialGradient(
                colors = listOf(
                    colors.tertiary.copy(alpha = 0.13f),
                    colors.secondary.copy(alpha = 0.055f),
                    Color.Transparent,
                ),
                center = Offset(size.width * centerX, size.height * 0.59f),
                radius = size.width * 0.25f,
            ),
            topLeft = Offset(size.width * (centerX - 0.25f), size.height * 0.20f),
            size = Size(size.width * 0.50f, size.height * 0.76f),
        )
    }

    val rest = if (animated) faceRestEnvelope(phase) else 0f
    val phaseRadians = phase * 2f * PI.toFloat()
    val motionEnergy = audioEnergy.coerceIn(0f, 1f) * (0.28f + rest * 0.72f)
    val pupilState = reactivePupilState(
        audioEnergy = audioEnergy,
        audioContour = audioContour,
        animated = animated,
        activeAmount = rest,
    )
    val eyeY = size.height * (0.565f + sin(phaseRadians) * 0.008f * rest)
    // The 3–6% asymmetry control is applied to a quarter-rig travel range so it reads as a
    // micro-offset instead of moving the two complete eye assemblies onto different rows.
    val asymmetryOffset = size.height * 0.17f * asymmetry
    drawEye(
        center = Offset(size.width * 0.29f, eyeY - asymmetryOffset),
        isLeft = true,
        pose = pose,
        colors = colors,
        phaseRadians = phaseRadians,
        motionEnergy = motionEnergy,
        rest = rest,
        pupilState = pupilState,
    )
    drawEye(
        center = Offset(size.width * 0.71f, eyeY + asymmetryOffset),
        isLeft = false,
        pose = pose,
        colors = colors,
        phaseRadians = phaseRadians + 0.18f,
        motionEnergy = motionEnergy,
        rest = rest,
        pupilState = pupilState,
    )
}

private fun DrawScope.drawEye(
    center: Offset,
    isLeft: Boolean,
    pose: ResonanceLensPose,
    colors: ResonanceLensColors,
    phaseRadians: Float,
    motionEnergy: Float,
    rest: Float,
    pupilState: ReactivePupilState,
) {
    val eyeWidth = size.width * 0.335f
    val eyeHeight = size.height * 0.68f
    val outerX = center.x + if (isLeft) -eyeWidth / 2f else eyeWidth / 2f
    val innerDirection = if (isLeft) 1f else -1f
    val innerX = center.x + innerDirection * eyeWidth * (0.5f + pose.centerPull * 0.10f)
    val upperHalf = eyeHeight * (0.10f + pose.upperOpen * 0.25f - pose.lidTightness * 0.035f)
    val lowerHalf = eyeHeight * (
        0.18f + pose.upperOpen * 0.10f - pose.lowerLift * 0.10f - pose.lidTightness * 0.025f
    )
    val upperEndpointBias = eyeHeight * 0.14f
    val outerUpper = center.y - upperHalf - pose.outerY * upperEndpointBias
    val innerUpper = center.y - upperHalf - pose.innerY * upperEndpointBias
    val upperArch = pose.arch * eyeHeight * 0.16f
    val lowerY = center.y + lowerHalf
    val aperture = Path().apply {
        moveTo(outerX, center.y)
        cubicTo(
            outerX + innerDirection * eyeWidth * 0.22f,
            outerUpper - upperArch,
            innerX - innerDirection * eyeWidth * 0.22f,
            innerUpper - upperArch,
            innerX,
            center.y,
        )
        cubicTo(
            innerX - innerDirection * eyeWidth * 0.22f,
            lowerY,
            outerX + innerDirection * eyeWidth * 0.22f,
            lowerY,
            outerX,
            center.y,
        )
        close()
    }

    drawEyeAtmosphere(
        center = center,
        eyeWidth = eyeWidth,
        eyeHeight = eyeHeight,
        lowerHalf = lowerHalf,
        colors = colors,
    )
    drawPath(
        path = aperture,
        color = colors.tertiary.copy(alpha = 0.075f),
        style = Stroke(width = 5.dp.toPx(), cap = StrokeCap.Round),
    )
    drawPath(
        path = aperture,
        brush = Brush.verticalGradient(
            colors = listOf(Color(0xFF09111F), Color(0xFF010207), Color(0xFF040916)),
            startY = center.y - upperHalf,
            endY = center.y + lowerHalf,
        ),
    )
    clipPath(aperture) {
        val gaze = Offset(
            x = center.x + sin(phaseRadians * 0.43f) * eyeWidth * 0.022f * rest,
            y = center.y + cos(phaseRadians * 0.37f) * eyeHeight * 0.018f * rest,
        )
        val glowWidth = eyeWidth * 0.86f
        val glowHeight = eyeHeight * 0.72f
        drawOval(
            brush = Brush.radialGradient(
                listOf(
                    colors.primary.copy(alpha = 0.24f),
                    colors.tertiary.copy(alpha = 0.13f),
                    Color.Transparent,
                ),
                center = Offset(gaze.x, gaze.y + glowHeight * 0.12f),
                radius = glowWidth * 0.52f,
            ),
            topLeft = Offset(gaze.x - glowWidth / 2f, gaze.y - glowHeight * 0.38f),
            size = Size(glowWidth, glowHeight),
        )
        val coreWidth = eyeWidth * (0.24f + pose.lidTightness * 0.035f)
        val coreHeight = eyeHeight * (0.47f + motionEnergy * 0.05f)
        drawOval(
            brush = Brush.horizontalGradient(
                listOf(Color.Black, Color(0xFF0A0F1B), Color.Black),
                startX = gaze.x - coreWidth / 2f,
                endX = gaze.x + coreWidth / 2f,
            ),
            topLeft = Offset(gaze.x - coreWidth / 2f, gaze.y - coreHeight * 0.36f),
            size = Size(coreWidth, coreHeight),
        )
        drawOval(
            color = colors.tertiary.copy(alpha = 0.16f),
            topLeft = Offset(gaze.x - coreWidth / 2f, gaze.y - coreHeight * 0.36f),
            size = Size(coreWidth, coreHeight),
            style = Stroke(width = 0.8.dp.toPx()),
        )

        // Soft orbital echoes create the smoky depth visible in the approved concept while the
        // four crisp U-rings remain the state-readable foreground geometry.
        repeat(3) { echo ->
            val scale = 1.06f - echo * 0.12f
            val echoWidth = eyeWidth * 0.92f * scale
            val echoHeight = lowerHalf * 2.05f * scale
            val echoRect = Rect(
                left = gaze.x - echoWidth / 2f,
                top = gaze.y - echoHeight * 0.42f,
                right = gaze.x + echoWidth / 2f,
                bottom = gaze.y + echoHeight * 0.58f,
            )
            drawArc(
                color = if (echo == 0) colors.tertiary else colors.secondary,
                startAngle = 1f,
                sweepAngle = 178f,
                useCenter = false,
                topLeft = echoRect.topLeft,
                size = echoRect.size,
                alpha = 0.055f + echo * 0.018f,
                style = Stroke(
                    width = (7.5f - echo * 1.5f).dp.toPx(),
                    cap = StrokeCap.Round,
                ),
            )
        }
        repeat(4) { layer ->
            val scale = 1f - layer * 0.145f
            val arcWidth = eyeWidth * 0.84f * scale
            val arcHeight = lowerHalf * 1.75f * scale
            val rect = Rect(
                left = gaze.x - arcWidth / 2f,
                top = gaze.y - arcHeight * 0.42f,
                right = gaze.x + arcWidth / 2f,
                bottom = gaze.y + arcHeight * 0.58f,
            )
            val color = when (layer) {
                0 -> colors.tertiary
                1 -> colors.secondary
                2 -> colors.primary
                else -> colors.filament
            }
            val startAngle = 2f + layer * 2.3f + phaseRadians * 2f * rest
            val sweepAngle = 176f - layer * 4f
            val strokeWidth = (2.35f - layer * 0.26f).dp.toPx() + motionEnergy * 1.2.dp.toPx()
            drawArc(
                color = color,
                startAngle = startAngle,
                sweepAngle = sweepAngle,
                useCenter = false,
                topLeft = rect.topLeft,
                size = rect.size,
                alpha = 0.07f,
                style = Stroke(width = strokeWidth + 8.dp.toPx(), cap = StrokeCap.Round),
            )
            drawArc(
                color = color,
                startAngle = startAngle,
                sweepAngle = sweepAngle,
                useCenter = false,
                topLeft = rect.topLeft,
                size = rect.size,
                alpha = 0.18f,
                style = Stroke(width = strokeWidth + 3.dp.toPx(), cap = StrokeCap.Round),
            )
            drawArc(
                color = color,
                startAngle = startAngle,
                sweepAngle = sweepAngle,
                useCenter = false,
                topLeft = rect.topLeft,
                size = rect.size,
                alpha = 0.68f + layer * 0.075f,
                style = Stroke(width = strokeWidth, cap = StrokeCap.Round),
            )
        }
        drawReactivePupil(
            gaze = gaze,
            coreWidth = coreWidth,
            coreHeight = coreHeight,
            colors = colors,
            state = pupilState,
        )
    }
    drawPath(
        path = aperture,
        color = colors.primary.copy(alpha = 0.09f),
        style = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round),
    )
    drawPath(
        path = aperture,
        brush = Brush.horizontalGradient(
            listOf(
                colors.tertiary.copy(alpha = 0.26f),
                colors.primary.copy(alpha = 0.52f),
                colors.tertiary.copy(alpha = 0.26f),
            ),
            startX = outerX,
            endX = innerX,
        ),
        style = Stroke(width = 0.9.dp.toPx(), cap = StrokeCap.Round),
    )
    drawUpperApertureRibbon(
        center = center,
        eyeWidth = eyeWidth,
        eyeHeight = eyeHeight,
        isLeft = isLeft,
        pose = pose,
        colors = colors,
    )
}

private fun DrawScope.drawReactivePupil(
    gaze: Offset,
    coreWidth: Float,
    coreHeight: Float,
    colors: ResonanceLensColors,
    state: ReactivePupilState,
) {
    val radius = coreWidth * 0.23f * state.dilation
    val pupilCenter = Offset(
        x = gaze.x + state.horizontalBias * coreWidth * 0.16f,
        y = gaze.y + coreHeight * 0.025f + state.verticalBias * coreHeight * 0.10f,
    )
    val quietColor = blendColor(colors.tertiary, colors.secondary, 0.34f)
    val activeColor = blendColor(colors.secondary, colors.filament, state.colorMix)
    val energyColor = blendColor(quietColor, activeColor, state.colorMix)
    val pupilColor = if (state.contourBias >= 0f) {
        blendColor(energyColor, colors.filament, state.contourBias * 0.72f)
    } else {
        blendColor(energyColor, colors.primary, -state.contourBias * 0.58f)
    }

    drawCircle(
        color = pupilColor.copy(alpha = 0.06f + state.colorMix * 0.06f),
        radius = radius * 2.25f,
        center = pupilCenter,
    )
    drawCircle(
        color = pupilColor.copy(alpha = 0.14f + state.colorMix * 0.10f),
        radius = radius * 1.48f,
        center = pupilCenter,
    )
    drawCircle(
        brush = Brush.radialGradient(
            colors = listOf(
                Color(0xFF010207),
                Color(0xFF02040A),
                pupilColor.copy(alpha = 0.92f),
                pupilColor.copy(alpha = 0.48f),
                Color(0xFF010207).copy(alpha = 0.96f),
            ),
            center = pupilCenter,
            radius = radius,
        ),
        radius = radius,
        center = pupilCenter,
    )
    drawCircle(
        color = pupilColor.copy(alpha = 0.72f + state.colorMix * 0.22f),
        radius = radius,
        center = pupilCenter,
        style = Stroke(width = (0.85f + state.colorMix * 0.55f).dp.toPx()),
    )
    drawCircle(
        color = colors.filament.copy(alpha = 0.72f),
        radius = (0.85f + state.colorMix * 0.75f).dp.toPx(),
        center = Offset(
            x = pupilCenter.x - radius * 0.27f,
            y = pupilCenter.y - radius * 0.27f,
        ),
    )
}

private fun DrawScope.drawEyeAtmosphere(
    center: Offset,
    eyeWidth: Float,
    eyeHeight: Float,
    lowerHalf: Float,
    colors: ResonanceLensColors,
) {
    val haloWidth = eyeWidth * 1.12f
    val haloHeight = eyeHeight * 0.82f
    drawOval(
        brush = Brush.radialGradient(
            colors = listOf(
                colors.secondary.copy(alpha = 0.14f),
                colors.tertiary.copy(alpha = 0.065f),
                Color.Transparent,
            ),
            center = Offset(center.x, center.y + lowerHalf * 0.35f),
            radius = eyeWidth * 0.55f,
        ),
        topLeft = Offset(center.x - haloWidth / 2f, center.y - haloHeight * 0.40f),
        size = Size(haloWidth, haloHeight),
    )
    repeat(3) { band ->
        val scale = 1f - band * 0.12f
        val bandWidth = eyeWidth * scale
        val bandHeight = eyeHeight * (0.55f - band * 0.055f)
        val rect = Rect(
            left = center.x - bandWidth / 2f,
            top = center.y - bandHeight * 0.34f + band * eyeHeight * 0.018f,
            right = center.x + bandWidth / 2f,
            bottom = center.y + bandHeight * 0.66f + band * eyeHeight * 0.018f,
        )
        drawArc(
            color = if (band == 0) colors.tertiary else colors.secondary,
            startAngle = 5f,
            sweepAngle = 170f,
            useCenter = false,
            topLeft = rect.topLeft,
            size = rect.size,
            alpha = 0.035f + band * 0.018f,
            style = Stroke(width = (9f - band * 2f).dp.toPx(), cap = StrokeCap.Round),
        )
    }
}

private fun DrawScope.drawUpperApertureRibbon(
    center: Offset,
    eyeWidth: Float,
    eyeHeight: Float,
    isLeft: Boolean,
    pose: ResonanceLensPose,
    colors: ResonanceLensColors,
) {
    val innerDirection = if (isLeft) 1f else -1f
    val outerX = center.x - innerDirection * eyeWidth / 2f
    val innerX = center.x + innerDirection * eyeWidth * (0.5f + pose.centerPull * 0.10f)
    val profile = upperRibbonProfile(pose)
    val outerY = center.y - eyeHeight * profile.outerHeight
    val innerY = center.y - eyeHeight * profile.innerHeight
    val control1X = outerX + innerDirection * eyeWidth * profile.controlInset
    val control2X = innerX - innerDirection * eyeWidth * profile.controlInset
    val midpointY = center.y - eyeHeight * profile.midpointHeight
    val thickness = eyeHeight * profile.thickness

    val mainRibbon = upperRibbonPath(
        outerX = outerX,
        outerY = outerY,
        innerX = innerX,
        innerY = innerY,
        control1X = control1X,
        control2X = control2X,
        midpointY = midpointY,
        thickness = thickness,
    )
    drawPath(
        path = mainRibbon,
        color = colors.primary.copy(alpha = 0.065f),
        style = Stroke(width = 7.dp.toPx(), cap = StrokeCap.Round),
    )

    for (layer in 3 downTo 0) {
        val lift = eyeHeight * 0.026f * layer
        val layerThickness = thickness * (1f + layer * 0.22f)
        val alpha = when (layer) {
            3 -> 0.055f
            2 -> 0.10f
            1 -> 0.22f
            else -> 0.78f
        }
        val ribbon = upperRibbonPath(
            outerX = outerX,
            outerY = outerY - lift,
            innerX = innerX,
            innerY = innerY - lift,
            control1X = control1X,
            control2X = control2X,
            midpointY = midpointY - lift,
            thickness = layerThickness,
        )
        drawPath(
            path = ribbon,
            brush = Brush.linearGradient(
                listOf(
                    colors.tertiary.copy(alpha = alpha * 0.54f),
                    colors.primary.copy(alpha = alpha),
                    colors.secondary.copy(alpha = alpha * 0.82f),
                ),
                start = Offset(outerX, outerY),
                end = Offset(innerX, innerY),
            ),
        )
        drawPath(
            path = ribbon,
            color = colors.primary.copy(alpha = if (layer == 0) 0.42f else alpha * 0.65f),
            style = Stroke(
                width = if (layer == 0) 1.1.dp.toPx() else 0.55.dp.toPx(),
                cap = StrokeCap.Round,
            ),
        )
    }

    val upperFacet = Path().apply {
        moveTo(outerX, outerY - thickness * 0.23f)
        cubicTo(
            control1X,
            midpointY - thickness * 0.23f,
            control2X,
            midpointY - thickness * 0.23f,
            innerX,
            innerY - thickness * 0.23f,
        )
    }
    drawPath(
        path = upperFacet,
        color = colors.filament.copy(alpha = 0.64f),
        style = Stroke(width = 1.05.dp.toPx(), cap = StrokeCap.Round),
    )
    val lowerFacet = Path().apply {
        moveTo(outerX, outerY + thickness * 0.22f)
        cubicTo(
            control1X,
            midpointY + thickness * 0.22f,
            control2X,
            midpointY + thickness * 0.22f,
            innerX,
            innerY + thickness * 0.22f,
        )
    }
    drawPath(
        path = lowerFacet,
        color = colors.secondary.copy(alpha = 0.40f),
        style = Stroke(width = 0.75.dp.toPx(), cap = StrokeCap.Round),
    )
    val centerSheen = upperRibbonPath(
        outerX = outerX,
        outerY = outerY - thickness * 0.05f,
        innerX = innerX,
        innerY = innerY - thickness * 0.05f,
        control1X = control1X,
        control2X = control2X,
        midpointY = midpointY - thickness * 0.05f,
        thickness = thickness * 0.18f,
    )
    drawPath(
        path = centerSheen,
        brush = Brush.linearGradient(
            listOf(
                Color.Transparent,
                colors.filament.copy(alpha = 0.20f),
                colors.primary.copy(alpha = 0.10f),
                Color.Transparent,
            ),
            start = Offset(outerX, outerY),
            end = Offset(innerX, innerY),
        ),
    )
}

private fun upperRibbonPath(
    outerX: Float,
    outerY: Float,
    innerX: Float,
    innerY: Float,
    control1X: Float,
    control2X: Float,
    midpointY: Float,
    thickness: Float,
): Path {
    val half = thickness / 2f
    return Path().apply {
        moveTo(outerX, outerY - half * 0.12f)
        cubicTo(
            control1X,
            midpointY - half,
            control2X,
            midpointY - half,
            innerX,
            innerY - half * 0.12f,
        )
        cubicTo(
            control2X,
            midpointY + half,
            control1X,
            midpointY + half,
            outerX,
            outerY + half * 0.12f,
        )
        close()
    }
}

private fun stableMicroAsymmetry(seed: Int): Float {
    val magnitude = 0.03f + (seed.absoluteValue % 4) * 0.01f
    // Expressed as 3–6% of the eye rig height, not of the whole visor.
    return magnitude * if (seed and 1 == 0) 1f else -1f
}

private fun smoothStep(value: Float): Float {
    val t = value.coerceIn(0f, 1f)
    return t * t * (3f - 2f * t)
}

private fun blendColor(start: Color, end: Color, fraction: Float): Color {
    val t = fraction.coerceIn(0f, 1f)
    return Color(
        red = start.red + (end.red - start.red) * t,
        green = start.green + (end.green - start.green) * t,
        blue = start.blue + (end.blue - start.blue) * t,
        alpha = start.alpha + (end.alpha - start.alpha) * t,
    )
}

private const val FACE_ASPECT_RATIO = 1.84f
