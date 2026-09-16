package app.autplay.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import kotlin.math.abs

class HomeSwipeAnimationTest {
    @get:Rule val compose = createComposeRule()
    private var next = 0
    private var previous = 0
    private var pixelsPerDp = 1f
    private lateinit var motion: HomeCarouselMotion
    private val current = mutableStateOf("current")
    private val enabled = mutableStateOf(true)
    private val targets = mutableStateOf(HomeSwipeTargets(
        HomeSwipeTarget("previous", "previous", "Previous song", "Artist"),
        HomeSwipeTarget("next", "next", "Next song", "Artist"),
    ))

    private fun render(animations: Boolean = true, acknowledge: Boolean = true) {
        compose.setContent {
            pixelsPerDp = LocalDensity.current.density
            motion = rememberHomeCarouselMotion(current.value)
            AutPlayTheme {
                Box(Modifier.width(320.dp)) {
                    HomeArtworkCarousel(
                        title = current.value, targets = targets.value, motion = motion,
                        enabled = enabled.value, canOpen = true, onOpen = {}, animations = animations,
                        onPrevious = { previous++; if (acknowledge) current.value = "previous-$previous" },
                        onNext = { next++; if (acknowledge) current.value = "next-$next" },
                    )
                }
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
    }

    private fun drag(distance: Float, vertical: Float = 0f) {
        compose.onNodeWithTag("home-track-carousel").performTouchInput {
            down(Offset(if (distance < 0f) width * 0.9f else width * 0.1f, height * 0.4f))
            moveBy(Offset(distance * pixelsPerDp, vertical * pixelsPerDp), delayMillis = 160)
        }
        compose.mainClock.advanceTimeBy(32)
    }

    private fun release(cancelled: Boolean = false) {
        compose.onNodeWithTag("home-track-carousel").performTouchInput {
            if (cancelled) cancel() else up()
        }
        compose.mainClock.advanceTimeBy(400)
    }

    @Test fun adjacentCoverEntersFromEachSideAndCommitsExactlyOnce() {
        render()
        for (distance in listOf(-210f, 210f)) {
            drag(distance)
            val side = if (distance < 0f) 1 else -1
            val incoming = compose.onNodeWithTag("home-cover-$side", useUnmergedTree = true)
                .fetchSemanticsNode().boundsInRoot
            assertTrue("Adjacent artwork is already visible while held", incoming.width > 100f * pixelsPerDp)
            compose.runOnIdle {
                assertTrue("Cover follows the finger without damping", abs(motion.offset) > 160f * pixelsPerDp)
                assertEquals(if (distance < 0) 0 else 1, next)
            }
            release()
            compose.runOnIdle { assertEquals(0f, motion.offset, 0.5f) }
        }
        compose.runOnIdle { assertEquals(1, next); assertEquals(1, previous) }
    }

    @Test fun shortDiagonalVerticalAndCancelledGesturesDoNotSkip() {
        render()
        for ((horizontal, vertical) in listOf(-90f to 0f, 90f to 0f, 120f to 130f, 0f to 150f)) {
            drag(horizontal, vertical)
            release()
        }
        drag(-210f)
        release(cancelled = true)
        compose.runOnIdle {
            assertEquals(0, next); assertEquals(0, previous)
            assertEquals(0f, motion.offset, 0.5f)
        }
    }

    @Test fun missingNeighborAndLockedControlsDoNotSkip() {
        targets.value = HomeSwipeTargets()
        render()
        drag(-210f)
        compose.runOnIdle { assertTrue(abs(motion.offset) <= motion.width * 0.061f) }
        release()
        compose.runOnIdle {
            targets.value = HomeSwipeTargets(next = HomeSwipeTarget("new", "new", "New", "Artist"))
            enabled.value = false
        }
        compose.mainClock.advanceTimeBy(32)
        drag(-210f)
        release()
        compose.runOnIdle { assertEquals(0, next); assertEquals(0, previous) }
    }

    @Test fun aShortGestureDuringTheReturnAnimationDoesNotInheritThePreviousTravel() {
        render()
        drag(-210f)
        compose.onNodeWithTag("home-track-carousel").performTouchInput { cancel() }
        compose.mainClock.advanceTimeBy(16)
        drag(-50f)
        release()
        compose.runOnIdle { assertEquals(0, next); assertEquals(0f, motion.offset, 0.5f) }
    }

    @Test fun changingTheQueueOrCurrentTrackCancelsAnUncommittedGesture() {
        render()
        drag(-210f)
        compose.runOnIdle { targets.value = targets.value.copy(next = HomeSwipeTarget("replacement", "replacement", "Replacement", null)) }
        compose.mainClock.advanceTimeBy(400)
        release()
        drag(-210f)
        compose.runOnIdle { current.value = "external-change" }
        compose.mainClock.advanceTimeBy(400)
        release()
        compose.runOnIdle { assertEquals(0, next); assertEquals(0, previous) }
    }

    @Test fun systemReducedMotionStillSwitchesWithoutMovingTheCover() {
        render(animations = false)
        val origin = compose.onNodeWithTag("home-cover-0").fetchSemanticsNode().boundsInRoot
        drag(-210f)
        assertEquals(origin, compose.onNodeWithTag("home-cover-0").fetchSemanticsNode().boundsInRoot)
        release()
        compose.runOnIdle { assertEquals(1, next); assertEquals(0f, motion.offset, 0.5f) }
    }

    @Test fun aPendingPlaybackCommandCannotBeSentTwice() {
        render(acknowledge = false)
        drag(-210f)
        release()
        compose.runOnIdle { assertTrue(motion.busy); assertEquals(1, next) }
        drag(-210f)
        release()
        compose.runOnIdle { assertEquals(1, next) }
        compose.waitUntil(timeoutMillis = 4000) {
            compose.mainClock.advanceTimeBy(32)
            !motion.busy
        }
        compose.runOnIdle { assertEquals(0f, motion.offset, 0.5f) }
    }

    @Test fun movingTheFingerDoesNotRecomposeTheScreenEveryFrame() {
        render()
        drag(-60f)
        val before = Recomposer.runningRecomposers.value.sumOf { it.changeCount }
        repeat(12) {
            compose.onNodeWithTag("home-track-carousel").performTouchInput {
                moveBy(Offset(-5f * pixelsPerDp, 0f), delayMillis = 16)
            }
            compose.mainClock.advanceTimeBy(32)
        }
        val changes = Recomposer.runningRecomposers.value.sumOf { it.changeCount } - before
        assertTrue("Drag must update graphics layers without recomposing on each frame: $changes", changes <= 1)
        release(cancelled = true)
    }
}
