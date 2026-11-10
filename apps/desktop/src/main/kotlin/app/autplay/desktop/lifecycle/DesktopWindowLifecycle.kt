package app.autplay.desktop.lifecycle

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Bounded, conflated UI intent; no native event queue or competing playback owner. */
public data class DesktopWindowLifecycleState(
    public val visible: Boolean = true,
    public val canHideWindow: Boolean = false,
    public val closing: Boolean = false,
    public val disposed: Boolean = false,
    public val settingsRequest: Long = 0,
    public val focusRequest: Long = 0,
)

public class DesktopWindowLifecycle(
    private val requestShutdown: () -> Unit,
    private val allowHide: Boolean = true,
) {
    private val lock: Any = Any()
    private val mutableState: MutableStateFlow<DesktopWindowLifecycleState> = MutableStateFlow(DesktopWindowLifecycleState())
    public val state: StateFlow<DesktopWindowLifecycleState> = mutableState

    /** Advertise hiding only after both native routes have actually installed. */
    public fun installed(reopen: Boolean, quit: Boolean): Unit = synchronized(lock) {
        val current = mutableState.value
        if (current.disposed || current.closing) return@synchronized
        val canHide = allowHide && reopen && quit
        mutableState.value = if (!canHide && !current.visible) {
            current.copy(canHideWindow = false, visible = true, focusRequest = next(current.focusRequest))
        } else current.copy(canHideWindow = canHide)
    }

    public fun closeWindow(): Unit = synchronized(lock) {
        val current = mutableState.value
        if (current.disposed || current.closing) return@synchronized
        if (current.canHideWindow) mutableState.value = current.copy(visible = false)
        else quitLocked(current)
    }

    public fun showWindow(): Unit = synchronized(lock) {
        val current = mutableState.value
        if (current.disposed || current.closing) return@synchronized
        mutableState.value = current.copy(visible = true, focusRequest = next(current.focusRequest))
    }

    public fun showSettings(): Unit = synchronized(lock) {
        val current = mutableState.value
        if (current.disposed || current.closing) return@synchronized
        mutableState.value = current.copy(visible = true, settingsRequest = next(current.settingsRequest),
            focusRequest = next(current.focusRequest))
    }

    public fun quit(): Unit = synchronized(lock) {
        val current = mutableState.value
        if (!current.disposed && !current.closing) quitLocked(current)
    }

    /** Disposing a window cannot leave live callbacks targeting a retired application. */
    public fun dispose(): Unit = synchronized(lock) {
        val current = mutableState.value
        if (current.disposed) return@synchronized
        if (!current.closing) quitLocked(current)
        mutableState.value = mutableState.value.copy(disposed = true, canHideWindow = false)
    }

    private fun quitLocked(current: DesktopWindowLifecycleState): Unit {
        requestShutdown()
        mutableState.value = current.copy(closing = true)
    }

    private fun next(value: Long): Long = if (value == Long.MAX_VALUE) 1 else value + 1
}
