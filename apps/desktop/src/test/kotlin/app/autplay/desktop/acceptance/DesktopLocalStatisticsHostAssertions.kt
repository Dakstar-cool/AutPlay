package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalStatistics
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.desktop.history.DesktopLocalStatisticsController
import java.lang.reflect.Proxy
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue

/** Source scenarios within the existing queue composite; no new entry point or real IO. */
internal suspend fun assertDesktopLocalStatisticsHostPolicy(): Unit {
    assertStatisticsRefreshAndRetry()
    assertStatisticsRetirementAndDrain(sticky = false)
    assertStatisticsRetirementAndDrain(sticky = true)
}

private suspend fun assertStatisticsRefreshAndRetry(): Unit {
    val owner = SupervisorJob()
    val scope = CoroutineScope(owner + Dispatchers.Default)
    val clock = AtomicLong(100)
    val cutoffs = CopyOnWriteArrayList<Long>()
    var fail = false
    val store = statisticsStore { cutoff ->
        cutoffs += cutoff
        if (fail) error("private_statistics_failure_detail")
        DesktopLocalStatistics(cutoff, 0, 0, emptyList(), emptyList())
    }
    val controller = DesktopLocalStatisticsController(store, scope, { error("unexpected_snapshot_refusal") }, clockMs = clock::get)
    try {
        assertTrue(cutoffs.isEmpty())
        val initial = controller.state.value
        assertNull(initial.snapshot)
        controller.refresh(initial)
        val first = withTimeout(2_000) { controller.state.first { !it.loading && it.snapshot != null } }
        assertEquals(100L, first.snapshot?.throughInclusiveMs)
        assertEquals(0L, first.snapshot?.listenedMs)
        clock.set(200)
        fail = true
        controller.refresh(first)
        val failed = withTimeout(2_000) { controller.state.first { !it.loading && it.errorCode != null } }
        assertSame(first.snapshot, failed.snapshot)
        assertEquals("desktop_local_statistics_load_failed", failed.errorCode)
        fail = false
        clock.set(300)
        controller.retry(failed)
        val retried = withTimeout(2_000) { controller.state.first { !it.loading && it.snapshot?.throughInclusiveMs == 200L } }
        assertEquals(listOf(100L, 200L, 200L), cutoffs.toList())
        controller.refresh(initial)
        controller.retry(failed)
        assertSame(retried, controller.state.value)
        controller.beginClose()
        controller.refresh(controller.state.value)
        assertEquals(3, cutoffs.size)
    } finally {
        controller.close()
        owner.cancelAndJoin()
    }
}

private suspend fun assertStatisticsRetirementAndDrain(sticky: Boolean): Unit {
    val owner = SupervisorJob()
    val scope = CoroutineScope(owner + Dispatchers.Default)
    val started = CompletableDeferred<Unit>()
    val release = CompletableDeferred<Unit>()
    val calls = AtomicInteger()
    val markers = AtomicInteger()
    val store = statisticsStore { cutoff ->
        calls.incrementAndGet()
        started.complete(Unit)
        withContext(NonCancellable) {
            release.await()
            if (sticky) throw DesktopStorageException("desktop_snapshot_unavailable")
            DesktopLocalStatistics(cutoff, 1, 1, emptyList(), emptyList())
        }
    }
    val controller = DesktopLocalStatisticsController(store, scope, { markers.incrementAndGet() }, clockMs = { 10L })
    try {
        controller.refresh(controller.state.value)
        withTimeout(2_000) { started.await() }
        // Replace the request while the old database operation ignores cancellation.
        controller.refresh(controller.state.value)
        assertEquals(1, calls.get())
        val close = scope.async(start = CoroutineStart.UNDISPATCHED) { controller.close() }
        assertFalse(close.isCompleted)
        val retired = controller.state.value
        controller.refresh(retired)
        release.complete(Unit)
        withTimeout(2_000) { close.await() }
        assertEquals(1, calls.get())
        assertNull(controller.state.value.snapshot)
        assertEquals(if (sticky) 1 else 0, markers.get())
        assertEquals(if (sticky) "desktop_snapshot_unavailable" else null, controller.state.value.errorCode)
    } finally {
        release.complete(Unit)
        controller.close()
        owner.cancelAndJoin()
    }
}

private fun statisticsStore(read: suspend (Long) -> DesktopLocalStatistics): DesktopLibraryStore {
    val unexpected = Proxy.newProxyInstance(DesktopLibraryStore::class.java.classLoader,
        arrayOf(DesktopLibraryStore::class.java)) { _, _, _ -> error("unexpected_statistics_store_effect") } as DesktopLibraryStore
    return object : DesktopLibraryStore by unexpected {
        override suspend fun readLocalStatistics(throughInclusiveMs: Long): DesktopLocalStatistics = read(throughInclusiveMs)
    }
}
