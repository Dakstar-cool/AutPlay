package app.autplay.ui.face

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ResonanceLensFaceTest {
    @Test
    fun nineReferenceAnchorsRemainDistinctAndBounded() {
        val poses = FaceReferenceAnchor.entries.map { it.pose }
        val palettes = FaceReferenceAnchor.entries.map { it.palette }

        assertEquals(9, poses.distinct().size)
        assertEquals(9, palettes.distinct().size)
        poses.forEach { pose ->
            assertTrue(pose.innerY in -1f..1f)
            assertTrue(pose.outerY in -1f..1f)
            assertTrue(pose.arch in -1f..1f)
            assertTrue(pose.centerPull in 0f..1f)
            assertTrue(pose.ribbonTension in 0f..1f)
            assertTrue(pose.upperOpen in 0f..1f)
            assertTrue(pose.lowerLift in 0f..1f)
            assertTrue(pose.lidTightness in 0f..1f)
        }
    }

    @Test
    fun melancholyAndAggressionUseOppositeInnerRibbonGeometry() {
        val melancholy = FaceReferenceAnchor.MelancholicDark.pose
        val aggression = FaceReferenceAnchor.AggressiveTense.pose
        val melancholyRibbon = upperRibbonProfile(melancholy)
        val aggressionRibbon = upperRibbonProfile(aggression)

        assertTrue(melancholy.innerY > melancholy.outerY)
        assertTrue(aggression.innerY < aggression.outerY)
        assertTrue(melancholyRibbon.innerHeight > melancholyRibbon.outerHeight)
        assertTrue(aggressionRibbon.innerHeight < aggressionRibbon.outerHeight)
        assertTrue(aggression.centerPull > melancholy.centerPull)
        assertTrue(aggression.ribbonTension > melancholy.ribbonTension)
        assertTrue(aggression.lidTightness > melancholy.lidTightness)
    }

    @Test
    fun ribbonTensionControlsElasticityIndependentlyFromEyeOpening() {
        val soft = FaceReferenceAnchor.CalmSoft.pose
        val taut = soft.copy(ribbonTension = 1f)
        val softProfile = upperRibbonProfile(soft)
        val tautProfile = upperRibbonProfile(taut)

        assertTrue(softProfile.thickness > tautProfile.thickness)
        assertTrue(softProfile.controlInset > tautProfile.controlInset)
        assertEquals(softProfile.outerHeight, tautProfile.outerHeight, 0f)
        assertEquals(softProfile.innerHeight, tautProfile.innerHeight, 0f)
        assertEquals(softProfile.midpointHeight, tautProfile.midpointHeight, 0f)

        val opened = soft.copy(upperOpen = 1f)
        assertEquals(softProfile, upperRibbonProfile(opened))
    }

    @Test
    fun neutralRibbonTipsMeetTheApertureBaseline() {
        val profile = upperRibbonProfile(FaceReferenceAnchor.Neutral.pose)

        assertEquals(0f, profile.outerHeight, 0f)
        assertEquals(0f, profile.innerHeight, 0f)
        assertTrue(profile.midpointHeight > profile.outerHeight)
    }

    @Test
    fun referencePalettesFollowTheApprovedSpectralFamilies() {
        val energetic = FaceReferenceAnchor.EnergeticBright.palette.primary
        val aggressive = FaceReferenceAnchor.AggressiveTense.palette.primary
        val ominous = FaceReferenceAnchor.Ominous.palette.primary

        assertTrue(energetic.green > energetic.red)
        assertTrue(energetic.blue > energetic.red)
        assertTrue(aggressive.red > aggressive.green)
        assertTrue(ominous.blue > ominous.green)
    }

    @Test
    fun positiveAndEuphoricDependOnLowerApertureLift() {
        val positive = FaceReferenceAnchor.PositiveLight.pose
        val euphoric = FaceReferenceAnchor.Euphoric.pose

        assertTrue(positive.lowerLift > FaceReferenceAnchor.Neutral.pose.lowerLift)
        assertTrue(euphoric.lowerLift > positive.lowerLift)
        assertTrue(euphoric.upperOpen > positive.upperOpen)
    }

    @Test
    fun interpolationIsContinuousAndClamped() {
        val start = FaceReferenceAnchor.CalmSoft.pose
        val end = FaceReferenceAnchor.AggressiveTense.pose
        val midpoint = blendResonanceLensPose(start, end, 0.5f)

        assertEquals((start.innerY + end.innerY) / 2f, midpoint.innerY, 0.0001f)
        assertEquals((start.ribbonTension + end.ribbonTension) / 2f, midpoint.ribbonTension, 0.0001f)
        assertEquals((start.upperOpen + end.upperOpen) / 2f, midpoint.upperOpen, 0.0001f)
        assertEquals(start, blendResonanceLensPose(start, end, -1f))
        assertEquals(end, blendResonanceLensPose(start, end, 2f))
    }

    @Test
    fun localFallbackNeverInventsMoodGeometry() {
        val neutral = FaceReferenceAnchor.Neutral.pose
        val playing = localFallbackPose(neutral, FacePlaybackMode.Playing, audioEnergy = 0.8f)
        val paused = localFallbackPose(neutral, FacePlaybackMode.Paused, audioEnergy = 0.8f)

        assertEquals(neutral.innerY, playing.innerY)
        assertEquals(neutral.outerY, playing.outerY)
        assertEquals(neutral.arch, playing.arch)
        assertTrue(playing.upperOpen > neutral.upperOpen)
        assertEquals(neutral.innerY, paused.innerY)
        assertTrue(paused.upperOpen < neutral.upperOpen)
    }

    @Test
    fun reactivePupilFreezesWithoutActiveMotion() {
        val contour = List(12) { 0.22f }

        assertEquals(
            ReactivePupilState(),
            reactivePupilState(0.8f, contour, animated = false, activeAmount = 1f),
        )
        assertEquals(
            ReactivePupilState(),
            reactivePupilState(0.8f, contour, animated = true, activeAmount = 0f),
        )
    }

    @Test
    fun reactivePupilUsesBoundedEnergyAndTemporalContour() {
        val lateAttack = List(12) { index -> if (index >= 8) 0.24f else 0.02f }
        val earlyAttack = lateAttack.reversed()
        val lateState = reactivePupilState(
            audioEnergy = 0.78f,
            audioContour = lateAttack,
            animated = true,
            activeAmount = 1f,
        )
        val earlyState = reactivePupilState(
            audioEnergy = 0.78f,
            audioContour = earlyAttack,
            animated = true,
            activeAmount = 1f,
        )

        assertTrue(lateState.dilation in 1f..1.42f)
        assertTrue(lateState.dilation > 1f)
        assertTrue(lateState.horizontalBias in -1f..1f)
        assertTrue(lateState.verticalBias in -1f..1f)
        assertTrue(lateState.colorMix in 0f..1f)
        assertTrue(lateState.colorMix > 0f)
        assertTrue(lateState.horizontalBias > 0f)
        assertTrue(earlyState.horizontalBias < 0f)
        assertTrue(lateState.contourBias > 0f)
        assertTrue(earlyState.contourBias < 0f)
    }

    @Test
    fun confirmedPreferenceReactionsAreBriefPoseDeltas() {
        val neutral = FaceReferenceAnchor.Neutral.pose
        val liked = applyFaceReaction(neutral, FaceAppReaction.Like, 1f)
        val disliked = applyFaceReaction(neutral, FaceAppReaction.Dislike, 1f)

        assertTrue(liked.lowerLift > neutral.lowerLift)
        assertTrue(disliked.centerPull > neutral.centerPull)
        assertTrue(disliked.lidTightness > neutral.lidTightness)
        assertEquals(neutral, applyFaceReaction(neutral, FaceAppReaction.Like, 0f))
        assertNotEquals(liked, disliked)
    }

    @Test
    fun reducedMotionAndRestPolicyStopContinuousMovement() {
        assertTrue(shouldAnimateResonanceLens(FacePlaybackMode.Playing, true))
        assertTrue(!shouldAnimateResonanceLens(FacePlaybackMode.Playing, false))
        assertTrue(!shouldAnimateResonanceLens(FacePlaybackMode.Paused, true))
        assertTrue(shouldObserveAudioContour(animated = true, lifecycleStarted = true))
        assertTrue(!shouldObserveAudioContour(animated = true, lifecycleStarted = false))
        assertTrue(!shouldObserveAudioContour(animated = false, lifecycleStarted = true))
        assertEquals(0f, faceRestEnvelope(0.10f), 0f)
        assertEquals(1f, faceRestEnvelope(0.55f), 0f)
        assertEquals(0f, faceRestEnvelope(0.95f), 0f)
    }
}
