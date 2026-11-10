package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopTextSearchMatches
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.desktop.DesktopRuntime
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.settings.DesktopSettingsController
import java.lang.reflect.Proxy
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue

/** Future existing queue-composite hook only. No test entry, database, files, settings or native IO. */
internal suspend fun assertFtsSearchHostPolicy(): Unit {
    assertFtsSearchHostLifetime(snapshotRefusal = false)
    assertFtsSearchHostLifetime(snapshotRefusal = true)
}

private suspend fun assertFtsSearchHostLifetime(snapshotRefusal: Boolean): Unit = coroutineScope {
    val ownerJob = SupervisorJob()
    val ownerScope = CoroutineScope(ownerJob + Dispatchers.Default)
    val state = MutableStateFlow(DesktopLibrarySnapshot())
    val storeCloses = AtomicInteger()
    val search = HostSearchFixture()
    val store = Proxy.newProxyInstance(DesktopLibraryStore::class.java.classLoader,
        arrayOf(DesktopLibraryStore::class.java)) { _, method, _ ->
        when (method.name) {
            "getState" -> state
            "close" -> { assertTrue(search.closed.get()); storeCloses.incrementAndGet(); Unit }
            else -> error("unexpected_fts_host_store_call")
        }
    } as DesktopLibraryStore
    val preferences = object : DesktopPreferencesStore {
        override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState())
        override suspend fun setAppLanguage(value: String): Unit = error("unexpected_fts_settings_write")
        override suspend fun setAppearanceMode(value: String): Unit = error("unexpected_fts_settings_write")
        override suspend fun setAccentPalette(value: String): Unit = error("unexpected_fts_settings_write")
        override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("unexpected_fts_settings_write")
        override suspend fun applyPortable(value: DesktopPreferences): Unit = error("unexpected_fts_settings_write")
        override suspend fun close(): Unit = Unit
    }
    val controller = DesktopPlaybackController(store, PolicyEngine(), ownerScope, periodicObservations = emptyFlow())
    val settings = DesktopSettingsController(preferences, ownerScope)
    val importer = object : DesktopImportPort {
        override suspend fun importSelected(paths: List<String>): DesktopImportResult = error("unexpected_fts_import")
    }
    val constructor = DesktopRuntime::class.java.declaredConstructors.single { !it.isSynthetic && it.parameterCount == 9 }
        .apply { isAccessible = true }
    val host = constructor.newInstance(store, importer, controller, ownerScope, settings, search, null, null, null) as DesktopRuntime
    try {
        assertSame(search, host.textSearch)
        assertEquals("tracks", host.textSearch.search(state.value, "tracks").query)
        assertEquals("playlists", host.textSearch.search(state.value, "playlists").query)
        assertEquals(2, search.calls.get())
        search.hold.set(true)
        val read = async(Dispatchers.Default) { host.textSearch.search(state.value, "held") }
        withTimeout(2_000) { search.entered.await() }
        if (snapshotRefusal) {
            controller.reportSnapshotUnavailable()
            eventually("global snapshot refusal retires FTS admission") { search.retired.get() }
        } else {
            host.beginClose()
            assertTrue(search.retired.get()) // Synchronous, before suspendable cleanup can start.
        }
        assertEquals("desktop_search_closed", host.textSearch.search(state.value, "late").errorCode)
        val close = async(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) { host.close() }
        withTimeout(2_000) { search.closeEntered.await() }
        assertFalse(close.isCompleted)
        assertEquals(0, storeCloses.get())
        search.release.complete(Unit)
        assertEquals("desktop_search_closed", withTimeout(2_000) { read.await() }.errorCode)
        withTimeout(5_000) { close.await() }
        assertTrue(search.closed.get())
        assertEquals(1, storeCloses.get())
        host.close()
        assertEquals(1, storeCloses.get())
        assertSame(search, host.textSearch)
    } finally {
        search.release.complete(Unit)
        host.close()
        ownerJob.cancelAndJoin()
    }
}

/** Models a synchronous native step that finishes after cancellation, without invoking SQLite. */
private class HostSearchFixture : DesktopTextSearchPort {
    val retired: AtomicBoolean = AtomicBoolean(false)
    val closed: AtomicBoolean = AtomicBoolean(false)
    val hold: AtomicBoolean = AtomicBoolean(false)
    val calls: AtomicInteger = AtomicInteger()
    val entered: CompletableDeferred<Unit> = CompletableDeferred()
    val release: CompletableDeferred<Unit> = CompletableDeferred()
    val closeEntered: CompletableDeferred<Unit> = CompletableDeferred()
    private val mutex: Mutex = Mutex()

    override suspend fun search(snapshot: DesktopLibrarySnapshot, query: String): DesktopTextSearchMatches {
        fun refusal(): DesktopTextSearchMatches = DesktopTextSearchMatches(query, true, errorCode = "desktop_search_closed")
        if (retired.get()) return refusal()
        return mutex.withLock {
            if (retired.get()) return@withLock refusal()
            calls.incrementAndGet()
            if (hold.get()) {
                entered.complete(Unit)
                withContext(NonCancellable) { release.await() }
            }
            if (retired.get()) refusal() else DesktopTextSearchMatches(query, true)
        }
    }

    override fun retire(): Unit { retired.set(true) }

    override suspend fun close(): Unit = withContext(NonCancellable) {
        retire()
        closeEntered.complete(Unit)
        mutex.withLock { closed.set(true) }
    }
}
