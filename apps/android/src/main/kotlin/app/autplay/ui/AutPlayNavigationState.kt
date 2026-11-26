package app.autplay.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue

/**
 * Small saveable navigation state for the delivered single-activity surface.
 *
 * Primary destinations replace one another. Secondary destinations form a deterministic local
 * back stack, without introducing a second navigation framework or recreating playback state.
 */
@Stable
public class AutPlayNavigationState internal constructor(
    current: UiDestination,
    backStack: List<UiDestination>,
) {
    public var current: UiDestination by mutableStateOf(current)
        private set

    private val stack: MutableList<UiDestination> = backStack.toMutableList()

    internal val previousDestination: UiDestination?
        get() = stack.lastOrNull()

    public val canNavigateBack: Boolean
        get() = stack.isNotEmpty()

    public fun navigate(destination: UiDestination) {
        if (destination in UiDestination.compactNavigation) {
            stack.clear()
        } else {
            if (destination == current) return
            stack.remove(destination)
            stack.add(current)
        }
        current = destination
    }

    internal fun openLibraryDetail() {
        if (current == UiDestination.Library) return
        stack.add(current)
        current = UiDestination.Library
    }

    public fun navigateBack(): Boolean {
        val previous = stack.removeLastOrNull() ?: return false
        current = previous
        return true
    }

    internal fun savedRoutes(): List<String> = stack.map(UiDestination::route) + current.route

    public companion object {
        public val Saver: Saver<AutPlayNavigationState, List<String>> = Saver(
            save = { state -> state.savedRoutes() },
            restore = { routes ->
                val destinations = routes.mapNotNull(UiDestination::fromRoute)
                AutPlayNavigationState(
                    current = destinations.lastOrNull() ?: UiDestination.Home,
                    backStack = destinations.dropLast(1),
                )
            },
        )
    }
}

@Composable
public fun rememberAutPlayNavigationState(
    initialDestination: UiDestination = UiDestination.Home,
): AutPlayNavigationState = rememberSaveable(
    saver = AutPlayNavigationState.Saver,
) {
    val backStack = if (initialDestination == UiDestination.SettingsAccount) listOf(UiDestination.Home) else emptyList()
    AutPlayNavigationState(initialDestination, backStack)
}
