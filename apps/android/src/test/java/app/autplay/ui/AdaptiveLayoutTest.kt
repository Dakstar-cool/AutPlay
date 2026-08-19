package app.autplay.ui

import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Test

class AdaptiveLayoutTest {
    @Test
    fun widthBreakpointsCoverPhonesFoldablesAndTablets() {
        assertEquals(UiWidthClass.Compact, widthClassFor(320.dp))
        assertEquals(UiWidthClass.Compact, widthClassFor(599.dp))
        assertEquals(UiWidthClass.Medium, widthClassFor(600.dp))
        assertEquals(UiWidthClass.Medium, widthClassFor(839.dp))
        assertEquals(UiWidthClass.Expanded, widthClassFor(840.dp))
    }

    @Test
    fun everyDeliveredFeatureDestinationHasAStableRoute() {
        assertEquals(
            UiDestination.railNavigation.size,
            UiDestination.railNavigation.map(UiDestination::route).toSet().size,
        )
        assertEquals(
            listOf("home", "search", "library"),
            UiDestination.compactNavigation.map(UiDestination::route),
        )
        assertEquals(UiDestination.all.size, UiDestination.all.map(UiDestination::route).toSet().size)
    }
}
