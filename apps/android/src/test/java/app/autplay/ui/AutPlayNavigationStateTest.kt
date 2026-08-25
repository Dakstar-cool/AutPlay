package app.autplay.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AutPlayNavigationStateTest {
    @Test
    fun secondaryRoutesUseDeterministicBackStack() {
        val state = AutPlayNavigationState(UiDestination.Home, emptyList())

        state.navigate(UiDestination.Settings)
        state.navigate(UiDestination.PrivacyAndData)

        assertTrue(state.canNavigateBack)
        assertTrue(state.navigateBack())
        assertEquals(UiDestination.Settings, state.current)
        assertTrue(state.navigateBack())
        assertEquals(UiDestination.Home, state.current)
        assertFalse(state.navigateBack())
    }

    @Test
    fun primarySelectionClearsSecondaryHistory() {
        val state = AutPlayNavigationState(UiDestination.Home, emptyList())
        state.navigate(UiDestination.WaveRooms)
        state.navigate(UiDestination.Library)

        assertEquals(UiDestination.Library, state.current)
        assertFalse(state.canNavigateBack)
    }

    @Test
    fun saverStoresStableRoutesWithoutPlayerState() {
        val state = AutPlayNavigationState(UiDestination.Search, listOf(UiDestination.Home))
        assertEquals(listOf("home", "search"), state.savedRoutes())
    }

    @Test
    fun initialDestinationCanOpenTheServerConnectionProfile() {
        val state = AutPlayNavigationState(UiDestination.Profile, emptyList())

        assertEquals(UiDestination.Profile, state.current)
        assertFalse(state.canNavigateBack)
    }
}
