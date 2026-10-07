package app.autplay.ui.player

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PlayerCollapseMotionTest {
    @get:Rule val compose = createComposeRule()
    private lateinit var motion: PlayerCollapseMotion

    private fun render(onCollapse: (() -> Unit)? = null) {
        compose.setContent {
            motion = rememberPlayerCollapseMotion()
            SideEffect { motion.travel = 600f }
            Box(Modifier.fillMaxSize().graphicsLayer { translationY = motion.offset }
                .playerCollapseGesture(onCollapse, motion, true).testTag("panel"))
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
    }

    @Test fun panelCanFollowTheFingerAlmostToMiniPlayerWithoutNavigatingWhileHeld() {
        var calls = 0
        render { calls++ }
        compose.onNodeWithTag("panel").performTouchInput {
            down(Offset(100f, 20f))
            moveBy(Offset(0f, 580f), delayMillis = 600)
        }
        compose.mainClock.advanceTimeBy(32)
        compose.runOnIdle {
            assertEquals(0, calls)
            assertTrue("Panel tracks beyond the old 72dp threshold", motion.offset >= 500f)
            assertTrue(motion.offset <= motion.travel)
        }
        compose.onNodeWithTag("panel").performTouchInput { up() }
        compose.mainClock.advanceTimeBy(32)
        compose.runOnIdle { assertEquals(0, calls) }
        compose.mainClock.advanceTimeBy(2500)
        compose.runOnIdle { assertEquals(1, calls) }
    }

    @Test fun collapseSettlesToTheAnchorBeforeNavigationExactlyOnce() {
        render()
        var calls = 0
        var offsetAtNavigation = 0f
        compose.runOnIdle {
            assertTrue(motion.begin())
            motion.drag(260f)
            assertEquals(260f, motion.offset, 0.01f)
            motion.release(0f, 1f, true) { calls++; offsetAtNavigation = motion.offset }
        }
        compose.mainClock.advanceTimeBy(64)
        compose.runOnIdle {
            assertEquals(0, calls)
            assertTrue(motion.offset > 260f && motion.offset < 600f)
        }
        compose.mainClock.advanceTimeBy(2500)
        compose.runOnIdle {
            assertEquals(1, calls)
            assertEquals(600f, offsetAtNavigation, 0.01f)
        }
    }

    @Test fun cancelledAndHorizontalGesturesDoNotNavigate() {
        var calls = 0
        render { calls++ }
        compose.onNodeWithTag("panel").performTouchInput {
            down(Offset(100f, 20f))
            moveBy(Offset(120f, 60f), delayMillis = 300)
            up()
        }
        compose.runOnIdle { assertEquals(0f, motion.offset, 0.01f); assertEquals(0, calls) }
        compose.onNodeWithTag("panel").performTouchInput {
            down(Offset(100f, 20f))
            moveBy(Offset(0f, 400f), delayMillis = 600)
        }
        compose.mainClock.advanceTimeBy(32)
        compose.onNodeWithTag("panel").performTouchInput { cancel() }
        compose.mainClock.advanceTimeBy(2500)
        compose.runOnIdle { assertEquals(0f, motion.offset, 0.01f); assertEquals(0, calls) }
    }

    @Test fun interruptedReturnResumesUnderTheFingerAndCancellationReturnsSmoothly() {
        render()
        var calls = 0
        compose.runOnIdle {
            assertTrue(motion.begin())
            motion.drag(140f)
            motion.release(0f, 1f, true) { calls++ }
        }
        compose.mainClock.advanceTimeBy(64)
        compose.runOnIdle {
            assertTrue(motion.offset > 0f && motion.offset < 140f)
            val returningOffset = motion.offset
            assertTrue(motion.begin())
            motion.drag(40f)
            assertEquals(returningOffset + 40f, motion.offset, 0.01f)
            motion.cancel(true)
        }
        compose.mainClock.advanceTimeBy(2500)
        compose.runOnIdle { assertEquals(0, calls); assertEquals(0f, motion.offset, 0.01f) }
    }

    @Test fun reducedMotionCompletesOrReturnsWithoutWaitingForAnimationFrames() {
        render()
        var calls = 0
        compose.runOnIdle {
            motion.begin()
            motion.drag(140f)
            motion.release(0f, 1f, false) { calls++ }
        }
        compose.runOnIdle { assertEquals(0f, motion.offset, 0.01f); assertEquals(0, calls) }
        compose.runOnIdle {
            motion.begin()
            motion.drag(260f)
            motion.release(0f, 1f, false) { calls++ }
        }
        compose.runOnIdle { assertEquals(1, calls); assertEquals(0f, motion.offset, 0.01f) }
    }
}
