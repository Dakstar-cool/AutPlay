package app.autplay.ui.player

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayerCollapsePolicyTest {
    @Test fun deliberateShortDragCommitsWithoutRequiringHalfTheScreen() {
        assertFalse(shouldCompletePlayerCollapse(95f, 600f, 0f, 1f))
        assertTrue(shouldCompletePlayerCollapse(96f, 600f, 0f, 1f))
        assertTrue(shouldCompletePlayerCollapse(96f, 1200f, 0f, 1f))
        assertFalse(shouldCompletePlayerCollapse(55f, 90f, 0f, 1f))
        assertTrue(shouldCompletePlayerCollapse(56f, 90f, 0f, 1f))
    }

    @Test fun deliberateFlingRespectsDensityAndMinimumTravel() {
        assertFalse(shouldCompletePlayerCollapse(24f, 600f, 3000f, 1f))
        assertTrue(shouldCompletePlayerCollapse(40f, 600f, 800f, 1f))
        assertFalse(shouldCompletePlayerCollapse(79f, 1200f, 1600f, 2f))
        assertFalse(shouldCompletePlayerCollapse(80f, 1200f, 1599f, 2f))
        assertTrue(shouldCompletePlayerCollapse(80f, 1200f, 1600f, 2f))
    }

    @Test fun upwardReleaseAndMissingAnchorKeepPlayerOpen() {
        assertFalse(shouldCompletePlayerCollapse(300f, 600f, -1100f, 1f))
        assertFalse(shouldCompletePlayerCollapse(0f, 0f, 3000f, 1f))
    }
}
