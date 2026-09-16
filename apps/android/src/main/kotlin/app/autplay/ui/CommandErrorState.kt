package app.autplay.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlin.reflect.KProperty

@Composable
internal fun rememberCommandErrorState(profileId: String?, route: UiDestination, detail: Any?): CommandErrorState {
    val state = remember(profileId) { CommandErrorState() }
    val renderedEvent = state.event
    SideEffect { state.onRendered(route, detail, renderedEvent) }
    return state
}

/** Each result has its own identity, including repeated failures with the same code. */
internal class CommandErrorState {
    class Event(val code: String)
    var event: Event? by mutableStateOf(null)
        private set
    private data class Rendered(val route: UiDestination, val detail: Any?, val event: Event?)
    private var rendered: Rendered? = null

    fun onRendered(route: UiDestination, detail: Any?, visible: Event?) {
        val previous = rendered
        if (previous != null && (previous.route != route || previous.detail != detail)) {
            clearIfCurrent(previous.event)
        }
        rendered = Rendered(route, detail, visible.takeIf { it === event })
    }

    operator fun getValue(owner: Any?, property: KProperty<*>): String? = event?.code
    operator fun setValue(owner: Any?, property: KProperty<*>, code: String?) {
        event = code?.let(::Event)
    }

    fun clearIfCurrent(previous: Event?) {
        if (event === previous) event = null
    }
}
