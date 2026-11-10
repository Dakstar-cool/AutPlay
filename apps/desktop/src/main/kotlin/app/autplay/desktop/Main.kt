package app.autplay.desktop

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.draganddrop.dragAndDropTarget
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.draganddrop.DragAndDropEvent
import androidx.compose.ui.draganddrop.DragAndDropTarget
import androidx.compose.ui.draganddrop.DragAndDropTransferAction
import androidx.compose.ui.draganddrop.awtTransferable
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.MenuBar
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopHistoryState
import app.autplay.application.desktop.DesktopLocalStatisticsState
import app.autplay.application.desktop.DesktopImportCandidateMapping
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportMappingReview
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportReviewPort
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopSavedImportsState
import app.autplay.application.desktop.desktopImportCandidateEligible
import app.autplay.application.desktop.desktopImportMappingValid
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopSleepTimerState
import app.autplay.application.desktop.DesktopTasteState
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.sourceaccess.SourceAccessState
import app.autplay.data.desktop.DesktopFtsSearchOwner
import app.autplay.data.desktop.openDesktopLibraryStore
import app.autplay.data.desktop.openDesktopPreferencesStore
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.desktop.audio.DesktopAudioOwnerArbiter
import app.autplay.desktop.history.DesktopHistoryController
import app.autplay.desktop.history.DesktopLocalStatisticsController
import app.autplay.desktop.library.DesktopCopyInspectionController
import app.autplay.desktop.library.DesktopFileImporter
import app.autplay.desktop.library.DesktopImportJobsController
import app.autplay.desktop.lifecycle.DesktopNativeLifecycle
import app.autplay.desktop.lifecycle.DesktopShutdownOwner
import app.autplay.desktop.lifecycle.DesktopWindowLifecycle
import app.autplay.desktop.lifecycle.desktopNativeLifecycleApi
import app.autplay.desktop.metadata.DesktopMetadataController
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import app.autplay.desktop.presentation.DesktopAccountWorkspaceBinding
import app.autplay.desktop.presentation.DesktopAccountAvailability
import app.autplay.desktop.presentation.DesktopAccountProductionRuntime
import app.autplay.desktop.presentation.DesktopContextSettlement
import app.autplay.desktop.presentation.hostAwait
import app.autplay.desktop.presentation.DesktopAccountHomePreview
import app.autplay.desktop.sourceaccess.DesktopSourceAccessController
import app.autplay.desktop.sourceaccess.createDesktopSourceAccessOwner
import app.autplay.desktop.settings.DesktopSettingsController
import app.autplay.desktop.systemmedia.DesktopSystemMediaApi
import app.autplay.desktop.systemmedia.DesktopSystemMediaProjection
import app.autplay.desktop.systemmedia.macSystemMediaApi
import app.autplay.platform.PlaybackPort
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.desktop.AutPlayDesktopShell
import app.autplay.ui.desktop.AutPlayDesktopTheme
import app.autplay.ui.desktop.DesktopLanguage
import app.autplay.ui.desktop.DesktopAccountWorkspace
import app.autplay.ui.desktop.DesktopAccentPalette
import app.autplay.ui.desktop.DesktopCopyInspectionCallbacks
import app.autplay.ui.desktop.DesktopLibraryCallbacks
import app.autplay.ui.desktop.DesktopLibraryKind
import app.autplay.ui.desktop.DesktopLibraryCatalogBrowser
import app.autplay.ui.desktop.DesktopCatalogKind
import app.autplay.ui.desktop.DesktopLifecycleCallbacks
import app.autplay.ui.desktop.DesktopLifecycleState
import app.autplay.ui.desktop.DesktopLifecycleText
import app.autplay.ui.desktop.DesktopHistoryCallbacks
import app.autplay.ui.desktop.DesktopLocalStatisticsCallbacks
import app.autplay.ui.desktop.DesktopImportReviewCallbacks
import app.autplay.ui.desktop.DesktopSavedImportsCallbacks
import app.autplay.ui.desktop.DesktopMetadataCallbacks
import app.autplay.ui.desktop.DesktopPlaybackCallbacks
import app.autplay.ui.desktop.DesktopSection
import app.autplay.ui.desktop.DesktopSettingsCallbacks
import app.autplay.ui.desktop.DesktopSleepTimerCallbacks
import app.autplay.ui.desktop.DesktopTasteCallbacks
import app.autplay.ui.desktop.DesktopThemeMode
import app.autplay.ui.desktop.DesktopTransportCallbacks
import java.awt.Component
import java.awt.Dimension
import java.awt.EventQueue
import java.awt.datatransfer.DataFlavor
import java.io.File
import java.nio.file.Path
import java.util.Locale
import java.util.concurrent.CompletableFuture
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import javax.swing.JFileChooser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
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
    val runtime = runBlocking(Dispatchers.IO) { runCatching { DesktopRuntime.open(dataRoot, disposableSmokeRoot = smoke) } }
    val initialHost = runtime.getOrNull()
    val currentRuntime = AtomicReference(initialHost)
    val runtimeTransition = Mutex()
    val shutdownOwner = DesktopShutdownOwner(
        closeAdmission = { currentRuntime.get()?.beginClose() },
        closeRuntime = { runtimeTransition.withLock { currentRuntime.get()?.close() } },
        reportFailure = { println("AUTPLAY_DESKTOP_CLOSE_FAILED") },
    )
    val lifecycle = DesktopWindowLifecycle(
        requestShutdown = { shutdownOwner.request(); Unit },
        allowHide = !smoke && initialHost != null,
    )
    val shutdown = Thread({
        runBlocking(Dispatchers.IO) { shutdownOwner.request().await() }
    }, "AutPlay-shutdown")
    Runtime.getRuntime().addShutdownHook(shutdown)
    try {
        application {
            var activeHost by remember { mutableStateOf(initialHost) }
            var initializingAccounts by remember { mutableStateOf(false) }
            var accountInitializationFailed by remember { mutableStateOf(false) }
            var shutdownFailed by remember { mutableStateOf(false) }
            val replacementScope = rememberCoroutineScope()
            val host = activeHost
            val lifecycleState by lifecycle.state.collectAsState()
            val closing by shutdownOwner.requested.collectAsState()
            val windowState = rememberWindowState(width = 1080.dp, height = 740.dp)
            val preferences = host?.settings?.preferences?.collectAsState()?.value?.value
            val menuLanguage = when (preferences?.appLanguage) {
                "RU" -> DesktopLanguage.Russian
                "EN" -> DesktopLanguage.English
                else -> if (Locale.getDefault().language == "ru") DesktopLanguage.Russian else DesktopLanguage.English
            }
            val lifecycleCallbacks = remember(lifecycle) { DesktopLifecycleCallbacks(onQuit = lifecycle::quit) }
            LaunchedEffect(shutdownOwner) {
                shutdownOwner.requested.first { it }
                val outcome = shutdownOwner.request().await()
                // Smoke must check this same close result before it can report or exit.
                if (!smoke && outcome.isSuccess) exitApplication()
                else if (outcome.isFailure) shutdownFailed = true
            }
            Window(
                onCloseRequest = lifecycle::closeWindow, title = "AutPlay",
                state = windowState, visible = lifecycleState.visible,
            ) {
                SideEffect {
                    window.minimumSize = Dimension(860, 600)
                    host?.accounts?.setWindowVisible?.invoke(lifecycleState.visible && !closing)
                }
                DisposableEffect(host, window) {
                    // AWT/AppKit already owns the window. Keep this bridge with the runtime on hide.
                    host?.installSystemMedia()
                    host?.accounts?.attachWindow?.invoke(window)
                    onDispose { host?.accounts?.attachWindow?.invoke(null) }
                }
                DisposableEffect(lifecycle, window) {
                    val native = DesktopNativeLifecycle.install(
                        desktopNativeLifecycleApi(), lifecycle,
                        onSystemSleep = { currentRuntime.get()?.pauseForSystemSleep() },
                    )
                    onDispose {
                        lifecycle.dispose()
                        native.retire()
                    }
                }
                LaunchedEffect(lifecycleState.focusRequest) {
                    val request = lifecycleState.focusRequest
                    if (request > 0 && lifecycleState.visible && !closing) {
                        windowState.isMinimized = false
                        withFrameNanos { }
                        val current = lifecycle.state.value
                        if (current.focusRequest == request && current.visible && !current.closing && !current.disposed) {
                            window.toFront()
                            window.requestFocus()
                        }
                    }
                }
                MenuBar {
                    Menu(if (menuLanguage == DesktopLanguage.Russian) "Файл" else "File") {
                        Item(DesktopLifecycleText.Settings.label(menuLanguage), enabled = host != null && !closing,
                            onClick = lifecycle::showSettings)
                        Separator()
                        Item(DesktopLifecycleText.Quit.label(menuLanguage), enabled = !closing, onClick = lifecycle::quit)
                    }
                    Menu(if (menuLanguage == DesktopLanguage.Russian) "Окно" else "Window") {
                        Item(DesktopLifecycleText.ShowWindow.label(menuLanguage), enabled = !closing, onClick = lifecycle::showWindow)
                        Item(DesktopLifecycleText.CloseWindow.label(menuLanguage), enabled = !closing, onClick = lifecycle::closeWindow)
                    }
                }
                fun requestAccountReplacement(initializeVirgin: Boolean): Unit {
                    val expectedHost = host ?: return
                    if (smoke || closing || initializingAccounts || accountInitializationFailed ||
                        currentRuntime.get() !== expectedHost) return
                    val allowed = if (initializeVirgin) setOf(DesktopAccountAvailability.InitializeRequired)
                        else setOf(DesktopAccountAvailability.Retired, DesktopAccountAvailability.RecoveryRequired,
                            DesktopAccountAvailability.KeychainLocked, DesktopAccountAvailability.KeychainAccessRequired)
                    val availability = expectedHost.accounts?.availability?.value ?: return
                    if (availability !in allowed) return
                    initializingAccounts = true
                    accountInitializationFailed = false
                    replacementScope.launch {
                        try {
                            val replacement = withContext(NonCancellable + Dispatchers.IO) {
                                runtimeTransition.withLock {
                                    val current = currentRuntime.get()
                                    val currentAvailability = current?.accounts?.availability?.value
                                    if (current !== expectedHost || currentAvailability == null || currentAvailability !in allowed ||
                                        shutdownOwner.requested.value) null
                                    else {
                                        expectedHost.close()
                                        if (shutdownOwner.requested.value) null else {
                                            if (!initializeVirgin) readDesktopSignedBundle()?.identity?.takeIf { it.community }?.let {
                                                app.autplay.desktop.security.authorizeMacCommunityKeychain(it)
                                            }
                                            val next = DesktopRuntime.open(dataRoot, initializeAccounts = initializeVirgin)
                                            currentRuntime.set(next)
                                            if (shutdownOwner.requested.value) { next.beginClose(); next.close(); null } else next
                                        }
                                    }
                                }
                            }
                            if (replacement != null) activeHost = replacement
                        } catch (_: Exception) {
                            // Failure stays latched: an inaccessible partial graph is not settlement.
                            accountInitializationFailed = true
                        } finally { initializingAccounts = false }
                    }
                }
                if (host != null) DesktopApp(
                    host = host, chooserOwner = window,
                    lifecycleState = DesktopLifecycleState(canHideWindow = lifecycleState.canHideWindow,
                        closing = closing, available = true),
                    lifecycleCallbacks = lifecycleCallbacks,
                    systemSettingsRequest = lifecycleState.settingsRequest,
                    windowVisible = lifecycleState.visible,
                    initializingAccounts = initializingAccounts,
                    accountInitializationFailed = accountInitializationFailed,
                    shutdownFailed = shutdownFailed,
                    onInitializeAccounts = if (smoke) null else ({ requestAccountReplacement(true) }),
                    onReopenAccounts = if (smoke) null else ({ requestAccountReplacement(false) }),
                )
                else AutPlayDesktopTheme(DesktopThemeMode.Dark) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Text("Не удалось открыть локальную библиотеку. Данные сохранены. Закройте и снова запустите AutPlay. / Could not open the local library. Data preserved. Close and restart AutPlay.", Modifier.padding(32.dp))
                    }
                }
                if (smoke) LaunchedEffect(Unit) {
                    check(host != null) { "DESKTOP_INITIALIZATION_FAILED" }
                    withFrameNanos { }
                    withFrameNanos { }
                    check(window.isDisplayable && window.isShowing)
                    val bundledRuntime = System.getProperty("java.home").replace('\\', '/').contains("/AutPlay.app/Contents/")
                    check(bundledRuntime) { "BUNDLED_RUNTIME_REQUIRED" }
                    lifecycle.quit()
                    check(shutdownOwner.request().await().isSuccess) { "DESKTOP_SMOKE_CLOSE_FAILED" }
                    println("AUTPLAY_DESKTOP_SMOKE_READY java=${System.getProperty("java.version")} bundled=true")
                    exitApplication()
                }
            }
        }
    } finally {
        runBlocking(Dispatchers.IO) { shutdownOwner.request().await() }
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
    public val settings: DesktopSettingsController,
    public val textSearch: DesktopTextSearchPort,
    internal val accounts: DesktopAccountWorkspaceBinding? = null,
    private val productionAccounts: DesktopAccountProductionRuntime? = null,
    private val signedBundle: DesktopSignedBundle? = null,
) {
    // Preserve the existing isolated host fixture constructor; production can inject the port.
    private constructor(store: DesktopLibraryStore, importer: DesktopImportPort,
        controller: DesktopPlaybackController, scope: CoroutineScope, settings: DesktopSettingsController) :
        this(store, importer, controller, scope, settings, DesktopFtsSearchOwner())

    private val closeMutex: Mutex = Mutex()
    private val lifecycleLock: Any = Any()
    @Volatile private var closing: Boolean = false
    @Volatile private var closed: Boolean = false
    @Volatile private var retainedCloseFailure: Exception? = null
    private var systemMediaAttempted: Boolean = false
    @Volatile private var systemMedia: DesktopSystemMediaProjection? = null
    internal var sources: DesktopSourceAccessController? = null
        private set
    private var sourceLifecycleJob: Job? = null
    @Volatile private var sourceClosed: Boolean = false
    private var importJob: Job? = null
    private var importRequest: ImportRequest? = null
    private val snapshotUnavailable: AtomicBoolean = AtomicBoolean(false)
    private val mutableImporting: MutableStateFlow<Boolean> = MutableStateFlow(false)
    public val importing: StateFlow<Boolean> = mutableImporting
    private val mutableImportResult: MutableStateFlow<DesktopImportResult?> = MutableStateFlow(null)
    public val importResult: StateFlow<DesktopImportResult?> = mutableImportResult
    private val mutableImportReview: MutableStateFlow<DesktopImportReviewState> = MutableStateFlow(DesktopImportReviewState())
    public val importReview: StateFlow<DesktopImportReviewState> = mutableImportReview
    private val mutableImportMapping: MutableStateFlow<DesktopImportMappingReview?> = MutableStateFlow(null)
    public val importMapping: StateFlow<DesktopImportMappingReview?> = mutableImportMapping
    private val mutableLastImportProgress: MutableStateFlow<DesktopImportProgress?> = MutableStateFlow(null)
    public val lastImportProgress: StateFlow<DesktopImportProgress?> = mutableLastImportProgress
    private val mutableError: MutableStateFlow<String?> = MutableStateFlow(null)
    public val error: StateFlow<String?> = mutableError
    public val playerState: StateFlow<DesktopPlayerState> = controller.playerState
    public val history: DesktopHistoryController = DesktopHistoryController(
        store, scope, onSnapshotUnavailable = { reportError("desktop_snapshot_unavailable") },
        canRead = { !closing && !closed && !libraryReopenRequired() },
    )
    public val localStatistics: DesktopLocalStatisticsController = DesktopLocalStatisticsController(
        store, scope, onSnapshotUnavailable = { reportError("desktop_snapshot_unavailable") },
        canRead = { !closing && !closed && !libraryReopenRequired() },
    )
    public val metadata: DesktopMetadataController = DesktopMetadataController(
        store, scope, onSnapshotUnavailable = { reportError("desktop_snapshot_unavailable") },
        canRead = { !closing && !closed && !libraryReopenRequired() },
    )
    public val copyInspection: DesktopCopyInspectionController = DesktopCopyInspectionController(
        store, scope, onSnapshotUnavailable = { reportError("desktop_snapshot_unavailable") },
        canRead = { !closing && !closed && !libraryReopenRequired() },
    )
    public val savedImports: DesktopImportJobsController = DesktopImportJobsController(
        store, scope, onSnapshotUnavailable = { reportError("desktop_snapshot_unavailable") },
        canRead = { !closing && !closed && !libraryReopenRequired() },
    )
    private val storageRefusalOwner: Job = scope.launch {
        controller.error.collect { code ->
            if (code == "desktop_snapshot_unavailable") reportError(code)
        }
    }
    private val mutations: Channel<suspend () -> Unit> = Channel(32)
    private val mutationOwner: Job = scope.launch(Dispatchers.IO) {
        for (action in mutations) {
            if (closing || closed || libraryReopenRequired()) continue
            try {
                withTimeout(30_000) { action() }
                reportError(null)
            } catch (failure: Exception) {
                if (desktopSnapshotUnavailable(failure)) reportError("desktop_snapshot_unavailable")
                else if (failure !is CancellationException || failure is kotlinx.coroutines.TimeoutCancellationException) {
                    reportError(if (failure is DesktopStorageException) when (failure.code) {
                        "desktop_track_not_in_collection", "desktop_playlist_description_invalid",
                        "desktop_playlist_description_conflict", "desktop_playlist_missing",
                        "desktop_playlist_batch_limit", "desktop_playlist_limit", "desktop_track_missing",
                        "desktop_playlist_order_invalid", "desktop_identity_invalid" -> failure.code
                        else -> "LIBRARY_OPERATION_FAILED"
                    } else "LIBRARY_OPERATION_FAILED")
                }
                if (failure is CancellationException && failure !is kotlinx.coroutines.TimeoutCancellationException) throw failure
            }
        }
    }

    private fun libraryReopenRequired(): Boolean = snapshotUnavailable.get() ||
        controller.error.value == "desktop_snapshot_unavailable"

    /** Immediate host admission barrier; suspendable owner cleanup remains on the IO close job. */
    public fun beginClose(): Unit = synchronized(lifecycleLock) {
        closing = true
        if (!sourceClosed) sources?.beginClose()
        accounts?.setVisible?.invoke(false)
        productionAccounts?.beginClose()
        textSearch.retire()
        systemMedia?.retire()
        savedImports.beginClose()
        localStatistics.beginClose()
    }

    /** Explicit post-window hook; injected APIs never load a library or discover a Desktop. */
    public fun installSystemMedia(apiFactory: () -> DesktopSystemMediaApi? = ::macSystemMediaApi): Unit =
        synchronized(lifecycleLock) {
            if (closing || closed || systemMediaAttempted) return@synchronized
            systemMediaAttempted = true
            val api = try { apiFactory() } catch (_: Exception) { null } catch (_: LinkageError) { null }
            systemMedia = DesktopSystemMediaProjection(api, scope, playerState,
                productionAccounts?.libraryState ?: store.state, metadata.state,
                controller.error, error, canPublish = { !closing && !closed && !libraryReopenRequired() },
                onCommand = { command -> tryTransport(command.target, command.action, command.positionMs) })
        }

    /** All UI calls into independently owned controllers share the host shutdown barrier. */
    public fun admit(action: (DesktopRuntime) -> Unit): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed) action(this)
    }

    public fun transport(target: DesktopPlaybackTarget, action: DesktopTransportAction, positionMs: Long? = null): Unit {
        tryTransport(target, action, positionMs)
    }

    /** Native callbacks acknowledge only admission to the existing controller queue. */
    public fun tryTransport(target: DesktopPlaybackTarget, action: DesktopTransportAction, positionMs: Long? = null): Boolean =
        synchronized(lifecycleLock) {
            !closing && !closed && !libraryReopenRequired() && controller.tryTransport(target, action, positionMs)
        }

    /** Best effort only: capture the coherent target at native event delivery, without AWT IO. */
    public fun pauseForSystemSleep(): Unit {
        val signal = synchronized(lifecycleLock) {
            if (closing || closed) return
            productionAccounts?.sleep()
            if (!sourceClosed) sources?.beginFence()
            val importing = importJob?.takeUnless { it.isCompleted }
            if (importing != null) markImportCancelling()
            playerState.value.target?.takeUnless { libraryReopenRequired() }?.let {
                transport(it, DesktopTransportAction.PAUSE)
            }
            sources to importing
        }
        signal.second?.cancel(CancellationException("IMPORT_CANCELLED"))
        synchronized(lifecycleLock) {
            val source = signal.first
            if (closing || closed || source == null || sourceLifecycleJob?.isActive == true) return@synchronized
            sourceLifecycleJob = scope.launch(Dispatchers.IO) {
                withContext(NonCancellable) {
                    signal.second?.join()
                    try { source.settleSleep() }
                    catch (_: Exception) { reportError("SOURCE_ACCESS_DRAIN_FAILED") }
                }
            }
        }
    }

    /** Never await native/file work while holding lifecycleLock (G1 adoption also takes it). */
    private fun fenceSourcesForAccount(): Unit {
        val importing = synchronized(lifecycleLock) {
            if (!sourceClosed) sources?.beginFence()
            importJob?.takeUnless { it.isCompleted }?.also { markImportCancelling() }
        }
        importing?.cancel(CancellationException("IMPORT_CANCELLED"))
    }

    private suspend fun drainSourcesForAccount(): Unit = withContext(NonCancellable) {
        val captured = synchronized(lifecycleLock) { Triple(importJob, sourceLifecycleJob, sources?.takeUnless { sourceClosed }) }
        captured.first?.join()
        captured.second?.join()
        captured.third?.settleSleep()
    }

    private suspend fun closeSources(): Unit {
        if (!sourceClosed) { sources?.close(); sourceClosed = true }
    }

    internal fun resumeRememberedSources(expected: SourceAccessState): Unit = synchronized(lifecycleLock) {
        val source = sources ?: return@synchronized
        if (closing || closed || source.state.value !== expected || !expected.fenced ||
            sourceLifecycleJob?.isActive == true) return@synchronized
        sourceLifecycleJob = scope.launch(Dispatchers.IO) {
            withContext(NonCancellable) {
                try {
                    source.settleSleep()
                    synchronized(lifecycleLock) { if (closing || closed) return@withContext }
                    source.resumeAfterSleep()
                } catch (_: Exception) { reportError("SOURCE_ACCESS_DRAIN_FAILED") }
            }
        }
    }

    private fun installSourceAccess(bundle: DesktopSignedBundle, dataRoot: Path): Unit {
        val canonical = Path.of(System.getProperty("user.home")).toAbsolutePath().normalize()
            .resolve("Library/Application Support/AutPlay")
        // A disposable smoke root may never open the canonical remembered-source index.
        if (dataRoot.toAbsolutePath().normalize() != canonical) return
        val fileImporter = importer as? DesktopFileImporter ?: return
        val owner = createDesktopSourceAccessOwner(bundle.sourceIdentity, dataRoot)
        val host = DesktopSourceAccessController(owner, fileImporter, scope,
            ::canPrepareRememberedReview, ::adoptRememberedPreview)
        sources = host
        host.initialize()
    }

    private fun reportError(code: String?): Unit {
        if (code == "desktop_snapshot_unavailable" || libraryReopenRequired()) {
            snapshotUnavailable.set(true)
            textSearch.retire()
            systemMedia?.refuse()
            history.refuse()
            localStatistics.refuse()
            metadata.refuse()
            copyInspection.refuse()
            savedImports.refuse()
            controller.reportSnapshotUnavailable()
        }
        mutableError.update { current ->
            if (libraryReopenRequired() || current == "desktop_snapshot_unavailable") "desktop_snapshot_unavailable" else code
        }
    }

    public fun importSelected(owner: Component?): Unit {
        beginImport { chooseSources(owner) }
    }

    public fun refreshSavedImports(expected: DesktopSavedImportsState): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) savedImports.refresh(expected, mutableLastImportProgress.value?.jobId)
    }

    public fun nextSavedImports(expected: DesktopSavedImportsState): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) savedImports.next(expected)
    }

    public fun retrySavedImports(expected: DesktopSavedImportsState): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) savedImports.retry(expected)
    }

    public fun resumeImport(expected: DesktopSavedImportsState, job: DesktopImportJob, owner: Component?): Boolean =
        resumeImportUsingChooser(expected, job) { chooseSources(owner) }

    /** Same production route with an injected chooser for isolated source acceptance assertions. */
    internal fun resumeImportUsingChooser(expected: DesktopSavedImportsState, job: DesktopImportJob,
        choose: suspend () -> DesktopSourceSelection): Boolean =
        synchronized(lifecycleLock) {
            if (closing || closed || libraryReopenRequired() || importReconciliationRequired() ||
                importJob != null || importer !is DesktopImportReviewPort) return false
            val captured = savedImports.captureResume(expected, job) ?: return false
            beginImport(captured, select = choose)
        }

    public fun refreshHistory(): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) history.refresh()
    }

    public fun refreshLocalStatistics(expected: DesktopLocalStatisticsState): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) localStatistics.refresh(expected)
    }

    public fun retryLocalStatistics(expected: DesktopLocalStatisticsState): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) localStatistics.retry(expected)
    }

    public fun loadMoreHistory(expectedRevision: Long): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) history.loadMore(expectedRevision)
    }

    public fun retryHistory(expectedRevision: Long): Unit = synchronized(lifecycleLock) {
        if (!closing && !closed && !libraryReopenRequired()) history.retry(expectedRevision)
    }

    /** Only a completed, explicit OS file-list drop can supply these runtime capabilities. */
    public fun importDroppedFiles(files: List<File>): Boolean {
        if (files.isEmpty()) return false
        val selection = if (files.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) null else files.toList()
        return beginImport {
            if (selection == null) DesktopSourceSelection(limitExceeded = true)
            else DesktopSourceSelection(selection.map { it.toPath().toAbsolutePath().toString() })
        }
    }

    public fun reportInvalidDrop(): Unit {
        if (!closing && !closed) reportError("IMPORT_DROP_INVALID")
    }

    public fun rejectOversizedImport(): Boolean = beginImport { DesktopSourceSelection(limitExceeded = true) }

    /** Only the currently published preview can release its one explicit confirmation gate. */
    public fun confirmImport(previewId: String): Boolean = synchronized(lifecycleLock) {
        val request = importRequest ?: return false
        val review = mutableImportReview.value
        val preview = review.preview ?: return false
        if (closing || closed || libraryReopenRequired() || request.cancellationRequested || importJob?.isActive != true ||
            review.phase != DesktopImportPhase.REVIEWING || preview.id != previewId || preview.files.isEmpty() ||
            request.resume != null || preview.job != null ||
            request.previewId != previewId || request.confirmation.isCompleted) return false
        request.confirmation.complete(Unit)
    }

    /** Exact view instance and edit revision fence every pair/unpair operation, including A-B-A. */
    public fun mapImportCandidate(expected: DesktopImportMappingReview, candidateId: String, selectionId: String?): Boolean =
        synchronized(lifecycleLock) {
            val request = importRequest ?: return false
            val job = expected.preview.job ?: return false
            if (!ownsMapping(request, expected) || job.candidates.none { it.candidate.id == candidateId && desktopImportCandidateEligible(it) } ||
                (selectionId != null && (expected.preview.files.none { it.id == selectionId } ||
                    expected.mappings.any { it.candidateId != candidateId && it.selectionId == selectionId }))) return false
            val remaining = expected.mappings.filter { it.candidateId != candidateId }
            mutableImportMapping.value = expected.copy(revision = expected.revision + 1,
                mappings = if (selectionId == null) remaining else remaining + DesktopImportCandidateMapping(candidateId, selectionId))
            true
        }

    public fun confirmImportMapping(expected: DesktopImportMappingReview): Boolean = synchronized(lifecycleLock) {
        val request = importRequest ?: return false
        if (!ownsMapping(request, expected) || !desktopImportMappingValid(expected)) return false
        request.mappings = expected.mappings.toList()
        request.progress = DesktopImportProgress(expected.preview.id, expected.mappings.size, failed = expected.preview.failed,
            jobId = expected.preview.jobId, jobRevision = expected.preview.job?.revision, jobPhase = expected.preview.job?.phase)
        mutableImportReview.value = mutableImportReview.value.copy(progress = request.progress)
        mutableImportMapping.value = null
        request.confirmation.complete(Unit)
    }

    private fun ownsMapping(request: ImportRequest, expected: DesktopImportMappingReview): Boolean =
        !closing && !closed && !libraryReopenRequired() && !request.cancellationRequested &&
            importJob?.isActive == true && request.resume != null && !request.confirmation.isCompleted &&
            mutableImportMapping.value === expected && mutableImportReview.value.preview === expected.preview &&
            mutableImportReview.value.phase == DesktopImportPhase.REVIEWING && request.previewId == expected.preview.id &&
            expected.preview.jobId == request.resume.id

    /** Requests cancellation without waiting on the UI thread; admission stays closed through cleanup. */
    public fun cancelImport(): Unit {
        val current = synchronized(lifecycleLock) {
            importJob?.takeUnless { it.isCompleted }?.also { markImportCancelling() }
        }
        current?.cancel(CancellationException("IMPORT_CANCELLED"))
    }

    // Called under lifecycleLock; cancellation itself stays outside it because completion can run inline.
    private fun markImportCancelling(): Unit {
        importRequest?.cancellationRequested = true
        mutableImportMapping.value = null
        mutableImportReview.value = mutableImportReview.value.copy(
            phase = DesktopImportPhase.CANCELLING,
            progress = mutableImportReview.value.progress?.copy(currentTitle = null),
        )
    }

    private fun publishImportPhase(request: ImportRequest, phase: DesktopImportPhase): Boolean = synchronized(lifecycleLock) {
        if (importRequest !== request || closing || closed || libraryReopenRequired() || request.cancellationRequested) return false
        mutableImportReview.value = mutableImportReview.value.copy(phase = phase)
        true
    }

    private fun publishImportProgress(request: ImportRequest, progress: DesktopImportProgress): Unit = synchronized(lifecycleLock) {
        if (importRequest !== request || request.previewId != progress.previewId || importJob?.isCompleted != false) return
        val previous = request.progress
        if (previous != null && (progress.totalFiles != previous.totalFiles || progress.completedFiles > previous.totalFiles ||
            progress.completedFiles < previous.completedFiles || progress.imported < previous.imported ||
            progress.duplicates < previous.duplicates || progress.failed < previous.failed ||
            (previous.jobId != null && progress.jobId != previous.jobId) ||
            (previous.jobRevision?.let { previousRevision -> progress.jobRevision?.let { it < previousRevision } } == true))) return
        // A late settled commit still counts during cancellation, but cannot reactivate the review.
        val settled = if (request.cancellationRequested || closing || closed || libraryReopenRequired()) progress.copy(currentTitle = null) else progress
        request.progress = settled
        mutableImportReview.value = mutableImportReview.value.copy(progress = settled)
        mutableLastImportProgress.value = settled
        if (settled.errorCode == "import_library_reopen_required" || settled.errorCode == "desktop_snapshot_unavailable") {
            reportError("desktop_snapshot_unavailable")
        }
    }

    private fun importReconciliationRequired(): Boolean = mutableLastImportProgress.value?.errorCode == "import_job_reconciliation_required" ||
        mutableImportResult.value?.errorCode == "import_job_reconciliation_required"

    internal fun canPrepareRememberedReview(): Boolean = synchronized(lifecycleLock) {
        !closing && !closed && !libraryReopenRequired() && !importReconciliationRequired() &&
            importJob == null && importer is DesktopImportReviewPort
    }

    /** Ownership transfers only after the IO worker has installed its actual preview cleanup. */
    internal suspend fun adoptRememberedPreview(preview: DesktopImportPreview): Boolean {
        val adopted = CompletableDeferred<Boolean>()
        val admitted = beginImport(preparedPreview = preview, preparedAdmission = adopted) { DesktopSourceSelection() }
        return admitted && adopted.await()
    }

    private fun beginImport(resume: DesktopImportJob? = null, preparedPreview: DesktopImportPreview? = null,
        preparedAdmission: CompletableDeferred<Boolean>? = null, select: suspend () -> DesktopSourceSelection): Boolean {
        val request = ImportRequest(resume)
        val reviewImporter = importer as? DesktopImportReviewPort
        val job = synchronized(lifecycleLock) {
            if (closing || closed || libraryReopenRequired() || importReconciliationRequired() || importJob != null) return false
            if (preparedPreview != null && (resume != null || reviewImporter == null || preparedAdmission == null)) return false
            val admitted = scope.launch(Dispatchers.IO, start = CoroutineStart.LAZY) {
                try {
                    if (preparedPreview != null) {
                        request.previewId = preparedPreview.id
                        preparedAdmission?.complete(true)
                    }
                    val selected = if (preparedPreview == null) select() else DesktopSourceSelection()
                    request.chooserCancelled = selected.cancelled
                    currentCoroutineContext().ensureActive()
                    if (selected.limitExceeded || selected.paths.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) {
                        request.result = DesktopImportResult(0, 0, 1, "import_selection_limit")
                    } else if ((selected.paths.isNotEmpty() || preparedPreview != null) && !libraryReopenRequired()) {
                        if (!publishImportPhase(request, DesktopImportPhase.PREPARING)) throw CancellationException("IMPORT_CANCELLED")
                        if (reviewImporter == null) {
                            // Legacy injected fixtures retain their explicit selection contract.
                            if (!publishImportPhase(request, DesktopImportPhase.IMPORTING)) throw CancellationException("IMPORT_CANCELLED")
                            request.result = importer.importSelected(selected.paths)
                        } else {
                            val preview = (preparedPreview ?: if (resume == null) reviewImporter.prepareSelected(selected.paths)
                                else reviewImporter.prepareReselected(resume.id, selected.paths)).let { value ->
                                value.copy(files = value.files.toList(), job = value.job?.let { it.copy(candidates = it.candidates.toList()) })
                            }
                            val progress = DesktopImportProgress(preview.id, preview.files.size, failed = preview.failed,
                                jobId = preview.jobId, jobRevision = preview.job?.revision,
                                jobPhase = preview.job?.phase, errorCode = preview.errorCode)
                            synchronized(lifecycleLock) {
                                request.previewId = preview.id
                                request.progress = progress
                            }
                            currentCoroutineContext().ensureActive()
                            synchronized(lifecycleLock) {
                                if (importRequest !== request || closing || closed || libraryReopenRequired() || request.cancellationRequested) {
                                    throw CancellationException("IMPORT_CANCELLED")
                                }
                                mutableImportReview.value = DesktopImportReviewState(DesktopImportPhase.REVIEWING, preview, progress)
                                mutableLastImportProgress.value = progress
                                mutableImportMapping.value = if (resume == null) null else DesktopImportMappingReview(preview)
                                if (preview.errorCode == "import_library_reopen_required" || preview.errorCode == "desktop_snapshot_unavailable") {
                                    reportError("desktop_snapshot_unavailable")
                                }
                            }
                            // Empty reviews stay visible for explicit dismissal and can never confirm.
                            request.confirmation.await()
                            currentCoroutineContext().ensureActive()
                            if (!publishImportPhase(request, DesktopImportPhase.IMPORTING)) throw CancellationException("IMPORT_CANCELLED")
                            currentCoroutineContext().ensureActive()
                            request.result = if (resume == null) reviewImporter.importPrepared(preview.id) { publishImportProgress(request, it) }
                                else reviewImporter.importReselected(preview.id, checkNotNull(request.mappings)) { publishImportProgress(request, it) }
                        }
                    }
                } catch (failure: Exception) {
                    request.failureCode = when {
                        desktopSnapshotUnavailable(failure) -> "desktop_snapshot_unavailable"
                        failure is CancellationException -> "IMPORT_CANCELLED"
                        else -> "IMPORT_FAILED"
                    }
                    if (failure is CancellationException) throw failure
                } finally {
                    try {
                        request.previewId?.let { reviewImporter?.discardPrepared(it) }
                    } catch (failure: Exception) {
                        request.failureCode = if (desktopSnapshotUnavailable(failure) || request.failureCode == "desktop_snapshot_unavailable") {
                            "desktop_snapshot_unavailable"
                        } else request.failureCode ?: "IMPORT_FAILED"
                    }
                }
            }
            importJob = admitted
            importRequest = request
            mutableImportResult.value = null
            reportError(null)
            mutableImportReview.value = DesktopImportReviewState(phase = DesktopImportPhase.SELECTING)
            mutableImportMapping.value = null
            mutableLastImportProgress.value = null
            mutableImporting.value = true
            admitted
        }
        // Register outside the lifecycle lock: an already-cancelled lazy job completes inline.
        // This covers all child/chooser cleanup and keeps import admission closed during invalidation.
        job.invokeOnCompletion { failure ->
            // A never-started worker did not adopt the batch; the source controller still owns it.
            preparedAdmission?.complete(false)
            copyInspection.invalidate()
            savedImports.invalidate()
            synchronized(lifecycleLock) {
                if (importJob === job && importRequest === request) {
                    val cancelled = request.cancellationRequested || request.chooserCancelled || failure is CancellationException ||
                        request.result?.cancelled == true
                    val progress = request.progress
                    val unavailable = failure?.let(::desktopSnapshotUnavailable) == true ||
                        request.failureCode == "desktop_snapshot_unavailable" || request.result?.errorCode == "import_library_reopen_required" ||
                        progress?.errorCode in setOf("import_library_reopen_required", "desktop_snapshot_unavailable")
                    mutableImportResult.value = if (cancelled) {
                        DesktopImportResult(
                            maxOf(request.result?.imported ?: 0, progress?.imported ?: 0),
                            maxOf(request.result?.duplicates ?: 0, progress?.duplicates ?: 0),
                            maxOf(request.result?.failed ?: 0, progress?.failed ?: 0),
                            request.result?.errorCode ?: progress?.errorCode, cancelled = true,
                        )
                    } else request.result
                    reportError(when {
                        unavailable -> "desktop_snapshot_unavailable"
                        cancelled -> "IMPORT_CANCELLED"
                        else -> request.failureCode
                    })
                    mutableImportReview.value = DesktopImportReviewState()
                    mutableImportMapping.value = null
                    importRequest = null
                    importJob = null
                    mutableImporting.value = false
                }
            }
        }
        job.start()
        return true
    }

    private class ImportRequest(val resume: DesktopImportJob?) {
        val confirmation: CompletableDeferred<Unit> = CompletableDeferred()
        var mappings: List<DesktopImportCandidateMapping>? = null
        var previewId: String? = null
        var progress: DesktopImportProgress? = null
        var result: DesktopImportResult? = null
        var failureCode: String? = null
        var chooserCancelled: Boolean = false
        var cancellationRequested: Boolean = false
    }

    public fun createPlaylist(name: String): Unit = mutate { store.createPlaylist(name) }
    public fun updateTrackMetadata(trackId: String, title: String, artist: String): Unit =
        mutate { store.updateTrackTextMetadata(trackId, title, artist) }
    public fun setTrackPreference(trackId: String, preference: String): Unit =
        mutate { store.setTrackPreference(trackId, preference) }
    public fun removeTrackFromCollection(trackId: String): Unit =
        mutate { store.removeTrackFromCollection(trackId) }
    public fun restoreTrackToCollection(trackId: String): Unit =
        mutate { store.restoreTrackToCollection(trackId) }
    public fun renamePlaylist(playlistId: String, name: String): Unit = mutate { store.renamePlaylist(playlistId, name) }
    public fun updatePlaylistDescription(playlistId: String, expectedDescription: String?, description: String?): Unit =
        mutate { store.updatePlaylistDescription(playlistId, expectedDescription, description) }
    public fun deletePlaylist(playlistId: String): Unit = mutate { store.deletePlaylist(playlistId) }
    public fun addToPlaylist(playlistId: String, trackId: String): Unit = mutate { store.appendToPlaylist(playlistId, trackId) }
    public fun addTracksToPlaylist(playlistId: String, trackIds: List<String>): Unit {
        val selectedIds = trackIds.take(DesktopLocalLimits.MAX_QUEUE_ENTRIES + 1)
        mutate { store.appendTracksToPlaylist(playlistId, selectedIds) }
    }
    public fun removePlaylistEntry(playlistId: String, entryId: String): Unit = mutate { store.removePlaylistEntry(playlistId, entryId) }
    public fun movePlaylistEntry(playlistId: String, entryId: String, direction: Int): Unit =
        mutate { store.movePlaylistEntry(playlistId, entryId, direction) }

    private fun mutate(action: suspend () -> Unit): Unit {
        synchronized(lifecycleLock) {
            if (closing || closed || libraryReopenRequired()) return
            if (!mutations.trySend(action).isSuccess) reportError("LIBRARY_COMMAND_BUSY")
        }
    }

    public suspend fun close(): Unit = withContext(NonCancellable) {
        beginClose()
        closeMutex.withLock {
            retainedCloseFailure?.let { throw it }
            val currentImport = synchronized(lifecycleLock) {
                if (closed) return@withLock
                settings.beginClose()
                history.beginClose()
                metadata.beginClose()
                importJob?.takeUnless { it.isCompleted }?.also { markImportCancelling() }
            }
            copyInspection.beginClose()
            currentImport?.cancel(CancellationException("IMPORT_CANCELLED"))
            // Discard queued writes rather than draining them into a closing store.
            mutations.cancel()
            mutationOwner.cancel()
            var closeFailure: Exception? = null
            suspend fun cleanup(code: String, action: suspend () -> Unit) {
                try { action() }
                catch (failure: Exception) {
                    retainedCloseFailure = retainedCloseFailure ?: failure
                    val primary = closeFailure
                    if (primary == null) closeFailure = failure
                    else primary.addSuppressed(IllegalStateException(code))
                }
            }
            // Keep AppKit free to drain queued handler/metadata removal before exitApplication.
            cleanup("DESKTOP_SYSTEM_MEDIA_CLOSE_FAILED") { systemMedia?.close() }
            cleanup("DESKTOP_ACCOUNT_EXTERNAL_DRAIN_FAILED") { productionAccounts?.drainExternal() }
            cleanup("DESKTOP_PLAYBACK_CLOSE_FAILED") { controller.close() }
            cleanup("DESKTOP_SETTINGS_CLOSE_FAILED") { settings.close() }
            // Search owns only a derived connection. Join its mutex on IO before final disposal;
            // synchronous native steps can delay this and are not subject to a fake hard timeout.
            cleanup("DESKTOP_SEARCH_CLOSE_FAILED") {
                try { withContext(Dispatchers.IO) { textSearch.close() } }
                catch (_: LinkageError) { error("DESKTOP_SEARCH_CLOSE_FAILED") }
            }
            // Never close the database underneath a retired non-cooperative history read.
            history.close()
            localStatistics.close()
            // Saved-job reads can ignore cancellation too; drain before the store lifetime ends.
            savedImports.close()
            // Admitted metadata writes include their snapshot refresh; reads must finish too.
            metadata.close()
            // Attribute IO can ignore cancellation; never close the store underneath it.
            copyInspection.close()
            storageRefusalOwner.cancel()
            val owner = scope.coroutineContext[kotlinx.coroutines.Job]
            owner?.cancel()
            // Cancellation is a request: wait for every admitted effect and its cleanup to settle.
            currentImport?.join()
            sourceLifecycleJob?.join()
            cleanup("DESKTOP_SOURCE_ACCESS_CLOSE_FAILED") { closeSources() }
            mutationOwner.join()
            owner?.join()
            // Any unresolved external owner keeps J, its K pin and the standalone source alive.
            if (closeFailure == null) cleanup("DESKTOP_ACCOUNT_CLOSE_FAILED") { productionAccounts?.closeAfterPlaybackAndSources() }
            if (closeFailure == null) cleanup("DESKTOP_LIBRARY_CLOSE_FAILED") { store.close() }
            closeFailure?.let { retainedCloseFailure = it; throw it }
            synchronized(lifecycleLock) { closed = true }
        }
    }

    public companion object {
        public suspend fun open(dataRoot: Path,
            textSearchFactory: () -> DesktopTextSearchPort = { DesktopFtsSearchOwner() },
            disposableSmokeRoot: Boolean = false, initializeAccounts: Boolean = false): DesktopRuntime {
            val store = openDesktopLibraryStore(dataRoot)
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            var engine: DesktopAudioEngine? = null
            var preferences: DesktopPreferencesStore? = null
            var settingsOwner: DesktopSettingsController? = null
            var searchOwner: DesktopTextSearchPort? = null
            var runtimeOwner: DesktopRuntime? = null
            var accountOwner: DesktopAccountProductionRuntime? = null
            try {
                val defaults = DesktopPreferences(appLanguage = if (Locale.getDefault().language == "ru") "RU" else "EN")
                val openedPreferences = try { openDesktopPreferencesStore(dataRoot, defaults) }
                    catch (failure: Exception) {
                        if (failure is CancellationException) throw failure
                        unavailablePreferences(defaults)
                    }
                preferences = openedPreferences
                val settings = DesktopSettingsController(openedPreferences, scope,
                    applyMeteredPolicy = { allowed -> accountOwner?.applySavedDownloadPolicy(allowed) },
                    meteredPreferenceCommitted = { accountOwner?.meteredPreferenceCommitted() })
                settingsOwner = settings
                val audio = DesktopAudioOwnerArbiter.createLocalEngine(nativeHelper())
                engine = audio
                val search = textSearchFactory()
                searchOwner = search
                val canonical = Path.of(System.getProperty("user.home")).toAbsolutePath().normalize()
                    .resolve("Library/Application Support/AutPlay")
                val normalMac = !disposableSmokeRoot && System.getProperty("os.name") == "Mac OS X" &&
                    dataRoot.toAbsolutePath().normalize() == canonical
                // Retain the exact immutable public bundle identity for this runtime's entire lifetime.
                val bundle = if (normalMac) readDesktopSignedBundle() else null
                val production = if (normalMac && bundle?.identity != null) DesktopAccountProductionRuntime.open(
                    bundle, store, scope, audio, { DesktopAudioOwnerArbiter.createLocalEngine(nativeHelper()) },
                    settings.preferences.value.value.downloadOnMeteredNetwork, initializeAccounts,
                    language = { if (settings.preferences.value.value.appLanguage == "RU") DesktopLanguage.Russian else DesktopLanguage.English },
                    fenceSources = { runtimeOwner?.fenceSourcesForAccount() },
                    drainSources = { runtimeOwner?.drainSourcesForAccount() },
                    readMeteredPreference = { settings.preferences.value.value.downloadOnMeteredNetwork },
                    saveMeteredPreference = settings::setDownloadOnMeteredNetwork,
                ) else null
                accountOwner = production
                currentCoroutineContext().ensureActive()
                val workspace = production?.binding ?: if (normalMac)
                    DesktopAccountWorkspaceBinding.unavailable(DesktopAccountAvailability.IdentityUnavailable) else null
                val playback = production?.playbackController ?: DesktopPlaybackController(store, audio, scope)
                val runtime = DesktopRuntime(store, DesktopFileImporter(store, dataRoot), playback, scope, settings,
                    search, workspace, production, bundle)
                runtimeOwner = runtime
                bundle?.let { runtime.installSourceAccess(it, dataRoot) }
                return runtime
            } catch (failure: Throwable) {
                val failedSettingsOwner = settingsOwner
                failedSettingsOwner?.beginClose()
                searchOwner?.retire()
                withContext(NonCancellable) {
                    suspend fun cleanup(code: String, action: suspend () -> Unit) {
                        try { action() }
                        catch (_: Throwable) { failure.addSuppressed(IllegalStateException(code)) }
                    }
                    runtimeOwner?.sources?.beginClose()
                    accountOwner?.beginClose()
                    cleanup("DESKTOP_ACCOUNT_EXTERNAL_STARTUP_CLEANUP_FAILED") { accountOwner?.drainExternal() }
                    cleanup("DESKTOP_SOURCE_ACCESS_STARTUP_CLEANUP_FAILED") { runtimeOwner?.closeSources() }
                    cleanup("DESKTOP_SEARCH_STARTUP_CLEANUP_FAILED") { withContext(Dispatchers.IO) { searchOwner?.close() } }
                    cleanup("DESKTOP_AUDIO_STARTUP_CLEANUP_FAILED") {
                        val accountPlayback = accountOwner?.playbackController
                        if (accountPlayback != null) accountPlayback.close() else engine?.close()
                    }
                    // A failed prerequisite retains the graph, its stores and K lock for reconciliation.
                    if (failure.suppressed.isEmpty()) cleanup("DESKTOP_ACCOUNT_STARTUP_CLEANUP_FAILED") {
                        accountOwner?.closeAfterPlaybackAndSources()
                    }
                    cleanup("DESKTOP_SETTINGS_STARTUP_CLEANUP_FAILED") {
                        if (failedSettingsOwner != null) failedSettingsOwner.close() else preferences?.close()
                    }
                    scope.cancel()
                    if (failure.suppressed.isEmpty()) cleanup("DESKTOP_LIBRARY_STARTUP_CLEANUP_FAILED") { store.close() }
                    else retainedFailedStartupOwners.add(listOfNotNull(runtimeOwner, accountOwner, store, engine, scope))
                }
                throw failure
            }
        }

        private val retainedFailedStartupOwners: MutableSet<List<Any>> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    }
}

/** Retains an unavailable settings file; local playback can still expose its own independent state. */
private fun unavailablePreferences(defaults: DesktopPreferences): DesktopPreferencesStore = object : DesktopPreferencesStore {
    override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(
        DesktopPreferencesState(defaults, writable = false, errorCode = "desktop_settings_unavailable"),
    )
    override suspend fun setAppLanguage(value: String): Unit = error("desktop_settings_read_only")
    override suspend fun setAppearanceMode(value: String): Unit = error("desktop_settings_read_only")
    override suspend fun setAccentPalette(value: String): Unit = error("desktop_settings_read_only")
    override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("desktop_settings_read_only")
    override suspend fun setDownloadOnMeteredNetwork(value: Boolean): Unit = error("desktop_settings_read_only")
    override suspend fun applyPortable(value: DesktopPreferences): Unit = error("desktop_settings_read_only")
    override suspend fun close(): Unit = Unit
}

internal data class DesktopSourceSelection(
    val paths: List<String> = emptyList(),
    val limitExceeded: Boolean = false,
    val cancelled: Boolean = false,
)

private suspend fun chooseSources(owner: Component?): DesktopSourceSelection {
    val finished = CompletableDeferred<Unit>()
    try {
        return suspendCancellableCoroutine { continuation ->
            val showChooser = Runnable {
                try {
                    if (!continuation.isActive) return@Runnable
                    val chooser = JFileChooser().apply {
                        isMultiSelectionEnabled = true
                        fileSelectionMode = JFileChooser.FILES_AND_DIRECTORIES
                        dialogTitle = "AutPlay — выберите файлы или папки / Choose files or folders"
                    }
                    continuation.invokeOnCancellation { EventQueue.invokeLater { chooser.cancelSelection() } }
                    if (!continuation.isActive) return@Runnable
                    val selection = if (chooser.showOpenDialog(owner) == JFileChooser.APPROVE_OPTION) {
                        val selectedFiles = chooser.selectedFiles
                        if (selectedFiles.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) {
                            DesktopSourceSelection(limitExceeded = true)
                        } else DesktopSourceSelection(selectedFiles.map { it.toPath().toAbsolutePath().toString() })
                    } else DesktopSourceSelection(cancelled = true)
                    if (continuation.isActive) continuation.resume(selection)
                } catch (failure: Exception) {
                    if (continuation.isActive) continuation.resumeWith(Result.failure(failure))
                } finally {
                    finished.complete(Unit)
                }
            }
            try {
                EventQueue.invokeLater(showChooser)
            } catch (failure: Exception) {
                finished.complete(Unit)
                if (continuation.isActive) continuation.resumeWith(Result.failure(failure))
            }
        }
    } finally {
        // Prompt coroutine cancellation must not admit a second chooser before AWT disposes the first.
        withContext(NonCancellable) { finished.await() }
    }
}

private class DesktopNavigationIntent(
    val owner: DesktopRuntime?, val workspace: DesktopAccountWorkspaceBinding?, val window: Component?,
    val revision: Long, val sourceSection: DesktopSection, val sourceKind: DesktopLibraryKind,
    val sourceAccounts: Boolean, val sourceOwner: AccountLease?,
    val section: DesktopSection, val kind: DesktopLibraryKind, val accounts: Boolean,
    val settled: CompletableFuture<Unit>,
)

/** Confirmed presentation preferences and playback state keep independent owners. */
@Composable
@OptIn(ExperimentalFoundationApi::class, ExperimentalComposeUiApi::class)
public fun DesktopApp(
    playbackPort: PlaybackPort? = null,
    host: DesktopRuntime? = null,
    chooserOwner: Component? = null,
    lifecycleState: DesktopLifecycleState = DesktopLifecycleState(),
    lifecycleCallbacks: DesktopLifecycleCallbacks = DesktopLifecycleCallbacks(),
    systemSettingsRequest: Long = 0,
    initializingAccounts: Boolean = false,
    accountInitializationFailed: Boolean = false,
    shutdownFailed: Boolean = false,
    onInitializeAccounts: (() -> Unit)? = null,
    onReopenAccounts: (() -> Unit)? = null,
    windowVisible: Boolean = true,
): Unit {
    if (shutdownFailed) {
        AutPlayDesktopTheme(DesktopThemeMode.Dark) {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                Text("Не удалось подтвердить завершение операций. Данные и владельцы ресурсов сохранены. / Operations have not confirmed settlement. Data and resource owners are retained.", Modifier.padding(32.dp))
            }
        }
        return
    }
    var section by remember { mutableStateOf(DesktopSection.Home) }
    var navigationRevision by remember { mutableStateOf(0L) }
    var libraryKind by remember(host) { mutableStateOf(DesktopLibraryKind.Track) }
    var accountsVisible by remember(host) { mutableStateOf(false) }
    var pendingNavigation by remember(host, chooserOwner) { mutableStateOf<DesktopNavigationIntent?>(null) }
    var navigationFailed by remember(host, chooserOwner) { mutableStateOf(false) }
    val accountWorkspace = host?.accounts
    val contextSettlement = accountWorkspace?.contextSettlement?.collectAsState()?.value ?: DesktopContextSettlement.Ready
    val contextReady = contextSettlement == DesktopContextSettlement.Ready && pendingNavigation == null
    val currentClosing by rememberUpdatedState(lifecycleState.closing)
    val currentWindowVisible by rememberUpdatedState(windowVisible)
    val accountHomeAvailable = accountWorkspace?.availability?.collectAsState()?.value == DesktopAccountAvailability.Available
    val accountHomeVisible = contextReady && windowVisible && !accountsVisible && section == DesktopSection.Home && accountHomeAvailable && !lifecycleState.closing
    val accountHomeState = if (accountHomeVisible) accountWorkspace?.state?.collectAsState()?.value else null
    val catalogKind = when (libraryKind) {
        DesktopLibraryKind.Artist -> DesktopCatalogKind.Artist
        DesktopLibraryKind.Album -> DesktopCatalogKind.Album
        else -> null
    }
    val accountCatalogVisible = contextReady && windowVisible && !accountsVisible && section == DesktopSection.Library && catalogKind != null &&
        accountHomeAvailable && !lifecycleState.closing
    val accountCatalogState = if (accountCatalogVisible) accountWorkspace?.state?.collectAsState()?.value else null
    val sectionAtInput = section
    val kindAtInput = libraryKind
    val accountsAtInput = accountsVisible
    val navigationAtInput = navigationRevision
    val callbackLifetime = remember(host, playbackPort, chooserOwner) { AtomicBoolean(true) }
    DisposableEffect(callbackLifetime) { onDispose { callbackLifetime.set(false) } }
    DisposableEffect(accountWorkspace, accountsVisible, lifecycleState.closing, contextReady, windowVisible) {
        accountWorkspace?.setVisible?.invoke(accountsVisible && contextReady && windowVisible && !lifecycleState.closing)
        onDispose { if (accountsVisible) accountWorkspace?.setVisible?.invoke(false) }
    }
    DisposableEffect(accountWorkspace, accountHomeVisible) {
        accountWorkspace?.setHomeVisible?.invoke(accountHomeVisible)
        onDispose { if (accountHomeVisible) accountWorkspace?.setHomeVisible?.invoke(false) }
    }
    DisposableEffect(accountWorkspace, accountCatalogVisible, catalogKind) {
        accountWorkspace?.setCatalogVisible?.invoke(accountCatalogVisible)
        onDispose { if (accountCatalogVisible) accountWorkspace?.setCatalogVisible?.invoke(false) }
    }
    LaunchedEffect(lifecycleState.closing) {
        if (lifecycleState.closing) { navigationRevision += 1; pendingNavigation = null; accountsVisible = false }
    }
    LaunchedEffect(windowVisible) {
        if (!windowVisible) { navigationRevision += 1; pendingNavigation = null; navigationFailed = false }
    }
    fun navigationCurrent(): Boolean = callbackLifetime.get() && currentWindowVisible && !currentClosing &&
        pendingNavigation == null && accountWorkspace?.contextSettlement?.value.let { it == null || it == DesktopContextSettlement.Ready } &&
        section == sectionAtInput && libraryKind == kindAtInput && accountsVisible == accountsAtInput && navigationRevision == navigationAtInput
    fun requestNavigation(nextSection: DesktopSection, nextKind: DesktopLibraryKind, nextAccounts: Boolean): Unit {
        if (!callbackLifetime.get() || currentClosing || !currentWindowVisible) return
        // Retire every old UI callback before the retained owner starts any suspendable drain.
        navigationRevision += 1
        val owner = accountWorkspace?.accountOwner?.invoke()
        val local = !nextAccounts && (nextSection != DesktopSection.Library ||
            nextKind == DesktopLibraryKind.Track || nextKind == DesktopLibraryKind.Playlist)
        val settled = accountWorkspace?.settleNavigation?.invoke(local) ?: CompletableFuture.completedFuture(Unit)
        navigationFailed = false
        pendingNavigation = DesktopNavigationIntent(host, accountWorkspace, chooserOwner, navigationRevision,
            section, libraryKind, accountsVisible, owner,
            nextSection, nextKind, nextAccounts, settled)
    }
    LaunchedEffect(pendingNavigation, host, accountWorkspace, chooserOwner, windowVisible, lifecycleState.closing) {
        val requested = pendingNavigation ?: return@LaunchedEffect
        if (!windowVisible || lifecycleState.closing) return@LaunchedEffect
        try {
            requested.settled.hostAwait()
            currentCoroutineContext().ensureActive()
            if (callbackLifetime.get() && pendingNavigation === requested && requested.owner === host &&
                requested.workspace === accountWorkspace && requested.window === chooserOwner &&
                requested.revision == navigationRevision && section == requested.sourceSection &&
                libraryKind == requested.sourceKind && accountsVisible == requested.sourceAccounts &&
                requested.sourceOwner == accountWorkspace?.accountOwner?.invoke() &&
                currentWindowVisible && !currentClosing) {
                section = requested.section; libraryKind = requested.kind; accountsVisible = requested.accounts
                pendingNavigation = null
            } else if (pendingNavigation === requested && currentWindowVisible && !currentClosing) navigationFailed = true
        } catch (failure: CancellationException) { throw failure }
        catch (_: Exception) {
            if (callbackLifetime.get() && pendingNavigation === requested && currentWindowVisible && !currentClosing) navigationFailed = true
        }
    }
    fun openAccounts(): Unit {
        if (navigationCurrent() && !lifecycleState.closing && accountWorkspace != null) {
            requestNavigation(section, libraryKind, true)
        }
    }
    LaunchedEffect(systemSettingsRequest) {
        if (systemSettingsRequest > 0 && !lifecycleState.closing) {
            // A newer system intent replaces presentation intent, never its retained settlement.
            requestNavigation(DesktopSection.Settings, DesktopLibraryKind.Track, false)
        }
    }
    fun command(action: (DesktopRuntime) -> Unit): Unit {
        if (navigationCurrent()) host?.admit(action)
    }
    fun playbackCommand(action: (PlaybackPort) -> Unit): Unit {
        if (!navigationCurrent() || lifecycleState.closing) return
        if (host != null) host.admit { action(it.controller) } else playbackPort?.let(action)
    }
    var sessionThemeMode by remember { mutableStateOf(DesktopThemeMode.Dark) }
    var sessionLanguage by remember { mutableStateOf(if (Locale.getDefault().language == "ru") DesktopLanguage.Russian else DesktopLanguage.English) }
    val preferencesState = host?.settings?.preferences?.collectAsState()?.value
    val preferencesTransfer = host?.settings?.transfer?.collectAsState()?.value
    val storedPreferences = preferencesState?.value
    val language = when (storedPreferences?.appLanguage) {
        "RU" -> DesktopLanguage.Russian
        "EN" -> DesktopLanguage.English
        else -> sessionLanguage
    }
    val themeMode = when (storedPreferences?.appearanceMode) {
        "SYSTEM" -> DesktopThemeMode.System
        "LIGHT" -> DesktopThemeMode.Light
        "DARK" -> DesktopThemeMode.Dark
        null -> sessionThemeMode
        else -> DesktopThemeMode.Dark
    }
    val accentPalette = when (storedPreferences?.accentPalette) {
        "VIOLET" -> DesktopAccentPalette.Violet
        "GREEN" -> DesktopAccentPalette.Green
        "BLUE" -> DesktopAccentPalette.Blue
        else -> DesktopAccentPalette.Coral
    }
    if (!contextReady) {
        val failed = navigationFailed || contextSettlement == DesktopContextSettlement.Failed
        AutPlayDesktopTheme(themeMode, accentPalette) {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                Column(Modifier.fillMaxSize().padding(24.dp)) {
                    Text(if (language == DesktopLanguage.Russian) {
                        if (failed) "Переход не завершён. Данные и владельцы операций сохранены."
                        else "Завершение операций перед переходом..."
                    } else if (failed) "Transition has not settled. Data and operation owners are retained."
                        else "Settling operations before navigation...")
                    if (failed) TextButton(enabled = !currentClosing && currentWindowVisible, onClick = {
                        val previous = pendingNavigation
                        requestNavigation(previous?.section ?: section, previous?.kind ?: libraryKind, previous?.accounts ?: accountsVisible)
                    }) { Text(if (language == DesktopLanguage.Russian) "Повторить переход" else "Retry transition") }
                    if (failed && onReopenAccounts != null && !initializingAccounts && !accountInitializationFailed &&
                        accountWorkspace?.availability?.value == DesktopAccountAvailability.RecoveryRequired) {
                        TextButton(enabled = !currentClosing && currentWindowVisible, onClick = onReopenAccounts) {
                            Text(if (language == DesktopLanguage.Russian) "Открыть аккаунты заново" else "Reopen accounts")
                        }
                    }
                }
            }
        }
        return
    }
    if (accountsVisible && accountWorkspace != null) {
        val accountState = accountWorkspace.state.collectAsState().value
        val sensitiveOutput = accountWorkspace.sensitiveOutput.collectAsState().value
        val accountAvailability = accountWorkspace.availability.collectAsState().value
        val accountPlayer = host?.playerState?.collectAsState()?.value
        AutPlayDesktopTheme(themeMode, accentPalette) {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                Column(Modifier.fillMaxSize()) {
                    TextButton(enabled = !lifecycleState.closing, onClick = {
                        if (navigationCurrent() && !lifecycleState.closing) {
                            requestNavigation(section, libraryKind, false)
                        }
                    }) {
                        Text(if (language == DesktopLanguage.Russian) "Назад к библиотеке" else "Back to library")
                    }
                    if (accountAvailability == DesktopAccountAvailability.Available) {
                        DesktopAccountWorkspace(accountState, accountWorkspace.actions, language,
                            Modifier.weight(1f).fillMaxWidth(), sensitiveOutput)
                        val target = accountPlayer?.target
                        TextButton(enabled = !lifecycleState.closing && target != null &&
                            accountPlayer?.playback?.controls == PlaybackControlGate.Allowed, onClick = {
                            if (navigationCurrent() && !lifecycleState.closing && target != null) host?.transport(target,
                                if (accountPlayer?.playback?.isPlaying == true) DesktopTransportAction.PAUSE else DesktopTransportAction.PLAY)
                        }) {
                            Text(if (accountPlayer?.playback?.isPlaying == true) {
                                if (language == DesktopLanguage.Russian) "Пауза" else "Pause"
                            } else if (language == DesktopLanguage.Russian) "Воспроизвести" else "Play")
                        }
                    } else {
                        val ru = language == DesktopLanguage.Russian
                        Text(when (accountAvailability) {
                            DesktopAccountAvailability.InitializeRequired -> if (ru)
                                "Создать хранилище аккаунтов на этом Mac? Учётные данные защищены macOS; локальные метаданные аккаунтов хранятся без шифрования."
                                else "Create account storage on this Mac? macOS protects credentials; local account metadata is stored unencrypted."
                            DesktopAccountAvailability.IdentityUnavailable -> if (ru)
                                "Аккаунты недоступны: требуется квалифицированная подписанная сборка для этого Mac."
                                else "Accounts are unavailable. A qualified signed build for this Mac is required."
                            DesktopAccountAvailability.Retired -> if (ru)
                                "Доступ к аккаунтам приостановлен. Откройте их заново для повторной проверки."
                                else "Account access is suspended. Reopen accounts to revalidate."
                            DesktopAccountAvailability.KeychainLocked -> if (ru)
                                "Связка ключей заблокирована. Разблокируйте её в macOS и откройте аккаунты заново. Данные сохранены."
                                else "Keychain is locked. Unlock it in macOS, then reopen accounts. Your data is preserved."
                            DesktopAccountAvailability.KeychainAccessRequired -> if (ru)
                                "macOS требует разрешить доступ к сохранённым ключам. Нажмите «Открыть аккаунты заново» и подтвердите доступ в системном окне."
                                else "macOS requires permission to access saved keys. Reopen accounts and approve access in the system dialog."
                            DesktopAccountAvailability.Opening -> if (ru) "Открытие аккаунтов..." else "Opening accounts..."
                            else -> if (ru) "Требуется восстановление доступа. Существующие данные сохранены."
                                else "Account access needs recovery. Existing data is preserved."
                        }, Modifier.padding(24.dp))
                        if (accountInitializationFailed) Text(if (ru)
                            "Не удалось завершить открытие. Данные и незавершённые операции сохранены. Закройте и снова запустите приложение."
                            else "Opening could not finish. Data and unsettled operations are preserved. Close and restart the app.", Modifier.padding(24.dp))
                        if (accountAvailability == DesktopAccountAvailability.InitializeRequired && onInitializeAccounts != null) {
                            TextButton(enabled = !initializingAccounts && !lifecycleState.closing && !accountInitializationFailed,
                                onClick = { if (navigationCurrent() && !lifecycleState.closing) onInitializeAccounts() }) {
                                Text(if (ru) "Создать хранилище аккаунтов" else "Create account storage")
                            }
                        }
                        if ((accountAvailability == DesktopAccountAvailability.Retired ||
                                accountAvailability == DesktopAccountAvailability.KeychainLocked ||
                                accountAvailability == DesktopAccountAvailability.KeychainAccessRequired ||
                                accountAvailability == DesktopAccountAvailability.RecoveryRequired) && onReopenAccounts != null) {
                            TextButton(enabled = !initializingAccounts && !lifecycleState.closing && !accountInitializationFailed,
                                onClick = { if (navigationCurrent() && !lifecycleState.closing) onReopenAccounts() }) {
                                Text(if (ru) "Открыть аккаунты заново" else "Reopen accounts")
                            }
                        }
                    }
                }
            }
        }
        return
    }
    val port = host?.controller ?: playbackPort
    val disconnectedState: StateFlow<PlaybackPresentationState> = remember { MutableStateFlow(PlaybackPresentationState()) }
    val playerState = host?.playerState?.collectAsState()?.value
    val playback = if (host != null) checkNotNull(playerState).playback
        else (playbackPort?.state ?: disconnectedState).collectAsState().value
    val sleepTimer = host?.controller?.sleepTimer?.collectAsState()?.value ?: DesktopSleepTimerState()
    val taste = host?.controller?.taste?.collectAsState()?.value ?: DesktopTasteState()
    val history = host?.history?.state?.collectAsState()?.value ?: DesktopHistoryState()
    val localStatistics = host?.localStatistics?.state?.collectAsState()?.value ?: DesktopLocalStatisticsState()
    val metadata = host?.metadata?.state?.collectAsState()?.value ?: DesktopMetadataState()
    val copyInspection = host?.copyInspection?.state?.collectAsState()?.value ?: DesktopCopyInspectionState()
    LaunchedEffect(host, section) {
        if (navigationCurrent() && section == DesktopSection.History) host?.refreshHistory()
    }
    val library = host?.store?.state?.collectAsState()?.value
    val importing = host?.importing?.collectAsState()?.value ?: false
    val importReview = host?.importReview?.collectAsState()?.value ?: DesktopImportReviewState()
    val savedImports = host?.savedImports?.state?.collectAsState()?.value ?: DesktopSavedImportsState()
    val importMapping = host?.importMapping?.collectAsState()?.value
    val lastImportProgress = host?.lastImportProgress?.collectAsState()?.value
    val importResult = host?.importResult?.collectAsState()?.value
    val hostError = host?.error?.collectAsState()?.value
    val rememberedSources = host?.sources?.state?.collectAsState()?.value
    val playbackError = host?.controller?.error?.collectAsState()?.value
    val reopenRequired = hostError == "desktop_snapshot_unavailable" || playbackError == "desktop_snapshot_unavailable" ||
        importResult?.errorCode == "import_library_reopen_required"
    val acceptsDrop by rememberUpdatedState({ navigationCurrent() && host != null && section == DesktopSection.Library && libraryKind == DesktopLibraryKind.Track &&
        !importing && !reopenRequired && !lifecycleState.closing })
    val dropTarget = remember(host) {
        object : DragAndDropTarget {
            override fun onDrop(event: DragAndDropEvent): Boolean {
                if (!acceptsDrop() || event.action != DragAndDropTransferAction.Copy) return false
                return try {
                    val files = event.awtTransferable.getTransferData(DataFlavor.javaFileListFlavor) as? List<*>
                    if (files == null || files.isEmpty()) {
                        host?.reportInvalidDrop()
                        false
                    } else if (files.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) {
                        host?.rejectOversizedImport() ?: false
                    } else if (files.any { it !is File }) {
                        host?.reportInvalidDrop()
                        false
                    } else host?.importDroppedFiles(files.filterIsInstance<File>()) ?: false
                } catch (failure: Exception) {
                    host?.reportInvalidDrop()
                    false
                }
            }
        }
    }
    AutPlayDesktopShell(
        section, language, themeMode, playback, port != null,
        onSectionChange = {
            if (navigationCurrent() && !lifecycleState.closing && section != it) {
                requestNavigation(it, DesktopLibraryKind.Track, false)
            }
        },
        onLanguageChange = {
            if (host != null) command { owner -> owner.settings.setAppLanguage(if (it == DesktopLanguage.Russian) "RU" else "EN") }
            else sessionLanguage = it
        },
        onThemeChange = {
            if (host != null) command { owner -> owner.settings.setAppearanceMode(when (it) {
                DesktopThemeMode.System -> "SYSTEM"
                DesktopThemeMode.Light -> "LIGHT"
                DesktopThemeMode.Dark -> "DARK"
            }) } else sessionThemeMode = it
        },
        playbackCallbacks = DesktopPlaybackCallbacks(
            { playbackCommand { it.play() } }, { playbackCommand { it.pause() } },
            { playbackCommand { it.previous() } }, { playbackCommand { it.next() } },
            { position -> playbackCommand { it.seekTo(position) } },
        ),
        modifier = Modifier.dragAndDropTarget(
            shouldStartDragAndDrop = { event -> acceptsDrop() && event.action == DragAndDropTransferAction.Copy &&
                runCatching { event.awtTransferable.isDataFlavorSupported(DataFlavor.javaFileListFlavor) }.getOrDefault(false) },
            target = dropTarget,
        ).onPreviewKeyEvent { event ->
            if (navigationCurrent() && !lifecycleState.closing && event.type == KeyEventType.KeyDown && (event.isCtrlPressed || event.isMetaPressed) && port != null) {
                if (host != null) {
                    val capture = playerState ?: return@onPreviewKeyEvent false
                    val target = capture.target ?: return@onPreviewKeyEvent false
                    if (reopenRequired || capture.playback.controls != PlaybackControlGate.Allowed) return@onPreviewKeyEvent false
                    val action = when (event.key) {
                        Key.Spacebar -> if (capture.playback.isPlaying) DesktopTransportAction.PAUSE else DesktopTransportAction.PLAY
                        Key.DirectionRight -> DesktopTransportAction.NEXT
                        Key.DirectionLeft -> DesktopTransportAction.PREVIOUS
                        else -> return@onPreviewKeyEvent false
                    }
                    host.transport(target, action)
                    true
                } else {
                    when (event.key) {
                        Key.Spacebar -> { if (playback.isPlaying) port.pause() else port.play(); true }
                        Key.DirectionRight -> { port.next(); true }
                        Key.DirectionLeft -> { port.previous(); true }
                        else -> false
                    }
                }
            } else false
        },
        library = library, importing = importing, importResult = importResult,
        errorCode = if (reopenRequired) "desktop_snapshot_unavailable" else hostError ?: playbackError,
        preferencesState = preferencesState,
        preferencesTransfer = preferencesTransfer ?: app.autplay.application.desktop.DesktopSettingsTransferState(),
        preferencesCallbacks = DesktopSettingsCallbacks(
            onLanguage = { value -> command { it.settings.setAppLanguage(value) } },
            onAppearance = { value -> command { it.settings.setAppearanceMode(value) } },
            onAccent = { value -> command { it.settings.setAccentPalette(value) } },
            onImport = { command { it.settings.importSettings(chooserOwner) } },
            onConfirmImport = { preview -> command { it.settings.confirmImport(preview) } },
            onDismissImport = { preview -> command { it.settings.dismissImport(preview) } },
            onExport = { command { it.settings.exportSettings(chooserOwner) } },
            onCancelTransfer = { command { it.settings.cancelTransfer() } },
            onSmoothTrackTransitions = { value -> command { it.settings.setSmoothTrackTransitions(value) } },
        ),
        accentPalette = accentPalette,
        sleepTimer = sleepTimer,
        tasteState = taste,
        historyState = history,
        localStatisticsState = localStatistics,
        localStatisticsCallbacks = DesktopLocalStatisticsCallbacks(
            onRefresh = { expected -> command { it.refreshLocalStatistics(expected) } },
            onRetry = { expected -> command { it.retryLocalStatistics(expected) } },
            isCurrent = { expected -> host?.localStatistics?.state?.value === expected && navigationCurrent() },
        ),
        textSearch = host?.textSearch,
        metadataState = metadata,
        copyInspectionState = copyInspection,
        copyInspectionCallbacks = DesktopCopyInspectionCallbacks(
            onInspect = { ids -> command { it.copyInspection.inspect(ids) } },
            onCancel = { request -> command { it.copyInspection.cancel(request) } },
        ),
        metadataCallbacks = DesktopMetadataCallbacks(
            onInspect = { track -> command { it.metadata.inspect(track) } },
            onCancel = { request -> command { it.metadata.cancel(request) } },
            onApply = { preview -> command { it.metadata.apply(preview) } },
            onDismiss = { preview -> command { it.metadata.dismiss(preview) } },
        ),
        historyCallbacks = DesktopHistoryCallbacks(
            onRefresh = { command { it.refreshHistory() } },
            onLoadMore = { revision -> command { it.loadMoreHistory(revision) } },
            onRetry = { revision -> command { it.retryHistory(revision) } },
        ),
        tasteCallbacks = DesktopTasteCallbacks(
            onSetCurrentListenExcluded = { queueId, entryId, listeningId, excluded ->
                command { it.controller.setCurrentListenTasteExcluded(queueId, entryId, listeningId, excluded) }
            },
            onSetQueueSessionExcluded = { queueId, excluded ->
                command { it.controller.setQueueSessionTasteExcluded(queueId, excluded) }
            },
        ),
        sleepTimerCallbacks = DesktopSleepTimerCallbacks(
            onSchedule = { queueId, durationMs -> command { it.controller.scheduleSleepTimer(queueId, durationMs) } },
            onStopAfterCurrent = { queueId, entryId, sessionId -> command { it.controller.stopAfterCurrent(queueId, entryId, sessionId) } },
            onCancel = { planId -> command { it.controller.cancelSleepTimer(planId) } },
        ),
        libraryCallbacks = DesktopLibraryCallbacks(
            onImport = { command { it.importSelected(chooserOwner) } }, onCancelImport = { command { it.cancelImport() } },
            onPlayTracks = { ids, type -> command { it.controller.playTracks(ids, type) } },
            onContinueQueue = { queue, entry, session -> command { it.controller.resumeCurrentQueue(queue, entry, session) } },
            onEnqueue = { track -> command { it.controller.enqueue(track) } }, onSelectEntry = { entry -> command { it.controller.selectEntry(entry) } },
            onRemoveEntry = { entry -> command { it.controller.removeEntry(entry) } }, onCreatePlaylist = { name -> command { it.createPlaylist(name) } },
            onAddToPlaylist = { playlist, track -> command { it.addToPlaylist(playlist, track) } },
            onRemovePlaylistEntry = { playlist, entry -> command { it.removePlaylistEntry(playlist, entry) } },
            onRenamePlaylist = { playlist, name -> command { it.renamePlaylist(playlist, name) } },
            onUpdatePlaylistDescription = { playlist, previous, description ->
                command { it.updatePlaylistDescription(playlist, previous, description) }
            },
            onMovePlaylistEntry = { playlist, entry, direction -> command { it.movePlaylistEntry(playlist, entry, direction) } },
            onUpdateTrackMetadata = { track, title, artist -> command { it.updateTrackMetadata(track, title, artist) } },
            onDeletePlaylist = { playlist -> command { it.deletePlaylist(playlist) } },
            onEnqueuePlaylist = { playlist -> command { it.controller.enqueuePlaylist(playlist) } },
            onPlayPlaylistNext = { playlist -> command { it.controller.playPlaylistNext(playlist) } },
            onSetTrackPreference = { track, preference -> command { it.setTrackPreference(track, preference) } },
            onRemoveTrackFromCollection = { track -> command { it.removeTrackFromCollection(track) } },
            onRestoreTrackToCollection = { track -> command { it.restoreTrackToCollection(track) } },
            onShuffle = { enabled -> command { it.controller.setShuffle(enabled) } }, onRepeat = { mode -> command { it.controller.setRepeat(mode) } },
            onPlayNext = { track -> command { it.controller.playNext(track) } },
            onMoveUpcoming = { entry, direction -> command { it.controller.moveUpcoming(entry, direction) } },
            onClearUpcoming = { command { it.controller.clearUpcoming() } },
            onEnqueueTracks = { ids -> command { it.controller.enqueueTracks(ids) } },
            onPlayTracksNext = { ids -> command { it.controller.playTracksNext(ids) } },
            onAddTracksToPlaylist = { playlist, ids -> command { it.addTracksToPlaylist(playlist, ids) } },
            onEditUpcoming = { target, action, ids -> command { it.controller.editUpcoming(target, action, ids) } },
        ),
        playerState = playerState,
        transportCallbacks = DesktopTransportCallbacks { target, action, position -> command { it.transport(target, action, position) } },
        importReviewState = importReview,
        importReviewCallbacks = DesktopImportReviewCallbacks(
            onConfirm = { preview -> command { it.confirmImport(preview) } },
            onCancel = { command { it.cancelImport() } },
            isCurrent = { expected -> navigationCurrent() && !lifecycleState.closing &&
                host?.importReview?.value === expected && host?.importMapping?.value === importMapping &&
                host?.store?.state?.value === library },
        ),
        savedImportsState = savedImports,
        importMapping = importMapping,
        importJobProgress = lastImportProgress,
        savedImportsCallbacks = DesktopSavedImportsCallbacks(
            onRefresh = { expected -> command { it.refreshSavedImports(expected) } },
            onNext = { expected -> command { it.nextSavedImports(expected) } },
            onRetry = { expected -> command { it.retrySavedImports(expected) } },
            onDismiss = { expected -> command { it.savedImports.dismiss(expected) } },
            onResume = { expected, job -> command { it.resumeImport(expected, job, chooserOwner) } },
            onMap = { expected, candidate, selection -> command { it.mapImportCandidate(expected, candidate, selection) } },
            onConfirm = { expected -> command { it.confirmImportMapping(expected) } },
            isCurrent = { expected -> navigationCurrent() && !lifecycleState.closing &&
                host?.savedImports?.state?.value === expected && host?.store?.state?.value === library },
            isMappingCurrent = { expected -> navigationCurrent() && !lifecycleState.closing &&
                host?.importMapping?.value === expected && host?.store?.state?.value === library &&
                host?.error?.value != "desktop_snapshot_unavailable" && host?.controller?.error?.value != "desktop_snapshot_unavailable" },
        ),
        lifecycleState = lifecycleState,
        lifecycleCallbacks = lifecycleCallbacks,
        sourceAccessState = rememberedSources,
        sourceAccessActions = host?.sources?.actions,
        onResumeSourceAccess = { expected -> command { it.resumeRememberedSources(expected) } },
        onOpenAccount = if (accountWorkspace != null) ({ openAccounts() }) else null,
        homeAccountContent = if (accountHomeState != null) ({
            DesktopAccountHomePreview(accountHomeState, language, ::openAccounts)
        }) else null,
        libraryKind = libraryKind,
        onLibraryKindChange = { next ->
            if (navigationCurrent() && !lifecycleState.closing && libraryKind != next) {
                requestNavigation(section, next, false)
            }
        },
        libraryCatalogContent = if (accountCatalogState != null && catalogKind != null && accountWorkspace != null) ({
            DesktopLibraryCatalogBrowser(accountCatalogState, accountWorkspace.actions, catalogKind, language,
                isNavigationCurrent = { navigationCurrent() && accountCatalogVisible && !lifecycleState.closing })
        }) else null,
    )
}
