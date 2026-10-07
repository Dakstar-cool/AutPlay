package app.autplay.ui.player

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayerCollapsePolicyTest {
    @Test fun releaseUsesScreenTravelRatherThanTheOldShortThreshold() {
        assertFalse(shouldCompletePlayerCollapse(72f, 600f, 0f, 1f))
        assertFalse(shouldCompletePlayerCollapse(239f, 600f, 0f, 1f))
        assertTrue(shouldCompletePlayerCollapse(240f, 600f, 0f, 1f))
        assertTrue(shouldCompletePlayerCollapse(120f, 300f, 0f, 1f))
    }

    @Test fun deliberateFlingRespectsDensityAndMinimumTravel() {
        assertFalse(shouldCompletePlayerCollapse(24f, 600f, 3000f, 1f))
        assertTrue(shouldCompletePlayerCollapse(72f, 600f, 1000f, 1f))
        assertFalse(shouldCompletePlayerCollapse(143f, 1200f, 2000f, 2f))
        assertFalse(shouldCompletePlayerCollapse(144f, 1200f, 1999f, 2f))
        assertTrue(shouldCompletePlayerCollapse(144f, 1200f, 2000f, 2f))
    }

    @Test fun upwardReleaseAndMissingAnchorKeepPlayerOpen() {
        assertFalse(shouldCompletePlayerCollapse(300f, 600f, -1100f, 1f))
        assertFalse(shouldCompletePlayerCollapse(0f, 0f, 3000f, 1f))
    }
}
