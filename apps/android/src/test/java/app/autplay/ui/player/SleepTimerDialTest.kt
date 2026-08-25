package app.autplay.ui.player

import androidx.compose.ui.geometry.Offset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SleepTimerDialTest {
    @Test
    fun mapsClockCardinalsToMinuteRange() {
        assertEquals(60, dialMinutesFromPosition(Offset(100f, 0f), 200f, 200f))
        assertEquals(15, dialMinutesFromPosition(Offset(200f, 100f), 200f, 200f))
        assertEquals(30, dialMinutesFromPosition(Offset(100f, 200f), 200f, 200f))
        assertEquals(45, dialMinutesFromPosition(Offset(0f, 100f), 200f, 200f))
    }

    @Test
    fun ignoresTouchesNearCenter() {
        assertNull(dialMinutesFromPosition(Offset(100f, 100f), 200f, 200f))
    }
}
