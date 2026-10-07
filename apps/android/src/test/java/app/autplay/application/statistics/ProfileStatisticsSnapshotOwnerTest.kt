package app.autplay.application.statistics

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfileStatisticsSnapshotOwnerTest {
    @Test
    fun initialLoadIsOnceAndExplicitRefreshRetainsSnapshotAndCoalescesTaps() = runBlocking {
        var calls = 0
        var gate = CompletableDeferred<OwnerProfileStatistics>()
        val owner = ProfileStatisticsSnapshotOwner(this, "profile-a") { profile ->
            assertEquals("profile-a", profile)
            calls++
            gate.await()
        }
        owner.loadIfNeeded()
        owner.loadIfNeeded()
        owner.refresh()
        yield()
        assertEquals(1, calls)
        assertTrue(owner.state.value.loading)
        val first = snapshot(1)
        gate.complete(first)
        yield()
        assertSame(first, owner.state.value.snapshot)
        assertFalse(owner.state.value.loading)
        repeat(10) { owner.loadIfNeeded() }
        yield()
        assertEquals(1, calls)

        gate = CompletableDeferred()
        owner.refresh()
        owner.refresh()
        yield()
        assertEquals(2, calls)
        assertTrue(owner.state.value.loading)
        assertSame(first, owner.state.value.snapshot)
        val refreshed = snapshot(2)
        gate.complete(refreshed)
        yield()
        assertSame(refreshed, owner.state.value.snapshot)
    }

    @Test
    fun initialErrorDoesNotSpinAndRefreshErrorKeepsLastSuccessfulSnapshot() = runBlocking {
        var calls = 0
        var fail = true
        val owner = ProfileStatisticsSnapshotOwner(this, null) {
            calls++
            if (fail) error("storage unavailable")
            snapshot(calls.toLong())
        }
        owner.loadIfNeeded()
        yield()
        assertTrue(owner.state.value.refreshFailed)
        assertEquals(null, owner.state.value.snapshot)
        owner.loadIfNeeded()
        yield()
        assertEquals(1, calls)
        fail = false
        owner.refresh()
        yield()
        val saved = owner.state.value.snapshot
        assertFalse(owner.state.value.refreshFailed)
        fail = true
        owner.refresh()
        yield()
        assertTrue(owner.state.value.refreshFailed)
        assertFalse(owner.state.value.loading)
        assertSame(saved, owner.state.value.snapshot)
    }

    @Test
    fun disposingProfileCancelsItsPendingRead() = runBlocking {
        val scope = CoroutineScope(coroutineContext + SupervisorJob())
        val gate = CompletableDeferred<OwnerProfileStatistics>()
        val owner = ProfileStatisticsSnapshotOwner(scope, "profile-a") { gate.await() }
        try {
            owner.loadIfNeeded()
            yield()
            owner.close()
            yield()
            gate.complete(snapshot(1))
            yield()
            assertEquals(null, owner.state.value.snapshot)
            assertFalse(owner.state.value.refreshFailed)
        } finally {
            scope.cancel()
        }
    }

    private fun snapshot(time: Long) = OwnerProfileStatistics(time, time, emptyList(), emptyList(), emptyList())
}
