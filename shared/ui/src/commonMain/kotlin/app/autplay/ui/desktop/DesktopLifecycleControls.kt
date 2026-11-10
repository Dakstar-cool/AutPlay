package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** The host advertises window hiding only while it can keep the same app lifetime alive. */
public data class DesktopLifecycleState(
    public val canHideWindow: Boolean = false,
    public val closing: Boolean = false,
    public val available: Boolean = false,
)

/** Admission, coalescing, storage shutdown and native UI belong to the host. */
public data class DesktopLifecycleCallbacks(
    public val onQuit: () -> Unit = {},
)

/** Fixed product copy is shared with native menus without exposing platform details to common UI. */
public enum class DesktopLifecycleText {
    Settings, ShowWindow, CloseWindow, Quit, Closing, WindowCloseHint;

    public fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.English -> when (this) {
            Settings -> "Settings"
            ShowWindow -> "Show window"
            CloseWindow -> "Close window"
            Quit -> "Quit AutPlay"
            Closing -> "Closing AutPlay…"
            WindowCloseHint -> "Closing the window hides it; playback continues. Quit AutPlay ends the app and playback."
        }
        DesktopLanguage.Russian -> when (this) {
            Settings -> "Настройки"
            ShowWindow -> "Показать окно"
            CloseWindow -> "Закрыть окно"
            Quit -> "Выйти из AutPlay"
            Closing -> "Завершение работы AutPlay…"
            WindowCloseHint -> "Закрытие окна скрывает его; воспроизведение продолжается. «Выйти из AutPlay» завершает приложение и воспроизведение."
        }
    }
}

internal fun desktopLifecycleCanQuit(state: DesktopLifecycleState): Boolean = state.available && !state.closing

internal fun desktopLifecycleShowWindowHint(state: DesktopLifecycleState): Boolean = state.available && state.canHideWindow

internal fun desktopLifecycleShowClosing(state: DesktopLifecycleState): Boolean = state.available && state.closing

/** A structurally equal replacement still owns a different callback lifetime. */
internal class DesktopLifecycleOwnerKey(private val owner: DesktopLifecycleCallbacks) {
    override fun equals(other: Any?): Boolean = other is DesktopLifecycleOwnerKey && owner === other.owner
    override fun hashCode(): Int = 0
}

/** Scalar changes replace this target, so A -> B -> A cannot revive an earlier handler. */
internal class DesktopLifecycleTarget(
    private val state: DesktopLifecycleState,
    private val owner: DesktopLifecycleCallbacks,
) {
    private var active: Boolean = true

    internal fun retire(): Unit { active = false }

    internal fun canQuit(
        currentState: DesktopLifecycleState,
        currentOwner: DesktopLifecycleCallbacks,
        currentTarget: DesktopLifecycleTarget,
        navigationCurrent: Boolean,
    ): Boolean = active && currentTarget === this && currentOwner === owner && state == currentState &&
        navigationCurrent && desktopLifecycleCanQuit(currentState)

    /** Plain factory avoids Compose memoizing a closure around an equal retired owner. */
    internal fun quitHandler(
        latestState: () -> DesktopLifecycleState,
        latestOwner: () -> DesktopLifecycleCallbacks,
        latestTarget: () -> DesktopLifecycleTarget,
        isNavigationCurrent: () -> Boolean,
    ): () -> Unit = {
        if (canQuit(latestState(), latestOwner(), latestTarget(), isNavigationCurrent())) {
            // Do not claim completion or latch success here; the host coalesces admitted Quit requests.
            owner.onQuit()
        }
    }
}

@Composable
@NonSkippableComposable // Equal callback data must still publish the replacement owner's identity.
internal fun DesktopLifecycleControls(
    state: DesktopLifecycleState,
    callbacks: DesktopLifecycleCallbacks,
    language: DesktopLanguage,
    navigationRevision: Long,
    isNavigationCurrent: () -> Boolean,
    modifier: Modifier = Modifier,
): Unit {
    val currentState by rememberDesktopBrowseReference(state)
    val currentOwner by rememberDesktopBrowseReference(callbacks)
    val currentNavigation by rememberUpdatedState(isNavigationCurrent)
    val target = remember(state, DesktopLifecycleOwnerKey(callbacks), navigationRevision) {
        DesktopLifecycleTarget(state, callbacks)
    }
    val currentTarget by rememberDesktopBrowseReference(target)
    DisposableEffect(target) { onDispose { target.retire() } }
    val quit = target.quitHandler({ currentState }, { currentOwner }, { currentTarget }, { currentNavigation() })
    if (!state.available) return
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = quit, enabled = desktopLifecycleCanQuit(state)) {
            Text(DesktopLifecycleText.Quit.label(language))
        }
        if (desktopLifecycleShowWindowHint(state)) {
            Text(DesktopLifecycleText.WindowCloseHint.label(language), style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
internal fun DesktopLifecycleClosingStatus(state: DesktopLifecycleState, language: DesktopLanguage): Unit {
    if (desktopLifecycleShowClosing(state)) {
        Text(DesktopLifecycleText.Closing.label(language),
            modifier = Modifier.fillMaxWidth().padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite },
            style = MaterialTheme.typography.bodyMedium)
    }
}
