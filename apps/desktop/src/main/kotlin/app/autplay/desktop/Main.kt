package app.autplay.desktop

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.platform.PlaybackPort
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.desktop.AutPlayDesktopShell
import app.autplay.ui.desktop.DesktopLanguage
import app.autplay.ui.desktop.DesktopPlaybackCallbacks
import app.autplay.ui.desktop.DesktopSection
import app.autplay.ui.desktop.DesktopThemeMode
import java.awt.Dimension
import java.util.Locale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** No media adapter is installed by the foundation entry point. */
public fun main(args: Array<String>): Unit = application {
    Window(
        onCloseRequest = ::exitApplication,
        title = "AutPlay",
        state = rememberWindowState(width = 1080.dp, height = 740.dp),
    ) {
        SideEffect { window.minimumSize = Dimension(860, 600) }
        DesktopApp()
        if (args.contentEquals(arrayOf("--smoke-test"))) {
            LaunchedEffect(Unit) {
                // Exercise the packaged JVM and native renderer before acknowledging startup.
                withFrameNanos { }
                withFrameNanos { }
                check(window.isDisplayable && window.isShowing)
                val bundledRuntime = System.getProperty("java.home")
                    .replace('\\', '/')
                    .contains("/AutPlay.app/Contents/")
                check(bundledRuntime)
                println("AUTPLAY_DESKTOP_SMOKE_READY java=${System.getProperty("java.version")} bundled=true")
                exitApplication()
            }
        }
    }
}

/** The platform host can inject a real adapter without giving Compose queue ownership. */
@Composable
public fun DesktopApp(playbackPort: PlaybackPort? = null) {
    var section by remember { mutableStateOf(DesktopSection.Library) }
    var themeMode by remember { mutableStateOf(DesktopThemeMode.Dark) }
    var language by remember {
        mutableStateOf(if (Locale.getDefault().language == "ru") DesktopLanguage.Russian else DesktopLanguage.English)
    }
    val disconnectedState: StateFlow<PlaybackPresentationState> = remember {
        MutableStateFlow(
            PlaybackPresentationState(
                context = ActiveQueueContext.Absent,
                controls = PlaybackControlGate.Locked(PlaybackControlLockReason.NO_ACTIVE_QUEUE),
            ),
        )
    }
    val playback by (playbackPort?.state ?: disconnectedState).collectAsState()
    AutPlayDesktopShell(
        section = section,
        language = language,
        themeMode = themeMode,
        playback = playback,
        playbackAdapterAvailable = playbackPort != null,
        onSectionChange = { section = it },
        onLanguageChange = { language = it },
        onThemeChange = { themeMode = it },
        playbackCallbacks = DesktopPlaybackCallbacks(
            onPlay = { playbackPort?.play() },
            onPause = { playbackPort?.pause() },
            onPrevious = { playbackPort?.previous() },
            onNext = { playbackPort?.next() },
            onSeek = { playbackPort?.seekTo(it) },
        ),
    )
}
