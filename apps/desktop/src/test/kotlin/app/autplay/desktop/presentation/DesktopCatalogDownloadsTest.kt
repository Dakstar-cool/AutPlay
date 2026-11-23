package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import java.util.concurrent.atomic.AtomicInteger
import java.util.prefs.Preferences
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.junit.Assert.*
import org.junit.Test

class DesktopCatalogDownloadsTest {
    @Test fun restoresOnlyApprovedWorkForItsOwnerAndReusesOperation() = runBlocking {
        val lease = AccountLease(AccountUuid.random(), AccountUuid.random(), AccountUuid.random(), AccountUuid.random(),
            1, AccountUuid.random(), 1, AccountUuid.random(), 1, 1)
        val other = lease.copy(scopeId = AccountUuid.random())
        val prefs = Preferences.userRoot().node("app/autplay/tests/" + AccountUuid.random().value)
        val first = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val resumed = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val target = CatalogTarget("artist", AccountUuid.random(), false)
        val entered = CompletableDeferred<Unit>()
        val starts = AtomicInteger()
        val syncs = AtomicInteger()
        var active = lease
        var phase = "preview"
        fun worker(scope: CoroutineScope, interrupted: Boolean) = DesktopCatalogDownloads(scope, { active },
            { _, id, _, _, _ -> CatalogJob(id, phase, emptyMap()) },
            { _, id, verb ->
                if (verb == "start") { starts.incrementAndGet(); phase = "discovery" }
                if (verb == "advance" && interrupted) { entered.complete(Unit); awaitCancellation() }
                if (verb == "advance") phase = "complete"
                CatalogJob(id, phase, emptyMap())
            }, { syncs.incrementAndGet() }, prefs)
        try {
            withTimeout(10_000) {
                val initial = worker(first, true)
                initial.submit(lease, target)
                val ready = initial.states.first { it[lease to target]?.job?.phase == "preview" }
                val operation = ready.getValue(lease to target).job!!.id
                assertEquals(0, starts.get())
                initial.submit(lease, target, start = true)
                entered.await()
                first.coroutineContext.job.cancelAndJoin()
                active = other
                val next = worker(resumed, false)
                assertFalse(next.hasOperation(other, target))
                active = lease
                next.restore(lease)
                val done = next.states.first { it[lease to target]?.job?.phase == "complete" }
                assertEquals(operation, done.getValue(lease to target).job!!.id)
                assertEquals(1, starts.get())
                assertEquals(1, syncs.get())
            }
        } finally {
            first.cancel(); resumed.cancel()
            prefs.removeNode(); prefs.flush()
        }
    }
}
