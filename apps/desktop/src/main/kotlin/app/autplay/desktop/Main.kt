package app.autplay.desktop

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.data.desktop.openDesktopLibraryStore
import app.autplay.desktop.audio.MacAudioEngine
import app.autplay.desktop.library.DesktopFileImporter
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.platform.PlaybackPort
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.desktop.AutPlayDesktopShell
import app.autplay.ui.desktop.AutPlayDesktopTheme
import app.autplay.ui.desktop.DesktopLanguage
import app.autplay.ui.desktop.DesktopLibraryCallbacks
import app.autplay.ui.desktop.DesktopPlaybackCallbacks
import app.autplay.ui.desktop.DesktopSection
import app.autplay.ui.desktop.DesktopThemeMode
import java.awt.Component
import java.awt.Dimension
import java.awt.EventQueue
import java.nio.file.Path
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import javax.swing.JFileChooser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlin.coroutines.resume

/** Database initialization and native startup run off the AWT event loop. */
public fun main(args: Array<String>): Unit {
    val smoke = "--smoke-test" in args
    val dataRoot = desktopDataRoot(args, smoke)
    val runtime = runBlocking(Dispatchers.IO) { runCatching { DesktopRuntime.open(dataRoot) } }
    val host = runtime.getOrNull()
    val shutdown = Thread({
        if (host != null) runBlocking(Dispatchers.IO) {
            runCatching { withTimeout(10_000) { host.close() } }.onFailure { println("AUTPLAY_DESKTOP_CLOSE_FAILED") }
        }
    }, "AutPlay-shutdown")
    Runtime.getRuntime().addShutdownHook(shutdown)
    try {
        application {
            var closing by remember { mutableStateOf(false) }
            Window(
                onCloseRequest = { closing = true }, title = "AutPlay",
                state = rememberWindowState(width = 1080.dp, height = 740.dp),
            ) {
                SideEffect { window.minimumSize = Dimension(860, 600) }
                if (host != null) DesktopApp(host = host, chooserOwner = window)
                else AutPlayDesktopTheme(DesktopThemeMode.Dark) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Text("Не удалось открыть локальную библиотеку. Данные сохранены. / Could not open the local library. Data preserved.", Modifier.padding(32.dp))
                    }
                }
                LaunchedEffect(closing) {
                    if (closing) {
                        withContext(Dispatchers.IO) {
                            runCatching { withTimeout(10_000) { host?.close() } }.onFailure { println("AUTPLAY_DESKTOP_CLOSE_FAILED") }
                        }
                        exitApplication()
                    }
                }
                if (smoke) LaunchedEffect(Unit) {
                    check(host != null) { "DESKTOP_INITIALIZATION_FAILED" }
                    withFrameNanos { }
                    withFrameNanos { }
                    check(window.isDisplayable && window.isShowing)
                    val bundledRuntime = System.getProperty("java.home").replace('\\', '/').contains("/AutPlay.app/Contents/")
                    check(bundledRuntime) { "BUNDLED_RUNTIME_REQUIRED" }
                    val closeSucceeded = withContext(Dispatchers.IO) {
                        runCatching { withTimeout(10_000) { host.close() } }.isSuccess
                    }
                    check(closeSucceeded) { "DESKTOP_SMOKE_CLOSE_FAILED" }
                    println("AUTPLAY_DESKTOP_SMOKE_READY java=${System.getProperty("java.version")} bundled=true")
                    closing = true
                }
            }
        }
    } finally {
        if (host != null) runBlocking(Dispatchers.IO) {
            runCatching { withTimeout(10_000) { host.close() } }.onFailure { println("AUTPLAY_DESKTOP_CLOSE_FAILED") }
        }
        runCatching { Runtime.getRuntime().removeShutdownHook(shutdown) }
    }
}

private fun desktopDataRoot(args: Array<String>, smoke: Boolean): Path {
    val home = Path.of(System.getProperty("user.home"))
    val platform = System.getProperty("os.name").lowercase(Locale.ROOT)
    val normal = when {
        platform.contains("mac") -> home.resolve("Library/Application Support/AutPlay")
        platform.contains("win") -> Path.of(System.getenv("LOCALAPPDATA") ?: home.resolve("AppData/Local").toString()).resolve("AutPlay")
        else -> Path.of(System.getenv("XDG_DATA_HOME")?.takeIf { it.isNotBlank() } ?: home.resolve(".local/share").toString()).resolve("AutPlay")
    }.toAbsolutePath().normalize()
    if (!smoke) return normal
    // Packaged startup checks must explicitly target disposable storage.
    val explicit = args.firstOrNull { it.startsWith("--test-data-root=") }?.substringAfter('=')
        ?: args.indexOf("--test-data-root").takeIf { it >= 0 }?.let { args.getOrNull(it + 1)?.takeUnless { value -> value.startsWith("--") } }
        ?: System.getProperty("autplay.testDataRoot")
    require(!explicit.isNullOrBlank()) { "SMOKE_DISPOSABLE_DATA_ROOT_REQUIRED" }
    val testRoot = Path.of(explicit).toAbsolutePath().normalize()
    require(testRoot != normal && testRoot != home.toAbsolutePath().normalize()) { "SMOKE_DISPOSABLE_DATA_ROOT_REQUIRED" }
    return testRoot
}

private fun nativeHelper(): Path {
    System.getProperty("autplay.nativeHelper")?.takeIf { it.isNotBlank() }?.let { return Path.of(it) }
    val resources = System.getProperty("compose.application.resources.dir")
    return if (resources != null) Path.of(resources).resolve("native/AutPlayAudioHelper")
        else Path.of("native/AutPlayAudioHelper").toAbsolutePath()
}

/** Host owns paths, chooser and lifetime. Compose receives only safe projections. */
public class DesktopRuntime private constructor(
    public val store: DesktopLibraryStore,
    private val importer: DesktopImportPort,
    public val controller: DesktopPlaybackController,
    private val scope: CoroutineScope,
) {
    private val closeMutex: Mutex = Mutex()
    private var closed: Boolean = false
    private val importingFlag: AtomicBoolean = AtomicBoolean(false)
    private val mutableImporting: MutableStateFlow<Boolean> = MutableStateFlow(false)
    public val importing: StateFlow<Boolean> = mutableImporting
    private val mutableImportResult: MutableStateFlow<DesktopImportResult?> = MutableStateFlow(null)
    public val importResult: StateFlow<DesktopImportResult?> = mutableImportResult
    private val mutableError: MutableStateFlow<String?> = MutableStateFlow(null)
    public val error: StateFlow<String?> = mutableError

    public fun importSelected(owner: Component?): Unit {
        if (!importingFlag.compareAndSet(false, true)) return
        mutableImporting.value = true
        scope.launch {
            try {
                val selected = chooseSources(owner)
                if (selected.limitExceeded) {
                    mutableError.value = null
                    mutableImportResult.value = DesktopImportResult(0, 0, 1, "import_selection_limit")
                } else if (selected.paths.isNotEmpty()) {
                    mutableError.value = null
                    mutableImportResult.value = importer.importSelected(selected.paths)
                }
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                mutableError.value = "IMPORT_FAILED"
            } finally {
                importingFlag.set(false)
                mutableImporting.value = false
            }
        }
    }

    public fun createPlaylist(name: String): Unit = mutate { store.createPlaylist(name) }
    public fun addToPlaylist(playlistId: String, trackId: String): Unit = mutate { store.appendToPlaylist(playlistId, trackId) }
    public fun removePlaylistEntry(playlistId: String, entryId: String): Unit = mutate { store.removePlaylistEntry(playlistId, entryId) }

    private fun mutate(action: suspend () -> Unit): Unit {
        scope.launch {
            try {
                action()
                mutableError.value = null
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                mutableError.value = "LIBRARY_OPERATION_FAILED"
            }
        }
    }

    public suspend fun close(): Unit = closeMutex.withLock {
        if (closed) return@withLock
        closed = true
        try {
            controller.close()
        } finally {
            withContext(NonCancellable) {
                val owner = scope.coroutineContext[kotlinx.coroutines.Job]
                owner?.cancel()
                try {
                    withTimeout(5_000) { owner?.join() }
                } finally {
                    withTimeout(5_000) { store.close() }
                }
            }
        }
    }

    public companion object {
        public suspend fun open(dataRoot: Path): DesktopRuntime {
            val store = openDesktopLibraryStore(dataRoot)
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            var engine: DesktopAudioEngine? = null
            try {
                val audio = MacAudioEngine(nativeHelper())
                engine = audio
                return DesktopRuntime(store, DesktopFileImporter(store, dataRoot), DesktopPlaybackController(store, audio, scope), scope)
            } catch (failure: Exception) {
                scope.cancel()
                withContext(NonCancellable) {
                    try { withTimeout(5_000) { engine?.close() } }
                    finally { withTimeout(5_000) { store.close() } }
                }
                throw failure
            }
        }
    }
}

private data class DesktopSourceSelection(val paths: List<String> = emptyList(), val limitExceeded: Boolean = false)

private suspend fun chooseSources(owner: Component?): DesktopSourceSelection = suspendCancellableCoroutine { continuation ->
    EventQueue.invokeLater {
        if (!continuation.isActive) return@invokeLater
        try {
            val chooser = JFileChooser().apply {
                isMultiSelectionEnabled = true
                fileSelectionMode = JFileChooser.FILES_AND_DIRECTORIES
                dialogTitle = "AutPlay — выберите файлы или папки / Choose files or folders"
            }
            continuation.invokeOnCancellation { EventQueue.invokeLater { chooser.cancelSelection() } }
            val selection = if (chooser.showOpenDialog(owner) == JFileChooser.APPROVE_OPTION) {
                val selectedFiles = chooser.selectedFiles
                if (selectedFiles.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) {
                    DesktopSourceSelection(limitExceeded = true)
                } else DesktopSourceSelection(selectedFiles.map { it.toPath().toAbsolutePath().toString() })
            } else DesktopSourceSelection()
            if (continuation.isActive) continuation.resume(selection)
        } catch (failure: Exception) {
            if (continuation.isActive) continuation.resumeWith(Result.failure(failure))
        }
    }
}

/** Theme and language are process-local preferences. Queue selection remains application-owned. */
@Composable
public fun DesktopApp(playbackPort: PlaybackPort? = null, host: DesktopRuntime? = null, chooserOwner: Component? = null) {
    var section by remember { mutableStateOf(DesktopSection.Library) }
    var themeMode by remember { mutableStateOf(DesktopThemeMode.Dark) }
    var language by remember { mutableStateOf(if (Locale.getDefault().language == "ru") DesktopLanguage.Russian else DesktopLanguage.English) }
    val port = host?.controller ?: playbackPort
    val disconnectedState: StateFlow<PlaybackPresentationState> = remember { MutableStateFlow(PlaybackPresentationState()) }
    val playback by (port?.state ?: disconnectedState).collectAsState()
    val library = host?.store?.state?.collectAsState()?.value
    val importing = host?.importing?.collectAsState()?.value ?: false
    val importResult = host?.importResult?.collectAsState()?.value
    val hostError = host?.error?.collectAsState()?.value
    val playbackError = host?.controller?.error?.collectAsState()?.value
    AutPlayDesktopShell(
        section, language, themeMode, playback, port != null,
        onSectionChange = { section = it }, onLanguageChange = { language = it }, onThemeChange = { themeMode = it },
        playbackCallbacks = DesktopPlaybackCallbacks({ port?.play() }, { port?.pause() }, { port?.previous() }, { port?.next() }, { port?.seekTo(it) }),
        modifier = Modifier.onPreviewKeyEvent { event ->
            if (event.type == KeyEventType.KeyDown && event.isCtrlPressed && port != null) {
                when (event.key) {
                    Key.Spacebar -> { if (playback.isPlaying) port.pause() else port.play(); true }
                    Key.DirectionRight -> { port.next(); true }
                    Key.DirectionLeft -> { port.previous(); true }
                    else -> false
                }
            } else false
        },
        library = library, importing = importing, importResult = importResult, errorCode = hostError ?: playbackError,
        libraryCallbacks = DesktopLibraryCallbacks(
            onImport = { host?.importSelected(chooserOwner) }, onPlayTracks = { ids, type -> host?.controller?.playTracks(ids, type) },
            onEnqueue = { host?.controller?.enqueue(it) }, onSelectEntry = { host?.controller?.selectEntry(it) },
            onRemoveEntry = { host?.controller?.removeEntry(it) }, onCreatePlaylist = { host?.createPlaylist(it) },
            onAddToPlaylist = { playlist, track -> host?.addToPlaylist(playlist, track) },
            onRemovePlaylistEntry = { playlist, entry -> host?.removePlaylistEntry(playlist, entry) },
            onShuffle = { host?.controller?.setShuffle(it) }, onRepeat = { host?.controller?.setRepeat(it) },
        ),
    )
}
