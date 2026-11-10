package app.autplay.desktop.lifecycle

import java.awt.Desktop
import java.awt.EventQueue
import java.awt.desktop.AppReopenedListener
import java.awt.desktop.QuitResponse
import java.awt.desktop.SystemSleepEvent
import java.awt.desktop.SystemSleepListener
import java.util.Locale

public enum class DesktopNativeLifecycleFeature { Settings, Quit, Reopen, SystemSleep }

/** Small injectable boundary; deterministic fixtures need no Desktop instance or native calls. */
public interface DesktopNativeLifecycleApi {
    public val isMacOS: Boolean
    public fun supports(feature: DesktopNativeLifecycleFeature): Boolean
    public fun prepareForOrderlyQuit(): Unit
    public fun setSettingsHandler(handler: (() -> Unit)?): Unit
    public fun setQuitHandler(handler: (QuitResponse) -> Unit): Unit
    public fun addReopenListener(handler: () -> Unit): () -> Unit
    /** Older injected APIs may omit this optional capability. */
    public fun addSystemSleepListener(aboutToSleep: () -> Unit, awoke: () -> Unit): () -> Unit =
        throw UnsupportedOperationException("SYSTEM_SLEEP_UNSUPPORTED")
}

/** Installs on AWT; each installed callback has a retireable, application-free native tail. */
public class DesktopNativeLifecycle private constructor(
    private val lifetime: CallbackLifetime,
    private val lifecycle: DesktopWindowLifecycle,
    private val clearSettings: (() -> Unit)?,
    private val removeReopen: (() -> Unit)?,
    private val removeSystemSleep: (() -> Unit)?,
) {
    public fun retire(): Unit {
        if (!lifetime.retire()) return
        lifecycle.installed(reopen = false, quit = false)
        runCatching { removeSystemSleep?.invoke() }
        runCatching { removeReopen?.invoke() }
        runCatching { clearSettings?.invoke() }
        // Keep the detached Quit handler until process termination. setQuitHandler(null) would
        // restore OS default exit while finally/shutdown-hook cleanup could still be running.
    }

    public companion object {
        public fun install(
            api: DesktopNativeLifecycleApi?,
            lifecycle: DesktopWindowLifecycle,
            onSystemSleep: (() -> Unit)? = null,
        ): DesktopNativeLifecycle {
            val lifetime = CallbackLifetime(lifecycle, onSystemSleep)
            var clearSettings: (() -> Unit)? = null
            var removeReopen: (() -> Unit)? = null
            var removeSystemSleep: (() -> Unit)? = null
            var quitInstalled = false
            if (api != null && api.isMacOS) {
                fun supported(feature: DesktopNativeLifecycleFeature): Boolean = runCatching { api.supports(feature) }.getOrDefault(false)
                if (supported(DesktopNativeLifecycleFeature.Quit)) {
                    quitInstalled = runCatching {
                        api.prepareForOrderlyQuit()
                        api.setQuitHandler { _ ->
                            // QuitHandler explicitly permits ensuring application termination.
                            // Never performQuit (default OS exit), cancelQuit, retain or reuse a
                            // one-shot response. Repeated identical/distinct responses are harmless;
                            // the single IO close owner terminates the app after cleanup settles.
                            lifetime.quit()
                        }
                    }.isSuccess
                }
                if (supported(DesktopNativeLifecycleFeature.Settings)) {
                    clearSettings = { api.setSettingsHandler(null) }
                    runCatching { api.setSettingsHandler(lifetime::settings) }
                }
                if (supported(DesktopNativeLifecycleFeature.Reopen)) {
                    removeReopen = runCatching { api.addReopenListener(lifetime::reopen) }.getOrNull()
                }
                if (onSystemSleep != null && supported(DesktopNativeLifecycleFeature.SystemSleep)) {
                    removeSystemSleep = runCatching {
                        api.addSystemSleepListener(lifetime::aboutToSleep, {})
                    }.getOrNull()
                    if (removeSystemSleep != null) lifetime.enableSystemSleep()
                }
            }
            lifecycle.installed(reopen = removeReopen != null, quit = quitInstalled)
            return DesktopNativeLifecycle(lifetime, lifecycle, clearSettings, removeReopen, removeSystemSleep)
        }
    }

    private class CallbackLifetime(lifecycle: DesktopWindowLifecycle, onSystemSleep: (() -> Unit)?) {
        private val lock: Any = Any()
        private var current: DesktopWindowLifecycle? = lifecycle
        private var systemSleep: (() -> Unit)? = onSystemSleep
        private var systemSleepEnabled: Boolean = false
        fun settings(): Unit = synchronized(lock) { current?.showSettings(); Unit }
        fun reopen(): Unit = synchronized(lock) { current?.showWindow(); Unit }
        fun quit(): Unit = synchronized(lock) { current?.quit(); Unit }
        fun enableSystemSleep(): Unit = synchronized(lock) { systemSleepEnabled = current != null }
        fun aboutToSleep(): Unit = synchronized(lock) {
            val state = current?.state?.value ?: return@synchronized
            if (systemSleepEnabled && !state.closing && !state.disposed) {
                // Capture/submit synchronously at event delivery, before the actor boundary.
                // Java permits delivery after wake; neither pause nor checkpoint is guaranteed
                // to finish before suspension. Wake deliberately performs no application action.
                systemSleep?.invoke()
            }
        }
        fun retire(): Boolean = synchronized(lock) {
            if (current == null) false else {
                current = null
                systemSleep = null
                systemSleepEnabled = false
                true
            }
        }
    }
}

/** Discovery and handler installation happen only inside the window's AWT DisposableEffect. */
public fun desktopNativeLifecycleApi(): DesktopNativeLifecycleApi? = runCatching {
    check(EventQueue.isDispatchThread())
    if (!System.getProperty("os.name").lowercase(Locale.ROOT).contains("mac") || !Desktop.isDesktopSupported()) null
    else JavaDesktopLifecycleApi(Desktop.getDesktop())
}.getOrNull()

private class JavaDesktopLifecycleApi(private val desktop: Desktop) : DesktopNativeLifecycleApi {
    override val isMacOS: Boolean = true

    override fun supports(feature: DesktopNativeLifecycleFeature): Boolean = desktop.isSupported(when (feature) {
        DesktopNativeLifecycleFeature.Settings -> Desktop.Action.APP_PREFERENCES
        DesktopNativeLifecycleFeature.Quit -> Desktop.Action.APP_QUIT_HANDLER
        DesktopNativeLifecycleFeature.Reopen -> Desktop.Action.APP_EVENT_REOPENED
        DesktopNativeLifecycleFeature.SystemSleep -> Desktop.Action.APP_EVENT_SYSTEM_SLEEP
    })

    override fun prepareForOrderlyQuit(): Unit {
        if (desktop.isSupported(Desktop.Action.APP_SUDDEN_TERMINATION)) desktop.disableSuddenTermination()
    }

    override fun setSettingsHandler(handler: (() -> Unit)?): Unit {
        desktop.setPreferencesHandler(if (handler == null) null else java.awt.desktop.PreferencesHandler { handler() })
    }

    override fun setQuitHandler(handler: (QuitResponse) -> Unit): Unit {
        desktop.setQuitHandler { _, response -> handler(response) }
    }

    override fun addReopenListener(handler: () -> Unit): () -> Unit {
        val listener = AppReopenedListener { handler() }
        desktop.addAppEventListener(listener)
        return { desktop.removeAppEventListener(listener) }
    }

    override fun addSystemSleepListener(aboutToSleep: () -> Unit, awoke: () -> Unit): () -> Unit {
        val listener = object : SystemSleepListener {
            override fun systemAboutToSleep(e: SystemSleepEvent): Unit = aboutToSleep()
            override fun systemAwoke(e: SystemSleepEvent): Unit = awoke()
        }
        desktop.addAppEventListener(listener)
        return { desktop.removeAppEventListener(listener) }
    }
}
